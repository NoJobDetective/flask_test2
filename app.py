# === app.py (full) ===
from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
from bs4 import BeautifulSoup
import json
import os
import base64
import re                      # 改行コード統一
import markdown                # マークダウン変換
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

# --- Elasticsearch -------------------------------
from elasticsearch import Elasticsearch, helpers
# -------------------------------------------------

# JST タイムゾーン
JST = timezone(timedelta(hours=9))

app = Flask(__name__)
app.secret_key = "mysecretkey"          # 適宜変更

# ファイルパス
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE  = os.path.join(BASE_DIR, "projects.json")

# ===== Elasticsearch 設定 =====
ES_URL      = "http://localhost:9200"
ES          = Elasticsearch(ES_URL)
INDEX_NAME  = "projects"


def init_index():
    """インデックスが無ければ作成"""
    if not ES.indices.exists(index=INDEX_NAME):
        ES.indices.create(
            index=INDEX_NAME,
            mappings={
                "properties": {
                    "id":     {"type": "integer"},
                    "title":  {"type": "text"},
                    "comment":{"type": "text"},
                    "url":    {"type": "keyword"},
                    "tags":   {"type": "keyword"},
                    "rating": {"type": "float"},
                    "likes":  {"type": "integer"},
                    "user":   {"type": "keyword"},
                    "date":   {"type": "date"}
                }
            }
        )


def index_doc(doc: dict):
    ES.index(index=INDEX_NAME, id=doc["id"], document=doc)


def bulk_index_docs(docs: list[dict]):
    actions = [{"_index": INDEX_NAME, "_id": d["id"], "_source": d} for d in docs]
    if actions:
        helpers.bulk(ES, actions)

# ============= ユーティリティ =====================

def get_metadata(url: str):
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 403:
            raise Exception("403")
        resp.raise_for_status()
    except Exception:
        domain = urlparse(url).netloc.lstrip("www.")
        return {"error403": True, "title": domain, "description": "説明が見つかりませんでした", "image": None, "url": url}

    soup = BeautifulSoup(resp.text, "html.parser")
    # title
    title_tag = soup.find("meta", property="og:title") or soup.find("title")
    title = title_tag.get("content") if title_tag and title_tag.has_attr("content") else (title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした")
    # description
    desc_tag = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    description = desc_tag.get("content") if desc_tag else "説明が見つかりませんでした"
    # image
    image_tag = soup.find("meta", property="og:image")
    if image_tag and image_tag.get("content"):
        try:
            img = requests.get(image_tag["content"], timeout=10)
            img.raise_for_status()
            mime = img.headers.get("Content-Type", "image/jpeg")
            image_data = f"data:{mime};base64," + base64.b64encode(img.content).decode()
        except Exception:
            image_data = None
    else:
        image_data = None

    return {"error403": False, "title": title, "description": description, "image": image_data, "url": url}


def load_projects():
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_all_projects(projects):
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
    # ES を最新化
    ES.delete_by_query(index=INDEX_NAME, body={"query": {"match_all": {}}})
    bulk_index_docs(projects)


# ------------------ Jinja フィルタ -----------------

def render_stars(r):
    try:
        r = float(r)
    except:
        r = 0.0
    return "★" * int(r) + "☆" * (10 - int(r))

app.jinja_env.filters["render_stars"] = render_stars
app.jinja_env.filters["markdown"] = lambda t: markdown.markdown(t, extensions=["nl2br"])

# ==================================================
#                     ルート
# ==================================================

@app.route("/", methods=["GET", "POST"])
def index():
    # ---- 検索パラメータ ----
    q           = request.args.get("q", "").strip()
    tag_filter  = request.args.get("tag")
    start_date  = request.args.get("start_date")
    end_date    = request.args.get("end_date")
    rating_min  = request.args.get("rating_min")
    rating_max  = request.args.get("rating_max")

    if request.method == "POST":
        url_input = request.form.get("url", "").strip()
        comment   = re.sub(r"\n+", "\n", request.form.get("comment", ""))
        rating    = float(request.form.get("rating", "5") or 5)
        tags      = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()][:5]
        user      = session.get("username", "guest")

        metadata  = get_metadata(url_input) if url_input else {"title": "", "image": None, "error403": False, "url": ""}

        projects  = load_projects()
        new_id    = max([p.get("id", 0) for p in projects] or [0]) + 1
        project   = {
            "id": new_id,
            "url": url_input,
            "title": metadata["title"],
            "image": metadata["image"],
            "error403": metadata["error403"],
            "comment": comment,
            "rating": rating,
            "likes": 0,
            "tags": tags,
            "user": user,
            "date": datetime.now(JST).strftime("%Y-%m-%d")
        }
        projects.append(project)
        save_all_projects(projects)
        index_doc(project)
        return redirect(url_for("index"))

    # ----------------- ES クエリ生成 ----------------
    must   = []
    filter = []

    if q:
        must.append({"multi_match": {"query": q, "fields": ["title", "comment"]}})
    if tag_filter:
        filter.append({"term": {"tags": tag_filter}})
    if start_date or end_date:
        rng = {}
        if start_date:
            rng["gte"] = start_date
        if end_date:
            rng["lte"] = end_date
        filter.append({"range": {"date": rng}})
    if rating_min or rating_max:
        rng = {}
        if rating_min:
            rng["gte"] = float(rating_min)
        if rating_max:
            rng["lte"] = float(rating_max)
        filter.append({"range": {"rating": rng}})

    es_query = {"match_all": {}} if not (must or filter) else {"bool": {}}
    if must:
        es_query["bool"]["must"] = must
    if filter:
        es_query["bool"]["filter"] = filter

    res = ES.search(index=INDEX_NAME, query=es_query, size=500, sort=[{"rating": "desc"}])
    projects = [h["_source"] for h in res["hits"]["hits"]]

    return render_template("index.html", projects=projects, q=q, tag_filter=tag_filter,
                           start_date=start_date, end_date=end_date, rating_min=rating_min, rating_max=rating_max)


