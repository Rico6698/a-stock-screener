#!/usr/bin/env python3
"""
send_email.py - 读取 results.json，生成 HTML 邮件并发送到 QQ 邮箱
"""

import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


def generate_html(data):
    stocks = data.get("stocks", [])
    update_time = data.get("update_time", "")
    win_rate = data.get("win_rate", "")
    total = data.get("total_passed", len(stocks))

    cards = ""
    for i, s in enumerate(stocks):
        rank = i + 1
        rank_color = "#fa8c16" if rank == 1 else "#faad14" if rank <= 3 else "#8c8c8c"
        reasons = s.get("tech_reason", "").split(", ")
        reason_tags = "".join(
            f'<span style="background:#e6f4ff;color:#1677ff;padding:2px 6px;border-radius:4px;font-size:11px;margin:2px;display:inline-block">{r}</span>'
            for r in reasons if r
        )
        cards += f"""
        <div style="background:#fff;border-radius:12px;padding:16px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,0.06)">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <div><span style="background:{rank_color};color:#fff;padding:3px 8px;border-radius:5px;font-size:12px;font-weight:700;margin-right:6px">{rank}</span>
            <span style="font-size:16px;font-weight:600">{s['name']}</span>
            <span style="color:#8c8c8c;font-size:12px">{s['code']}</span></div>
            <div style="text-align:right"><span style="font-size:20px;font-weight:700;color:#ff4d4f">¥{s['price']:.2f}</span>
            <span style="font-size:11px;color:#8c8c8c;display:block">1手 ¥{s['hand_cost']:.0f}</span></div>
          </div>
          <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">
            <span style="background:#f5f5f5;padding:4px 8px;border-radius:6px;font-size:12px">评分<b>{s['score']}</b></span>
            <span style="background:#f5f5f5;padding:4px 8px;border-radius:6px;font-size:12px">PE {s['pe']:.1f}</span>
            <span style="background:#f5f5f5;padding:4px 8px;border-radius:6px;font-size:12px">负债率 {s['fzl']}%</span>
            <span style="background:#f5f5f5;padding:4px 8px;border-radius:6px;font-size:12px">RSI {s['rsi_14']:.1f}</span>
            <span style="background:#f5f5f5;padding:4px 8px;border-radius:6px;font-size:12px">MACD {s['macd_bull']}</span>
          </div>
          <div style="background:#f6f8fa;border-radius:6px;padding:8px">{reason_tags}</div>
        </div>"""

    winrate_html = ""
    if win_rate:
        winrate_html = f"""
        <div style="background:linear-gradient(135deg,#fff7e6,#fffbe6);border:1px solid #ffe7ba;border-radius:10px;padding:12px;margin-bottom:16px;display:flex;align-items:center;gap:10px">
          <span style="font-size:24px">🏆</span>
          <div><span style="font-size:11px;color:#8c8c8c">历史推荐胜率</span><br><span style="font-size:15px;font-weight:700;color:#d48806">{win_rate}</span></div>
        </div>"""

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;max-width:600px;margin:0 auto;padding:16px;background:#f5f5f7">
<h2 style="color:#1a1a1a;text-align:center;margin-bottom:4px">📊 A股选股结果</h2>
<p style="text-align:center;color:#8c8c8c;font-size:13px;margin-bottom:16px">{update_time} · 通过{total}只</p>
{winrate_html}
{cards}
<p style="text-align:center;font-size:11px;color:#8c8c8c;margin-top:16px;line-height:1.6">本工具仅做客观数据筛选，不构成任何投资建议。<br>入市有风险，投资需谨慎。</p>
</body></html>"""


def send_email(html_content, subject, to_addr, smtp_addr, smtp_auth):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = to_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    server = smtplib.SMTP_SSL("smtp.qq.com", 465)
    server.login(smtp_addr, smtp_auth)
    server.sendmail(to_addr, [to_addr], msg.as_string())
    server.quit()
    print(f">>> 邮件已发送至 {to_addr}")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    results_path = os.path.join(script_dir, "results.json")

    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)

    html = generate_html(data)

    stocks = data.get("stocks", [])
    top_names = " ".join(s["name"] for s in stocks[:3])
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"📊 A股选股结果 {today} | Top{len(stocks)}: {top_names}"

    to_addr = os.environ.get("QQ_MAIL_ADDR", "3405947985@qq.com")
    smtp_auth = os.environ.get("QQ_MAIL_AUTH", "")

    if not smtp_auth:
        print("!!! QQ_MAIL_AUTH 环境变量未设置")
        return

    send_email(html, subject, to_addr, to_addr, smtp_auth)


if __name__ == "__main__":
    main()
