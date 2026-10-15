#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库级编排：扫描 base 目录下所有 04_工具/consistency_config.json，
逐个运行同目录的 check_consistency.py，汇总退出码。

用法：
    python3 check_all.py [--base <仓库根目录>]
退出码：任一模块不通过则为 1。
"""
import argparse
import pathlib
import subprocess
import sys


def main() -> None:
    ap = argparse.ArgumentParser(description="仓库级 PRD/原型 一致性校验编排")
    ap.add_argument("--base", default=".")
    args = ap.parse_args()
    base = pathlib.Path(args.base).resolve()
    configs = sorted(base.rglob("04_工具/consistency_config.json"))
    if not configs:
        print("未发现任何 04_工具/consistency_config.json，跳过一致性门禁。")
        sys.exit(0)
    rc = 0
    for cfg in configs:
        tool = cfg.parent
        print(f"\n########## 模块：{tool.parent.name} ##########")
        r = subprocess.run([sys.executable, str(tool / "check_consistency.py")], cwd=str(tool))
        rc = max(rc, r.returncode)
    sys.exit(rc)


if __name__ == "__main__":
    main()
