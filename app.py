import os
import base64
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # Наприклад: "Sersheremet/nkon-stock-checker"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}

def get_products_from_github():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/products.txt"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        file_data = response.json()
        content = base64.b64decode(file_data["content"]).decode("utf-8")
        sha = file_data["sha"]
        # Розбиваємо на рядки, прибираємо порожні
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
    return render_template("index.html")

@app.route("/api/products", methods=["GET"])
def list_products():
    products, _ = get_products_from_github()
    return jsonify({"products": products})

@app.route("/api/products", methods=["POST"])
def add_product():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"success": False, "error": "Порожня URL-адреса"}), 400

    products, sha = get_products_from_github()
    if url in products:
        return jsonify({"success": False, "error": "Таке посилання вже є в списку!"}), 400

    products.append(url)
    success = save_products_to_github(products, sha, f"Add product via WebApp: {url}")
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Помилка збереження на GitHub"}), 500

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
        return jsonify({"success": False, "error": "Помилка оновлення на GitHub"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
