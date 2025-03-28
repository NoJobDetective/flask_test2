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
    数値評価（0～5, 1刻み）を星マークに変換します。
    例：3 → ★★★☆☆
    """
    try:
        r = float(rating)
    except:
        r = 0.0
    full = int(r)
    empty = 5 - full
    return "★" * full + "☆" * empty

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
        # タグ入力（カンマ区切り）の取得と整形（最大5個）
        tags_str = request.form.get("tags", "")
        tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]
        if len(tags) > 5:
            tags = tags[:5]
        metadata = get_metadata(url_input)
        if metadata:
            projects = load_projects()
            # 新規プロジェクトのIDを決定（既存IDの最大値+1、なければ1）
            new_id = max((p.get("id", 0) for p in projects), default=0) + 1
            project = {
                "id": new_id,
                "url": url_input,
                "comment": comment,
                "title": metadata.get("title", ""),
                "image": metadata.get("image") if not metadata.get("error403", False) else None,
                "error403": metadata.get("error403", False),
                "rating": rating,
                "likes": 0,
                "tags": tags
            }
            projects.append(project)
            save_all_projects(projects)
        else:
            sorted_projects = sorted(load_projects(), key=lambda p: float(p.get('rating', 0)), reverse=True)
            return render_template("index.html", error="指定されたURLからメタデータを取得できませんでした。", projects=sorted_projects)
    sorted_projects = sorted(load_projects(), key=lambda p: float(p.get('rating', 0)), reverse=True)
    return render_template("index.html", projects=sorted_projects)

@app.route("/edit/<int:project_id>", methods=["GET", "POST"])
def edit(project_id):
    projects = load_projects()
    project = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return "プロジェクトが見つかりません", 404
    if request.method == "POST":
        new_url = request.form.get("url")
        new_comment = request.form.get("comment")
        new_rating_str = request.form.get("rating", "3")
        try:
            new_rating = float(new_rating_str)
        except:
            new_rating = 3.0
        # タグ入力の更新
        new_tags_str = request.form.get("tags", "")
        new_tags = [tag.strip() for tag in new_tags_str.split(",") if tag.strip()]
        if len(new_tags) > 5:
            new_tags = new_tags[:5]
        metadata = get_metadata(new_url)
        if metadata:
            project["url"] = new_url
            project["title"] = metadata.get("title", "")
            project["image"] = metadata.get("image") if not metadata.get("error403", False) else None
            project["error403"] = metadata.get("error403", False)
            project["comment"] = new_comment
            project["rating"] = new_rating
            project["tags"] = new_tags
            save_all_projects(projects)
            return redirect(url_for("index"))
        else:
            error_message = "指定されたURLからメタデータを取得できませんでした。"
            return render_template("edit.html", project=project, project_id=project_id, error=error_message)
    return render_template("edit.html", project=project, project_id=project_id)

@app.route("/like/<int:project_id>")
def like(project_id):
    projects = load_projects()
    project = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return "プロジェクトが見つかりません", 404
    project["likes"] = project.get("likes", 0) + 1
    save_all_projects(projects)
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
