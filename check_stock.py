import os
import re
import json
import time
import html
import requests

from datetime import datetime
from bs4 import BeautifulSoup
from requests.exceptions import RequestException


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = "1063437372"

CACHE_FILE = "status_cache.json"
PRODUCTS_FILE = "products.txt"

REQUEST_TIMEOUT = 20
RETRIES = 2
REQUEST_DELAY = 1.5
TELEGRAM_MAX_LENGTH = 3500


# Назви товарів для повідомлень Telegram
PRODUCT_NAMES = {
    "deye-sun-12k": "Deye SUN-12K (інвертор 12кВт)",
    "deye-se-f16": "Deye SE-F16-C (16 кВт·год батарея)",
    "deye-se-f5-pro": "Deye SE-F5 Pro-C (5.12 кВт·год)",
}


STATUS_TEXTS = {
    "in_stock": "✅ Є в наявності (відвантаження одразу)",
    "pre_order": "🟡 Доступне попереднє замовлення (Pre-order)",
    "out_of_stock": "❌ Немає в наявності",
}


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def get_product_name(url):
    """Повертає зрозумілу назву товару."""
    url_lower = url.lower()

    for product_key, name in PRODUCT_NAMES.items():
        if product_key in url_lower:
            return name

    return url


def send_telegram(message):
    """Надсилає повідомлення в Telegram і перевіряє відповідь API."""
    if not TELEGRAM_BOT_TOKEN:
        print("Помилка: змінна TELEGRAM_BOT_TOKEN не задана.")
        return False

    api_url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            api_url,
            json=payload,
            timeout=15,
        )
        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            print("Помилка Telegram API:", result)
            return False

        print("Telegram: повідомлення успішно прийнято API.")
        return True

    except (RequestException, ValueError) as error:
        print("Помилка надсилання в Telegram:", error)
        return False


def load_cache():
    """
    Читає як старий кеш (URL -> рядок статусу),
    так і новий (URL -> об'єкт зі статусом та датою).
    """
    if not os.path.exists(CACHE_FILE):
        return {}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            print("Помилка: формат кешу невідомий. Кеш не використано.")
            return {}

        cache = {}

        for url, value in data.items():
            if isinstance(value, str):
                # Старий формат:
                # "URL": "in_stock"
                cache[url] = {
                    "state": value,
                    "date": None,
                }

            elif isinstance(value, dict):
                # Новий формат:
                # "URL": {"state": "pre_order", "date": "06-11-2026"}
                state = value.get("state")
                date = value.get("date")

                if isinstance(state, str):
                    cache[url] = {
                        "state": state,
                        "date": date if isinstance(date, str) else None,
                    }

        print(f"Завантажено товарів із кешу: {len(cache)}")
        return cache

    except (OSError, json.JSONDecodeError) as error:
        print("Помилка читання кешу:", error)
        return {}


def save_cache(cache):
    """Зберігає кеш атомарно, щоб зменшити ризик пошкодження файлу."""
    temp_file = CACHE_FILE + ".tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(
                cache,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_file, CACHE_FILE)
        print("Кеш збережено.")

    except OSError as error:
        print("Помилка збереження кешу:", error)

        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass


def extract_preorder_date(soup):
    """
    Витягує дату з елемента:
    <span class="ampreorder-observed">
        Estimated shipping date: 06-11-2026
    </span>

    Повертає дату у форматі DD-MM-YYYY або None.
    """
    date_element = soup.select_one(".ampreorder-observed")

    if not date_element:
        return None

    text = date_element.get_text(" ", strip=True)

    match = re.search(
        r"(\d{2})-(\d{2})-(\d{4})",
        text,
    )

    if not match:
        print(f"Не вдалося розпізнати дату передзамовлення: {text}")
        return None

    date_text = match.group(0)

    try:
        datetime.strptime(date_text, "%d-%m-%Y")
        return date_text
    except ValueError:
        print(f"Некоректна дата передзамовлення: {date_text}")
        return None


