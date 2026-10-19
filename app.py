import os
import re
import json
import base64
import html
import time
import threading
import requests
from bs4 import BeautifulSoup
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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
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
}

REQUEST_TIMEOUT = 20
REQUEST_RETRIES = 2
REQUEST_DELAY = 1.0
TELEGRAM_MAX_LENGTH = 3500


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================================

def get_product_name(url):
    url_lower = url.lower()
    for key, name in PRODUCT_NAMES.items():
        if key in url_lower:
            return name
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
# ЛОГІКА ПАРСИНГУ ТОВАРІВ (З check_stock.py)
# ============================================================

def extract_preorder_date(soup, raw_html=""):
    element = soup.select_one(".ampreorder-observed")
    if element:
        text = element.get_text(" ", strip=True)
        match = re.search(r"\b\d{2}-\d{2}-\d{4}\b", text)
        if match:
            return match.group(0)

    if raw_html and re.search(r"This product is for pre-order|Estimated shipping date", raw_html, re.IGNORECASE):
        dates = re.findall(r"\b\d{2}-\d{2}-\d{4}\b", raw_html)
        if dates:
            return dates[0]
    return None

def detect_product_status(soup, url, raw_html=""):
    stock = soup.select_one(".product-info-stock-sku .stock")
    button = soup.select_one("#product-addtocart-button")
    notify_link = soup.select_one(".product.alert.stock a.action.alert")
    preorder_message = soup.select_one(".preorder-message-block")

    stock_text = stock.get_text(" ", strip=True).lower() if stock else ""
    stock_classes = stock.get("class", []) if stock else []
    button_text = button.get_text(" ", strip=True).lower() if button else ""

    preorder_date = extract_preorder_date(soup, raw_html)

    if "unavailable" in stock_classes or "out of stock" in stock_text:
        return "out_of_stock", None

    if preorder_date is not None or "pre order" in button_text or "pre-order" in button_text or preorder_message is not None:
        return "pre_order", preorder_date

    if "in stock" in stock_text:
        return "in_stock", None

    if notify_link is not None:
        return "out_of_stock", None

    return "unknown", None

def check_product(url):
    name = get_product_name(url)

    for attempt in range(REQUEST_RETRIES + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            if not soup.select_one(".product-info-price"):
                raise ValueError("Не знайдено .product-info-price")

            state, date = detect_product_status(soup, url, response.text)

            status_texts = {
                "in_stock": "✅ Є в наявності",
                "out_of_stock": "❌ Немає в наявності",
                "pre_order": "🟡 Попереднє замовлення",
                "unknown": "⚪ Статус невідомий"
            }

            return {
                "success": True,
                "name": name,
                "url": url,
                "state": state,
                "date": date,
                "status_text": status_texts.get(state, "⚪ Статус невідомий")
            }

        except Exception as error:
            if attempt < REQUEST_RETRIES:
                time.sleep(1.5)

    return {
        "success": False,
        "name": name,
        "url": url,
        "state": "error",
        "date": None,
        "status_text": "⚠️ Помилка завантаження"
    }


# ============================================================
# ЗБІР І ВІДПРАВКА ЗВІТУ
# ============================================================

def format_product_message(item):
    name = html.escape(item["name"])
    url = html.escape(item["url"], quote=True)
    status_text = html.escape(item["status_text"])

    lines = [
        f"<b>{name}</b>",
        f"Статус: {status_text}",
    ]

    if item.get("date"):
        date = html.escape(item["date"])
        lines.append(f"📅 Очікувана дата відвантаження: <b>{date}</b>")

    lines.append(f'<a href="{url}">Відкрити товар на NKON</a>')
    return "\n".join(lines)

def run_check_and_report(chat_id):
    send_telegram(chat_id, "⏳ Збираю інформацію про всі товари, зачекайте...")

    products = get_products_from_github()
    if not products:
        send_telegram(chat_id, "⚠️ Список товарів у products.txt порожній.", reply_markup=get_main_keyboard())
        return

    heading = "📦 <b>Поточний статус усіх товарів NKON</b>\n\n"
    current_message = heading

    for url in products:
        result = check_product(url)
        block = format_product_message(result) + "\n\n"

        if len(current_message) + len(block) > TELEGRAM_MAX_LENGTH and current_message != heading:
            send_telegram(chat_id, current_message.strip())
            current_message = heading

        current_message += block
        time.sleep(REQUEST_DELAY)

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
        welcome_text = "Вітаю! Натисніть кнопку нижче, щоб швидко перевірити всі товари."
        send_telegram(chat_id, welcome_text, reply_markup=get_main_keyboard())

    elif text == "🔍 Перевірити всі товари":
        # Фоновий потік, щоб Telegram отримав розрив з'єднання одразу і не робив повторних запитів
        threading.Thread(target=run_check_and_report, args=(chat_id,)).start()

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
