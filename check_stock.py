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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,nl;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

PRODUCT_NAMES = {
    "deye-sun-12k-sg02lp1": "Deye SUN-12K SG02 (однофазний 12 кВт)",
    "deye-sun-12k-sg01hp3": "Deye SUN-12K SG01 (трифазний HV 12 кВт)",
    "deye-sun-12k": "Deye SUN-12K (інвертор 12 кВт)",
    "deye-sun-80k": "Deye SUN-80K (трифазний HV 80 кВт)",
    "deye-15kwlp3": "Deye 15kW LP3 (низьковольтний інвертор)",
    "deye-se-f16": "Deye SE-F16-C (батарея 16 кВт·год)",
    "deye-se-f5-pro": "Deye SE-F5 Pro-C (батарея 5.12 кВт·год)",
    "deye-sun-8k": "Deye SUN-8K (інвертор 8 кВт)",
    "deye-sun-5k": "Deye SUN-5K (інвертор 5 кВт)",
    "deye-sun-6k": "Deye SUN-6K (інвертор 6 кВт)",
    "deye-se-f12": "Deye SE-F12-C (батарея)",
    "sun-5k-sg06": "Deye SUN-5K SG06 (інвертор 5 кВт)",
    "felicity-fla48314": "Felicity FLA48314 (батарея 16 кВт·год)",
    "felicity-flb48314": "Felicity 16 кВт·год (HV батарея)",
}


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ URL ТА НАЗВ
# ============================================================

def clean_url(url):
    """Видаляє UTM-мітки, параметри та якорі з URL для чистоти кешу."""
    if not url:
        return ""
    return url.split("?")[0].split("#")[0].strip()

def get_ua_fetch_url(url):
    """Перетворює посилання на українську версію (/ua/)."""
    cleaned = clean_url(url)
    if "/novat/" in cleaned:
        return cleaned.replace("/novat/", "/ua/")
    elif "www.nkon.nl/" in cleaned and "/ua/" not in cleaned:
        return cleaned.replace("www.nkon.nl/", "www.nkon.nl/ua/")
    return cleaned

