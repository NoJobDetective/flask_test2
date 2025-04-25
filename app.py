from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
from bs4 import BeautifulSoup
import json
import os
import base64
import re                    # 改行コード統一用
import markdown              # マークダウン変換用
from datetime import datetime, timezone, timedelta  # 登録日の自動入力用
from urllib.parse import urlparse                   # URL解析用
import fcntl                 # ★ ファイルロック用
from contextlib import contextmanager
import tempfile              # ★ 一時ファイル作成用
from typing import List, Optional

# ── 基本設定 ───────────────────────────────────────
JST = timezone(timedelta(hours=9))
app = Flask(__name__)
app.secret_key = "mysecretkey"           # 適宜変更してください
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_FILE = os.path.join(BASE_DIR, "projects.json")
BACKUP_FILE   = PROJECTS_FILE + ".bak"
LOCK_FILE     = PROJECTS_FILE + ".lock"      # ★ ロックファイル

# ────────────────────────────────────────────────
#  ロック用コンテキストマネージャ
# ────────────────────────────────────────────────
@contextmanager
def file_lock():
    """with file_lock(): ブロック内で排他制御が働く。"""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "a") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)

# ────────────────────────────────────────────────
#  低レベル I/O ユーティリティ
# ────────────────────────────────────────────────
def _atomic_write_json(path: str, data: List[dict]) -> None:
    """一時ファイルへ書き込み後、fsync → os.replace で原子的に置換"""
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="projects_", suffix=".json",
                                        dir=os.path.dirname(path))
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
        json.dump(data, tmp_f, ensure_ascii=False, indent=2)
        tmp_f.flush()
        os.fsync(tmp_f.fileno())
    os.replace(tmp_path, path)

def _restore_from_backup() -> Optional[List[dict]]:
    """
    backup が存在し、かつ中身が空でなければ projects.json に戻す。
    復元成功時はリストを返す。失敗時は None。
    """
    if not os.path.exists(BACKUP_FILE):
        return None
    try:
        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data:      # 空バックアップは無視
            _atomic_write_json(PROJECTS_FILE, data)
            return data
    except Exception:
        pass
    return None

# ────────────────────────────────────────────────
#  JSON 読み込み／保存
# ────────────────────────────────────────────────
def load_projects() -> List[dict]:
    """
    projects.json を読み込む。存在しない場合や壊れている場合は
    空リストを返すが、「空リストかつバックアップあり」のときは
    バックアップから自動復元する。
    """
    if not os.path.exists(PROJECTS_FILE):
        return []
    with file_lock():
        try:
            with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # 読み込み失敗 → バックアップ復元を試みる
            restored = _restore_from_backup()
            return restored if restored is not None else []

        if not data:
            restored = _restore_from_backup()
            return restored if restored is not None else []
        return data

def save_all_projects(projects: List[dict], *, allow_empty: bool = False) -> None:
    """
    projects を保存。保存前に現在のファイルを BACKUP_FILE へコピーしておく。
    allow_empty=False のとき、保存結果が空リストならバックアップからロールバック。
    """
    with file_lock():
        # 現状をバックアップ
        if os.path.exists(PROJECTS_FILE):
            try:
                os.replace(PROJECTS_FILE, BACKUP_FILE)
            except Exception as e:
                print(f"バックアップ作成失敗: {e}")

        try:
            _atomic_write_json(PROJECTS_FILE, projects)
        except IOError as e:
            print(f"保存エラー: {e}")
            # 書き込み失敗 → バックアップ復元
            _restore_from_backup()
            return

        # 意図せぬ空ファイルなら復元
        if not projects and not allow_empty:
            print("空リストを検知したためバックアップから復元")
            _restore_from_backup()

# ────────────────────────────────────────────────
#  メタデータ取得（省略なし）
# ────────────────────────────────────────────────
def get_metadata(url):
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 403:
            parsed = urlparse(url)
            domain = parsed.netloc.lstrip("www.")
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
        domain = parsed.netloc.lstrip("www.")
        return {
            "error403": True,
            "title": domain,
            "description": "説明が見つかりませんでした",
            "image": None,
            "url": url
        }

    soup = BeautifulSoup(response.text, 'html.parser')

    title_tag = soup.find('meta', property='og:title') or soup.find('title')
    title = (title_tag.get('content') if title_tag and title_tag.has_attr('content')
             else (title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした"))

    desc_tag = soup.find('meta', property='og:description') \
        or soup.find('meta', attrs={'name': 'description'})
    description = desc_tag.get('content') if desc_tag and desc_tag.has_attr('content') \
        else "説明が見つかりませんでした"

    image_tag = soup.find('meta', property='og:image')
    if image_tag and image_tag.get('content'):
        image_url = image_tag.get('content')
        try:
            img_response = requests.get(image_url, timeout=5)
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

# ────────────────────────────────────────────────
#  Jinja フィルタ
# ────────────────────────────────────────────────
def render_stars(rating):
    try:
        r = float(rating)
    except Exception:
        r = 0.0
    full = int(r)
    empty = 10 - full
    return "★" * full + "☆" * empty
app.jinja_env.filters['render_stars'] = render_stars

def markdown_filter(text):
    return markdown.markdown(text, extensions=['nl2br'])
app.jinja_env.filters['markdown'] = markdown_filter

# ────────────────────────────────────────────────
#  ルーティング
# ────────────────────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def index():
    tag_filter = request.args.get("tag")

    if request.method == "POST":
        url_input = request.form.get("url", "").strip()
        comment = re.sub(r'\n+', '\n', request.form.get("comment", ""))
        rating  = float(request.form.get("rating", "5") or 5)
        tags    = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()][:5]

        metadata = get_metadata(url_input) if url_input else \
            {"error403": False, "title": "", "description": "", "image": None, "url": ""}

        projects = load_projects()
        new_id   = max((p.get("id", 0) for p in projects), default=0) + 1
        projects.append({
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
        })
        save_all_projects(projects)
        return redirect(url_for("index"))

    projects = sorted(load_projects(), key=lambda p: float(p.get('rating', 0)), reverse=True)
    if tag_filter:
        projects = [p for p in projects if tag_filter in p.get("tags", [])]
    return render_template("index.html", projects=projects, tag_filter=tag_filter)

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
    # 管理者権限チェック
    if not session.get("master"):
        return "削除権限がありません", 403

    # 現在のプロジェクト一覧を読み込み
    projects = load_projects()
    orig_count = len(projects)

    # 指定IDを除外した新しいリストを作成
    new_projects = [p for p in projects if p.get("id") != project_id]

    # 元データが1件だけだった場合は空リストを許可
    allow_empty = (orig_count == 1)

    # 保存処理（allow_empty=Trueなら空リストでもロールバックしない）
    save_all_projects(new_projects, allow_empty=allow_empty)

    # 最後の1件を削除したときはバックアップファイルを削除して復元を防止
    if allow_empty:
        try:
            os.remove(BACKUP_FILE)
        except OSError:
            pass

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
