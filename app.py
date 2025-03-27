from flask import Flask, request, render_template, url_for
import requests
from bs4 import BeautifulSoup
import json
import os

app = Flask(__name__)

PROJECTS_FILE = "projects.json"

def get_metadata(url):
    """
    指定したURLからウェブページのメタデータ（タイトル、説明、画像）を取得する関数です。
    ※403エラーの場合は error403 フラグをTrueとして返します。
    """
    try:
        response = requests.get(url)
        if response.status_code == 403:
            return {"error403": True, "url": url}
        response.raise_for_status()
    except Exception as e:
        print(f"URLの取得中にエラーが発生しました: {e}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 【タイトルの取得】
    title_tag = soup.find('meta', property='og:title')
    if title_tag and title_tag.get('content'):
        title = title_tag.get('content')
    else:
        title_tag = soup.find('title')
        title = title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした"
    
    # 【説明文の取得】（取得例）
    desc_tag = soup.find('meta', property='og:description')
    if desc_tag and desc_tag.get('content'):
        description = desc_tag.get('content')
    else:
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        description = desc_tag.get('content') if desc_tag else "説明が見つかりませんでした"
    
    # 【サムネイル画像の取得】
    image_tag = soup.find('meta', property='og:image')
    if image_tag and image_tag.get('content'):
        image = image_tag.get('content')
    else:
        image = None

    return {"error403": False, "title": title, "description": description, "image": image, "url": url}

def save_project(project):
    """
    project（辞書型：URL, 感想, タイトル, サムネイル画像）をprojects.jsonに追記します。
    """
    if os.path.exists(PROJECTS_FILE):
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            try:
                projects = json.load(f)
            except json.JSONDecodeError:
                projects = []
    else:
        projects = []
    projects.append(project)
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url")
        comment = request.form.get("comment")
        metadata = get_metadata(url)
        if metadata:
            # プロジェクトとしてURLと感想、タイトル、サムネイル画像（403エラーでなければ）を登録
            project = {
                "url": url,
                "comment": comment,
                "title": metadata.get("title", ""),
                "image": metadata.get("image") if not metadata.get("error403", False) else None
            }
            save_project(project)
            return render_template("result.html", metadata=metadata, comment=comment)
        else:
            error_message = "指定されたURLからメタデータを取得できませんでした。"
            return render_template("index.html", error=error_message)
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