def get_product_name(url):
    url_lower = url.lower()
    sorted_keys = sorted(PRODUCT_NAMES.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in url_lower:
            return PRODUCT_NAMES[key]

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
        return True
    except Exception as error:
        print(f"❌ Помилка Telegram: {error}")
        return False


# ============================================================
# КЕШ
# ============================================================

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            raw_cache = json.load(file)

        if not isinstance(raw_cache, dict):
            return {}

        cache = {}
        for url, value in raw_cache.items():
            cleaned_url = clean_url(url)
            if not cleaned_url:
                continue

            if isinstance(value, str):
                cache[cleaned_url] = {"state": value, "date": None, "price": None, "shipping": None}
            elif isinstance(value, dict):
                cache[cleaned_url] = {
                    "state": value.get("state"),
                    "date": value.get("date"),
                    "price": value.get("price"),
                    "shipping": value.get("shipping"),
                }
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
    except Exception as error:
        print(f"❌ Помилка збереження кешу: {error}")


# ============================================================
# ВИДОБУВАННЯ ЦІНИ, ДОСТАВКИ ТА ДАТИ
# ============================================================

def extract_price(soup):
    price_elem = soup.select_one(".price-box .price-wrapper .price")
    if price_elem:
        price_text = price_elem.get_text(" ", strip=True)
        return price_text.replace("\xa0", " ")
    return None

def extract_shipping(soup):
    shipping_elem = soup.select_one(".meest-estimated-shipping p strong")
    if shipping_elem:
        text = shipping_elem.get_text(" ", strip=True)
        return text.replace("\xa0", " ")
    return None

def extract_preorder_date(soup, raw_html=""):
    stock_sku_block = soup.select_one(".product-info-stock-sku")
    if stock_sku_block:
        text = stock_sku_block.get_text(" ", strip=True)
        match = re.search(r"\b\d{2}-\d{2}-\d{4}\b", text)
        if match:
            return match.group(0)

    element = soup.select_one(".ampreorder-observed")
    if element:
        text = element.get_text(" ", strip=True)
        match = re.search(r"\b\d{2}-\d{2}-\d{4}\b", text)
        if match:
            return match.group(0)

    if raw_html and re.search(r"pre-order|попереднє замовлення|estimated shipping date|орієнтовна дата доставки|дорозі до нас", raw_html, re.IGNORECASE):
        dates = re.findall(r"\b\d{2}-\d{2}-\d{4}\b", raw_html)
        if dates:
            return dates[0]

    return None


# ============================================================
# ВИЗНАЧЕННЯ СТАТУСУ ТОВАРУ
# ============================================================

def detect_product_status(soup, raw_html=""):
    stock = soup.select_one(".product-info-stock-sku .stock")
    button = soup.select_one("#product-addtocart-button")
    notify_link = soup.select_one(".product.alert.stock a.action.alert")

    stock_text = stock.get_text(" ", strip=True).lower() if stock else ""
    stock_classes = stock.get("class", []) if stock else []
    button_text = button.get_text(" ", strip=True).lower() if button else ""

    preorder_date = extract_preorder_date(soup, raw_html)

    if preorder_date is not None or "preorder-message-block" in raw_html or "попереднього замовлення" in raw_html.lower():
        return "pre_order", preorder_date

    if "pre order" in button_text or "pre-order" in button_text:
        return "pre_order", preorder_date

    if "unavailable" in stock_classes or "out of stock" in stock_text or "немає в наявності" in stock_text or notify_link is not None:
        return "out_of_stock", None

    if "available" in stock_classes or "in stock" in stock_text or "в наявності" in stock_text:
        return "in_stock", None

    return "unknown", None


# ============================================================
# ПЕРЕВІРКА ОДНОГО ТОВАРУ
# ============================================================

def check_product(session, original_url):
    clean_target_url = clean_url(original_url)
    fetch_url = get_ua_fetch_url(clean_target_url)
    name = get_product_name(clean_target_url)

    for attempt in range(REQUEST_RETRIES + 1):
        try:
            session.post(
                "https://www.nkon.nl/ua/directory/currency/switch/",
                data={"currency": "UAH", "uenc": "aHR0cHM6Ly93d3cubmtvbi5ubC91YS8,"},
                headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
                timeout=10
            )

            response = session.get(fetch_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            if not soup.select_one(".product-info-price"):
                raise ValueError("Не знайдено .product-info-price.")

            state, date = detect_product_status(soup, response.text)
            price = extract_price(soup)
            shipping = extract_shipping(soup)

            if state == "unknown":
                return {"success": False, "name": name, "url": clean_target_url, "state": "unknown", "date": None, "price": None, "shipping": None}

            status_texts = {
                "in_stock": "✅ Є в наявності",
                "out_of_stock": "❌ Немає в наявності",
                "pre_order": "🟡 Попереднє замовлення",
            }

            return {
                "success": True,
                "name": name,
                "url": clean_target_url,
                "state": state,
                "date": date,
                "price": price,
                "shipping": shipping,
                "status_text": status_texts[state],
            }
        except Exception as error:
            print(f"  ⚠️ Спроба {attempt + 1} не вдалася: {error}")
            if attempt < REQUEST_RETRIES:
                time.sleep(2)

    return {"success": False, "name": name, "url": clean_target_url, "state": "error", "date": None, "price": None, "shipping": None}


# ============================================================
# КОМПАКТНЕ ФОРМУВАННЯ ПОВІДОМЛЕНЬ
# ============================================================

def format_change_message(item):
    name = html.escape(item["name"])
    url = html.escape(item["url"], quote=True)
    status_text = html.escape(item["status_text"])
    price = item.get("price")
    shipping = item.get("shipping")
    date = item.get("date")

    status_emojis = {"in_stock": "✅", "out_of_stock": "❌", "pre_order": "🟡"}
    state_key = "in_stock" if "Є в" in status_text else ("out_of_stock" if "Немає" in status_text else "pre_order")
    emoji = status_emojis.get(state_key, "⚪")

    parts = []
    if price:
        parts.append(f"💰 <b>{html.escape(price)}</b>")
    if shipping:
        ship_match = re.search(r"€[\d.,]+", shipping)
        ship_str = ship_match.group(0) if ship_match else shipping
        parts.append(f"📦 {ship_str}")
    if date:
        parts.append(f"📅 <b>{html.escape(date)}</b>")

    details_line = " | ".join(parts) if parts else status_text

    lines = [
        f"{emoji} <b>{name}</b>",
        details_line,
        f'<a href="{url}">🔗 Відкрити на NKON</a>'
    ]
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
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as file:
        urls = [clean_url(line) for line in file if line.strip() and not line.strip().startswith("#")]
        urls = list(dict.fromkeys([u for u in urls if u]))

    if not urls:
        return

    cache = load_cache()
    new_cache = {url: cache[url] for url in urls if url in cache}
    changes = []

    session = requests.Session()

    # Ініціалізація сесії на головній сторінці для отримання Cloudflare кук
    try:
        session.get("https://www.nkon.nl/ua/", headers=HEADERS, timeout=15)
        time.sleep(1)
    except Exception as e:
        print(f"⚠️ Не вдалося ініціалізувати сесію на головній: {e}")

    for url in urls:
        try:
            result = check_product(session, url)
            if not result["success"]:
                continue

            target_url = result["url"]
            old_record = cache.get(target_url)

            if old_record is None:
                new_cache[target_url] = {
                    "state": result["state"],
                    "date": result["date"],
                    "price": result["price"],
                    "shipping": result["shipping"]
                }
                continue

            old_state = old_record.get("state")
            old_date = old_record.get("date")
            old_price = old_record.get("price")
            old_shipping = old_record.get("shipping")

            new_state = result["state"]
            new_date = result["date"]
            new_price = result["price"]
            new_shipping = result["shipping"]

            state_changed = old_state is not None and old_state != new_state
            price_changed = old_price is not None and new_price is not None and old_price != new_price
            shipping_changed = old_shipping is not None and new_shipping is not None and old_shipping != new_shipping
            date_changed = (old_state == "pre_order" and new_state == "pre_order" and old_date and new_date and old_date != new_date)

            if new_state == "pre_order" and new_date is None and old_date:
                new_date = old_date

            if state_changed or price_changed or shipping_changed:
                changes.append({
                    "name": result["name"],
                    "url": target_url,
                    "status_text": result["status_text"],
                    "price": new_price,
                    "shipping": new_shipping,
                    "date": new_date,
                    "change_type": "status_changed",
                })
            elif date_changed:
                changes.append({
                    "name": result["name"],
                    "url": target_url,
                    "status_text": "🟡 Попереднє замовлення",
                    "price": new_price,
                    "shipping": new_shipping,
                    "old_date": old_date,
                    "date": new_date,
                    "change_type": "date_changed",
                })

            new_cache[target_url] = {
                "state": new_state,
                "date": new_date,
                "price": new_price,
                "shipping": new_shipping
            }
        except Exception as error:
            print(f"Помилка: {error}")
        finally:
            time.sleep(REQUEST_DELAY)

    save_cache(new_cache)
    if changes:
        send_changes_in_chunks(changes)


if __name__ == "__main__":
    main()
