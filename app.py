from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
from bs4 import BeautifulSoup
import json
import os
import base64
import re
import markdown
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import fcntl
from contextlib import contextmanager
import tempfile
from typing import List, Optional, Dict, Any
from pygments.formatters import HtmlFormatter

# ── Elasticsearch 依存 ────────────────────────────────
from elasticsearch import Elasticsearch

ES_INDEX = "project"  # インデックス名
es = Elasticsearch(hosts="http://localhost:9200",http_auth=("elastic", "l8cci2G2qepihVtGhD3o"))  # 適宜変更してください

# ── 基本設定 ───────────────────────────────────────
JST = timezone(timedelta(hours=9))
app = Flask(__name__)
app.secret_key = "mysecretkey"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE = os.path.join(BASE_DIR, "projects.json")
BACKUP_FILE = PROJECTS_FILE + ".bak"
LOCK_FILE = PROJECTS_FILE + ".lock"

# ────────────────────────────────────────────────
#  ファイルロック
# ────────────────────────────────────────────────
@contextmanager
def file_lock():
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "a") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

# ────────────────────────────────────────────────
#  JSON I/O
# ────────────────────────────────────────────────
def _atomic_write_json(path: str, data: List[dict]) -> None:
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="projects_", suffix=".json", dir=os.path.dirname(path))
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
        json.dump(data, tmp_f, ensure_ascii=False, indent=2)
        tmp_f.flush(); os.fsync(tmp_f.fileno())
    os.replace(tmp_path, path)

def _restore_from_backup() -> Optional[List[dict]]:
    if not os.path.exists(BACKUP_FILE):
        return None
    try:
        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data:
            _atomic_write_json(PROJECTS_FILE, data)
            return data
    except Exception:
        pass
    return None

def load_projects() -> List[dict]:
    if not os.path.exists(PROJECTS_FILE):
        return []
    with file_lock():
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            restored = _restore_from_backup(); return restored or []
        if not data:
            restored = _restore_from_backup(); return restored or []
        return data

def save_all_projects(projects: List[dict], *, allow_empty: bool=False) -> None:
    with file_lock():
        if os.path.exists(PROJECTS_FILE):
            try:
                os.replace(PROJECTS_FILE, BACKUP_FILE)
            except Exception as e:
                print(f"バックアップ作成失敗: {e}")
        try:
            _atomic_write_json(PROJECTS_FILE, projects)
        except IOError as e:
            print(f"保存エラー: {e}"); _restore_from_backup(); return
        if not projects and not allow_empty:
            print("空リスト検知 → 復元"); _restore_from_backup()

# ────────────────────────────────────────────────
#  Elasticsearch 補助
# ────────────────────────────────────────────────

def _index_project_es(project: Dict[str, Any]):
    try:
        es.index(index=ES_INDEX, id=project["id"], document=project)
    except Exception as e:
        print(f"ES index error: {e}")

def _delete_project_es(pid: int):
    try:
        es.delete(index=ES_INDEX, id=pid, ignore=[404])
    except Exception as e:
        print(f"ES delete error: {e}")

# ────────────────────────────────────────────────
#  メタデータ取得
# ────────────────────────────────────────────────

