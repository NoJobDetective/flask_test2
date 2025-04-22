from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
from bs4 import BeautifulSoup
import json
import os
import base64
import re  # 改行コード統一用
import markdown  # マークダウン変換用
from datetime import datetime, timezone, timedelta  # 登録日の自動入力用
from urllib.parse import urlparse  # URL解析用

# JSTタイムゾーンの設定
JST = timezone(timedelta(hours=9))

app = Flask(__name__)
app.secret_key = "mysecretkey"  # 適宜変更してください

# projects.json の絶対パス
PROJECTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects.json")


def get_metadata(url):
    try:
        response = requests.get(url)
        if response.status_code == 403:
            parsed = urlparse(url)
            domain = parsed.netloc
            if domain.startswith("www."):
                domain = domain[4:]
            return {
                "error403": True,
                "title": domain,
                "description": "説明が見つかりませんでした",
                "image": None,
                "url": url
            }
        response.raise_for_status()
    except Exception:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        return {
            "error403": True,
            "title": domain,
            "description": "説明が見つかりませんでした",
            "image": None,
            "url": url
        }

    soup = BeautifulSoup(response.text, 'html.parser')
    # title
    title_tag = soup.find('meta', property='og:title')
    if title_tag and title_tag.get('content'):
        title = title_tag.get('content')
    else:
        title_tag = soup.find('title')
        title = title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした"
    # description
    desc_tag = soup.find('meta', property='og:description')
    if desc_tag and desc_tag.get('content'):
        description = desc_tag.get('content')
    else:
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        description = desc_tag.get('content') if desc_tag else "説明が見つかりませんでした"
    # image
    image_tag = soup.find('meta', property='og:image')
    if image_tag and image_tag.get('content'):
        image_url = image_tag.get('content')
        try:
            img_response = requests.get(image_url)
            img_response.raise_for_status()
            mime_type = img_response.headers.get("Content-Type", "image/jpeg")
            image_data = base64.b64encode(img_response.content).decode('utf-8')
            image_data = f"data:{mime_type};base64,{image_data}"
        except Exception:
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
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_all_projects(projects):
    try:
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"保存エラー: {e}")


def render_stars(rating):
    try:
        r = float(rating)
    except:
        r = 0.0
    full = int(r)
    empty = 10 - full
    return "★" * full + "☆" * empty


app.jinja_env.filters['render_stars'] = render_stars


def markdown_filter(text):
    return markdown.markdown(text, extensions=['nl2br'])


app.jinja_env.filters['markdown'] = markdown_filter


@app.route("/", methods=["GET", "POST"])
def index():
    # クエリパラメータで絞り込みタグを取得
    tag_filter = request.args.get("tag")

    if request.method == "POST":
        url_input = request.form.get("url", "").strip()
        comment = request.form.get("comment", "")
        comment = re.sub(r'\n+', '\n', comment)
        rating_str = request.form.get("rating", "5")
        try:
            rating = float(rating_str)
        except:
            rating = 5.0
        tags_str = request.form.get("tags", "")
        tags = [tag.strip() for tag in tags_str.split(",") if tag.strip()]
        if len(tags) > 5:
            tags = tags[:5]

        # URL未入力時はメタデータ取得をスキップ
        if url_input:
            metadata = get_metadata(url_input)
        else:
            metadata = {
                "error403": False,
                "title": "",
                "description": "",
                "image": None,
                "url": ""
            }

        # プロジェクト保存
        projects = load_projects()
        new_id = max([p.get("id", 0) for p in projects] or [0]) + 1
        project = {
            "id": new_id,
            "url": url_input,
            "comment": comment,
            "title": metadata.get("title", ""),
            "image": metadata.get("image"),
            "error403": metadata.get("error403", False),
            "rating": rating,
            "likes": 0,
            "tags": tags,
            "登録日": datetime.now(JST).strftime("%Y-%m-%d")
        }
        projects.append(project)
        save_all_projects(projects)
        return redirect(url_for("index"))

    # GET: 全プロジェクトを読み込み、評価順ソート
    projects = sorted(load_projects(), key=lambda p: float(p.get('rating', 0)), reverse=True)
    # タグフィルタ適用
    if tag_filter:
        projects = [p for p in projects if tag_filter in p.get("tags", [])]

    return render_template("index.html", projects=projects, tag_filter=tag_filter)