def detect_product_status(soup, url):
    """
    Визначає статус за конкретними елементами HTML NKON.

    Повертає:
        state_key: in_stock / pre_order / out_of_stock / unknown
        preorder_date: дата або None
    """
    stock_element = soup.select_one(".product-info-stock-sku .stock")
    stock_text = (
        stock_element.get_text(" ", strip=True).lower()
        if stock_element
        else ""
    )

    stock_classes = (
        stock_element.get("class", [])
        if stock_element
        else []
    )

    button = soup.select_one("#product-addtocart-button")
    button_text = (
        button.get_text(" ", strip=True).lower()
        if button
        else ""
    )

    notify_button = soup.select_one(
        ".product.alert.stock a.action.alert"
    )

    preorder_date = extract_preorder_date(soup)

    # Ознаки передзамовлення
    preorder_message = soup.select_one(".preorder-message-block")

    is_preorder = (
        preorder_date is not None
        or "pre order" in button_text
        or "pre-order" in button_text
        or preorder_message is not None
    )

    # 1. Явний статус відсутності товару
    if (
        "unavailable" in stock_classes
        or "out of stock" in stock_text
    ):
        return "out_of_stock", None

    # 2. Передзамовлення має пріоритет над загальним
    # класом stock available, оскільки NKON використовує
    # цей клас і для товарів, які ще їдуть зі складу постачальника.
    if is_preorder:
        return "pre_order", preorder_date

    # 3. Товар реально доступний для звичайної покупки
    if (
        "available" in stock_classes
        and "in stock" in stock_text
        and "pre order" not in button_text
    ):
        return "in_stock", None

    # 4. Додаткова ознака відсутності
    if notify_button:
        return "out_of_stock", None

    # 5. Збереження старого спеціального правила для SE-F5 Pro-C:
    # використовуємо його лише тоді, коли сторінка схожа на
    # сторінку товару, але статус не вдалося визначити.
    # Явні статуси вище завжди мають пріоритет.
    if (
        "deye-se-f5-pro" in url.lower()
        and soup.select_one(".price-box")
        and soup.select_one("#product_addtocart_form")
    ):
        print(
            "Увага: для SE-F5 Pro-C застосовано "
            "резервне правило передзамовлення."
        )
        return "pre_order", preorder_date

    # Незнайомий HTML: не вважаємо це зміною статусу
    return "unknown", None


def check_product(url):
    """Завантажує сторінку й визначає статус товару."""
    name = get_product_name(url)

    for attempt in range(1, RETRIES + 2):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # Захист від сторінок помилок, заглушок та
            # HTML, який не схожий на сторінку товару.
            if not soup.select_one(".product-info-price"):
                raise ValueError(
                    "Не знайдено блок .product-info-price. "
                    "Можливо, сайт повернув заглушку або змінив HTML."
                )

            state_key, preorder_date = detect_product_status(
                soup,
                url,
            )

            if state_key == "unknown":
                print(
                    f"Не вдалося визначити статус товару: {url}"
                )
                return {
                    "success": False,
                    "name": name,
                    "url": url,
                    "state_key": "unknown",
                    "status_text": "⚠️ Невизначений статус",
                    "date": None,
                }

            status_text = STATUS_TEXTS[state_key]

            print(
                f"Визначено: {status_text}"
                + (
                    f"; дата: {preorder_date}"
                    if preorder_date
                    else ""
                )
            )

            return {
                "success": True,
                "name": name,
                "url": url,
                "state_key": state_key,
                "status_text": status_text,
                "date": preorder_date,
            }

        except (RequestException, ValueError) as error:
            print(
                f"Помилка перевірки {url} "
                f"(спроба {attempt}/{RETRIES + 1}): {error}"
            )

            if attempt <= RETRIES:
                time.sleep(2 * attempt)

        except Exception as error:
            # Несподівані помилки також не повинні
            # змінювати кеш товару.
            print(f"Непередбачена помилка для {url}: {error}")
            break

    return {
        "success": False,
        "name": name,
        "url": url,
        "state_key": "error",
        "status_text": "⚠️ Помилка перевірки",
        "date": None,
    }


