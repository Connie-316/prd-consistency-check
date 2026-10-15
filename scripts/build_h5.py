#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 PRD .md（唯一可信源）生成 h5 阅读版 HTML（通用版，配置驱动）。

背景：手工维护 h5 阅读版必然与 .md 漂移（本项目曾脱节 13 天）。
改为脚本生成，并在 h5 头部写入 .md 内容指纹（sha256 前12位），
供 check_consistency.py 校验两者是否同源。

用法：
    python3 build_h5.py [--config <配置.json>] [--src <md>] [--dst <html>] [--title <标题>]

    - 默认从同目录的 consistency_config.json 读取 files.md / files.h5 / module。
    - 依赖：pip install markdown
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

try:
    import markdown
except ImportError:
    sys.exit("缺少依赖：pip install markdown")

DEFAULT_CONFIG_NAME = "consistency_config.json"

CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
       background: #f5f6f8; color: #333; line-height: 1.7; }
.container { max-width: 960px; margin: 0 auto; padding: 20px; }
.doc-header { background: linear-gradient(135deg, #1664FF 0%, #0958D9 100%); color: #fff;
              padding: 36px 30px; border-radius: 12px; margin-bottom: 20px; }
.doc-header h1 { font-size: 26px; margin-bottom: 12px; }
.doc-meta { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.doc-meta-item { background: rgba(255,255,255,0.2); padding: 4px 12px; border-radius: 4px; font-size: 13px; }
.genbar { background: #FFFBE6; border: 1px solid #FFE58F; border-radius: 8px; padding: 10px 14px;
          font-size: 12px; color: #7C5000; margin-bottom: 20px; }
.genbar code { background: #fff; padding: 1px 5px; border-radius: 3px; }
.content { background: #fff; border-radius: 12px; padding: 28px 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.content h1 { display: none; }
.content h2 { font-size: 20px; color: #1A1A1A; margin: 32px 0 14px; padding-bottom: 10px;
              border-bottom: 2px solid #E8F0FF; }
.content h2:first-of-type { margin-top: 0; }
.content h3 { font-size: 16px; color: #1664FF; margin: 24px 0 10px; }
.content h4 { font-size: 14px; color: #0958D9; margin: 18px 0 8px; }
table { width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }
th, td { padding: 9px 12px; text-align: left; border: 1px solid #E5E5E5; vertical-align: top; }
th { background: #F7F8FA; font-weight: 600; }
tr:hover td { background: #FAFAFA; }
ul, ol { padding-left: 22px; margin: 8px 0; }
li { margin-bottom: 5px; font-size: 14px; }
p { margin: 10px 0; font-size: 14px; }
blockquote { background: #F7F8FA; border-left: 4px solid #1664FF; border-radius: 0 8px 8px 0;
             padding: 12px 16px; margin: 12px 0; font-size: 13px; color: #444; }
blockquote p { margin: 6px 0; }
pre { background: #F7F8FA; border-radius: 8px; padding: 14px 16px; margin: 12px 0;
      overflow-x: auto; border: 1px solid #EEE; }
pre code { font-family: "SF Mono", Consolas, Monaco, monospace; font-size: 12px; line-height: 1.75;
           background: none; padding: 0; color: #333; }
code { font-family: "SF Mono", Consolas, Monaco, monospace; font-size: 12.5px;
       background: #F0F2F5; padding: 1px 5px; border-radius: 3px; color: #C7254E; }
hr { border: none; border-top: 1px dashed #DDD; margin: 28px 0; }
strong { color: #1A1A1A; }
.toc { background: #F7F8FA; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; font-size: 13px; }
.toc-title { font-weight: 600; margin-bottom: 8px; color: #1664FF; }
.toc a { color: #444; text-decoration: none; display: inline-block; margin: 3px 14px 3px 0; }
.toc a:hover { color: #1664FF; text-decoration: underline; }
@media (max-width: 640px) {
  .container { padding: 12px; }
  .content { padding: 18px 16px; }
  table { font-size: 12px; }
  th, td { padding: 7px 8px; }
}
"""


def extract_meta(md_text: str) -> dict:
    meta = {}
    for key in ("版本", "最后更新", "更新日期", "创建日期", "状态", "作者"):
        m = re.search(rf"^\|\s*{key}\s*\|\s*(.+?)\s*\|", md_text, re.M)
        if m:
            meta[key] = m.group(1).strip()
    return meta


def build_toc(html: str) -> str:
    items = re.findall(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S)
    if not items:
        return ""
    links = "".join(
        f'<a href="#{a}">{re.sub(r"<[^>]+>", "", t).strip()}</a>'
        for a, t in items
    )
    return f'<div class="toc"><div class="toc-title">目录</div>{links}</div>'


def main() -> None:
    ap = argparse.ArgumentParser(description="从 PRD .md 生成 h5（配置驱动）")
    ap.add_argument("--config", help="consistency_config.json 路径")
    ap.add_argument("--src", help="PRD .md 路径（覆盖配置）")
    ap.add_argument("--dst", help="输出 h5 路径（覆盖配置）")
    ap.add_argument("--title", help="页面标题（覆盖配置）")
    args = ap.parse_args()

    script_dir = pathlib.Path(__file__).resolve().parent
    cfg_path = pathlib.Path(args.config) if args.config else (script_dir / DEFAULT_CONFIG_NAME)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    files = cfg.get("files", {})
    root = cfg_path.parent.parent

    src = pathlib.Path(args.src) if args.src else (root / files["md"] if files.get("md") else None)
    dst = pathlib.Path(args.dst) if args.dst else (root / files["h5"] if files.get("h5") else None)
    module = args.title or cfg.get("module", "PRD")
    if not src or not dst:
        sys.exit("[参数缺失] 需通过 --src/--dst 或配置 files.md/files.h5 指定路径")

    md_text = src.read_text(encoding="utf-8")
    digest = hashlib.sha256(md_text.encode("utf-8")).hexdigest()[:12]
    meta = extract_meta(md_text)
    version = meta.get("版本", "未知")
    updated = meta.get("最后更新") or meta.get("更新日期") or "未知"

    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
        output_format="html5",
    )
    toc = build_toc(body)
    meta_chips = "".join(f'<span class="doc-meta-item">{k}：{v}</span>' for k, v in meta.items())

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{module} PRD {version}</title>
<!-- AUTOGENERATED: 本文件由 build_h5.py 从 PRD .md 自动生成，请勿手改 -->
<!-- SOURCE_DIGEST: {digest} -->
<style>{CSS}</style>
</head>
<body>
<div class="container">
  <div class="doc-header">
    <h1>{module} PRD</h1>
    <div class="doc-meta">{meta_chips}</div>
  </div>
  <div class="genbar">
    ⚙️ 本页由 <code>build_h5.py</code> 从 <code>{src.name}</code> 自动生成，<strong>请勿直接修改本文件</strong>。
    唯一可信源为 .md；源文件指纹 <code>{digest}</code>，最后更新 <strong>{updated}</strong>。
  </div>
  {toc}
  <div class="content">
{body}
  </div>
</div>
</body>
</html>
"""
    dst.write_text(html, encoding="utf-8")
    print(f"[OK] 已生成 {dst}")
    print(f"     版本 {version} / 最后更新 {updated} / 源指纹 {digest}")


if __name__ == "__main__":
    main()
