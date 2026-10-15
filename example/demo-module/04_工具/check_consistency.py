#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PRD / 原型 自动一致性校验（通用版，配置驱动）。

为什么需要它：
    纯靠 AI 或人"通读一遍"来发现文档矛盾，本质是概率性检查——同一个错误（例如原型里
    "剩余 3 小时 12 分" 却标 "⏰ 中风险"）可能这次抓到、下次漏掉，因为它依赖注意力
    恰好同时落在两个相距 200 行的数字上。
    本脚本把可机械判定的规则固化成确定性断言：只要违反就必然报错，与注意力无关。

用法：
    python3 check_consistency.py [--root <模块目录>] [--config <配置.json>]

    - 默认在同目录查找 consistency_config.json；脚本所在目录视为 <模块>/04_工具/。
    - 退出码 0 = 全部通过；1 = 存在错误（可直接接入 git pre-commit / CI）。

规则清单（enable 见配置 enabled_rules）：
    R0  单一可信源：模块必须存在 PRD .md，否则报告结构性缺口并跳过依赖 PRD 的规则
    R1  版本同步：.md 版本/日期 与 h5 同源（含 SOURCE_DIGEST 比对 + 日期不早于 changelog）
    R2  变更记录：changelog 日期必须升序
    R3  示例数据：原型待发货行的剩余时间 与 风险等级标签/行底色 符合阈值
    R4  软件订单：软件订单不进预警池（不得显示待发货/可点发货/有风险标签）
    R5  配色：色彩语义只服务风险等级，商品类型标签不得复用风险色
    R6  阈值：4 级阈值在所有文档一致；「提前催办」仅否定语境
    R7  精度表述：倒计时精度在 PRD/原型必须一致（如精确到秒）
    R8  表格结构：原型表格每行单元格数须与表头一致
    R9  章节引用：§x.y 交叉引用须指向真实存在的标题
    R10 字段定义：原型出现的字段在 PRD 须被显式定义并说明关系
    R11 门户版本：门户入口的版本号/日期须与 .md 一致
    R12 数值声明：PRD 声明的数量（"N 种"/"N 个维度"）须与实际一致（配置驱动）
    R13 术语统一：PRD 定义的正典术语，原型/门户/h5 不得出现禁用同义表述（配置驱动）
    R14 待定项追踪：统计开放问题标记，提示是否缺集中追踪表（仅警告）
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

# ----------------------------------------------------------------------- 配置
DEFAULT_CONFIG_NAME = "consistency_config.json"

DATE_KEYS = ("最后更新", "更新日期")  # 兼容两种表头写法


def load_config(script_dir: pathlib.Path, cli_config, cli_root):
    candidates = []
    if cli_config:
        candidates.append(pathlib.Path(cli_config))
    candidates.append(script_dir / DEFAULT_CONFIG_NAME)
    candidates.append(pathlib.Path.cwd() / DEFAULT_CONFIG_NAME)
    for c in candidates:
        if c.exists():
            cfg = json.loads(c.read_text(encoding="utf-8"))
            root = pathlib.Path(cli_root) if cli_root else (c.parent.parent)
            return cfg, root.resolve()
    sys.exit(
        f"[配置缺失] 未找到 {DEFAULT_CONFIG_NAME}（搜索位置：--config / 脚本目录 / 当前目录）\n"
        "请参考 consistency_config.example.json 创建配置。"
    )


def resolve(root: pathlib.Path, rel: str | None) -> pathlib.Path | None:
    if not rel:
        return None
    p = pathlib.Path(rel)
    return p if p.is_absolute() else (root / p)


errors: list[str] = []
warns: list[str] = []


def err(rule: str, msg: str) -> None:
    errors.append(f"[{rule}] {msg}")


def warn(rule: str, msg: str) -> None:
    warns.append(f"[{rule}] {msg}")


def enabled(cfg: dict, code: str) -> bool:
    return code in cfg.get("enabled_rules", [])


def extract_field(text: str, *names: str) -> str | None:
    for n in names:
        m = re.search(rf"^\|\s*{n}\s*\|\s*(.+?)\s*\|\s*$", text, re.M)
        if m:
            return m.group(1).strip()
    return None


