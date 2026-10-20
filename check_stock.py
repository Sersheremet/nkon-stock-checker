import os
import re
import json
import time
import html
import requests

from bs4 import BeautifulSoup


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = "1063437372"

CACHE_FILE = "status_cache.json"
PRODUCTS_FILE = "products.txt"

REQUEST_TIMEOUT = 20
REQUEST_RETRIES = 2
REQUEST_DELAY = 1.5
TELEGRAM_MAX_LENGTH = 3500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,uk;q=0.8,nl;q=0.7",
}

# Уточнені назви товарів (довші та специфічніші ключі розташовані нижче, але сортуються автоматично)
PRODUCT_NAMES = {
    "deye-sun-12k-sg02lp1": "Deye SUN-12K SG02 (однофазний 12 кВт)",
    "deye-sun-12k-sg01hp3": "Deye SUN-12K SG01 (трифазний HV 12 кВт)",
    "deye-sun-12k": "Deye SUN-12K (інвертор 12 кВт)",
    "deye-se-f16": "Deye SE-F16-C (батарея 16 кВт·год)",
    "deye-se-f5-pro": "Deye SE-F5 Pro-C (батарея 5.12 кВт·год)",
    "deye-sun-8k": "Deye SUN-8K (інвертор 8 кВт)",
    "deye-sun-5k": "Deye SUN-5K (інвертор 5 кВт)",
    "deye-sun-6k": "Deye SUN-6K (інвертор 6 кВт)",
    "deye-se-f12": "Deye SE-F12-C (батарея)",
    "sun-5k-sg06": "Deye SUN-5K SG06 (інвертор 5 кВт)",
}


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ URL ТА НАЗВ
# ============================================================

def clean_url(url):
    """Видаляє UTM-мітки, параметри та якорі з URL для чистоти кешу."""
    if not url:
        return ""
    return url.split("?")[0].split("#")[0].strip()

