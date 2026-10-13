import os
import json
import requests
from bs4 import BeautifulSoup
import time

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = "1063437372"
CACHE_FILE = "status_cache.json"

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print("No TELEGRAM_BOT_TOKEN")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        print("Telegram status:", r.status_code)
    except Exception as e:
        print("Telegram error:", e)

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print("Error loading cache:", e)
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Error saving cache:", e)

def check_product(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True).lower()

        # Зручні назви товарів
        if "deye-sun-12k" in url:
            name = "Deye SUN-12K (інвертор 12кВт)"
        elif "deye-se-f16" in url:
            name = "Deye SE-F16-C (16 кВт·год батарея)"
        elif "deye-se-f5-pro" in url:
            name = "Deye SE-F5 Pro-C (5.12 кВт·год)"
        else:
            name = url

        out_of_stock = "out of stock" in text
        pre_order = "pre order" in text or "estimated shipping date" in text
        add_to_cart = "add to cart" in text

        # Точне визначення статусу
        if out_of_stock:
            state_key = "out_of_stock"
            status_text = "❌ Немає в наявності"
        elif pre_order:
            state_key = "pre_order"
            status_text = "🟡 Доступне попереднє замовлення (Pre-order)"
        elif add_to_cart and not pre_order:
            state_key = "in_stock"
            status_text = "✅ Є в наявності (відвантаження одразу)"
        else:
            state_key = "unknown"
            status_text = "⚠️ Невизначений статус"

        return {
            "name": name,
            "url": url,
            "state_key": state_key,
            "status_text": status_text
        }
    except Exception as e:
        return {
            "name": url,
            "url": url,
            "state_key": "error",
            "status_text": f"Помилка перевірки: {e}"
        }

def main():
    if not os.path.exists("products.txt"):
        print("products.txt not found")
        return

    with open("products.txt", "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    cache = load_cache()
    new_cache = cache.copy()
    changes = []

    print(f"Checking {len(urls)} products...")

    for url in urls:
        print(f"Checking: {url}")
        res = check_product(url)
        old_state = cache.get(url, None)
        new_state = res["state_key"]

        print(f"  → Статус: {res['status_text']} (Минулий: {old_state}, Новий: {new_state})")

        # Надсилаємо сповіщення лише при реальній зміні статусу (якщо це не перший запуск)
        if old_state is not None and old_state != new_state:
            changes.append({
                "name": res["name"],
                "url": url,
                "old_state": old_state,
                "new_state": new_state,
                "status_text": res["status_text"]
            })

        # Записуємо новий стан у кеш
        new_cache[url] = new_state
        time.sleep(1.5)

    save_cache(new_cache)

    # Якщо відбулися зміни в статусах — надсилаємо в Telegram
    if changes:
        message = "🔄 <b>Зміна статусу товару на NKON!</b>\n\n"
        for item in changes:
            message += f"<b>{item['name']}</b>\nНовий статус: {item['status_text']}\n{item['url']}\n\n"
        send_telegram(message)
    else:
        print("Змін статусів не виявлено.")

if __name__ == "__main__":
    main()
