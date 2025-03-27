from flask import Flask, request, render_template, redirect, url_for
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
    ※403エラーの場合は error403 フラグを True として返します。
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
            mime_type = img_response.headers.get("Content-Type", "image/jpeg")
            image_data = base64.b64encode(img_response.content).decode('utf-8')
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

def load_projects():
    """ projects.json から既存のプロジェクトを読み込み """
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"プロジェクトの読み込みに失敗しました: {e}")
            return []
    return []

def save_all_projects(projects):
    """ projects.json に全プロジェクトを保存 """
    try:
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        print("全プロジェクトが保存されました。現在の件数:", len(projects))
    except IOError as e:
        print(f"{PROJECTS_FILE}への書き込みに失敗しました: {e}")

def render_stars(rating):
    """
    数値評価（0～5, 0.5刻み）を星マークに変換します。
    例：3.5 → ★★★⯨☆☆
    ※全星は★、半星は⯨（環境により表示が異なる場合があります）、残りは☆。
    """
    try:
        r = float(rating)
    except:
        r = 0.0
    full = int(r)
    half = 1 if (r - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + ("⯨" if half else "") + "☆" * empty

# カスタムフィルターとして登録
app.jinja_env.filters['render_stars'] = render_stars

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url_input = request.form.get("url")
        comment = request.form.get("comment")
        rating_str = request.form.get("rating", "3")
        try:
            rating = float(rating_str)
        except:
            rating = 3.0
        metadata = get_metadata(url_input)
        if metadata:
            project = {
                "url": url_input,
                "comment": comment,
                "title": metadata.get("title", ""),
                # 403エラーの場合は image は None とし、error403 フラグを True とする
                "image": metadata.get("image") if not metadata.get("error403", False) else None,
                "error403": metadata.get("error403", False),
                "rating": rating
            }
            projects = load_projects()
            projects.append(project)
            save_all_projects(projects)
        else:
            return render_template("index.html", error="指定されたURLからメタデータを取得できませんでした。", projects=load_projects())
    return render_template("index.html", projects=load_projects())

@app.route("/edit/<int:project_index>", methods=["GET", "POST"])
def edit(project_index):
    projects = load_projects()
    if project_index < 0 or project_index >= len(projects):
        return "プロジェクトが見つかりません", 404
    project = projects[project_index]
    if request.method == "POST":
        new_url = request.form.get("url")
        new_comment = request.form.get("comment")
        new_rating_str = request.form.get("rating", "3")
        try:
            new_rating = float(new_rating_str)
        except:
            new_rating = 3.0
        metadata = get_metadata(new_url)
        if metadata:
            project["url"] = new_url
            project["title"] = metadata.get("title", "")
            project["image"] = metadata.get("image") if not metadata.get("error403", False) else None
            project["error403"] = metadata.get("error403", False)
            project["comment"] = new_comment
            project["rating"] = new_rating
            save_all_projects(projects)
            return redirect(url_for("index"))
        else:
            error_message = "指定されたURLからメタデータを取得できませんでした。"
            return render_template("edit.html", project=project, index=project_index, error=error_message)
    return render_template("edit.html", project=project, index=project_index)

if __name__ == "__main__":
    app.run(debug=True)
