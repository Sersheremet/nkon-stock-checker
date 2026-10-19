import os
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# Налаштування з перемінних середовища (Environment Variables)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # наприклад: "your_username/your_repo_name"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Простий HTML-інтерфейс для Telegram WebApp (у стилі Telegram)
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
            background-color: var(--tg-theme-bg-color, #ffffff);
            color: var(--tg-theme-text-color, #000000);
            margin: 0;
            padding: 16px;
        }
        h2 { font-size: 20px; margin-bottom: 12px; }
        .card {
            background-color: var(--tg-theme-secondary-bg-color, #f1f1f1);
            padding: 12px;
            border-radius: 10px;
            margin-bottom: 10px;
            word-break: break-all;
            font-size: 14px;
        }
        button {
            background-color: var(--tg-theme-button-color, #2481cc);
            color: var(--tg-theme-button-text-color, #ffffff);
            border: none;
            padding: 10px 16px;
            border-radius: 8px;
            font-size: 16px;
            width: 100%;
            cursor: pointer;
            margin-top: 10px;
        }
        input {
            width: 100%;
            padding: 10px;
            border-radius: 8px;
            border: 1px solid #ccc;
            margin-bottom: 10px;
            box-sizing: border-box;
        }
    </style>
</head>
<body>
    <h2>📦 Керування моніторингом NKON</h2>
    
    <div>
        <h3>Додати новий товар:</h3>
        <input type="text" id="productUrl" placeholder="Вставте посилання на товар...">
        <button onclick="addProduct()">Додати в список</button>
    </div>

    <h3 style="margin-top: 24px;">Активні товари:</h3>
    <div id="productList">Завантаження...</div>

    <button style="background-color: #28a745; margin-top: 20px;" onclick="triggerCheck()">🔄 Запустити перевірку зараз</button>

    <script>
        let tg = window.Telegram.WebApp;
        tg.expand();

        function loadProducts() {
            fetch('/get_products')
                .then(res => res.json())
                .then(data => {
                    let list = document.getElementById('productList');
                    list.innerHTML = '';
                    if(data.length === 0) {
                        list.innerHTML = '<div class="card">Список порожній</div>';
                        return;
                    }
                    data.forEach(url => {
                        list.innerHTML += `<div class="card">${url}</div>`;
                    });
                });
        }

        function addProduct() {
            let url = document.getElementById('productUrl').value;
            if(!url) return alert('Введіть посилання!');
            
            fetch('/add_product', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url: url})
            }).then(res => res.json()).then(data => {
                if(data.success) {
                    document.getElementById('productUrl').value = '';
                    loadProducts();
                    tg.showAlert('Товар успішно додано!');
                } else {
                    alert('Помилка: ' + data.error);
                }
            });
        }

        function triggerCheck() {
            fetch('/trigger_check', {method: 'POST'})
                .then(res => res.json())
                .then(data => {
                    if(data.success) {
                        tg.showAlert('Перевірку запущено на GitHub!');
                    } else {
                        alert('Помилка запуску: ' + data.error);
                    }
                });
        }

        loadProducts();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/get_products', methods=['GET'])
def get_products():
    # Отримуємо products.txt з GitHub
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt", headers=headers)
    if r.status_code == 200:
        import base64
        content = base64.b64decode(r.json()['content']).decode('utf-8')
        urls = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
        return jsonify(urls)
    return jsonify([])

@app.route('/add_product', methods=['POST'])
def add_product():
    data = request.json
    new_url = data.get('url')
    if not new_url:
        return jsonify({"success": False, "error": "No URL provided"})

    # Читаємо поточний файл з GitHub (разом із SHA, який потрібен для оновлення)
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    r = requests.get(api_url, headers=headers)
    
    if r.status_code != 200:
        return jsonify({"success": False, "error": "Could not read products.txt from GitHub"})
    
    file_info = r.json()
    import base64
    content = base64.b64decode(file_info['content']).decode('utf-8')
    sha = file_info['sha']

    # Додаємо новий рядок
    updated_content = content.strip() + "\n" + new_url + "\n"
    encoded_content = base64.b64encode(updated_content.encode('utf-8')).decode('utf-8')

    # Робимо коміт через GitHub API
    payload = {
        "message": f"Add product via Telegram WebApp: {new_url}",
        "content": encoded_content,
        "sha": sha
    }
    put_r = requests.put(api_url, headers=headers, json=payload)
    
    if put_r.status_code in [200, 201]:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": put_r.text})

@app.route('/trigger_check', methods=['POST'])
def trigger_check():
    # Запускаємо GitHub Actions workflow вручну через API
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    # Припускаємо, що файл workflow називається check.yml
    workflow_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/check.yml/dispatches"
    payload = {"ref": "main"}
    r = requests.post(workflow_url, headers=headers, json=payload)
    
    if r.status_code == 204:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": r.text})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
