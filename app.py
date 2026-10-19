import os
import base64
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Моніторинг NKON</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--tg-theme-bg-color, #f4f4f5);
            color: var(--tg-theme-text-color, #222);
            margin: 0;
            padding: 16px;
        }
        h2 {
            font-size: 18px;
            margin-top: 10px;
            margin-bottom: 12px;
        }
        .card {
            background: var(--tg-theme-secondary-bg-color, #fff);
            padding: 12px;
            border-radius: 12px;
            margin-bottom: 10px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }
        .card-link {
            color: var(--tg-theme-link-color, #2481cc);
            text-decoration: none;
            word-break: break-all;
            max-width: 80%;
        }
        input {
            width: 100%;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid #ccc;
            font-size: 14px;
            box-sizing: border-box;
            margin-bottom: 10px;
            background: var(--tg-theme-bg-color, #fff);
            color: var(--tg-theme-text-color, #222);
        }
        button.primary {
            width: 100%;
            padding: 12px;
            border-radius: 10px;
            background-color: var(--tg-theme-button-color, #2481cc);
            color: var(--tg-theme-button-text-color, #fff);
            border: none;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            margin-bottom: 16px;
        }
        button.danger {
            background-color: #ff3b30;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            flex-shrink: 0;
        }
        #loading {
            text-align: center;
            color: #888;
            margin-top: 20px;
        }
    </style>
</head>
<body>

    <h2>➕ Додати нове посилання</h2>
    <input type="text" id="urlInput" placeholder="Вставити посилання з NKON...">
    <button class="primary" onclick="addProduct()">Додати в моніторинг</button>

    <h2>📋 Відстежувані товари</h2>
    <div id="loading">Завантаження...</div>
    <div id="productList"></div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        async function loadProducts() {
            const listDiv = document.getElementById('productList');
            const loadingDiv = document.getElementById('loading');
            try {
                const response = await fetch('/api/products');
                const data = await response.json();
                loadingDiv.style.display = 'none';
                listDiv.innerHTML = '';

                if (!response.ok) {
                    listDiv.innerHTML = `<p style="color: #ff3b30; text-align: center;">Помилка сервера: ${data.error || response.status}</p>`;
                    return;
                }

                if (!data.products || data.products.length === 0) {
                    listDiv.innerHTML = '<p style="color: #888; text-align: center;">Список порожній</p>';
                    return;
                }

                data.products.forEach(url => {
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.innerHTML = `
                        <a href="${url}" target="_blank" class="card-link">${url}</a>
                        <button class="danger" onclick="deleteProduct('${url}')">Видалити</button>
                    `;
                    listDiv.appendChild(card);
                });
            } catch (err) {
                loadingDiv.style.display = 'block';
                loadingDiv.innerText = 'Помилка мережі: ' + err.message;
            }
        }

        async function addProduct() {
            const input = document.getElementById('urlInput');
            const url = input.value.trim();
            if (!url) {
                tg.showAlert('Будь ласка, введіть посилання!');
                return;
            }

            try {
                const res = await fetch('/api/products', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url })
                });
                const data = await res.json();
                if (data.success) {
                    input.value = '';
                    loadProducts();
                    tg.showAlert('Товар додано!');
                } else {
                    tg.showAlert('Помилка: ' + (data.error || 'Не вдалося додати'));
                }
            } catch (e) {
                tg.showAlert('Помилка з\'єднання');
            }
        }

        async function deleteProduct(url) {
            tg.showConfirm('Видалити це посилання з моніторингу?', async (confirmed) => {
                if (confirmed) {
                    try {
                        const res = await fetch('/api/products', {
                            method: 'DELETE',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ url })
                        });
                        const data = await res.json();
                        if (data.success) {
                            loadProducts();
                        } else {
                            tg.showAlert('Помилка видалення');
                        }
                    } catch (e) {
                        tg.showAlert('Помилка з\'єднання');
                    }
                }
            });
        }

        loadProducts();
    </script>
</body>
</html>
"""

def get_products_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url, headers=HEADERS)
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
    response = requests.put(url, headers=HEADERS, json=payload)
    return response.status_code in [200, 201]

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
