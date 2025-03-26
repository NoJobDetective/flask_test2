from flask import Flask, request, render_template, url_for
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

def get_metadata(url):
    """
    指定したURLからウェブページのメタデータ（タイトル、説明、画像）を取得する関数です。
    """
    try:
        response = requests.get(url)
        response.raise_for_status()  # HTTPエラーの場合、例外を発生
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
    
    # 【説明文の取得】
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

    # URLも合わせて返す
    return {"title": title, "description": description, "image": image, "url": url}

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        url = request.form.get("url")
        metadata = get_metadata(url)
        if metadata:
            return render_template("result.html", metadata=metadata)
        else:
            error_message = "指定されたURLからメタデータを取得できませんでした。"
            return render_template("index.html", error=error_message)
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
