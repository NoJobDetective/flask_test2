#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys

# ── 設定 ──
# projects.json の絶対パスを指定
PROJECTS_FILE = '~/flask_project/flask_test2/projects.json'
BACKUP_FILE   = PROJECTS_FILE + '.bak'

def load_projects(path):
    """JSONファイルを読み込み、Pythonオブジェクトとして返す"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_projects(path, data):
    """PythonオブジェクトをJSONとしてファイルに書き込む"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def cleanup_projects():
    """likes が 0 のプロジェクトを削除して保存する"""
    if not os.path.exists(PROJECTS_FILE):
        print(f'ERROR: {PROJECTS_FILE} が見つかりません', file=sys.stderr)
        sys.exit(1)

    # バックアップを作成（万が一に備えて）
    os.replace(PROJECTS_FILE, BACKUP_FILE)
    projects = load_projects(BACKUP_FILE)

    # フィルタリング：likes > 0 のものだけ残す
    cleaned = [p for p in projects if p.get('likes', 0) != 0]

    # 上書き保存
    save_projects(PROJECTS_FILE, cleaned)

if __name__ == '__main__':
    cleanup_projects()