# ----------------- 管理ルート ----------------------

@app.route("/admin-login")
def admin_login():
    session["master"] = True
    return redirect(url_for("index"))


@app.route("/admin-logout")
def admin_logout():
    session.pop("master", None)
    return redirect(url_for("index"))

# ----------------- like / unlike ------------------


def update_likes_in_es(project_id: int, likes: int):
    ES.update(index=INDEX_NAME, id=project_id, doc={"likes": likes})


@app.route("/like/<int:project_id>", methods=["POST"])
def like(project_id):
    projects = load_projects()
    project  = next((p for p in projects if p["id"] == project_id), None)
    if not project:
        return jsonify({"error": "not found"}), 404
    project["likes"] = project.get("likes", 0) + 1
    save_all_projects(projects)
    update_likes_in_es(project_id, project["likes"])
    return jsonify({"likes": project["likes"]})


@app.route("/unlike/<int:project_id>", methods=["POST"])
def unlike(project_id):
    projects = load_projects()
    project  = next((p for p in projects if p["id"] == project_id), None)
    if not project:
        return jsonify({"error": "not found"}), 404
    if project.get("likes", 0) > 0:
        project["likes"] -= 1
    save_all_projects(projects)
    update_likes_in_es(project_id, project["likes"])
    return jsonify({"likes": project["likes"]})

# ----------------- 編集 ---------------------------

@app.route("/edit/<int:project_id>", methods=["GET", "POST"])
def edit(project_id):
    projects = load_projects()
    project  = next((p for p in projects if p["id"] == project_id), None)
    if not project:
        return "Not found", 404
    if request.method == "POST":
        new_url     = request.form.get("url", "").strip()
        new_comment = re.sub(r"\n+", "\n", request.form.get("comment", ""))
        new_rating  = float(request.form.get("rating", "5") or 5)
        new_tags    = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()][:5]

        metadata = get_metadata(new_url) if new_url else {"title": "", "image": None, "error403": False, "url": ""}

        project.update({
            "url": new_url,
            "title": metadata["title"],
            "image": metadata["image"],
            "error403": metadata["error403"],
            "comment": new_comment,
            "rating": new_rating,
            "tags": new_tags
        })
        save_all_projects(projects)
        index_doc(project)
        return redirect(url_for("index"))

    return render_template("edit.html", project=project)

# ----------------- 削除 ---------------------------

@app.route("/delete/<int:project_id>")
def delete(project_id):
    if not session.get("master"):
        return "Forbidden", 403
    projects = load_projects()
    new_projects = [p for p in projects if p["id"] != project_id]
    save_all_projects(new_projects)
    if ES.exists(index=INDEX_NAME, id=project_id):
        ES.delete(index=INDEX_NAME, id=project_id)
    return redirect(url_for("index"))

# ----------------- likes==0 cleanup ---------------

@app.route("/d")
def cleanup():
    if not os.path.exists(PROJECTS_FILE):
        return "PROJECTS_FILE missing", 500
    with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
        projects = json.load(f)
    cleaned = [p for p in projects if p.get("likes", 0)]
    save_all_projects(cleaned)
    return redirect(url_for("index"))

# ==================================================

if __name__ == "__main__":
    init_index()
    # ES が空なら JSON から流し込む
    if ES.count(index=INDEX_NAME)["count"] == 0:
        bulk_index_docs(load_projects())
    app.run(debug=True)
