
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
