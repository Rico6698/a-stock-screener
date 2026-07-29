#!/usr/bin/env python3
"""
generate_results.py - 运行选股脚本并生成 results.json 供 Web UI 使用
"""

import json
import os
import glob
import re
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENER = os.path.join(SCRIPT_DIR, "a_stock_screener_v4.py")


def main():
    # 运行选股脚本
    print(">>> 运行选股脚本...")
    proc = subprocess.run(
        [sys.executable, SCREENER, "--output", "json", "--ci"],
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    print(stdout[-500:] if len(stdout) > 500 else stdout)
    if stderr:
        print("STDERR:", stderr[-300:])

    # 查找最新的 JSON 输出文件
    output_dirs = sorted(glob.glob(os.path.join(SCRIPT_DIR, "outputs", "*")))
    if not output_dirs:
        print("!!! 未找到输出目录")
        sys.exit(1)

    latest_dir = output_dirs[-1]
    json_files = sorted(glob.glob(os.path.join(latest_dir, "top_*.json")))
    if not json_files:
        print("!!! 未找到 JSON 输出文件")
        sys.exit(1)

    with open(json_files[-1], encoding="utf-8") as f:
        stocks = json.load(f)

    # 从 stdout 解析元数据
    total_passed = len(stocks)
    m = re.search(r"通过\s*(\d+)\s*只", stdout)
    if m:
        total_passed = int(m.group(1))

    win_rate = ""
    m = re.search(r"胜率:\s*(\d+/\d+\s*\([\d.]+%\))", stdout)
    if m:
        win_rate = m.group(1).strip()

    # 生成 results.json
    results = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "win_rate": win_rate,
        "total_passed": total_passed,
        "stocks": stocks,
    }

    output_path = os.path.join(SCRIPT_DIR, "results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f">>> 已生成 {output_path}")
    print(f">>> 共 {len(stocks)} 只候选, 通过 {total_passed} 只, 胜率: {win_rate or 'N/A'}")


if __name__ == "__main__":
    main()
