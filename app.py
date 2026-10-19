import os
import base64
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

HEADERS_PARSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_products_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url, headers=HEADERS_GITHUB)
    if response.status_code == 200:
        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        sha = file_data["sha"]
        products = [line.strip() for line in content.splitlines() if line.strip()]
        return products, sha
    return [], None

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

def check_stock_and_format_report():
    products, _ = get_products_from_github()
    if not products:
        return "📋 <b>Список відстежуваних товарів порожній.</b>\nДодайте посилання через WebApp!"

    report_lines = ["📦 <b>Поточний статус товарів на NKON:</b>\n"]
    
    for url in products:
        try:
            res = requests.get(url, headers=HEADERS_PARSER, timeout=10)
            if res.status_code != 200:
                report_lines.append(f"❌ <b>Помилка завантаження:</b>\n{url}\n")
                continue

            soup = BeautifulSoup(res.text, 'html.parser')

            # Назва товару
            title_el = soup.select_one('h1.page-title') or soup.select_one('h1')
            title = title_el.get_text(strip=True) if title_el else "Товар NKON"

            # Ціна
            price_el = soup.select_one('.price-container .price') or soup.select_one('.price')
            price = price_el.get_text(strip=True) if price_el else "Ціна не вказана"

            # Статус наявності
            stock_el = soup.select_one('.stock.available span') or soup.select_one('.availability')
            if stock_el:
                stock_text = stock_el.get_text(strip=True)
                status_icon = "🟢" if "in stock" in stock_text.lower() or "в наявності" in stock_text.lower() else "🔴"
            else:
                stock_text = "Статус невідомий"
                status_icon = "⚪"

            report_lines.append(
                f"{status_icon} <a href='{url}'><b>{title}</b></a>\n"
                f"💰 Ціна: <b>{price}</b>\n"
                f"📊 Наявність: {stock_text}\n"
            )
        except Exception as e:
            report_lines.append(f"⚠️ Помилка перевірки: {url}\n")

    return "\n".join(report_lines)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/products", methods=["GET"])
def list_products():
    products, _ = get_products_from_github()
    return jsonify({"products": products})

@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "Порожня адреса"}), 400

    products, sha = get_products_from_github()
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
    
    products, sha = get_products_from_github()
    if url_to_delete not in products:
        return jsonify({"success": False, "error": "Посилання не знайдено"}), 404

    products.remove(url_to_delete)
    success = save_products_to_github(products, sha, f"Delete product via WebApp: {url_to_delete}")
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Помилка оновлення"}), 500

# Обробник подій від Telegram-бота (Webhook)
@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.json
    if not update or "message" not in update:
        return jsonify({"status": "ok"})

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text in ["/start", "/help"]:
        welcome_text = "Вітаю! Натисніть кнопку нижче, щоб швидко перевірити статус усіх товарів на NKON."
        send_telegram_message(chat_id, welcome_text, reply_markup=get_main_keyboard())

    elif text == "🔍 Перевірити всі товари":
        send_telegram_message(chat_id, "⏳ Збираю інформацію про товари, зачекайте кілька секунд...")
        report = check_stock_and_format_report()
        send_telegram_message(chat_id, report, reply_markup=get_main_keyboard())

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
