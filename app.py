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
}

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

def send_telegram_message(chat_id, text, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "🔍 Перевірити всі товари"}]
        ],
        "resize_keyboard": True
    }

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

def detect_product_status(soup, raw_html=""):
    stock = soup.select_one(".product-info-stock-sku .stock")
    button = soup.select_one("#product-addtocart-button")
    notify_link = soup.select_one(".product.alert.stock a.action.alert")
    preorder_message = soup.select_one(".preorder-message-block")

    stock_text = stock.get_text(" ", strip=True).lower() if stock else ""
    stock_classes = stock.get("class", []) if stock else []
    button_text = button.get_text(" ", strip=True).lower() if button else ""
    preorder_date = extract_preorder_date(soup, raw_html)

    if "unavailable" in stock_classes or "out of stock" in stock_text:
        return "out_of_stock", None, "❌ Немає в наявності"

    if preorder_date is not None or "pre order" in button_text or "pre-order" in button_text or preorder_message is not None:
        return "pre_order", preorder_date, "🟡 Попереднє замовлення"

    if "in stock" in stock_text:
        return "in_stock", None, "✅ Є в наявності"

    if notify_link is not None:
        return "out_of_stock", None, "❌ Немає в наявності"

    return "unknown", None, "⚪ Статус невідомий"

def check_single_product(url):
    name = get_product_name(url)
    for attempt in range(2):
        try:
            proxy_url = f"https://api.allorigins.win/get?url={requests.utils.quote(url)}"
            response = requests.get(proxy_url, timeout=25)
            response.raise_for_status()
            
            data = response.json()
            html_content = data.get("contents", "")
            if not html_content:
                raise ValueError("Порожня відповідь проксі")

            soup = BeautifulSoup(html_content, "html.parser")
            state, date, status_text = detect_product_status(soup, html_content)
            return {
                "success": True,
                "name": name,
                "url": url,
                "state": state,
                "date": date,
                "status_text": status_text
            }
        except Exception:
            if attempt == 0:
                time.sleep(1.5)
            else:
                return {
                    "success": False,
                    "name": name,
                    "url": url,
                    "status_text": "❌ Помилка завантаження"
                }

def process_and_send_report(chat_id):
    send_telegram_message(chat_id, "⏳ Збираю актуальну інформацію про всі товари з NKON, зачекайте хвилинку...")
    
    products = get_products_from_github()
    if not products:
        send_telegram_message(chat_id, "📋 <b>Список відстежуваних товарів порожній.</b>\nДодайте посилання через WebApp!", reply_markup=get_main_keyboard())
        return

    report_lines = ["📦 <b>Поточний статус усіх товарів на NKON:</b>\n"]

    for url in products:
        res = check_single_product(url)
        name = html.escape(res["name"])
        url_escaped = html.escape(res["url"], quote=True)
        status_text = html.escape(res["status_text"])

        item_lines = [f"<b>{name}</b>", f"Статус: {status_text}"]

        if res.get("date"):
            date_str = html.escape(res["date"])
            item_lines.append(f"📅 Дата відвантаження: <b>{date_str}</b>")

        item_lines.append(f'<a href="{url_escaped}">Відкрити товар на NKON</a>\n')
        report_lines.append("\n".join(item_lines))
        time.sleep(0.5)

    report_text = "\n".join(report_lines)
    send_telegram_message(chat_id, report_text, reply_markup=get_main_keyboard())

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/products", methods=["GET"])
def list_products():
    url_github = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url_github, headers=HEADERS_GITHUB)
    if response.status_code == 200:
        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        products = [line.strip() for line in content.splitlines() if line.strip()]
        return jsonify({"products": products})
    return jsonify({"products": []})

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
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Помилка збереження"}), 500

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
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Помилка оновлення"}), 500

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.json
    if not update or "message" not in update:
        return jsonify({"status": "ok"})

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text in ["/start", "/help"]:
        welcome_text = "Вітаю! Натисніть кнопку нижче, щоб отримати повний звіт по всіх товарах NKON."
        send_telegram_message(chat_id, welcome_text, reply_markup=get_main_keyboard())

    elif text == "🔍 Перевірити всі товари":
        # Запускаємо важкий парсинг у фоновому потоці, щоб Telegram отримав миттєву відповідь 200 OK
        threading.Thread(target=process_and_send_report, args=(chat_id,)).start()

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
