from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
from bs4 import BeautifulSoup
import json, os, base64, re
import markdown
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
# --- ★ Elasticsearch 追加 --------------------------
from elasticsearch import Elasticsearch, helpers
# ---------------------------------------------------

JST = timezone(timedelta(hours=9))

app = Flask(__name__)
app.secret_key = "mysecretkey"       # ←適宜変更

# JSON ファイル
DATA_DIR       = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE  = os.path.join(DATA_DIR, "projects.json")

# --- ★ Elasticsearch 接続設定 -----------------------
ES           = Elasticsearch("http://localhost:9200")
INDEX_NAME   = "projects"

def init_index() -> None:
    """マッピングが無ければ作成"""
    if not ES.indices.exists(index=INDEX_NAME):
        ES.indices.create(
            index   = INDEX_NAME,
            mappings= {
                "properties": {
                    "id":        {"type": "integer"},
                    "title":     {"type": "text"},
                    "comment":   {"type": "text"},
                    "url":       {"type": "keyword"},
                    "tags":      {"type": "keyword"},
                    "rating":    {"type": "float"},
                    "likes":     {"type": "integer"},
                    "user":      {"type": "keyword"},
                    "date":      {"type": "date"}          # YYYY-MM-DD
                }
            }
        )
# ---------------------------------------------------

def index_doc(doc: dict) -> None:
    """1レコードを Elasticsearch に投入"""
    ES.index(index=INDEX_NAME, id=doc["id"], document=doc)

def bulk_index_docs(docs: list[dict]) -> None:
    """まとめて投入（起動時の再構築等）"""
    actions = [{"_index": INDEX_NAME, "_id": d["id"], "_source": d} for d in docs]
    if actions:
        helpers.bulk(ES, actions)

# ---------------- メタデータ取得 --------------------
def get_metadata(url:str)->dict:
    try:
        resp = requests.get(url)
        if resp.status_code==403:
            domain = urlparse(url).netloc.lstrip("www.")
            return {"error403":True,"title":domain,"description":"説明が見つかりませんでした","image":None,"url":url}
        resp.raise_for_status()
    except Exception:
        domain = urlparse(url).netloc.lstrip("www.")
        return {"error403":True,"title":domain,"description":"説明が見つかりませんでした","image":None,"url":url}

    soup = BeautifulSoup(resp.text,'html.parser')
    title_tag = soup.find('meta',property='og:title') or soup.find('title')
    title = (title_tag.get('content') if title_tag and title_tag.has_attr('content')
             else title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした")
    desc_tag  = soup.find('meta',property='og:description') or soup.find('meta',attrs={'name':'description'})
    description = desc_tag.get('content') if desc_tag else "説明が見つかりませんでした"
    image_tag= soup.find('meta',property='og:image')
    if image_tag and image_tag.get('content'):
        try:
            img = requests.get(image_tag['content']); img.raise_for_status()
            mime = img.headers.get("Content-Type","image/jpeg")
            image_data = f"data:{mime};base64,"+base64.b64encode(img.content).decode()
        except Exception:
            image_data = None
    else:
        image_data=None
    return {"error403":False,"title":title,"description":description,"image":image_data,"url":url}

# ------------- JSON Utility ------------------------
def load_projects()->list:
    if os.path.exists(PROJECTS_FILE):
        try:
            with open(PROJECTS_FILE,"r",encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_all_projects(projects:list)->None:
    with open(PROJECTS_FILE,"w",encoding="utf-8") as f:
        json.dump(projects,f,ensure_ascii=False,indent=2)
    # ★ ES に同期
    ES.delete_by_query(index=INDEX_NAME, body={"query":{"match_all":{}}})
    bulk_index_docs(projects)

# ------------- Jinja フィルタ -----------------------
def render_stars(r)->str:
    try: r=float(r)
    except: r=0.0
    return "★"*int(r)+"☆"*(10-int(r))
app.jinja_env.filters['render_stars']=render_stars
app.jinja_env.filters['markdown']    = lambda text: markdown.markdown(text,extensions=['nl2br'])

# ===================================================
#                     ルート
# ===================================================
@app.route("/", methods=["GET","POST"])
def index():
    # ------- 検索パラメータ -------------
    q            = request.args.get("q","").strip()
    tag_filter   = request.args.get("tag")
    start_date   = request.args.get("start_date")   # YYYY-MM-DD
    end_date     = request.args.get("end_date")
    rating_min   = request.args.get("rating_min")
    rating_max   = request.args.get("rating_max")
    # -----------------------------------

    if request.method=="POST":
        url_input=request.form.get("url","").strip()
        comment  =re.sub(r'\n+','\n', request.form.get("comment",""))
        rating   =float(request.form.get("rating","5") or 5)
        tags     =[t.strip() for t in request.form.get("tags","").split(",") if t.strip()][:5]
        user     =session.get("username","guest")

        metadata = get_metadata(url_input) if url_input else {"title":"","image":None,"error403":False,"url":""}

        projects = load_projects()
        new_id   = max([p.get("id",0) for p in projects] or [0])+1
        project  = {
            "id":new_id,"url":url_input,"title":metadata["title"],"image":metadata["image"],
            "error403":metadata["error403"],"comment":comment,"rating":rating,"likes":0,
            "tags":tags,"user":user,"date":datetime.now(JST).strftime("%Y-%m-%d")
        }
        projects.append(project)
        save_all_projects(projects)     # ←ESへも同期
        index_doc(project)              # 1件だけ即時投入
        return redirect(url_for("index"))

    # ---------------- 検索クエリ生成 -----------------
    must   = []
    filter = []

    if q:
        must.append({"multi_match":{"query":q,"fields":["title","comment"]}})
    if tag_filter:
        filter.append({"term":{"tags.keyword":tag_filter}})
    if start_date or end_date:
        rng={}
        if start_date: rng["gte"]=start_date
        if end_date:   rng["lte"]=end_date
        filter.append({"range":{"date":rng}})
    if rating_min or rating_max:
        rng={}
        if rating_min: rng["gte"]=float(rating_min)
        if rating_max: rng["lte"]=float(rating_max)
        filter.append({"range":{"rating":rng}})

    es_query = {"bool":{}}
    if must:   es_query["bool"]["must"]=must
    if filter: es_query["bool"]["filter"]=filter
    if not (must or filter):            # 検索なし→全件
        es_query = {"match_all":{}}

    res = ES.search(index=INDEX_NAME, query=es_query, size=200, sort=[{"rating":"desc"}])
    projects = [hit["_source"] for hit in res["hits"]["hits"]]

    return render_template("index.html", projects=projects, tag_filter=tag_filter,
                           q=q, start_date=start_date, end_date=end_date,
                           rating_min=rating_min, rating_max=rating_max)

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
    init_index()
    # 起動時、JSON と ES がずれていれば再投入
    if not ES.count(index=INDEX_NAME)['count']:
        bulk_index_docs(load_projects())
    app.run(debug=True)