def parse_duration(text: str) -> float | None:
    """把 '4天2小时' / '5小时22分' / '32分' 解析为小时数。"""
    text = text.strip()
    if not re.fullmatch(r"[\d天小时分]+", text):
        return None
    d = re.search(r"(\d+)\s*天", text)
    h = re.search(r"(\d+)\s*小时", text)
    m = re.search(r"(\d+)\s*分", text)
    if not (d or h or m):
        return None
    return (int(d.group(1)) * 24 if d else 0) + (int(h.group(1)) if h else 0) + (
        int(m.group(1)) / 60 if m else 0
    )


def expected_level(hours: float, rules: list[dict]) -> str:
    for r in rules:
        lo, hi = r["lo"], r["hi"]
        if (r is rules[0] and hours <= hi) or (lo < hours <= hi):
            return r["key"]
    return "none"


# ---------------------------------------------------------------- 规则 0
def check_single_source(cfg, md_path):
    """R0 模块必须有 PRD .md 作为单一可信源。"""
    if not md_path:
        warn("R0-单一可信源",
             f"{cfg.get('module','本模块')} 缺少 PRD .md，无单一可信源（结构性缺口），依赖 PRD 的规则已跳过")


# ---------------------------------------------------------------- 规则 1
def check_version_sync(cfg, root, md_text, h5_text, md_path):
    """R1 .md 版本/日期 与 h5 同源（防 h5 快照脱节）。"""
    ver = re.search(r"^\|\s*版本\s*\|\s*(.+?)\s*\|\s*$", md_text, re.M)
    upd = extract_field(md_text, *DATE_KEYS)
    if not ver:
        err("R1-版本同步", f"{md_path.name} 文档信息表缺少「版本」行")
        return
    md_ver, md_upd = ver.group(1), upd
    if not upd:
        warn("R1-版本同步", f"{md_path.name} 文档信息表缺少「更新日期/最后更新」行（建议补充，否则无法校验 h5 日期同源）")

    if not h5_text:
        warn("R1-版本同步", "未配置 h5 路径，跳过 .md↔h5 同源校验")
        return
    if md_ver not in h5_text:
        err("R1-版本同步", f"h5 中找不到 .md 的版本号 {md_ver}，h5 疑似过期快照")
    if md_upd and md_upd not in h5_text:
        err("R1-版本同步", f"h5 中找不到 .md 的更新日期「{md_upd}」，h5 疑似过期快照")

    digest = hashlib.sha256(md_text.encode("utf-8")).hexdigest()[:12]
    m = re.search(r"SOURCE_DIGEST:\s*([0-9a-f]{12})", h5_text)
    if not m:
        err("R1-版本同步", "h5 缺少 SOURCE_DIGEST 标记，无法验证是否由 .md 生成（请运行 build_h5.py）")
    elif m.group(1) != digest:
        err("R1-版本同步", f".md 已修改但 h5 未重新生成（指纹 {digest} ≠ h5 记录 {m.group(1)}），请运行 build_h5.py")

    if md_upd:
        dates = re.findall(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*\*{0,2}v", md_text, re.M)
        if dates and max(dates) > md_upd:
            err("R1-版本同步", f"「更新日期」={md_upd} 早于 changelog 最新条目 {max(dates)}，版本元数据失真")


# ---------------------------------------------------------------- 规则 2
def check_changelog_order(cfg, md_text):
    """R2 变更记录必须按日期升序。"""
    term = cfg.get("changelog_heading", "变更记录")
    m = re.search(r"^#{2,3}\s+.*?" + re.escape(term), md_text, re.M)
    if not m:
        err("R2-变更记录", f"找不到包含「{term}」的章节标题")
        return
    body = md_text[m.end():]
    nxt = re.search(r"^#{2,3}\s+", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    rows = re.findall(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\**v[\d.]+\**[^|]*)\|", body, re.M)
    dates = [d for d, _ in rows]
    if not dates:
        err("R2-变更记录", "变更记录表未解析到任何日期行")
        return
    for i in range(1, len(dates)):
        if dates[i] < dates[i - 1]:
            err("R2-变更记录", f"日期顺序颠倒：第 {i + 1} 行 {dates[i]} 早于上一行 {dates[i - 1]}")


# ---------------------------------------------------------------- 规则 3
def check_prototype_risk_rows(cfg, proto_text):
    """R3 原型待发货表：剩余时间 与 风险等级标签/行底色 符合阈值。"""
    rules = cfg["risk_rules"]
    row_bg = {r["key"]: r.get("row_bg") for r in rules}
    anchor = cfg.get("prototype", {}).get("risk_table_anchor", "<!-- 红色紧急 -->")
    tbody = re.search(re.escape(anchor) + r"(.*?)</tbody>", proto_text, re.S)
    if not tbody:
        err("R3-示例数据", f"原型中找不到待发货表格 tbody（锚点 {anchor}）")
        return
    rows = re.findall(r"<tr([^>]*)>(.*?)</tr>", tbody.group(1), re.S)
    checked = 0
    for attrs, body in rows:
        if "expand-row" in attrs:
            continue
        badge = re.search(r'risk-badge\s+(red|orange|yellow|blue)"[^>]*>([^<]*)<', body)
        left = re.search(r'time-left\s+(?:red|orange|yellow|blue|gray)"[^>]*>([^<]*)<', body)
        if not left:
            continue
        hours = parse_duration(left.group(1))
        if hours is None:
            continue
        checked += 1
        actual = badge.group(1) if badge else "none"
        exp = expected_level(hours, rules)
        order = re.search(r'order-link">([^<]*)<', body)
        oid = order.group(1) if order else "?"
        if actual != exp:
            names = {r["key"]: r["label"] for r in rules}
            err("R3-示例数据", f"{oid}：剩余 {left.group(1)}（{hours:.2f}h）标为 "
                f"{names.get(actual, '无风险')}，按阈值应为 {names.get(exp, '无风险')}")
        want_bg = row_bg.get(exp)
        has_bg = re.search(r'class="(risk-red|risk-orange)"', attrs)
        if want_bg and (not has_bg or has_bg.group(1) != want_bg):
            err("R3-示例数据", f"{oid}：{exp} 等级行应有底色 class={want_bg}")
        if not want_bg and has_bg:
            err("R3-示例数据", f"{oid}：{exp} 等级按阈值行背景应为「—」，但存在 class={has_bg.group(1)}")
    if checked < len(rules):
        warn("R3-示例数据", f"仅校验到 {checked} 行示例数据，建议覆盖全部 {len(rules)} 个等级")


# ---------------------------------------------------------------- 规则 4
def check_software_order(cfg, proto_text):
    """R4 软件订单核心规则：不进预警池。"""
    sw_class = cfg.get("prototype", {}).get("software_class", "goods-type software")
    for m in re.finditer(r"<tr>(?:(?!</tr>).)*?" + re.escape(sw_class) + r".*?</tr>", proto_text, re.S):
        row = m.group(0)
        oid = re.search(r'order-link">([^<]*)<', row)
        oid = oid.group(1) if oid else "?"
        if re.search(r'status-tag\s+pending"[^>]*>\s*待发货', row):
            err("R4-软件订单", f"{oid}：软件订单状态显示「待发货」，与「付款后自动处理、不进预警池」矛盾")
        for btn in re.finditer(r"<button([^>]*)>([^<]*)</button>", row):
            if "发货" in btn.group(2) and "已发货" not in btn.group(2) and "disabled" not in btn.group(1):
                err("R4-软件订单", f"{oid}：软件订单存在可点击「发货」按钮，与「自动处理、不进预警池」矛盾")
        if re.search(r"risk-badge", row):
            err("R4-软件订单", f"{oid}：软件订单出现风险等级标签，但它不应进入预警池")


# ---------------------------------------------------------------- 规则 5
def check_color_semantics(cfg, proto_text):
    """R5 配色约束：商品类型标签不得复用风险色系。"""
    risk_colors = set(c.upper() for c in cfg.get("prototype", {}).get("risk_colors", []))
    if not risk_colors:
        return
    sel = cfg.get("prototype", {}).get("goods_type_selector", r"\.goods-type")
    for m in re.finditer(sel + r"[^{]*\{([^}]*)\}", proto_text):
        for c in re.findall(r"#[0-9A-Fa-f]{6}", m.group(1)):
            if c.upper() in risk_colors:
                err("R5-配色", f"{sel} 使用了风险色系 {c}，与风险预警撞色（应统一中性灰）")


# ---------------------------------------------------------------- 规则 6
def check_threshold_consistency(cfg, docs):
    """R6 4 级阈值在所有文档必须一致；「提前催办」仅否定语境。"""
    neg = r"(取消|删除|废弃|移除|不再|残留|无[\"「]?提前催办|双轨)"
    for name, text in docs:
        for m in re.finditer(r"提前催办", text):
            ctx = text[max(0, m.start() - 60): m.end() + 60]
            if not re.search(neg, ctx):
                err("R6-阈值", f"{name}：「提前催办」出现在非否定语境中（若已取消双轨机制请改为否定语境说明）：…{ctx.strip()[:80]}…")
        for bad in re.findall(r"剩余时间\s*≤\s*4\s*h", text):
            err("R6-阈值", f"{name}：出现残留的 4h 触发点 {bad}")
    boundaries = [str(r["hi"]) for r in cfg.get("risk_rules", [])]
    if boundaries:
        md_name, md = docs[0]
        for b in boundaries:
            if b not in md:
                err("R6-阈值", f"{md_name}：缺少阈值边界 {b}h 的定义（来自配置 risk_rules.hi）")


# ---------------------------------------------------------------- 规则 7
def check_precision_wording(cfg, md_text, proto_text):
    """R7 倒计时精度表述必须一致。"""
    kw = cfg.get("precision_keywords", {"second": "精确到秒", "minute_only": "精确到分钟"})
    if kw["second"] not in md_text:
        err("R7-精度表述", f".md 未声明倒计时{kw['second']}")
    if re.search(kw["minute_only"], proto_text) and kw["second"] not in proto_text:
        err("R7-精度表述", f"原型称「{kw['minute_only']}」但未提及「{kw['second']}」，与 .md 不一致")


# ---------------------------------------------------------------- 规则 8
def check_table_structure(cfg, proto_text):
    """R8 原型表格每行 td 数须与表头一致。"""
    for tbl in re.finditer(r"<table[^>]*>(.*?)</table>", proto_text, re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl.group(1), re.S)
        if len(rows) < 2:
            continue
        widths = [len(re.findall(r"<t[dh][^>]*>", r)) for r in rows]
        head = widths[0]
        for i, w in enumerate(widths[1:], start=2):
            if w != head and w != 1 and not re.search(r"colspan", rows[i - 1]):
                err("R8-表格结构", f"表格第 {i} 行有 {w} 个单元格，但表头为 {head} 列，会导致渲染错位")


# ---------------------------------------------------------------- 规则 9
def check_section_refs(cfg, md_text):
    """R9 PRD 内部 §x.y 交叉引用须指向真实存在的标题。"""
    heads = set()
    for m in re.finditer(r"^#{1,4}\s+(\d+(?:\.\d+)*)", md_text, re.M):
        heads.add(m.group(1))
    for m in re.finditer(r"§\s*(\d+(?:\.\d+)*)", md_text):
        ref = m.group(1)
        if ref not in heads and not any(h.startswith(ref + ".") for h in heads):
            err("R9-章节引用", f"引用 §{ref} 不存在于本文档标题中（失效交叉引用）")


# ---------------------------------------------------------------- 规则 10
def check_orthogonal_field(cfg, md_text, proto_text):
    """R10 原型出现的字段在 PRD 须被显式定义并说明与其它字段关系。"""
    for field in cfg.get("orthogonal_fields", []):
        if field in proto_text and field not in md_text:
            err("R10-字段定义", f"原型存在「{field}」列，但 PRD 全文未定义该字段（认知黑洞）")
        if field in md_text:
            ctx = md_text[md_text.find(field) - 200: md_text.find(field) + 600]
            if not re.search(r"存量|不改动|正交|原有|区别|关系", ctx + md_text):
                err("R10-字段定义", f"PRD 提到「{field}」但未说明它与相关字段的关系（是否存量/是否正交）")


# ---------------------------------------------------------------- 规则 11
def check_portal_version(cfg, md_text, portal_text, md_path):
    """R11 门户入口的版本号/日期必须与 .md 一致。"""
    if not portal_text:
        warn("R11-门户版本", "未配置门户路径，跳过门户版本校验")
        return
    sels = cfg.get("prototype", {}).get("portal_version_selectors", {})
    ver = re.search(r"^\|\s*版本\s*\|\s*(.+?)\s*\|\s*$", md_text, re.M)
    upd = extract_field(md_text, *DATE_KEYS)
    if not (ver and upd):
        return
    md_ver, md_upd = ver.group(1), upd
    checks = [
        ("current_version", md_ver, False),
        ("update_time", md_upd, True),
        ("header_tag", md_ver, False),
        ("title", md_ver, False),
    ]
    for key, expect, is_date in checks:
        pat = sels.get(key)
        if not pat:
            continue
        m = re.search(pat, portal_text)
        if m and m.group(1) != expect:
            err("R11-门户版本", f"门户「{key}」={m.group(1)}，但 .md 为 {expect}")


# ---------------------------------------------------------------- 规则 12
def check_numeric_claims(cfg, md_text):
    """R12 数值声明校验：PRD 中声明的数量须与实际一致（配置驱动）。"""
    for c in cfg.get("numeric_claims", []):
        pat = c["claim_regex"]
        expected = c["expected"]
        for m in re.finditer(pat, md_text):
            try:
                stated = int(m.group(1))
            except (IndexError, ValueError):
                continue
            if stated != expected:
                err("R12-数值声明",
                    f"声明「{m.group(0).strip()}」= {stated}，但应为 {expected}（{c.get('note', '')}）")


# ---------------------------------------------------------------- 规则 13
def check_term_unification(cfg, md_text, h5_text, proto_text, portal_text):
    """R13 术语统一：原型/门户/h5 不得出现 PRD 定义的禁用同义表述（配置驱动）。"""
    CORRECTION = re.compile(r"误写|此前|原误|旧版|曾误|不该|已废弃|历史上|原来|修正前|废弃|统一为")
    for t in cfg.get("terms", []):
        forbidden = t["forbidden"]
        canonical = t.get("canonical", "")
        scope = t.get("scope", ["prototype", "portal", "h5"])
        targets = []
        if "md" in scope and md_text:
            targets.append(("PRD.md", md_text))
        if "h5" in scope and h5_text:
            targets.append(("h5", h5_text))
        if "prototype" in scope and proto_text:
            targets.append(("原型", proto_text))
        if "portal" in scope and portal_text:
            targets.append(("门户", portal_text))
        for name, text in targets:
            for m in re.finditer(re.escape(forbidden), text):
                ctx = text[max(0, m.start() - 40): m.end() + 10]
                if CORRECTION.search(ctx):
                    continue  # 历史纠错引用（"此前误写X"），非实际使用
                err("R13-术语统一",
                    f"{name}：出现禁用表述「{forbidden}」，应统一为「{canonical}」…{ctx.strip()[:50]}…")


# ---------------------------------------------------------------- 规则 14
def check_open_items(cfg, md_text):
    """R14 待定项追踪：统计「当前未关闭」的开放问题，提示是否缺集中追踪表（仅警告）。

    判定为当前待定项：行内出现「🔵 待X」（待/待PM/待研发/待确认/待定），且
      - 不是历史引用：同行含「原🔵」「P0级🔵」视为已关闭项的回顾，不计；
      - 不是交叉引用：标记前出现「见§」视为指向别处的引用，不计；
      - 不在变更记录行：含 `| YYYY-MM-DD |` 的表格行跳过。
    这样可避免把已关闭项（写进 changelog 的「原🔵待确认项」）和配色图例误报。
    """
    marker = cfg.get("open_item_marker", "🔵")
    open_re = re.compile(re.escape(marker) + r"\s*(?:\*\*)?\s*待[PM研发确认定]")
    date_row = re.compile(r"\|\s*\d{4}-\d{2}-\d{2}\s*\|")
    count = 0
    for line in md_text.splitlines():
        if date_row.search(line):
            continue
        m = open_re.search(line)
        if not m:
            continue
        if "原🔵" in line or "P0级🔵" in line:
            continue
        if "见§" in line[:m.start()]:
            continue
        count += 1
    if count:
        has_register = bool(re.search(r"待.{0,3}确认|开放问题|待办清单|🔵\s*待", md_text))
        msg = f"PRD 含 {count} 处当前未关闭的「🔵 待定项」"
        msg += "" if has_register else "，但未见集中追踪登记表，建议补 §待确认事项"
        warn("R14-待定项", msg)


def main() -> None:
    ap = argparse.ArgumentParser(description="PRD/原型 一致性校验（配置驱动）")
    ap.add_argument("--root", help="模块根目录，默认取配置所在目录的上一级")
    ap.add_argument("--config", help="consistency_config.json 路径")
    args = ap.parse_args()

    script_dir = pathlib.Path(__file__).resolve().parent
    cfg, root = load_config(script_dir, args.config, args.root)
    files = cfg.get("files", {})
    md_path = resolve(root, files.get("md"))
    h5_path = resolve(root, files.get("h5"))
    portal_path = resolve(root, files.get("portal"))

    # 原型：支持单文件或整目录全量扫描
    proto_files: list[pathlib.Path] = []
    if files.get("prototype_dir"):
        d = resolve(root, files["prototype_dir"])
        if d:
            proto_files = sorted(d.rglob("*.html"))
    elif files.get("prototype"):
        p = resolve(root, files["prototype"])
        if p:
            proto_files = [p]

    for label, p in (("md", md_path), ("h5", h5_path), ("portal", portal_path)):
        if p and not p.exists():
            sys.exit(f"[文件缺失] {label}: {p}")

    md_text = md_path.read_text(encoding="utf-8") if (md_path and md_path.exists()) else ""
    h5_text = h5_path.read_text(encoding="utf-8") if (h5_path and h5_path.exists()) else ""
    portal_text = portal_path.read_text(encoding="utf-8") if (portal_path and portal_path.exists()) else ""
    proto_text = ""
    for pf in proto_files:
        if pf.exists():
            proto_text += "\n" + pf.read_text(encoding="utf-8")

    if enabled(cfg, "R0"):
        check_single_source(cfg, md_path)
    if enabled(cfg, "R1"):
        check_version_sync(cfg, root, md_text, h5_text, md_path or pathlib.Path(cfg.get("module", "PRD.md")))
    if enabled(cfg, "R2"):
        check_changelog_order(cfg, md_text)
    if enabled(cfg, "R3") and proto_text:
        check_prototype_risk_rows(cfg, proto_text)
    if enabled(cfg, "R4") and proto_text:
        check_software_order(cfg, proto_text)
    if enabled(cfg, "R5") and proto_text:
        check_color_semantics(cfg, proto_text)
    if enabled(cfg, "R6"):
        docs = [("PRD.md", md_text)]
        if proto_text:
            docs.append(("原型", proto_text))
        if portal_text:
            docs.append(("门户", portal_text))
        check_threshold_consistency(cfg, docs)
    if enabled(cfg, "R7"):
        check_precision_wording(cfg, md_text, proto_text)
    if enabled(cfg, "R8") and proto_text:
        check_table_structure(cfg, proto_text)
    if enabled(cfg, "R9"):
        check_section_refs(cfg, md_text)
    if enabled(cfg, "R10"):
        check_orthogonal_field(cfg, md_text, proto_text)
    if enabled(cfg, "R11"):
        check_portal_version(cfg, md_text, portal_text, md_path or pathlib.Path("PRD.md"))
    if enabled(cfg, "R12"):
        check_numeric_claims(cfg, md_text)
    if enabled(cfg, "R13"):
        check_term_unification(cfg, md_text, h5_text, proto_text, portal_text)
    if enabled(cfg, "R14"):
        check_open_items(cfg, md_text)

    mod = cfg.get("module", "PRD/原型")
    print("=" * 68)
    print(f"{mod} · PRD/原型 一致性校验")
    print("=" * 68)
    if warns:
        print(f"\n⚠️  提示 {len(warns)} 条：")
        for w in warns:
            print("   " + w)
    if errors:
        print(f"\n❌ 发现 {len(errors)} 处不一致：\n")
        for e in errors:
            print("   " + e)
        print(f"\n结论：未通过（{len(errors)} 处错误）")
        sys.exit(1)
    print(f"\n✅ 规则全部通过，文档一致性校验通过。")
    sys.exit(0)


if __name__ == "__main__":
    main()