def get_metadata(url):
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 403:
            domain = urlparse(url).netloc.lstrip("www.")
            return {"error403": True, "title": domain, "description": "説明が見つかりませんでした", "image": None, "url": url}
        response.raise_for_status()
    except Exception:
        domain = urlparse(url).netloc.lstrip("www.")
        return {"error403": True, "title": domain, "description": "説明が見つかりませんでした", "image": None, "url": url}

    soup = BeautifulSoup(response.text, "html.parser")
    title_tag = soup.find('meta', property='og:title') or soup.find('title')
    title = (title_tag.get('content') if title_tag and title_tag.has_attr('content') else (title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした"))
    desc_tag = soup.find('meta', property='og:description') or soup.find('meta', attrs={'name': 'description'})
    description = desc_tag.get('content') if desc_tag and desc_tag.has_attr('content') else "説明が見つかりませんでした"
    image_tag = soup.find('meta', property='og:image')
    if image_tag and image_tag.get('content'):
        try:
            img_r = requests.get(image_tag['content'], timeout=5); img_r.raise_for_status()
            mime = img_r.headers.get("Content-Type", "image/jpeg")
            image_data = f"data:{mime};base64," + base64.b64encode(img_r.content).decode()
        except Exception:
            image_data = None
    else:
        image_data = None
    return {"error403": False, "title": title, "description": description, "image": image_data, "url": url}

# ────────────────────────────────────────────────
#  Jinja
# ────────────────────────────────────────────────

def normalize_code_blocks(text):
    """
    マークダウン内のコードブロックを正規化する関数
    - 複数のコードブロックを適切に処理
    - バックティック記号の誤検出を防止
    - コードブロック内の改行を保持
    """
    import re
    
    # すでにHTMLタグが含まれているかチェック
    if re.search(r'<div class="codehilite"><pre>', text):
        # すでに変換済みの場合は処理をスキップ
        return text
    
    # マークダウンのコードブロックを検出するパターン
    # より厳密なパターンマッチングを行う
    pattern = r'```(.*?)\n(.*?)```'
    
    def process_code_block(match):
        # 言語指定の取得と正規化
        lang = match.group(1).strip()
        code = match.group(2)
        
        # 行の処理（余分な末尾空白を削除、インデントは保持）
        lines = [line.rstrip() for line in code.split('\n')]
        
        # 空行を適切に処理
        clean_code = "\n".join(lines)
        
        # 適切なフォーマットで返す
        return f'```{lang}\n{clean_code}\n```'
    
    # すべてのコードブロックを処理
    processed_text = re.sub(pattern, process_code_block, text, flags=re.DOTALL)
    
    return processed_text

def markdown_filter(text):
    """
    マークダウンをHTMLに変換するフィルター
    """
    # コードブロックを正規化
    normalized_text = normalize_code_blocks(text)
    
    # マークダウンをHTMLに変換
    return markdown.markdown(
        normalized_text,
        extensions=[
            'markdown.extensions.fenced_code',  # コードフェンスのサポート
            'markdown.extensions.codehilite',   # コードハイライト
            'markdown.extensions.tables',       # テーブルのサポート
            'markdown.extensions.sane_lists',   # リストの適切な処理
            'markdown.extensions.nl2br',        # 改行の処理（最後に配置）
        ],
        extension_configs={
            'markdown.extensions.codehilite': {
                'linenums': False,               # 行番号表示なし
                'css_class': 'codehilite',       # CSSクラス名
                'guess_lang': True,              # 言語自動推測
                'use_pygments': True             # Pygmentsを使用
            },
            'markdown.extensions.fenced_code': {
                'lang_prefix': 'language-'       # 言語指定プレフィックス
            }
        },
        output_format='html5'                    # HTML5形式で出力
    )
    
# Flaskアプリケーションにフィルターを登録
app.jinja_env.filters['markdown'] = markdown_filter
    
def render_stars(rating):
    try: r = float(rating)
    except: r = 0.0
    return "★"*int(r)+"☆"*(10-int(r))
app.jinja_env.filters['render_stars'] = render_stars
# ────────────────────────────────────────────────
#  ルーティング
# ────────────────────────────────────────────────

# Pygmentsスタイルシートを生成するためのルート
@app.route('/pygments.css')
def pygments_css():
    formatter = HtmlFormatter(style='default')
    css = formatter.get_style_defs('.codehilite')
    return css, 200, {'Content-Type': 'text/css'}

# アプリケーションのコンテキストプロセッサに追加
@app.context_processor
def inject_pygments_css():
    def get_pygments_css():
        formatter = HtmlFormatter(style='default')
        return formatter.get_style_defs('.codehilite')
    return dict(get_pygments_css=get_pygments_css)

@app.route('/', methods=['GET','POST'])
def index():
    tag_filter = request.args.get('tag')
    if request.method == 'POST':
        url_input = request.form.get('url','').strip()
        comment = re.sub(r'\n+','\n', request.form.get('comment',''))
        rating  = float(request.form.get('rating','5') or 5)
        tags    = [t.strip() for t in request.form.get('tags','').split(',') if t.strip()][:5]
        author  = session.get('username','guest')
        meta    = get_metadata(url_input) if url_input else {"error403":False,"title":"","description":"","image":None,"url":""}
        projects = load_projects(); new_id = max((p.get('id',0) for p in projects), default=0)+1
        proj = {"id":new_id,"url":url_input,"comment":comment,"title":meta['title'],"image":meta['image'],"error403":meta['error403'],"rating":rating,"likes":0,"tags":tags,"author":author,"登録日":datetime.now(JST).strftime('%Y-%m-%d')}
        projects.append(proj); save_all_projects(projects); _index_project_es(proj)
        return redirect(url_for('index'))
    projects = sorted(load_projects(), key=lambda p:float(p.get('rating',0)), reverse=True)
    if tag_filter: projects = [p for p in projects if tag_filter in p.get('tags',[])]
    return render_template('index.html', projects=projects, tag_filter=tag_filter, is_search=False, query="")

# ────────────────── 検索 ─────────────────────────
@app.route('/search')
def search():
    query = request.args.get('q',''); tag = request.args.get('tag'); author = request.args.get('author');
    date_from = request.args.get('date_from'); date_to = request.args.get('date_to');
    rating_min = request.args.get('rating_min', type=float); rating_max = request.args.get('rating_max', type=float)

    es_q = {"bool":{"must":[],"filter":[]}}
    es_q['bool']['must'].append({"multi_match":{"query":query if query else "*","fields":["title^3","comment","description","url"]}}) if query else es_q['bool']['must'].append({"match_all":{}})
    if tag: es_q['bool']['filter'].append({"term":{"tags.keyword":tag}})
    if author: es_q['bool']['filter'].append({"term":{"author.keyword":author}})
    if date_from or date_to:
        rng = {}; 
        if date_from: rng['gte']=date_from
        if date_to: rng['lte']=date_to
        es_q['bool']['filter'].append({"range":{"登録日":rng}})
    if rating_min is not None or rating_max is not None:
        rng = {}
        if rating_min is not None: rng['gte']=rating_min
        if rating_max is not None: rng['lte']=rating_max
        es_q['bool']['filter'].append({"range":{"rating":rng}})
    try:
        resp = es.search(index=ES_INDEX, query=es_q, size=100)
        ids = [int(h['_id']) for h in resp['hits']['hits']]
    except Exception as e:
        print(f"ES search error: {e}"); ids=[]
    proj_dict = {p['id']:p for p in load_projects()}
    projects = [proj_dict[i] for i in ids if i in proj_dict]
    return render_template('index.html', projects=projects, is_search=True, query=query)

@app.route("/admin-login")
def admin_login():
    session.update(authenticated=True, master=True)
    return redirect(url_for("index"))

@app.route("/admin-logout")
def admin_logout():
    session.pop("master", None)
    return redirect(url_for("index"))

@app.route("/edit/<int:project_id>", methods=["GET", "POST"])
def edit(project_id):
    projects = load_projects()
    project  = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return "プロジェクトが見つかりません", 404

    if request.method == "POST":
        new_url     = request.form.get("url", "").strip()
        new_comment = re.sub(r'\n+', '\n', request.form.get("comment", ""))
        new_rating  = float(request.form.get("rating", "5") or 5)
        new_tags    = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()][:5]

        metadata = get_metadata(new_url) if new_url else \
            {"error403": False, "title": "", "description": "", "image": None, "url": ""}

        project.update({
            "url": new_url,
            "title": metadata.get("title", ""),
            "image": metadata.get("image"),
            "error403": metadata.get("error403", False),
            "comment": new_comment,
            "rating": new_rating,
            "tags": new_tags
        })
        save_all_projects(projects)
        return redirect(url_for("index"))

    return render_template("edit.html", project=project, project_id=project_id)

@app.route("/like/<int:project_id>", methods=["POST"])
def like(project_id):
    projects = load_projects()
    project  = next((p for p in projects if p.get("id") == project_id), None)
    if not project:
        return jsonify({"error": "プロジェクトが見つかりません"}), 404
    project["likes"] = project.get("likes", 0) + 1
    save_all_projects(projects)
    return jsonify({"likes": project["likes"]})

@app.route("/unlike/<int:project_id>", methods=["POST"])
def unlike(project_id):
    projects = load_projects()
    project  = next((p for p in projects if p.get("id") == project_id), None)
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
    # 元データ件数を取得して意図的な空リストか判定
    projects = load_projects()
    orig_count = len(projects)
    new_projects = [p for p in projects if p.get("id") != project_id]
    allow_empty = (orig_count == 1)
    save_all_projects(new_projects, allow_empty=allow_empty)
    return redirect(url_for("index"))


# ── likes==0 のプロジェクト削除 ───────────────────
@app.route("/d")
def cleanup_projects():
    if not os.path.exists(PROJECTS_FILE):
        return f'ERROR: {PROJECTS_FILE} が見つかりません', 500

    with file_lock():
        # 現行を退避
        os.replace(PROJECTS_FILE, BACKUP_FILE)

        try:
            with open(BACKUP_FILE, 'r', encoding='utf-8') as f:
                projects = json.load(f)
        except Exception as e:
            os.replace(BACKUP_FILE, PROJECTS_FILE)
            return f'バックアップ読み込みエラー: {e}', 500

        cleaned = [p for p in projects if p.get("likes", 0) != 0]
        save_all_projects(cleaned, allow_empty=True)

    return redirect(url_for("index"))

# ────────────────────────────────────────────────
#  エントリーポイント
# ────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