@app.route("/admin-login")
def admin_login():
    session["authenticated"] = True
    session["master"] = True
    return redirect(url_for("index"))


@app.route("/admin-logout")
def admin_logout():
    session.pop("master", None)
    return redirect(url_for("index"))


@app.route("/edit/<int:project_id>", methods=["GET", "POST"])
def edit(project_id):
    projects = load_projects()
    project = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return "プロジェクトが見つかりません", 404
    if request.method == "POST":
        new_url = request.form.get("url", "").strip()
        new_comment = request.form.get("comment", "")
        new_comment = re.sub(r'\n+', '\n', new_comment)
        new_rating_str = request.form.get("rating", "5")
        try:
            new_rating = float(new_rating_str)
        except:
            new_rating = 5.0
        new_tags_str = request.form.get("tags", "")
        new_tags = [tag.strip() for tag in new_tags_str.split(",") if tag.strip()]
        if len(new_tags) > 5:
            new_tags = new_tags[:5]

        if new_url:
            metadata = get_metadata(new_url)
        else:
            metadata = {"error403": False, "title": "", "description": "", "image": None, "url": ""}

        project["url"] = new_url
        project["title"] = metadata.get("title", "")
        project["image"] = metadata.get("image")
        project["error403"] = metadata.get("error403", False)
        project["comment"] = new_comment
        project["rating"] = new_rating
        project["tags"] = new_tags
        save_all_projects(projects)
        return redirect(url_for("index"))

    return render_template("edit.html", project=project, project_id=project_id)


@app.route("/like/<int:project_id>", methods=["POST"])
def like(project_id):
    projects = load_projects()
    project = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return jsonify({"error": "プロジェクトが見つかりません"}), 404
    project["likes"] = project.get("likes", 0) + 1
    save_all_projects(projects)
    return jsonify({"likes": project["likes"]})


@app.route("/unlike/<int:project_id>", methods=["POST"])
def unlike(project_id):
    projects = load_projects()
    project = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return jsonify({"error": "プロジェクトが見つかりません"}), 404
    if project.get("likes", 0) > 0:
        project["likes"] -= 1
    save_all_projects(projects)
    return jsonify({"likes": project["likes"]})


@app.route("/delete/<int:project_id>")
def delete(project_id):
    if not session.get("master"):
        return "削除権限がありません", 403
    projects = load_projects()
    new_projects = [p for p in projects if p.get("id") != project_id]
    save_all_projects(new_projects)
    return redirect(url_for("index"))


# ── likes が 0 のプロジェクトを削除するクリーンアップエンドポイント ──
@app.route("/d")
def cleanup_projects():
    # プロジェクトファイルの存在確認
    if not os.path.exists(PROJECTS_FILE):
        return f'ERROR: {PROJECTS_FILE} が見つかりません', 500

    # バックアップ作成
    BACKUP_FILE = PROJECTS_FILE + '.bak'
    os.replace(PROJECTS_FILE, BACKUP_FILE)

    # バックアップファイルから読み込み
    try:
        with open(BACKUP_FILE, 'r', encoding='utf-8') as f:
            projects = json.load(f)
    except Exception as e:
        return f'バックアップ読み込みエラー: {e}', 500

    # likes が 0 のものを除去
    cleaned = [p for p in projects if p.get("likes", 0) != 0]

    # クリーンなデータを保存
    save_all_projects(cleaned)

    # インデックスページへリダイレクト
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
