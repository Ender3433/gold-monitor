#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金价监控系统 - 后端服务器
功能：API代理 + 配置管理 + 邮件发送
"""

import http.server
import json
import urllib.request
import urllib.parse
import smtplib
import ssl
import os
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

PORT = 8090
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
HTML_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold-monitor.html")
GOLD_API = "https://v2.xxapi.cn/api/goldprice"
NEODATA_TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".workbuddy", ".neodata_token")
NEODATA_API = "https://copilot.tencent.com/agenttool/v1/neodata"

# ===================== 配置管理 =====================

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 自动补全缺失的默认告警规则（如新版本新增的 Au99.99）
        ensure_default_alerts(cfg)
        return cfg
    return get_default_config()

def get_default_config():
    return {
        "email": {
            "smtp_server": "smtp.qq.com",
            "smtp_port": 465,
            "sender_email": "",
            "sender_password": "",
            "receiver_email": ""
        },
        "alerts": list(DEFAULT_ALERTS),
        "check_interval_minutes": 60,
        "last_price_snapshot": {},
        "price_history": []
    }

# 默认告警规则定义（作为权威来源，供自动补全使用）
DEFAULT_ALERTS = [
    {"name": "银行金条低于阈值", "type": "bank", "field": "price", "enabled": False, "direction": "below", "threshold": 1000},
    {"name": "银行金条高于阈值", "type": "bank", "field": "price", "enabled": False, "direction": "above", "threshold": 1200},
    {"name": "首饰金价低于阈值", "type": "jewelry", "field": "gold_price", "enabled": False, "direction": "below", "threshold": 1300},
    {"name": "首饰金价高于阈值", "type": "jewelry", "field": "gold_price", "enabled": False, "direction": "above", "threshold": 1600},
    {"name": "Au99.99 低于阈值", "type": "au9999", "field": "price", "enabled": False, "direction": "below", "threshold": 960},
    {"name": "Au99.99 高于阈值", "type": "au9999", "field": "price", "enabled": False, "direction": "above", "threshold": 1020}
]

def ensure_default_alerts(cfg):
    """确保配置中包含了所有默认告警类型，缺失的自动补上"""
    existing = cfg.get("alerts", [])
    existing_types = {(a.get("type"), a.get("direction")) for a in existing}
    for default in DEFAULT_ALERTS:
        key = (default["type"], default["direction"])
        if key not in existing_types:
            existing.append(default)
    cfg["alerts"] = existing

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ===================== 金价获取 =====================

def fetch_gold_price():
    """获取金价数据"""
    try:
        req = urllib.request.Request(GOLD_API, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 200 and data.get("data"):
                return data["data"]
        return None
    except Exception as e:
        print(f"[ERROR] 获取金价失败: {e}")
        return None


def fetch_au9999_price():
    """从 Neodata 查询上海金交所 Au99.99 价格"""
    token = None
    token_file = NEODATA_TOKEN_FILE
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                raw = f.read().strip()
                # token 可能是 JSON 对象 {"token":"xxx"} 也可能是纯文本
                if raw.startswith("{"):
                    parsed = json.loads(raw)
                    token = parsed.get("token") or parsed.get("tempToken")
                else:
                    token = raw
        except:
            pass
    if not token:
        return None
    try:
        payload = json.dumps({
            "query": "上海黄金交易所 Au99.99 最新价格",
            "channel": "neodata",
            "sub_channel": "workbuddy",
            "data_type": "api"
        }).encode("utf-8")
        req = urllib.request.Request(NEODATA_API, data=payload, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("suc") and result.get("data", {}).get("apiData"):
                for recall in result["data"]["apiData"].get("apiRecall", []):
                    for line in recall.get("content", "").strip().split("\n"):
                        if "AU9999" in line.upper():
                            cols = line.split("|")
                            if len(cols) >= 7:
                                try:
                                    price = float(cols[5].strip())
                                    change = cols[16].strip() if len(cols) > 16 else "-"
                                    high = cols[17].strip() if len(cols) > 17 else "-"
                                    low = cols[18].strip() if len(cols) > 18 else "-"
                                    ts = cols[14].strip() if len(cols) > 14 else "-"
                                    return {
                                        "price": price,
                                        "change": change,
                                        "high": high,
                                        "low": low,
                                        "time": ts
                                    }
                                except:
                                    pass
        return None
    except Exception as e:
        print(f"[WARN] 获取 Au99.99 失败: {e}")
        return None


def get_all_prices():
    """获取所有金价数据（含 Au99.99）"""
    price_data = fetch_gold_price()
    if price_data:
        au9999 = fetch_au9999_price()
        if au9999:
            au9999["source"] = "上海金交所"
            price_data["au9999"] = au9999
    return price_data

def check_alerts(cfg, price_data):
    """检查所有告警条件，触发返回 True 的告警列表"""
    if not price_data:
        return []
    
    triggered = []
    alerts = cfg.get("alerts", [])
    
    for alert in alerts:
        if not alert.get("enabled"):
            continue
        
        current_value = None
        alert_type = alert.get("type", "")
        direction = alert.get("direction", "above")
        threshold = alert.get("threshold", 0)
        field = alert.get("field", "price")
        
        if alert_type == "bank":
            # 取所有银行金条的平均价格
            banks = price_data.get("bank_gold_bar_price", [])
            if banks:
                values = [float(b.get(field, b.get("price", 0))) for b in banks if b.get(field, b.get("price", 0))]
                if values:
                    current_value = sum(values) / len(values)
        
        elif alert_type == "jewelry":
            # 取所有首饰品牌的平均价格
            brands = price_data.get("precious_metal_price", [])
            if brands:
                values = [float(b.get(field, b.get("gold_price", 0))) for b in brands if b.get(field, b.get("gold_price", 0))]
                if values:
                    current_value = sum(values) / len(values)
        
        elif alert_type == "recycle":
            # 回收价格
            recycles = price_data.get("gold_recycle_price", [])
            if recycles:
                values = [float(r.get(field, r.get("recycle_price", 0))) for r in recycles if r.get(field, r.get("recycle_price", 0))]
                if values:
                    current_value = sum(values) / len(values)

        elif alert_type == "au9999":
            # 上海金交所 Au99.99
            au = price_data.get("au9999", {})
            if au:
                current_value = au.get("price", 0)
                if current_value == 0:
                    current_value = None
        
        if current_value is not None:
            if direction == "above" and current_value > threshold:
                triggered.append({**alert, "current_value": round(current_value, 2)})
            elif direction == "below" and current_value < threshold:
                triggered.append({**alert, "current_value": round(current_value, 2)})
    
    return triggered

# ===================== 邮件发送 =====================

def send_email(cfg, subject, body):
    """发送邮件"""
    email_cfg = cfg.get("email", {})
    sender = email_cfg.get("sender_email", "")
    password = email_cfg.get("sender_password", "")
    receiver = email_cfg.get("receiver_email", "")
    smtp_server = email_cfg.get("smtp_server", "smtp.qq.com")
    smtp_port = email_cfg.get("smtp_port", 465)
    
    if not sender or not password or not receiver:
        return {"success": False, "error": "邮件配置不完整，请先在设置中填写邮箱信息"}
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = receiver

        part = MIMEText(body, "html", "utf-8")
        msg.attach(part)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        
        return {"success": True, "message": "邮件发送成功"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "邮箱认证失败，请检查密码/授权码是否正确（QQ邮箱请使用授权码）"}
    except smtplib.SMTPException as e:
        return {"success": False, "error": f"SMTP错误: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"发送失败: {str(e)}"}

def build_alert_email_body(triggered, price_data):
    """构建告警邮件内容"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    html = f"""
    <html>
    <head><meta charset="utf-8"><style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #d4a017; font-size: 22px; margin-bottom: 10px; }}
        .time {{ color: #888; font-size: 13px; margin-bottom: 20px; }}
        .alert-item {{ background: #fff8e1; border-left: 4px solid #ff9800; padding: 12px 16px; margin: 10px 0; border-radius: 4px; }}
        .alert-item .name {{ font-weight: bold; color: #e65100; }}
        .alert-item .value {{ font-size: 18px; color: #c62828; font-weight: bold; margin: 5px 0; }}
        .alert-item .threshold {{ color: #666; font-size: 13px; }}
        .summary {{ background: #fafafa; padding: 15px; border-radius: 8px; margin-top: 20px; }}
        .summary h3 {{ margin-top: 0; color: #333; }}
        .price-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #eee; }}
        .footer {{ margin-top: 25px; color: #999; font-size: 12px; text-align: center; }}
    </style></head>
    <body>
    <div class="container">
        <h1>⚠️ 金价告警通知</h1>
        <div class="time">检测时间：{now}</div>
    """
    
    for t in triggered:
        direction_text = "高于" if t.get("direction") == "above" else "低于"
        html += f"""
        <div class="alert-item">
            <div class="name">{t.get('name', '未命名告警')}</div>
            <div class="value">当前值：{t.get('current_value', 'N/A')} 元/克</div>
            <div class="threshold">{direction_text}阈值：{t.get('threshold', 'N/A')} 元/克</div>
        </div>
        """
    
    # 附上当前金价概览
    html += '<div class="summary"><h3>📊 当前金价概览</h3>'
    
    if price_data.get("bank_gold_bar_price"):
        html += '<p style="font-weight:bold;margin-top:12px;">🏦 银行金条：</p>'
        for b in price_data["bank_gold_bar_price"][:3]:
            html += f'<div class="price-row"><span>{b.get("bank","")}</span><span>{b.get("price","")} 元/克</span></div>'
    
    if price_data.get("precious_metal_price"):
        html += '<p style="font-weight:bold;margin-top:12px;">💎 首饰金价：</p>'
        for b in price_data["precious_metal_price"][:3]:
            html += f'<div class="price-row"><span>{b.get("brand","")}</span><span>{b.get("gold_price","")} 元/克</span></div>'
    
    au = price_data.get("au9999")
    if au:
        html += '<p style="font-weight:bold;margin-top:12px;">📊 上海金交所 Au99.99：</p>'
        html += f'<div class="price-row"><span>Au99.99 现货</span><span style="color:#1565c0;font-weight:700;">{au.get("price","-")} 元/克</span></div>'
        html += f'<div class="price-row"><span>日内涨跌</span><span>{au.get("change","-")}</span></div>'
    
    html += """
    </div>
    <div class="footer">
        此邮件由金价监控系统自动发送 · 如有疑问请联系管理员
    </div>
    </div></body></html>
    """
    return html

# ===================== CLI 模式 =====================

def cli_check_and_alert():
    """命令行模式：检查金价并发告警"""
    print("=" * 50)
    print(f"[检查] 金价监控检查 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    cfg = load_config()
    price_data = get_all_prices()
    
    if not price_data:
        print("[失败] 获取金价数据失败")
        return
    
    # 打印当前金价
    print("\n[银行金条] 价格：")
    for b in price_data.get("bank_gold_bar_price", []):
        print(f"   {b.get('bank', '')}: {b.get('price', '')} 元/克")
    
    print("\n[首饰金价] 价格：")
    for b in price_data.get("precious_metal_price", []):
        print(f"   {b.get('brand', '')}: {b.get('gold_price', '')} 元/克")
    
    print("\n[回收金价] 价格：")
    for b in price_data.get("gold_recycle_price", []):
        print(f"   {b.get('gold_type', '')}: {b.get('recycle_price', '')} 元/克")
    
    au9999 = price_data.get("au9999")
    if au9999:
        print(f"\n[上海金交所 Au99.99] {au9999.get('price', '-')} 元/克  (涨跌: {au9999.get('change', '-')})")
    
    # 检查告警
    triggered = check_alerts(cfg, price_data)
    
    if triggered:
        print(f"\n[告警] 触发了 {len(triggered)} 个告警条件：")
        for t in triggered:
            direction_text = "高于" if t.get("direction") == "above" else "低于"
            print(f"   [{t.get('name','')}] 当前值 {t.get('current_value','')} {direction_text} 阈值 {t.get('threshold','')}")
        
        # 记录本次触发的价格快照，避免重复告警
        last_snapshot = cfg.get("last_price_snapshot", {})
        should_send = False
        
        for t in triggered:
            key = f"{t['type']}_{t['direction']}_{t['threshold']}"
            current_val = t['current_value']
            last_val = last_snapshot.get(key)
            
            if last_val is None or abs(current_val - last_val) > 0.5:
                should_send = True
                last_snapshot[key] = current_val
        
        if should_send:
            cfg["last_price_snapshot"] = last_snapshot
            save_config(cfg)
            
            subject = f"[告警] 金价告警：{len(triggered)} 个条件触发"
            body = build_alert_email_body(triggered, price_data)
            result = send_email(cfg, subject, body)
            
            if result["success"]:
                print(f"\n[成功] 告警邮件已发送")
            else:
                print(f"\n[失败] 邮件发送失败: {result.get('error', '未知错误')}")
        else:
            print(f"\n[跳过] 价格与上次告警变化不大，跳过重复通知")
    else:
        print(f"\n[正常] 所有价格正常，无告警")
    
    # 保存价格历史
    history = cfg.get("price_history", [])
    snapshot = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bank_avg": 0,
        "jewelry_avg": 0,
        "au9999": 0
    }
    
    banks = price_data.get("bank_gold_bar_price", [])
    if banks:
        vals = [float(b.get("price", 0)) for b in banks if b.get("price")]
        snapshot["bank_avg"] = round(sum(vals) / len(vals), 2) if vals else 0
    
    jewelries = price_data.get("precious_metal_price", [])
    if jewelries:
        vals = [float(j.get("gold_price", 0)) for j in jewelries if j.get("gold_price")]
        snapshot["jewelry_avg"] = round(sum(vals) / len(vals), 2) if vals else 0
    
    au9999 = price_data.get("au9999", {})
    if au9999.get("price"):
        snapshot["au9999"] = round(float(au9999["price"]), 2)
    
    history.append(snapshot)
    if len(history) > 200:
        history = history[-200:]
    cfg["price_history"] = history
    save_config(cfg)
    print(f"\n[完成] 价格历史已记录（共 {len(history)} 条）")

# ===================== HTTP 服务器 =====================

class GoldPriceHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/":
            self.serve_html()
        elif path == "/api/price":
            self.handle_get_price()
        elif path == "/api/config":
            self.handle_get_config()
        elif path == "/api/history":
            self.handle_get_history()
        else:
            self.send_error(404, "Not Found")
    
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        if path == "/api/config":
            self.handle_save_config(body)
        elif path == "/api/send-test":
            self.handle_send_test(body)
        elif path == "/api/check":
            self.handle_check_now()
        elif path == "/api/reset-history":
            self.handle_reset_history()
        else:
            self.send_error(404, "Not Found")
    
    def serve_html(self):
        if os.path.exists(HTML_FILE):
            with open(HTML_FILE, "r", encoding="utf-8") as f:
                html = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_error(500, "HTML file not found")
    
    def handle_get_price(self):
        price_data = get_all_prices()
        if price_data:
            cfg = load_config()
            triggered = check_alerts(cfg, price_data)
            resp = {"success": True, "data": price_data, "triggered_alerts": triggered}
        else:
            resp = {"success": False, "error": "获取金价数据失败"}
        
        self._json_response(resp)
    
    def handle_get_config(self):
        cfg = load_config()
        # 隐藏密码
        if "email" in cfg and "sender_password" in cfg["email"]:
            pwd = cfg["email"]["sender_password"]
            cfg["email"]["sender_password"] = "********" if pwd else ""
        self._json_response({"success": True, "data": cfg})
    
    def handle_get_history(self):
        cfg = load_config()
        self._json_response({"success": True, "data": cfg.get("price_history", [])})
    
    def handle_save_config(self, body):
        try:
            new_cfg = json.loads(body)
            old_cfg = load_config()
            
            # 保留密码：如果新配置传了 "********" 则不覆盖
            if new_cfg.get("email", {}).get("sender_password") == "********":
                new_cfg["email"]["sender_password"] = old_cfg.get("email", {}).get("sender_password", "")
            
            # 保留价格历史
            new_cfg["price_history"] = old_cfg.get("price_history", [])
            new_cfg["last_price_snapshot"] = old_cfg.get("last_price_snapshot", {})
            
            # 自动补全缺失的默认告警规则
            ensure_default_alerts(new_cfg)
            
            save_config(new_cfg)
            self._json_response({"success": True, "message": "配置已保存"})
        except Exception as e:
            self._json_response({"success": False, "error": str(e)})
    
    def handle_send_test(self, body):
        try:
            data = json.loads(body)
        except:
            data = {}
        
        cfg = load_config()
        # 如果用 POST 传了临时邮箱配置，合并进去
        if data.get("email"):
            for k, v in data["email"].items():
                if v:
                    cfg.setdefault("email", {})[k] = v
        
        subject = "✅ 金价监控系统 - 测试邮件"
        body_html = f"""
        <html><body style="font-family:'Microsoft YaHei',sans-serif;padding:20px;">
        <h2 style="color:#4caf50;">✅ 测试邮件发送成功</h2>
        <p>如果您收到这封邮件，说明金价监控系统的邮件配置正确。</p>
        <p>发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <hr>
        <p style="color:#999;font-size:12px;">此邮件由金价监控系统自动发送</p>
        </body></html>
        """
        
        result = send_email(cfg, subject, body_html)
        self._json_response(result)
    
    def handle_check_now(self):
        cfg = load_config()
        price_data = get_all_prices()
        
        if not price_data:
            self._json_response({"success": False, "error": "获取金价数据失败"})
            return
        
        triggered = check_alerts(cfg, price_data)
        
        result = {
            "success": True,
            "price_data": price_data,
            "triggered": triggered,
        }
        
        if triggered:
            # 检查是否应该发邮件（去重）
            last_snapshot = cfg.get("last_price_snapshot", {})
            should_send = False
            for t in triggered:
                key = f"{t['type']}_{t['direction']}_{t['threshold']}"
                last_val = last_snapshot.get(key)
                if last_val is None or abs(t['current_value'] - last_val) > 0.5:
                    should_send = True
                    last_snapshot[key] = t['current_value']
            
            if should_send:
                cfg["last_price_snapshot"] = last_snapshot
                save_config(cfg)
                
                subject = f"[告警] 金价告警：{len(triggered)} 个条件触发"
                email_body = build_alert_email_body(triggered, price_data)
                email_result = send_email(cfg, subject, email_body)
                result["email_sent"] = email_result
            else:
                result["email_sent"] = {"success": True, "skipped": True, "message": "价格变化不大，跳过重复通知"}
        
        # 保存价格历史
        history = cfg.get("price_history", [])
        snapshot = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "bank_avg": 0, "jewelry_avg": 0, "au9999": 0}
        banks = price_data.get("bank_gold_bar_price", [])
        if banks:
            vals = [float(b.get("price", 0)) for b in banks if b.get("price")]
            snapshot["bank_avg"] = round(sum(vals) / len(vals), 2) if vals else 0
        jewelries = price_data.get("precious_metal_price", [])
        if jewelries:
            vals = [float(j.get("gold_price", 0)) for j in jewelries if j.get("gold_price")]
            snapshot["jewelry_avg"] = round(sum(vals) / len(vals), 2) if vals else 0
        au9999 = price_data.get("au9999", {})
        if au9999.get("price"):
            snapshot["au9999"] = round(float(au9999["price"]), 2)
        history.append(snapshot)
        if len(history) > 200:
            history = history[-200:]
        cfg["price_history"] = history
        save_config(cfg)
        
        self._json_response(result)
    
    def handle_reset_history(self):
        cfg = load_config()
        cfg["price_history"] = []
        cfg["last_price_snapshot"] = {}
        save_config(cfg)
        self._json_response({"success": True, "message": "历史数据已清空"})
    
    def _json_response(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")

# ===================== 入口 =====================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        cli_check_and_alert()
    elif len(sys.argv) > 1 and sys.argv[1] == "--server":
        print(f"[启动] 金价监控服务器启动...")
        print(f"   地址: http://localhost:{PORT}")
        print(f"   停止: 按 Ctrl+C 停止服务器")
        print(f"   提示: 在浏览器中打开以上地址即可使用")
        server = http.server.HTTPServer(("0.0.0.0", PORT), GoldPriceHandler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[停止] 服务器已停止")
            server.server_close()
    else:
        print("使用方法:")
        print("  python gold-server.py --server   # 启动Web服务器")
        print("  python gold-server.py --check    # CLI模式检查金价")
