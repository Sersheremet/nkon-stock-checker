
import os
import re
import json
import time
import html
import requests

from datetime import datetime
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
        "Chrome/120.0.0.0 Safari/537.36"
    )
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


# ============================================================
# НАЗВА ТОВАРУ
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

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print("⚠️ Не задано секрет TELEGRAM_BOT_TOKEN.")
        return False

    api_url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            api_url,
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        print("DEBUG: amp preorder element:", "ampreorder-observed" in response.text)
        print("DEBUG: shipping date text:", "Estimated shipping date" in response.text)
        print("DEBUG: preorder text:", "This product is for pre-order" in response.text)
        print(
            "DEBUG: знайдені дати:",
            re.findall(r"\d{2}-\d{2}-\d{4}", response.text)[:10]
        )

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
# Підтримує як старий формат, так і новий:
# "URL": "in_stock"
# "URL": {"state": "pre_order", "date": "06-11-2026"}
# ============================================================

def load_cache():
    if not os.path.exists(CACHE_FILE):
        print("ℹ️ Кеш ще не існує. Буде створено новий.")
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            raw_cache = json.load(file)

        if not isinstance(raw_cache, dict):
            print("⚠️ Невідомий формат кешу. Починаємо з порожнього.")
            return {}

        cache = {}

        for url, value in raw_cache.items():
            if isinstance(value, str):
                cache[url] = {
                    "state": value,
                    "date": None,
                }

            elif isinstance(value, dict):
                cache[url] = {
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
            json.dump(
                cache,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temporary_file, CACHE_FILE)
        print("💾 Кеш збережено.")

    except Exception as error:
        print(f"❌ Помилка збереження кешу: {error}")


# ============================================================
# ВИТЯГУВАННЯ ДАТИ ВІДВАНТАЖЕННЯ
# Приклад на сайті: Estimated shipping date: 06-11-2026
# ============================================================

def extract_preorder_date(soup):
    element = soup.select_one(".ampreorder-observed")

    if element:
        text = element.get_text(" ", strip=True)
        match = re.search(
            r"(\d{2})-(\d{2})-(\d{4})",
            text,
        )

        if match:
            date_text = match.group(0)

            try:
                datetime.strptime(date_text, "%d-%m-%Y")
                print(f"  📅 Знайдено дату відвантаження: {date_text}")
                return date_text

            except ValueError:
                print(f"  ⚠️ Некоректна дата: {date_text}")

        else:
            print(f"  ℹ️ Елемент дати знайдено, але дати в ньому немає: {text}")

    else:
        print("  ℹ️ Елемент .ampreorder-observed у HTML не знайдено.")

    # Запасний пошук дати в тексті сторінки.
    # Використовується лише якщо є згадка про shipping date.
    page_text = soup.get_text(" ", strip=True)

    if re.search(r"estimated\s+shipping\s+date", page_text, re.I):
        match = re.search(
            r"estimated\s+shipping\s+date\s*:\s*"
            r"(\d{2}-\d{2}-\d{4})",
            page_text,
            re.I,
        )

        if match:
            date_text = match.group(1)

            try:
                datetime.strptime(date_text, "%d-%m-%Y")
                print(f"  📅 Дату знайдено запасним способом: {date_text}")
                return date_text
            except ValueError:
                pass

    return None


# ============================================================
# ВИЗНАЧЕННЯ СТАТУСУ ТОВАРУ
# ============================================================

def detect_product_status(soup, url):
    stock = soup.select_one(".product-info-stock-sku .stock")
    button = soup.select_one("#product-addtocart-button")
    notify_link = soup.select_one(
        ".product.alert.stock a.action.alert"
    )
    preorder_message = soup.select_one(".preorder-message-block")

    stock_text = (
        stock.get_text(" ", strip=True).lower()
        if stock else ""
    )
    stock_classes = stock.get("class", []) if stock else []

    button_text = (
        button.get_text(" ", strip=True).lower()
        if button else ""
    )

    preorder_date = extract_preorder_date(soup)

    # 1. Немає в наявності
    if (
        "unavailable" in stock_classes
        or "out of stock" in stock_text
    ):
        return "out_of_stock", None

    # 2. Попереднє замовлення
    if (
        preorder_date is not None
        or "pre order" in button_text
        or "pre-order" in button_text
        or preorder_message is not None
    ):
        return "pre_order", preorder_date

    # 3. Є в наявності
    if (
        "available" in stock_classes
        and (
            "in stock" in stock_text
            or "add to cart" in button_text
        )
    ):
        return "in_stock", None

    # 4. Альтернативна ознака відсутності товару
    if notify_link is not None:
        return "out_of_stock", None

    print("  ⚠️ Не вдалося достовірно визначити статус товару.")
    return "unknown", None


# ============================================================
# ПЕРЕВІРКА ОДНОГО ТОВАРУ
# ============================================================

def check_product(url):
    name = get_product_name(url)

    for attempt in range(REQUEST_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Переконуємося, що завантажено сторінку товару,
            # а не сторінку помилки чи блокування.
            if not soup.select_one(".product-info-price"):
                raise ValueError(
                    "У відповіді не знайдено блок .product-info-price. "
                    "Можливо, сайт повернув іншу сторінку."
                )

            state, date = detect_product_status(soup, url)

            if state == "unknown":
                print(f"  ⚠️ Статус невідомий для {url}")
                return {
                    "success": False,
                    "name": name,
                    "url": url,
                    "state": "unknown",
                    "date": None,
                }

            status_texts = {
                "in_stock": "✅ Є в наявності",
                "out_of_stock": "❌ Немає в наявності",
                "pre_order": "🟡 Попереднє замовлення",
            }

            status_text = status_texts[state]

            if state == "pre_order" and date:
                status_text += f" (очікувана дата: {date})"

            print(f"  → {status_text}")

            return {
                "success": True,
                "name": name,
                "url": url,
                "state": state,
                "date": date,
                "status_text": status_text,
            }

        except Exception as error:
            print(
                f"  ⚠️ Спроба {attempt + 1}/"
                f"{REQUEST_RETRIES + 1} не вдалася: {error}"
            )

            if attempt < REQUEST_RETRIES:
                time.sleep(2)

    print("  ❌ Не вдалося перевірити товар. Кеш не оновлюємо.")

    return {
        "success": False,
        "name": name,
        "url": url,
        "state": "error",
        "date": None,
    }


# ============================================================
# ФОРМУВАННЯ TELEGRAM-ПОВІДОМЛЕНЬ
# ============================================================

def format_change_message(item):
    name = html.escape(item["name"])
    url = html.escape(item["url"], quote=True)

    lines = [
        f"<b>{name}</b>",
        f"Новий статус: {item['status_text']}",
    ]

    if item.get("date"):
        lines.append(
            f"📅 Очікувана дата відвантаження: "
            f"<b>{html.escape(item['date'])}</b>"
        )

    lines.append(url)

    return "\n".join(lines)


def send_changes_in_chunks(changes):
    if not changes:
        return

    heading = "🔄 <b>Зміни на NKON</b>\n\n"
    current_message = heading

    for item in changes:
        block = format_change_message(item) + "\n\n"

        if (
            len(current_message) + len(block) > TELEGRAM_MAX_LENGTH
            and current_message != heading
        ):
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
            url = line.strip()

            if not url or url.startswith("#"):
                continue

            if url not in urls:
                urls.append(url)

    if not urls:
        print("⚠️ У products.txt немає URL для перевірки.")
        return

    cache = load_cache()
    new_cache = cache.copy()
    changes = []

    print(f"\n🔎 Перевіряємо товарів: {len(urls)}\n")

    for url in urls:
        print(f"Перевірка: {url}")

        try:
            result = check_product(url)

            # Не оновлюємо кеш, якщо перевірка не вдалася.
            if not result["success"]:
                continue

            old_record = cache.get(url)

            # Перший запис товару — без повідомлення.
            if old_record is None:
                print("  ℹ️ Перший запис у кеш — без сповіщення.")
                new_cache[url] = {
                    "state": result["state"],
                    "date": result["date"],
                }
                continue

            old_state = old_record.get("state")
            old_date = old_record.get("date")

            new_state = result["state"]
            new_date = result["date"]

            state_changed = (
                old_state is not None
                and old_state != new_state
            )

            date_changed = (
                new_state == "pre_order"
                and old_state == "pre_order"
                and old_date is not None
                and new_date is not None
                and old_date != new_date
            )

            # Зберігаємо раніше відому дату, якщо сторінка
            # тимчасово не повернула дату для pre-order.
            if (
                new_state == "pre_order"
                and new_date is None
                and old_state == "pre_order"
                and old_date is not None
            ):
                new_date = old_date
                print(
                    "  ℹ️ Нову дату не отримано; "
                    f"залишаємо попередню: {old_date}"
                )

            if state_changed:
                print(
                    f"  🔄 Статус змінився: "
                    f"{old_state} → {new_state}"
                )

                changes.append({
                    "name": result["name"],
                    "url": url,
                    "status_text": result["status_text"],
                    "date": new_date,
                })

            elif date_changed:
                print(
                    f"  📅 Дата змінилася: "
                    f"{old_date} → {new_date}"
                )

                changes.append({
                    "name": result["name"],
                    "url": url,
                    "status_text": (
                        "📅 Змінилася очікувана дата відвантаження"
                    ),
                    "date": new_date,
                })

            else:
                print("  ✓ Змін статусу або дати не виявлено.")

            new_cache[url] = {
                "state": new_state,
                "date": new_date,
            }

        except Exception as error:
            print(f"  ❌ Неочікувана помилка: {error}")
            print("  Кеш цього товару не оновлюємо.")

        finally:
            time.sleep(REQUEST_DELAY)

    # Зберігаємо кеш до надсилання повідомлень.
    save_cache(new_cache)

    if changes:
        print(f"\n📨 Змін виявлено: {len(changes)}")
        send_changes_in_chunks(changes)
    else:
        print("\n✓ Змін статусів або дат не виявлено.")

    print("\nГотово.")


if __name__ == "__main__":
    main()
