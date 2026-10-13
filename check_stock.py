import os
import requests
from bs4 import BeautifulSoup
import time

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = "1063437372"

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

def check_product(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True).lower()

        # Назви для зручності
        if "deye-sun-12k" in url:
            name = "Deye SUN-12K (інвертор 12кВт)"
        elif "deye-se-f16" in url:
            name = "Deye SE-F16-C (16 кВт·год батарея)"
        elif "deye-se-f5-pro" in url:
            name = "Deye SE-F5 Pro-C (5.12 кВт·год)"
        else:
            name = url

        out_of_stock = "out of stock" in text
        add_to_cart = "add to cart" in text

        if out_of_stock:
            status = "❌ Немає в наявності"
            available = False
        elif add_to_cart:
            status = "✅ Є в наявності / можна замовити"
            available = True
        else:
            status = "⚠️ Невизначений статус"
            available = False

        return {
            "name": name,
            "url": url,
            "status": status,
            "available": available,
            "out_of_stock": out_of_stock,
            "add_to_cart": add_to_cart
        }
    except Exception as e:
        return {
            "name": url,
            "url": url,
            "status": f"Помилка перевірки: {e}",
            "available": False,
            "error": True
        }

def main():
    if not os.path.exists("products.txt"):
        print("products.txt not found")
        return

    with open("products.txt", "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"Checking {len(urls)} products...")
    results = []
    newly_available = []

    for url in urls:
        print(f"Checking: {url}")
        result = check_product(url)
        results.append(result)
        print(f"  → {result['status']}")
        time.sleep(1.5)  # невелика пауза між запитами

        if result.get("available"):
            newly_available.append(result)

    # Формуємо повідомлення
    if newly_available:
        message = "🔔 <b>Товар з'явився в наявності!</b>\n\n"
        for item in newly_available:
            message += f"<b>{item['name']}</b>\n{item['status']}\n{item['url']}\n\n"
        send_telegram(message)
    else:
        # Можна закоментувати, якщо не хочеш отримувати повідомлення "все ще немає"
        print("Нічого нового не з'явилося")

if __name__ == "__main__":
    main()
