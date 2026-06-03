#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金价监控 - GitHub Actions 版
使用环境变量读取配置，仅依赖公开 API，适配云上运行
"""

import os, sys, json, urllib.request
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

API_URL = "https://v2.xxapi.cn/api/goldprice"

# ==================== 告警规则定义 ====================
DEFAULT_ALERTS = [
    {"name": "银行金条低于阈值", "type": "bank",  "enabled": True,  "direction": "below", "threshold": 1000},
    {"name": "银行金条高于阈值", "type": "bank",  "enabled": True,  "direction": "above", "threshold": 1200},
    {"name": "首饰金价低于阈值", "type": "jewelry","enabled": False, "direction": "below", "threshold": 1300},
    {"name": "首饰金价高于阈值", "type": "jewelry","enabled": False, "direction": "above", "threshold": 1600},
]

# 允许通过环境变量 ALERTS 覆盖默认规则（JSON 字符串）
ALERTS_JSON = os.environ.get("ALERTS", "")
if ALERTS_JSON:
    try:
        alerts = json.loads(ALERTS_JSON)
        DEFAULT_ALERTS = alerts
    except:
        pass

# ==================== 邮件配置（从环境变量读取） ====================
SMTP_SERVER   = os.environ.get("SMTP_SERVER", "smtp.qq.com")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "465"))
SENDER_EMAIL  = os.environ.get("SENDER_EMAIL", "")
SENDER_PASS   = os.environ.get("SENDER_PASSWORD", "")
RECEIVER_EMAIL= os.environ.get("RECEIVER_EMAIL", "")

# ==================== 金价获取 ====================

def fetch_prices():
    try:
        req = urllib.request.Request(API_URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 200 and data.get("data"):
                return data["data"]
        return None
    except Exception as e:
        print(f"[ERROR] API请求失败: {e}")
        return None

# ==================== 告警检查 ====================

def check_alerts(price_data):
    triggered = []
    for alert in DEFAULT_ALERTS:
        if not alert.get("enabled"):
            continue
        atype = alert.get("type", "")
        direction = alert.get("direction", "above")
        threshold = alert.get("threshold", 0)
        current = None

        if atype == "bank":
            items = price_data.get("bank_gold_bar_price", [])
            if items:
                vals = [float(i.get("price", 0)) for i in items if i.get("price")]
                if vals:
                    current = round(sum(vals) / len(vals), 2)
        elif atype == "jewelry":
            items = price_data.get("precious_metal_price", [])
            if items:
                vals = [float(i.get("gold_price", 0)) for i in items if i.get("gold_price")]
                if vals:
                    current = round(sum(vals) / len(vals), 2)

        if current is not None:
            if direction == "above" and current > threshold:
                triggered.append({**alert, "current": current})
            elif direction == "below" and current < threshold:
                triggered.append({**alert, "current": current})
    return triggered

# ==================== 邮件发送 ====================

def send_alert_email(triggered, price_data):
    if not SENDER_EMAIL or not SENDER_PASS or not RECEIVER_EMAIL:
        print("[SKIP] 邮件未配置，跳过发送")
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    items_html = ""
    for t in triggered:
        d = "高于" if t["direction"] == "above" else "低于"
        items_html += f"""
        <div style="background:#fff8e1;border-left:4px solid #ff9800;padding:12px;margin:10px 0;border-radius:4px;">
            <div style="font-weight:bold;color:#e65100;">{t['name']}</div>
            <div style="font-size:18px;color:#c62828;font-weight:bold;margin:5px 0;">当前：{t['current']} 元/克</div>
            <div style="color:#666;">{d}阈值：{t['threshold']} 元/克</div>
        </div>"""

    summary = '<div style="background:#fafafa;padding:15px;border-radius:8px;margin-top:20px;"><h3 style="margin-top:0;color:#333;">当前金价概览</h3>'
    if price_data.get("bank_gold_bar_price"):
        summary += '<p style="font-weight:bold;">银行金条：</p>'
        for b in price_data["bank_gold_bar_price"][:3]:
            summary += f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #eee;"><span>{b.get("bank","")}</span><span>{b.get("price","")} 元/克</span></div>'
    if price_data.get("precious_metal_price"):
        summary += '<p style="font-weight:bold;margin-top:12px;">首饰金价：</p>'
        for b in price_data["precious_metal_price"][:3]:
            summary += f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #eee;"><span>{b.get("brand","")}</span><span>{b.get("gold_price","")} 元/克</span></div>'
    summary += "</div>"

    html = f"""<html><head><meta charset="utf-8"></head><body style="font-family:'Microsoft YaHei',sans-serif;padding:20px;background:#f5f5f5;">
    <div style="max-width:600px;margin:0 auto;background:white;border-radius:12px;padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.1);">
        <h1 style="color:#d4a017;font-size:22px;">Au99.99 金价告警</h1>
        <div style="color:#888;font-size:13px;margin-bottom:20px;">检测时间：{now}</div>
        {items_html}
        {summary}
        <div style="margin-top:25px;color:#999;font-size:12px;text-align:center;">此邮件由金价监控系统自动发送</div>
    </div></body></html>"""

    subject = f"[GHA] 金价告警：{len(triggered)} 个条件触发"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL
        msg.attach(MIMEText(html, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=ctx) as s:
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"[OK] 告警邮件已发送 ({len(triggered)} 条)")
        return True
    except smtplib.SMTPAuthenticationError:
        print("[FAIL] 邮箱认证失败，请检查授权码")
    except Exception as e:
        print(f"[FAIL] 邮件发送失败: {e}")
    return False

# ==================== 检查结果报告 ====================

def format_result(price_data, triggered):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"=== 金价检查报告 ({now}) ==="]

    # 银行金条
    banks = price_data.get("bank_gold_bar_price", [])
    if banks:
        vals = [float(b["price"]) for b in banks if b.get("price")]
        avg = round(sum(vals)/len(vals), 2) if vals else 0
        lines.append(f"银行金条均价: {avg} 元/克 ({len(banks)} 家银行)")
    else:
        lines.append("银行金条: 无数据")

    # 首饰金价
    jewelries = price_data.get("precious_metal_price", [])
    if jewelries:
        vals = [float(j["gold_price"]) for j in jewelries if j.get("gold_price")]
        avg = round(sum(vals)/len(vals), 2) if vals else 0
        lines.append(f"首饰金价均价: {avg} 元/克 ({len(jewelries)} 个品牌)")
    else:
        lines.append("首饰金价: 无数据")

    if triggered:
        lines.append(f"\n触发告警: {len(triggered)} 条")
        for t in triggered:
            d = "高于" if t["direction"] == "above" else "低于"
            lines.append(f"  [{t['name']}] {t['current']} {d} {t['threshold']}")
    else:
        lines.append("\n所有价格正常，无告警")

    return "\n".join(lines)

# ==================== 主函数 ====================

def main():
    print(f"[START] 金价检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[INFO] 运行环境: GitHub Actions")

    price_data = fetch_prices()
    if not price_data:
        print("[FAIL] 获取金价失败")
        sys.exit(1)

    triggered = check_alerts(price_data)

    report = format_result(price_data, triggered)
    print(report)

    email_sent = False
    if triggered:
        email_sent = send_alert_email(triggered, price_data)
    else:
        print("[OK] 无告警")

    # GitHub Actions 的 Summary 输出
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(f"## Au99.99 金价监控 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(f"```\n{report}\n```\n")
            if email_sent:
                f.write("\n✅ 告警邮件已发送\n")

    print(f"[DONE] 检查完成")

if __name__ == "__main__":
    main()
