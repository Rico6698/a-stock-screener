#!/usr/bin/env python3
"""
generate_results.py - 运行选股脚本并生成 results.json 供 Web UI 使用
包含：top5结果 + 候选股票列表(watchlist)供网页版实时分析

无符合条件股票时生成空结果并正常退出，不再崩溃。
"""

import json
import os
import glob
import re
import subprocess
import sys
import pandas as pd
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCREENER = os.path.join(SCRIPT_DIR, "a_stock_screener_v6.py")


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

    stocks = []
    watchlist = []

    # 查找最新的输出目录（无目录时视为无符合条件股票）
    output_dirs = sorted(glob.glob(os.path.join(SCRIPT_DIR, "outputs", "*")))
    if output_dirs:
        latest_dir = output_dirs[-1]

        # 读取 top5 JSON（可能不存在 = 0 只通过）
        json_files = sorted(glob.glob(os.path.join(latest_dir, "top_*.json")))
        if json_files:
            with open(json_files[-1], encoding="utf-8") as f:
                stocks = json.load(f)
        else:
            print(">>> 本次无符合条件股票（top_*.json 未生成）")

        # 读取 full CSV 获取候选列表（用于网页版实时分析）
        csv_files = sorted(glob.glob(os.path.join(latest_dir, "full_*.csv")))
        if csv_files:
            df = pd.read_csv(csv_files[-1], encoding="utf-8-sig")
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                if not code or code == "nan":
                    continue
                prefix = "sh" if code.startswith("6") else "sz"
                watchlist.append({
                    "code": code,
                    "tencent_code": f"{prefix}{code}",
                    "name": str(row.get("名称", "")),
                    "price": float(row.get("价格", 0) or 0),
                    "pe": float(row.get("市盈率", 0) or 0),
                })
            print(f">>> 候选列表: {len(watchlist)} 只")
    else:
        print(">>> 未找到输出目录，本次无符合条件股票")

    # 从 stdout 解析元数据
    total_passed = len(stocks)
    m = re.search(r"通过\s*(\d+)\s*只", stdout)
    if m:
        total_passed = int(m.group(1))

    win_rate = ""
    m = re.search(r"胜率:\s*(\d+/\d+\s*\([\d.]+%\))", stdout)
    if m:
        win_rate = m.group(1).strip()

    # 生成 results.json（即使无股票也写入，避免下游崩溃）
    results = {
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "win_rate": win_rate,
        "total_passed": total_passed,
        "stocks": stocks,
        "watchlist": watchlist,
    }

    output_path = os.path.join(SCRIPT_DIR, "results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f">>> 已生成 {output_path}")
    print(f">>> 共 {len(stocks)} 只候选, 通过 {total_passed} 只, 候选列表 {len(watchlist)} 只")


if __name__ == "__main__":
    main()
