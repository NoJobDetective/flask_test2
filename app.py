from flask import Flask, request, render_template, redirect, url_for, session, jsonify
import requests
from bs4 import BeautifulSoup
import json
import os
import base64
import re  # 改行コード統一用
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
            # ブロックされた場合もフォールバック情報として返す
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
    except Exception as e:
        print(f"URL取得中のエラー: {e}")
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
    
    title_tag = soup.find('meta', property='og:title')
    if title_tag and title_tag.get('content'):
        title = title_tag.get('content')
    else:
        title_tag = soup.find('title')
        title = title_tag.string.strip() if title_tag else "タイトルが見つかりませんでした"
    
    desc_tag = soup.find('meta', property='og:description')
    if desc_tag and desc_tag.get('content'):
        description = desc_tag.get('content')
    else:
        desc_tag = soup.find('meta', attrs={'name': 'description'})
        description = desc_tag.get('content') if desc_tag else "説明が見つかりませんでした"
    
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
            print(f"画像ダウンロード中のエラー: {e}")
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
    if os.path.exists(PRO
