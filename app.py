import os
import json
import base64
import html
import threading
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ============================================================
# НАЛАШТУВАННЯ ТА ЗМІННІ ОТОЧЕННЯ
# ============================================================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

HEADERS_GITHUB = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

PRODUCT_NAMES = {
    "deye-sun-12k": "Deye SUN-12K (інвертор 12 кВт)",
    "deye-se-f16": "Deye SE-F16-C (батарея 16 кВт·год)",
    "deye-se-f5-pro": "Deye SE-F5 Pro-C (батарея 5.12 кВт·год)",
    "deye-sun-8k": "Deye SUN-8K (інвертор 8 кВт)",
    "deye-sun-5k": "Deye SUN-5K (інвертор 5 кВт)",
    "deye-sun-6k": "Deye SUN-6K (інвертор 6 кВт)",
    "deye-se-f12": "Deye SE-F12-C (батарея)",
    "sun-5k-sg06": "Deye SUN-5K SG06 (інвертор 5 кВт)",
    "felicity-fla48314": "Felicity FLA48314 (батарея 16 кВт·год)",
}

TELEGRAM_MAX_LENGTH = 3500


# ============================================================
# АВТОМАТИЧНЕ НАЛАШТУВАННЯ МЕНЮ БОТА
# ============================================================

def setup_bot_menu():
    if not TELEGRAM_BOT_TOKEN:
        return
    
    commands_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands"
    commands_payload = {
        "commands": [
            {"command": "check", "title": "Перевірити всі товари"},
            {"command": "start", "title": "Головне меню"}
        ]
    }
    try:
        requests.post(commands_url, json=commands_payload, timeout=10)
    except Exception as e:
        print(f"Помилка встановлення команд меню: {e}")

# Викликаємо налаштування при старті програми
setup_bot_menu()


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ РОБОТИ З GITHUB
# ============================================================

def get_product_name(url):
    url_lower = url.lower()
    
    # 1. Шукаємо у готовому словнику
    for key, name in PRODUCT_NAMES.items():
        if key in url_lower:
            return name

    # 2. Якщо товару немає в словнику — формуємо охайну назву з URL
    try:
        slug = url.split('/')[-1].split('?')[0].replace('.html', '')
        clean_name = slug.replace('-', ' ').title()
        return clean_name
    except Exception:
        return url

def get_products_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url, headers=HEADERS_GITHUB)
    if response.status_code == 200:
        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        products = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        return products
    return []

def get_cache_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/status_cache.json"
    response = requests.get(url, headers=HEADERS_GITHUB)
    if response.status_code == 200:
        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        try:
            return json.loads(content)
        except Exception:
            return {}
    return {}

def save_products_to_github(products, sha, commit_message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    new_content = "\n".join(products) + ("\n" if products else "")
    encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
    
    payload = {
        "message": commit_message,
        "content": encoded_content,
        "sha": sha
    }
    response = requests.put(url, headers=HEADERS_GITHUB, json=payload)
    return response.status_code in [200, 201]

def send_telegram(chat_id, message, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN:
        return False

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(api_url, json=payload, timeout=15)
        return response.status_code == 200
    except Exception as error:
        print(f"❌ Помилка Telegram: {error}")
        return False

def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "🔍 Перевірити всі товари"}]
        ],
        "resize_keyboard": True
    }


# ============================================================
# ФОРМУВАННЯ ЗВІТУ З status_cache.json
# ============================================================

def format_product_message(url, cache_data):
    name = get_product_name(url)
    escaped_name = html.escape(name)
    escaped_url = html.escape(url, quote=True)

    if not cache_data:
        status_text = "⚪ Статус невідомий"
        date = None
    else:
        state = cache_data.get("state")
        date = cache_data.get("date")

        status_texts = {
            "in_stock": "✅ Є в наявності",
            "out_of_stock": "❌ Немає в наявності",
            "pre_order": "🟡 Попереднє замовлення",
        }
        status_text = status_texts.get(state, "⚪ Статус невідомий")

    lines = [
        f"<b>{escaped_name}</b>",
        f"Статус: {status_text}",
    ]

    if date:
        escaped_date = html.escape(date)
        lines.append(f"📅 Очікувана дата відвантаження: <b>{escaped_date}</b>")

    lines.append(f'<a href="{escaped_url}">Відкрити товар на NKON</a>')
    return "\n".join(lines)

def run_report_from_cache(chat_id):
    products = get_products_from_github()
    if not products:
        send_telegram(chat_id, "⚠️ Список товарів у products.txt порожній.", reply_markup=get_main_keyboard())
        return

    cache = get_cache_from_github()

    heading = "📦 <b>Поточний статус усіх товарів NKON</b> (з кешу)\n\n"
    current_message = heading

    for url in products:
        cache_item = cache.get(url, {})
        block = format_product_message(url, cache_item) + "\n\n"

        if len(current_message) + len(block) > TELEGRAM_MAX_LENGTH and current_message != heading:
            send_telegram(chat_id, current_message.strip())
            current_message = heading

        current_message += block

    if current_message.strip() != heading.strip():
        send_telegram(chat_id, current_message.strip(), reply_markup=get_main_keyboard())


# ============================================================
# FLASK МАРШРУТИ ТА WEBHOOK
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/products", methods=["GET"])
def list_products():
    products = get_products_from_github()
    return jsonify({"products": products})

@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "Порожня адреса"}), 400

    url_github = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url_github, headers=HEADERS_GITHUB)
    sha = None
    products = []
    if response.status_code == 200:
        file_data = response.json()
        sha = file_data["sha"]
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        products = [line.strip() for line in content.splitlines() if line.strip()]

    if url in products:
        return jsonify({"success": False, "error": "Таке посилання вже є!"}), 400

    products.append(url)
    success = save_products_to_github(products, sha, f"Add product via WebApp: {url}")
    return jsonify({"success": success})

@app.route("/api/products", methods=["DELETE"])
def delete_product():
    data = request.json
    url_to_delete = data.get("url", "").strip()

    url_github = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url_github, headers=HEADERS_GITHUB)
    if response.status_code != 200:
        return jsonify({"success": False, "error": "Помилка доступу до GitHub"}), 500

    file_data = response.json()
    sha = file_data["sha"]
    content = base64.b64decode(file_data["content"]).decode("utf-8")
    products = [line.strip() for line in content.splitlines() if line.strip()]

    if url_to_delete not in products:
        return jsonify({"success": False, "error": "Посилання не знайдено"}), 404

    products.remove(url_to_delete)
    success = save_products_to_github(products, sha, f"Delete product via WebApp: {url_to_delete}")
    return jsonify({"success": success})

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.json
    if not update or "message" not in update:
        return jsonify({"status": "ok"})

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text in ["/start", "/help"]:
        welcome_text = "Вітаю! Натисніть кнопку нижче або скористайтеся меню команд, щоб отримати актуальний звіт."
        send_telegram(chat_id, welcome_text, reply_markup=get_main_keyboard())

    elif text in ["/check", "🔍 Перевірити всі товари"]:
        threading.Thread(target=run_report_from_cache, args=(chat_id,)).start()

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