def format_change_message(changes):
    """Формує HTML-повідомлення для Telegram."""
    message = "🔄 <b>Зміна статусу товару на NKON!</b>\n\n"

    for item in changes:
        name = html.escape(item["name"])
        url = html.escape(item["url"], quote=True)

        message += f"<b>{name}</b>\n"
        message += f"Новий статус: {item['status_text']}\n"

        if (
            item["new_state"] == "pre_order"
            and item.get("new_date")
        ):
            formatted_date = datetime.strptime(
                item["new_date"],
                "%d-%m-%Y",
            ).strftime("%d.%m.%Y")

            message += (
                f"📅 Очікувана дата відвантаження: "
                f"{formatted_date}\n"
            )

        message += f"{url}\n\n"

    return message


def send_changes_in_chunks(changes):
    """Надсилає повідомлення частинами, якщо змін багато."""
    chunk = []

    for change in changes:
        candidate = chunk + [change]
        message = format_change_message(candidate)

        if len(message) > TELEGRAM_MAX_LENGTH and chunk:
            send_telegram(format_change_message(chunk))
            chunk = [change]
        else:
            chunk = candidate

    if chunk:
        send_telegram(format_change_message(chunk))


def main():
    if not os.path.exists(PRODUCTS_FILE):
        print(f"{PRODUCTS_FILE} не знайдено.")
        return

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as file:
        urls = [
            line.strip()
            for line in file
            if line.strip()
            and not line.strip().startswith("#")
        ]

    # Прибираємо дублікати, зберігаючи початковий порядок
    urls = list(dict.fromkeys(urls))

    if not urls:
        print("У products.txt немає URL для перевірки.")
        return

    cache = load_cache()
    new_cache = cache.copy()
    changes = []

    print(f"Перевіряємо товарів: {len(urls)}")

    for url in urls:
        print(f"\nПеревірка: {url}")

        try:
            result = check_product(url)

            if not result["success"]:
                print("Кеш для цього товару не змінюємо.")
                continue

            old_record = cache.get(url)
            new_state = result["state_key"]
            new_date = result["date"]

            if old_record is None:
                print(
                    "Перший запис товару в кеш. "
                    "Сповіщення не надсилаємо."
                )

            else:
                old_state = old_record.get("state")
                old_date = old_record.get("date")

                # Невідомий статус зі старого кешу не вважаємо
                # достовірним станом для сповіщення.
                if old_state not in STATUS_TEXTS:
                    print(
                        "Старий статус не є достовірним; "
                        "оновлюємо кеш без сповіщення."
                    )

                elif old_state != new_state:
                    changes.append({
                        "name": result["name"],
                        "url": url,
                        "old_state": old_state,
                        "new_state": new_state,
                        "status_text": result["status_text"],
                        "new_date": new_date,
                    })

                elif (
                    new_state == "pre_order"
                    and old_date
                    and new_date
                    and old_date != new_date
                ):
                    # Статус не змінився, але змінилася дата
                    # очікуваного відвантаження.
                    changes.append({
                        "name": result["name"],
                        "url": url,
                        "old_state": old_state,
                        "new_state": new_state,
                        "status_text": (
                            "📅 Змінилася очікувана дата "
                            "відвантаження"
                        ),
                        "new_date": new_date,
                    })

                elif (
                    new_state == "pre_order"
                    and old_date
                    and not new_date
                ):
                    # Не стираємо вже відому дату через те,
                    # що сайт тимчасово перестав її показувати.
                    print(
                        "Дата передзамовлення зараз відсутня; "
                        "зберігаємо попередню дату."
                    )

                    new_date = old_date

            # Зберігаємо новий формат кешу.
            new_cache[url] = {
                "state": new_state,
                "date": new_date,
            }

            print(
                f"Статус: {result['status_text']}; "
                f"дата: {new_date or 'не вказана'}"
            )

        finally:
            # Затримка між товарами навіть після помилки
            time.sleep(REQUEST_DELAY)

    save_cache(new_cache)

    if changes:
        print(f"\nВиявлено змін: {len(changes)}")
        send_changes_in_chunks(changes)
    else:
        print("\nЗмін статусів не виявлено.")


if __name__ == "__main__":
    main()