def get_product_name(url):
    url_lower = url.lower()

    # Сортуємо ключі за довжиною (від найдовших до найкоротших), 
    # щоб спершу перевіряти більш специфічні моделі (наприклад, з урахуванням модифікації)
    sorted_keys = sorted(PRODUCT_NAMES.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in url_lower:
            return PRODUCT_NAMES[key]

    # Якщо в словнику немає — формуємо охайну назву з URL
    try:
        slug = url.split('/')[-1].split('?')[0].replace('.html', '')
        return slug.replace('-', ' ').title()
    except Exception:
        return url


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Не задано секрет TELEGRAM_BOT_TOKEN.")
        return False

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(api_url, json=payload, timeout=15)
        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            print("⚠️ Telegram повернув помилку:", result)
            return False

        print("✅ Повідомлення Telegram надіслано.")
        return True

    except Exception as error:
        print(f"❌ Помилка Telegram: {error}")
        return False


# ============================================================
# КЕШ
# ============================================================

def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("ℹ️ Кеш ще не існує. Буде створено новий.")
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            raw_cache = json.load(file)

        if not isinstance(raw_cache, dict):
            print("⚠️ Невідомий формат кешу.")
            return {}

        cache = {}

        for url, value in raw_cache.items():
            cleaned_url = clean_url(url)
            if not cleaned_url:
                continue

            if isinstance(value, str):
                cache[cleaned_url] = {
                    "state": value,
                    "date": None,
                }
            elif isinstance(value, dict):
                cache[cleaned_url] = {
                    "state": value.get("state"),
                    "date": value.get("date"),
                }

        print(f"📂 Завантажено записів кешу: {len(cache)}")
        return cache

    except Exception as error:
        print(f"❌ Помилка читання кешу: {error}")
        return {}


def save_cache(cache):
    temporary_file = CACHE_FILE + ".tmp"

    try:
        with open(temporary_file, "w", encoding="utf-8") as file:
            json.dump(cache, file, ensure_ascii=False, indent=2)

        os.replace(temporary_file, CACHE_FILE)
        print("💾 Кеш збережено.")

    except Exception as error:
        print(f"❌ Помилка збереження кешу: {error}")


# ============================================================
# ПОШУК ДАТИ ВІДВАНТАЖЕННЯ (Універсально)
# ============================================================

def extract_preorder_date(soup, raw_html=""):
    # 1. Шукаємо у блоці stock-sku
    stock_sku_block = soup.select_one(".product-info-stock-sku")
    if stock_sku_block:
        text = stock_sku_block.get_text(" ", strip=True)
        match = re.search(r"\b\d{2}-\d{2}-\d{4}\b", text)
        if match:
            date = match.group(0)
            print(f"  DEBUG: Дату знайдено в stock-sku: {date}")
            return date

    # 2. Шукаємо у класі ampreorder-observed
    element = soup.select_one(".ampreorder-observed")
    if element:
        text = element.get_text(" ", strip=True)
        match = re.search(r"\b\d{2}-\d{2}-\d{4}\b", text)
        if match:
            date = match.group(0)
            print(f"  DEBUG: Дату знайдено в ampreorder-observed: {date}")
            return date

    # 3. Резервний пошук у всьому HTML за наявності ключових слів передзамовлення
    if raw_html and re.search(
        r"pre-order|попереднє замовлення|estimated shipping date|орієнтовна дата доставки|дорозі до нас",
        raw_html,
        re.IGNORECASE,
    ):
        dates = re.findall(r"\b\d{2}-\d{2}-\d{4}\b", raw_html)
        if dates:
            date = dates[0]
            print(f"  DEBUG: Дату знайдено в HTML (резерв): {date}")
            return date

    print("  DEBUG: Дату передзамовлення не знайдено.")
    return None


# ============================================================
# ВИЗНАЧЕННЯ СТАТУСУ ТОВАРУ
# ============================================================

def detect_product_status(soup, url, raw_html=""):
    stock = soup.select_one(".product-info-stock-sku .stock")
    button = soup.select_one("#product-addtocart-button")
    notify_link = soup.select_one(".product.alert.stock a.action.alert")

    stock_text = stock.get_text(" ", strip=True).lower() if stock else ""
    stock_classes = stock.get("class", []) if stock else []
    button_text = button.get_text(" ", strip=True).lower() if button else ""

    preorder_date = extract_preorder_date(soup, raw_html)

    # ПРІОРИТЕТ №1: Якщо виявлено дату у форматі ДД-ММ-РРРР або блок передзамовлення — це 100% pre_order
    if preorder_date is not None or "preorder-message-block" in raw_html or "попереднього замовлення" in raw_html.lower():
        return "pre_order", preorder_date

    # ПРІОРИТЕТ №2: Кнопка або текст містять ознаки передзамовлення
    if "pre order" in button_text or "pre-order" in button_text:
        return "pre_order", preorder_date

    # ПРІОРИТЕТ №3: Товар точно відсутній
    if (
        "unavailable" in stock_classes
        or "out of stock" in stock_text
        or "немає в наявності" in stock_text
        or notify_link is not None
    ):
        return "out_of_stock", None

    # ПРІОРИТЕТ №4: Товар у наявності
    if (
        "available" in stock_classes
        or "in stock" in stock_text
        or "в наявності" in stock_text
    ):
        return "in_stock", None

    print("  ⚠️ Статус невідомий: недостатньо ознак.")
    return "unknown", None


# ============================================================
# ПЕРЕВІРКА ОДНОГО ТОВАРУ
# ============================================================

def check_product(url):
    clean_target_url = clean_url(url)
    name = get_product_name(clean_target_url)

    for attempt in range(REQUEST_RETRIES + 1):
        try:
            response = requests.get(clean_target_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            if not soup.select_one(".product-info-price"):
                raise ValueError("Не знайдено .product-info-price. Можливо, сторінка інша.")

            state, date = detect_product_status(soup, clean_target_url, response.text)

            if state == "unknown":
                print("  ⚠️ Результат неоднозначний. Кеш не оновлюємо.")
                return {
                    "success": False,
                    "name": name,
                    "url": clean_target_url,
                    "state": "unknown",
                    "date": None,
                }

            status_texts = {
                "in_stock": "✅ Є в наявності",
                "out_of_stock": "❌ Немає в наявності",
                "pre_order": "🟡 Попереднє замовлення",
            }

            status_text = status_texts[state]

            print(f"  → Статус: {status_text}" + (f" (дата: {date})" if date else ""))

            return {
                "success": True,
                "name": name,
                "url": clean_target_url,
                "state": state,
                "date": date,
                "status_text": status_text,
            }

        except Exception as error:
            print(f"  ⚠️ Спроба {attempt + 1}/{REQUEST_RETRIES + 1} не вдалася: {error}")
            if attempt < REQUEST_RETRIES:
                time.sleep(2)

    print("  ❌ Не вдалося перевірити товар.")
    return {
        "success": False,
        "name": name,
        "url": clean_target_url,
        "state": "error",
        "date": None,
    }


# ============================================================
# ФОРМУВАННЯ TELEGRAM-ПОВІДОМЛЕНЬ
# ============================================================

def format_change_message(item):
    name = html.escape(item["name"])
    url = html.escape(item["url"], quote=True)
    status_text = html.escape(item["status_text"])

    lines = [
        f"<b>{name}</b>",
        f"Новий статус: {status_text}",
    ]

    if item.get("change_type") == "date_changed":
        old_date = html.escape(item["old_date"])
        new_date = html.escape(item["date"])
        lines.append(f"📅 Дату відвантаження змінено: <b>{old_date} → {new_date}</b>")
    elif item.get("date"):
        date = html.escape(item["date"])
        lines.append(f"📅 Очікувана дата відвантаження: <b>{date}</b>")

    lines.append(f'<a href="{url}">Відкрити товар на NKON</a>')
    return "\n".join(lines)


def send_changes_in_chunks(changes):
    if not changes:
        return

    heading = "🔄 <b>Зміни на NKON</b>\n\n"
    current_message = heading

    for item in changes:
        block = format_change_message(item) + "\n\n"

        if len(current_message) + len(block) > TELEGRAM_MAX_LENGTH and current_message != heading:
            send_telegram(current_message.strip())
            current_message = heading

        current_message += block

    if current_message.strip() != heading.strip():
        send_telegram(current_message.strip())


# ============================================================
# ОСНОВНА ПРОГРАМА
# ============================================================

def main():
    if not os.path.exists(PRODUCTS_FILE):
        print(f"❌ Файл {PRODUCTS_FILE} не знайдено.")
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as file:
        urls = []
        for line in file:
            cleaned = clean_url(line)
            if not cleaned or cleaned.startswith("#"):
                continue
            if cleaned not in urls:
                urls.append(cleaned)

    if not urls:
        print("⚠️ У products.txt немає URL для перевірки.")
        return

    cache = load_cache()
    new_cache = {url: cache[url] for url in urls if url in cache}
    changes = []

    print(f"\n🔎 Перевіряємо товарів: {len(urls)}\n")

    for url in urls:
        print(f"Перевірка: {url}")
        try:
            result = check_product(url)

            if not result["success"]:
                continue

            target_url = result["url"]
            old_record = cache.get(target_url)

            if old_record is None:
                print("  ℹ️ Перший запис у кеш — без сповіщення.")
                new_cache[target_url] = {
                    "state": result["state"],
                    "date": result["date"],
                }
                continue

            old_state = old_record.get("state")
            old_date = old_record.get("date")

            new_state = result["state"]
            new_date = result["date"]

            state_changed = old_state is not None and old_state != new_state
            date_changed = (
                old_state == "pre_order"
                and new_state == "pre_order"
                and old_date is not None
                and new_date is not None
                and old_date != new_date
            )

            if new_state == "pre_order" and new_date is None and old_state == "pre_order" and old_date is not None:
                new_date = old_date
                print(f"  ℹ️ Залишаємо попередню дату: {old_date}")

            if state_changed:
                print(f"  🔄 Статус змінився: {old_state} → {new_state}")
                changes.append({
                    "name": result["name"],
                    "url": target_url,
                    "status_text": result["status_text"],
                    "date": new_date,
                    "change_type": "status_changed",
                })
            elif date_changed:
                print(f"  📅 Дата змінилася: {old_date} → {new_date}")
                changes.append({
                    "name": result["name"],
                    "url": target_url,
                    "status_text": "🟡 Попереднє замовлення",
                    "old_date": old_date,
                    "date": new_date,
                    "change_type": "date_changed",
                })
            else:
                print("  ✓ Змін статусу або дати не виявлено.")

            new_cache[target_url] = {
                "state": new_state,
                "date": new_date,
            }

        except Exception as error:
            print(f"  ❌ Неочікувана помилка: {error}")

        finally:
            time.sleep(REQUEST_DELAY)

    save_cache(new_cache)

    if changes:
        print(f"\n📨 Змін виявлено: {len(changes)}")
        send_changes_in_chunks(changes)
    else:
        print("\n✓ Змін статусів або дат не виявлено.")

    print("\nГотово.")


if __name__ == "__main__":
    main()
