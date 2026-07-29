#!/usr/bin/env python3
"""
validate_results.py - 读取早上推荐结果，获取收盘价，对比预测验证，发邮件
"""

import json
import os
import smtplib
import akshare as ak
import pandas as pd
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


def load_results():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(script_dir, "results.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch_current_prices(codes):
    """获取指定股票的当前价格"""
    print(">>> 获取实时行情...")
    df = None
    for fn in [lambda: ak.stock_zh_a_spot_tx(), lambda: ak.stock_zh_a_spot_em()]:
        try:
            df = fn()
            break
        except Exception:
            continue
    if df is None:
        return {}

    # 统一列名
    if "code" in df.columns:
        df = df.rename(columns={"code": "stock_code", "zxj": "price"})
    elif "代码" in df.columns:
        df = df.rename(columns={"代码": "stock_code", "最新价": "price"})

    df["stock_code"] = df["stock_code"].astype(str).str.replace(r"^(sz|sh|bj)", "", regex=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    result = {}
    for code in codes:
        match = df[df["stock_code"] == code]
        if not match.empty:
            result[code] = float(match.iloc[0]["price"])
    return result


def generate_validation_html(data, current_prices):
    stocks = data.get("stocks", [])
    update_time = data.get("update_time", "")
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    cards = ""
    verified = 0
    correct = 0
    total_change = 0.0

    for i, s in enumerate(stocks):
        code = s["code"]
        name = s["name"]
        rec_price = s["price"]
        cur_price = current_prices.get(code)

        if cur_price is None or cur_price <= 0 or rec_price <= 0:
            status_text = "数据异常"
            change_pct = 0
            color = "#8c8c8c"
            arrow = "—"
        else:
            verified += 1
            change_pct = (cur_price - rec_price) / rec_price * 100
            total_change += change_pct
            is_up = cur_price >= rec_price
            if is_up:
                correct += 1
                color = "#ff4d4f"  # 红色=涨
                arrow = "↑"
                status_text = "正确"
            else:
                color = "#52c41a"  # 绿色=跌
                arrow = "↓"
                status_text = "错误"

        rank = i + 1
        rank_color = "#fa8c16" if rank == 1 else "#faad14" if rank <= 3 else "#8c8c8c"

        cur_str = f"¥{cur_price:.2f}" if cur_price else "N/A"
        change_str = f"{change_pct:+.2f}%" if cur_price else "—"

        cards += f"""
        <div style="background:#fff;border-radius:12px;padding:16px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,0.06)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <div><span style="background:{rank_color};color:#fff;padding:3px 8px;border-radius:5px;font-size:12px;font-weight:700;margin-right:6px">{rank}</span>
            <span style="font-size:16px;font-weight:600">{name}</span>
            <span style="color:#8c8c8c;font-size:12px">{code}</span></div>
            <span style="background:{color};color:#fff;padding:3px 10px;border-radius:5px;font-size:13px;font-weight:700">{status_text} {arrow}</span>
          </div>
          <div style="display:flex;gap:16px;align-items:center">
            <div style="text-align:center;flex:1">
              <div style="font-size:11px;color:#8c8c8c;margin-bottom:4px">推荐价 (上午)</div>
              <div style="font-size:18px;font-weight:700;color:#1a1a1a">¥{rec_price:.2f}</div>
            </div>
            <div style="font-size:20px;color:#8c8c8c">→</div>
            <div style="text-align:center;flex:1">
              <div style="font-size:11px;color:#8c8c8c;margin-bottom:4px">现价 (收盘)</div>
              <div style="font-size:18px;font-weight:700;color:{color}">{cur_str}</div>
            </div>
            <div style="text-align:center;flex:1">
              <div style="font-size:11px;color:#8c8c8c;margin-bottom:4px">涨跌幅</div>
              <div style="font-size:18px;font-weight:700;color:{color}">{change_str}</div>
            </div>
          </div>
        </div>"""

    # 汇总
    win_rate = f"{correct}/{verified} ({correct/verified*100:.0f}%)" if verified > 0 else "N/A"
    avg_change = f"{total_change/verified:+.2f}%" if verified > 0 else "N/A"

    summary = f"""
    <div style="display:flex;gap:10px;margin-bottom:16px">
      <div style="flex:1;background:linear-gradient(135deg,#fff7e6,#fffbe6);border:1px solid #ffe7ba;border-radius:10px;padding:14px;text-align:center">
        <div style="font-size:11px;color:#8c8c8c;margin-bottom:4px">今日胜率</div>
        <div style="font-size:20px;font-weight:700;color:#d48806">{win_rate}</div>
      </div>
      <div style="flex:1;background:linear-gradient(135deg,#e6f4ff,#f0f5ff);border:1px solid #d6e4ff;border-radius:10px;padding:14px;text-align:center">
        <div style="font-size:11px;color:#8c8c8c;margin-bottom:4px">平均涨跌</div>
        <div style="font-size:20px;font-weight:700;color:#1677ff">{avg_change}</div>
      </div>
    </div>"""

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;max-width:600px;margin:0 auto;padding:16px;background:#f5f5f7">
<h2 style="color:#1a1a1a;text-align:center;margin-bottom:4px">📈 选股验证报告</h2>
<p style="text-align:center;color:#8c8c8c;font-size:13px;margin-bottom:16px">{today} · 对比上午推荐 vs 收盘价</p>
{summary}
{cards}
<p style="text-align:center;font-size:11px;color:#8c8c8c;margin-top:16px;line-height:1.6">本工具仅做客观数据筛选，不构成任何投资建议。<br>入市有风险，投资需谨慎。</p>
</body></html>"""


def send_email(html_content, subject, to_addr, smtp_auth):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = to_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    server = smtplib.SMTP_SSL("smtp.qq.com", 465)
    server.login(to_addr, smtp_auth)
    server.sendmail(to_addr, [to_addr], msg.as_string())
    server.quit()
    print(f">>> 验证邮件已发送至 {to_addr}")


def main():
    data = load_results()
    stocks = data.get("stocks", [])

    if not stocks:
        print("!!! 无推荐数据可验证")
        return

    print(f">>> 加载 {len(stocks)} 只推荐股票")
    codes = [s["code"] for s in stocks]
    current_prices = fetch_current_prices(codes)
    print(f">>> 获取到 {len(current_prices)}/{len(codes)} 只现价")

    html = generate_validation_html(data, current_prices)

    today = datetime.now().strftime("%Y-%m-%d")
    correct = sum(1 for s in stocks if current_prices.get(s["code"], 0) >= s["price"])
    subject = f"📈 选股验证 {today} | 正确 {correct}/{len(stocks)}"

    to_addr = os.environ.get("QQ_MAIL_ADDR", "3405947985@qq.com")
    smtp_auth = os.environ.get("QQ_MAIL_AUTH", "")

    if not smtp_auth:
        print("!!! QQ_MAIL_AUTH 环境变量未设置")
        return

    send_email(html, subject, to_addr, smtp_auth)


if __name__ == "__main__":
    main()
