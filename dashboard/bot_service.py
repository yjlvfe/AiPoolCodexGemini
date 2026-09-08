"""
Telegram Bot Long-Polling Daemon for Dashboard Access
"""
import os
import sys
import json
import time
import urllib.request
import urllib.parse
from server import auth_manager

BOT_TOKEN = "8923178439:AAGU-4SkIYKBHRWlfjnIY1BeCpX3NAy4Re4"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
DASHBOARD_BASE_URL = os.environ.get("POOL_DASHBOARD_URL", "https://64.112.42.39.sslip.io/aipool")

def call_tg(method: str, payload: dict = None):
    url = f"{API_URL}/{method}"
    data = None
    if payload:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"Telegram API Error ({method}): {e}")
        return None

def send_dashboard_link(chat_id: int, user_id: int):
    magic_link = auth_manager.generate_magic_link(DASHBOARD_BASE_URL, user_id)
    text = (
        "🔐 <b>رابط الدخول السريع للوحة تحكم الـ Pool</b>\n\n"
        "أهلاً يوسف، تم توليد رابط آمن وخاص بك للدخول للوحة التحكم:\n\n"
        f"🔗 <b><a href=\"{magic_link}\">اضغط هنا لفتح لوحة التحكم (AI Pools)</a></b>\n\n"
        "⏱️ <i>صلاحية الرابط: 30 دقيقة فقط للاستخدام لمرة واحدة.</i>\n"
        "🛡️ <i>الموديلات مفصولة تماماً (Gemini على 8123 و ChatGPT على 8124).</i>"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "🚀 فتح لوحة التحكم الآن", "url": magic_link}],
            [{"text": "🔄 طلب رابط جديد", "callback_data": "refresh_link"}]
        ]
    }
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": markup
    })

def run_bot():
    print("Starting Telegram Bot listener (@YJReportbot)...")
    offset = 0
    # Clear any stale webhooks
    call_tg("deleteWebhook", {"drop_pending_updates": True})

    while True:
        try:
            updates_res = call_tg("getUpdates", {"offset": offset, "timeout": 2})
            if updates_res and updates_res.get("ok"):
                for u in updates_res.get("result", []):
                    offset = u["update_id"] + 1

                    # Handle message
                    if "message" in u:
                        msg = u["message"]
                        chat_id = msg.get("chat", {}).get("id")
                        user_id = msg.get("from", {}).get("id")
                        text = msg.get("text", "")

                        # Any message or /start returns the magic link
                        if chat_id and user_id:
                            send_dashboard_link(chat_id, user_id)

                    # Handle callback query
                    elif "callback_query" in u:
                        cb = u["callback_query"]
                        chat_id = cb.get("message", {}).get("chat", {}).get("id")
                        user_id = cb.get("from", {}).get("id")
                        call_tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
                        if chat_id and user_id:
                            send_dashboard_link(chat_id, user_id)

        except Exception as e:
            print(f"Polling loop exception: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_bot()
