from flask import Flask, request, render_template, url_for
import requests
from bs4 import BeautifulSoup
import json
import os
import base64

app = Flask(__name__)

# app.py のあるディレクトリを基準に projects.json の絶対パスを指定
PROJECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects.json")

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
    
    # タイトルの取得
    title_tag = soup.find('meta', property='og:title')
    if title_tag and title_tag.get('content'):
        title = title_tag.get('content')
    else:
        title_tag = soup.find('title')
        title = title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした"
    
    # 説明文の取得（例）
    desc_tag = soup.find('meta', property='og:description')
    if desc_tag and desc_tag.get('content'):
        description = desc_tag.get('content')
    else:
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        description = desc_tag.get('content') if desc_tag else "説明が見つかりませんでした"
    
    # サムネイル画像の取得（画像URLから画像をダウンロードしてBase64エンコード）
    image_tag = soup.find('meta', property='og:image')
    if image_tag and image_tag.get('content'):
        image_url = image_tag.get('content')
        try:
            img_response = requests.get(image_url)
            img_response.raise_for_status()
            # MIMEタイプをヘッダから取得（取得できなければimage/jpegとする）
            mime_type = img_response.headers.get("Content-Type", "image/jpeg")
            image_data = base64.b64encode(img_response.content).decode('utf-8')
            # Data URIスキーム形式に変換
            image_data = f"data:{mime_type};base64,{image_data}"
        except Exception as e:
            print(f"画像のダウンロード中にエラーが発生しました: {e}")
            image_data = None
    else:
        image_data = None

    return {
        "error403": False,
        "title": title,
        "description": description,
        "image": image_data,
        "url": url
    }

def save_project(project):
    """
    project（辞書型：URL, 感想, タイトル, サムネイル画像）をprojects.jsonに追記します。
    """
    # 既存のデータを読み込む
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                projects = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"既存のJSON読み込みに失敗しました: {e}")
            projects = []
    else:
        projects = []
    
    projects.append(project)
    
    # データを書き込み
    try:
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        print("プロジェクトが保存されました。現在のプロジェクト数:", len(projects))
    except IOError as e:
        print(f"{PROJECTS_FILE}への書き込みに失敗しました: {e}")

def load_projects():
    """
    projects.jsonから既存のプロジェクトデータを読み込んで返します。
    """
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"プロジェクトの読み込みに失敗しました: {e}")
            return []
    return []

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
            print("保存対象プロジェクト:", project)
            save_project(project)
            return render_template("result.html", metadata=metadata, comment=comment)
        else:
            error_message = "指定されたURLからメタデータを取得できませんでした。"
            return render_template("index.html", error=error_message, projects=load_projects())
    # GETリクエスト時は既存のプロジェクトデータを読み込んで表示
    return render_template("index.html", projects=load_projects())

if __name__ == "__main__":
    app.run(debug=True)
