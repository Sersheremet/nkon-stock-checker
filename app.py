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
# НАЛАШТУВАННЯ
# ============================================================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

REQUEST_TIMEOUT = 25
REQUEST_RETRIES = 2
REQUEST_DELAY = 1.5
TELEGRAM_MAX_LENGTH = 3500

# Використовуємо прості заголовки, як у робочому check_stock.py.
# Не задаємо Accept-Encoding вручну: requests сам узгодить кодування.
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

# Окремі сесії для запитів до GitHub і NKON.
github_session = requests.Session()
github_session.headers.update({
    "Accept": "application/vnd.github+json",
    "User-Agent": "nkon-stock-checker",
})

nkon_session = requests.Session()
nkon_session.headers.update(HEADERS)

# Не запускаємо кілька великих перевірок одночасно, якщо кнопку натиснути
# декілька разів поспіль.
check_lock = threading.Lock()


# ============================================================
# НАЗВИ ТОВАРІВ
# ============================================================

def get_product_name(url):
    url_lower = url.lower()
    for key, name in PRODUCT_NAMES.items():
        if key in url_lower:
            return name
    return url


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(chat_id, message, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN:
        print("❌ Не задано змінну середовища TELEGRAM_BOT_TOKEN.", flush=True)
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
        response = requests.post(api_url, json=payload, timeout=20)
        if not response.ok:
            print(
                f"❌ Telegram HTTP {response.status_code}: "
                f"{response.text[:1000]}",
                flush=True,
            )
            return False

        result = response.json()
        if not result.get("ok"):
            print(f"❌ Telegram API помилка: {result}", flush=True)
            return False

        return True

    except Exception as error:
        print(
            f"❌ Помилка надсилання Telegram: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return False


def get_main_keyboard():
    return {
        "keyboard": [[{"text": "🔍 Перевірити всі товари"}]],
        "resize_keyboard": True,
    }


# ============================================================
# GITHUB: ЧИТАННЯ ТА ЗАПИС products.txt
# ============================================================

def github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nkon-stock-checker",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def get_products_from_github():
    if not GITHUB_REPO:
        print("❌ Не задано змінну середовища GITHUB_REPO.", flush=True)
        return []

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"

    try:
        response = requests.get(
            url,
            headers=github_headers(),
            timeout=20,
        )

        if response.status_code != 200:
            print(
                f"❌ GitHub не зміг прочитати products.txt: "
                f"HTTP {response.status_code}; {response.text[:1000]}",
                flush=True,
            )
            return []

        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")

        products = []
        seen = set()
        for line in content.splitlines():
            product_url = line.strip()
            if (
                product_url
                and not product_url.startswith("#")
                and product_url not in seen
            ):
                products.append(product_url)
                seen.add(product_url)

        print(f"📂 З GitHub завантажено товарів: {len(products)}", flush=True)
        return products

    except Exception as error:
        print(
            f"❌ Помилка читання products.txt з GitHub: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return []


def save_products_to_github(products, sha, commit_message):
    if not GITHUB_REPO:
        print("❌ Не задано змінну середовища GITHUB_REPO.", flush=True)
        return False
    if not GITHUB_TOKEN:
        print("❌ Не задано змінну середовища GITHUB_TOKEN.", flush=True)
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    new_content = "\n".join(products) + ("\n" if products else "")
    encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")

    payload = {
        "message": commit_message,
        "content": encoded_content,
    }
    if sha:
        payload["sha"] = sha

    try:
        response = requests.put(
            url,
            headers=github_headers(),
            json=payload,
            timeout=20,
        )
        if response.status_code not in (200, 201):
            print(
                f"❌ GitHub не зберіг products.txt: "
                f"HTTP {response.status_code}; {response.text[:1000]}",
                flush=True,
            )
            return False
        return True

    except Exception as error:
        print(
            f"❌ Помилка запису products.txt у GitHub: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        return False


def get_products_file_data():
    """Повертає список товарів і SHA файлу для безпечного оновлення."""
    if not GITHUB_REPO:
        return None, None, "Не задано GITHUB_REPO"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    try:
        response = requests.get(url, headers=github_headers(), timeout=20)
        if response.status_code != 200:
            return None, None, (
                f"GitHub HTTP {response.status_code}: {response.text[:500]}"
            )

        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        products = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return products, file_data.get("sha"), None

    except Exception as error:
        return None, None, f"{type(error).__name__}: {error}"


# ============================================================
# ПАРСИНГ СТАТУСУ NKON
# ============================================================

def extract_preorder_date(soup, raw_html=""):
    element = soup.select_one(".ampreorder-observed")
    if element:
        text = element.get_text(" ", strip=True)
        match = re.search(r"\b\d{2}-\d{2}-\d{4}\b", text)
        if match:
            return match.group(0)

    # Резервний пошук дати у вихідному HTML, лише якщо сторінка
    # справді містить ознаки передзамовлення.
    if raw_html and re.search(
        r"This product is for pre-order|Estimated shipping date",
        raw_html,
        re.IGNORECASE,
    ):
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
        return "out_of_stock", None

    if (
        preorder_date is not None
        or "pre order" in button_text
        or "pre-order" in button_text
        or preorder_message is not None
    ):
        return "pre_order", preorder_date

    if "in stock" in stock_text:
        return "in_stock", None

    if notify_link is not None:
        return "out_of_stock", None

    return "unknown", None


STATUS_TEXTS = {
    "in_stock": "✅ Є в наявності",
    "out_of_stock": "❌ Немає в наявності",
    "pre_order": "🟡 Попереднє замовлення",
    "unknown": "⚪ Статус не вдалося визначити",
    "error": "⚠️ Помилка завантаження",
}


def check_product(url):
    name = get_product_name(url)

    for attempt in range(REQUEST_RETRIES + 1):
        try:
            response = nkon_session.get(url, timeout=REQUEST_TIMEOUT)
            print(
                f"🔎 NKON {url} -> HTTP {response.status_code}; "
                f"кінцева адреса: {response.url}",
                flush=True,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            title_element = soup.find("title")
            page_title = (
                title_element.get_text(" ", strip=True)
                if title_element else "Заголовок сторінки відсутній"
            )

            # Цей селектор є перевіркою, що NKON повернув саме сторінку товару.
            if not soup.select_one(".product-info-price"):
                detail = (
                    f"Не знайдено блок ціни. Заголовок: {page_title}. "
                    f"Довжина HTML: {len(response.text)}."
                )
                if re.search(
                    r"captcha|cloudflare|access denied|robot",
                    response.text[:20000],
                    re.IGNORECASE,
                ):
                    detail += " Схоже, сайт показав захист від автоматичних запитів."
                raise ValueError(detail)

            state, date = detect_product_status(soup, response.text)

            if state == "unknown":
                detail = (
                    f"Сторінка товару завантажилась, але статус не розпізнано. "
                    f"Заголовок: {page_title}; HTML: {len(response.text)} символів."
                )
                print(f"⚠️ {name}: {detail}", flush=True)
                return {
                    "success": False,
                    "name": name,
                    "url": url,
                    "state": "unknown",
                    "date": None,
                    "status_text": STATUS_TEXTS["unknown"],
                    "error": detail,
                }

            return {
                "success": True,
                "name": name,
                "url": url,
                "state": state,
                "date": date,
                "status_text": STATUS_TEXTS[state],
                "error": None,
            }

        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            print(
                f"❌ Помилка перевірки {url} "
                f"(спроба {attempt + 1}/{REQUEST_RETRIES + 1}): {detail}",
                flush=True,
            )
            if attempt < REQUEST_RETRIES:
                time.sleep(1.5)

    return {
        "success": False,
        "name": name,
        "url": url,
        "state": "error",
        "date": None,
        "status_text": STATUS_TEXTS["error"],
        "error": "Не вдалося завантажити сторінку після кількох спроб.",
    }


# ============================================================
# ФОРМУВАННЯ ЗВІТУ
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

    # Для помилки додаємо коротке пояснення, щоб користувач бачив
    # причину прямо у звіті, а не лише загальний напис.
    if not item.get("success") and item.get("error"):
        error_text = html.escape(item["error"])
        if len(error_text) > 450:
            error_text = error_text[:447] + "..."
        lines.append(f"ℹ️ Деталі: {error_text}")

    lines.append(f'<a href="{url}">Відкрити товар на NKON</a>')
    return "\n".join(lines)


def send_report_in_chunks(chat_id, items):
    heading = "📦 <b>Поточний статус усіх товарів NKON</b>\n\n"
    current_message = heading

    for item in items:
        block = format_product_message(item) + "\n\n"

        if (
            len(current_message) + len(block) > TELEGRAM_MAX_LENGTH
            and current_message != heading
        ):
            send_telegram(chat_id, current_message.strip())
            current_message = heading

        current_message += block

    if current_message.strip() != heading.strip():
        send_telegram(
            chat_id,
            current_message.strip(),
            reply_markup=get_main_keyboard(),
        )


def run_check_and_report(chat_id):
    if not check_lock.acquire(blocking=False):
        send_telegram(
            chat_id,
            "⏳ Перевірка вже виконується. Будь ласка, дочекайтеся звіту.",
            reply_markup=get_main_keyboard(),
        )
        return

    try:
        send_telegram(
            chat_id,
            "⏳ Збираю інформацію про всі товари, зачекайте...",
        )

        products = get_products_from_github()
        if not products:
            send_telegram(
                chat_id,
                "⚠️ Не вдалося отримати список товарів із GitHub або products.txt порожній. "
                "Перевірте логи Render і змінні GITHUB_REPO / GITHUB_TOKEN.",
                reply_markup=get_main_keyboard(),
            )
            return

        results = []
        for index, url in enumerate(products, start=1):
            print(
                f"📦 Перевіряю товар {index}/{len(products)}: {url}",
                flush=True,
            )
            result = check_product(url)
            results.append(result)
            time.sleep(REQUEST_DELAY)

        send_report_in_chunks(chat_id, results)

        successful = sum(1 for item in results if item.get("success"))
        failed = len(results) - successful
        print(
            f"✅ Звіт сформовано. Усього: {len(results)}; "
            f"успішно: {successful}; з помилками/невідомим статусом: {failed}.",
            flush=True,
        )

    except Exception as error:
        print(
            f"❌ Непередбачена помилка формування звіту: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
        send_telegram(
            chat_id,
            "❌ Під час формування звіту сталася помилка. Перевірте логи Render.",
            reply_markup=get_main_keyboard(),
        )
    finally:
        check_lock.release()


# ============================================================
# WEBЗАСТОСУНОК
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/products", methods=["GET"])
def list_products():
    products = get_products_from_github()
    return jsonify({"products": products})


@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"success": False, "error": "Порожня адреса"}), 400
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return jsonify({"success": False, "error": "Потрібне повне посилання http:// або https://"}), 400

    products, sha, error = get_products_file_data()
    if error:
        return jsonify({"success": False, "error": error}), 502

    if url in products:
        return jsonify({"success": False, "error": "Таке посилання вже є!"}), 400

    products.append(url)
    success = save_products_to_github(
        products,
        sha,
        f"Add product via WebApp: {url}",
    )
    if not success:
        return jsonify({"success": False, "error": "Не вдалося зберегти зміни в GitHub"}), 502

    return jsonify({"success": True})


@app.route("/api/products", methods=["DELETE"])
def delete_product():
    data = request.get_json(silent=True) or {}
    url_to_delete = data.get("url", "").strip()

    if not url_to_delete:
        return jsonify({"success": False, "error": "Не вказано посилання"}), 400

    products, sha, error = get_products_file_data()
    if error:
        return jsonify({"success": False, "error": error}), 502

    if url_to_delete not in products:
        return jsonify({"success": False, "error": "Посилання не знайдено"}), 404

    products.remove(url_to_delete)
    success = save_products_to_github(
        products,
        sha,
        f"Delete product via WebApp: {url_to_delete}",
    )
    if not success:
        return jsonify({"success": False, "error": "Не вдалося зберегти зміни в GitHub"}), 502

    return jsonify({"success": True})


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message")

    if not message:
        return jsonify({"status": "ok"})

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id:
        return jsonify({"status": "ok"})

    if text in ("/start", "/help"):
        send_telegram(
            chat_id,
            "Вітаю! Натисніть кнопку нижче, щоб перевірити актуальні статуси всіх товарів NKON.",
            reply_markup=get_main_keyboard(),
        )

    elif text == "🔍 Перевірити всі товари":
        threading.Thread(
            target=run_check_and_report,
            args=(chat_id,),
            daemon=True,
        ).start()

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Render передає порт через змінну PORT.
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
