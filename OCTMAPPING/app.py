import os
import smtplib
import json
import io
import time
from email.mime.text import MIMEText
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
from authlib.integrations.flask_client import OAuth
from google.oauth2.credentials import Credentials 
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'random_secret_key_for_testing')

# ==========================================
# 🔑 APIキー ＆ フォルダID 設定 (Render本番用)
# ==========================================
# ※パスワードはここには書かず、Renderの環境変数から読み込みます
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')

GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS', 'octmapping@gmail.com')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD')

GOOGLE_DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID', '1O5XxPxOcBUnAzSCYID6EKJqw27-Amo03')
GOOGLE_REFRESH_TOKEN = os.environ.get('GOOGLE_REFRESH_TOKEN')
# ==========================================
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

def get_drive_service():
    creds = Credentials(
        token=None, refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET
    )
    return build('drive', 'v3', credentials=creds)

# 📦 Googleドライブから商品データベース(JSON)を読み書きする関数
def get_db_data(service):
    query = f"name = 'data.json' and '{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query).execute()
    files = results.get('files', [])
    
    if not files:
        # 初期状態のデフォルトデータ
        initial_data = {
            "products": [
                {"id": "item_1", "category": "karakuri", "title": "連発式ゴム鉄砲GH1（製造中止）", "description": "12連射を可能にする3Dプリンター製機構。エネルギー保存と解放の論理を形にしました。", "stock": 0, "has_image": False},
                {"id": "item_2", "category": "electronics", "title": "現在制作されていません", "description": "高精度な回路設計による次世代デバイス群のプロトタイプ。", "stock": 0, "has_image": False},
                {"id": "item_3", "category": "accessory", "title": "SR1", "description": "技術と感性が交差する場所に、新しい驚きを形にしたモダニズム・ジュエリー。", "stock": 5, "has_image": False}
            ]
        }
        save_db_data(service, initial_data)
        return initial_data
        
    file_id = files[0]['id']
    request_media = service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request_media)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    file_stream.seek(0)
    return json.loads(file_stream.read().decode('utf-8'))

def save_db_data(service, data):
    query = f"name = 'data.json' and '{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query).execute()
    for f in results.get('files', []):
        try: service.files().delete(fileId=f['id']).execute()
        except: pass
        
    file_metadata = {'name': 'data.json', 'parents': [GOOGLE_DRIVE_FOLDER_ID]}
    media = MediaIoBaseUpload(io.BytesIO(json.dumps(data, ensure_ascii=False).encode('utf-8')), mimetype='application/json', resumable=True)
    service.files().create(body=file_metadata, media_body=media, fields='id').execute()

# 🏠 トップページ
@app.route('/')
def index():
    user = session.get('user')
    is_admin = session.get('is_admin', False)
    
    # ドライブから動的に商品リストを取得してレンダリング
    try:
        service = get_drive_service()
        db = get_db_data(service)
        products = db.get("products", [])
    except Exception:
        products = [] # エラー時は空リスト
        
    return render_template('index.html', user=user, is_admin=is_admin, products=products)

# 🔓 ログイン・ログアウト
@app.route('/login')
def login():
    redirect_uri = url_for('auth', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth')
def auth():
    token = google.authorize_access_token()
    user = google.parse_id_token(token, nonce=None)
    session['user'] = user
    return redirect('/')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('is_admin', None)
    return redirect('/')

@app.route('/admin_auth', methods=['POST'])
def admin_auth():
    if request.form.get('admin_password') == 'oct#!$888':
        session['is_admin'] = True
        flash('管理者モードを有効化しました。', 'success')
    else:
        flash('パスワードが違います。', 'error')
    return redirect('/')

# 🛠️ 管理者専用画面
@app.route('/admin')
def admin_page():
    if not session.get('is_admin'):
        flash('管理者権限がありません。', 'error')
        return redirect('/')
    try:
        service = get_drive_service()
        db = get_db_data(service)
        products = db.get("products", [])
    except Exception as e:
        flash(f'データの読み込みに失敗しました: {str(e)}', 'error')
        products = []
    return render_template('admin.html', products=products)

# ➕ 【新規】商品追加処理
@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('is_admin'): return abort(403)
    
    category = request.form.get('category')
    title = request.form.get('title')
    description = request.form.get('description')
    try: stock = int(request.form.get('stock', 0))
    except: stock = 0
    file = request.files.get('file')
    
    try:
        service = get_drive_service()
        db = get_db_data(service)
        
        item_id = f"item_{int(time.time())}" # ユニークなIDを生成
        has_image = False
        
        if file and file.filename != '':
            filename = f"product_{item_id}.png"
            file_metadata = {'name': filename, 'parents': [GOOGLE_DRIVE_FOLDER_ID]}
            media = MediaIoBaseUpload(file.stream, mimetype=file.mimetype, resumable=True)
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            has_image = True
            
        new_item = {
            "id": item_id,
            "category": category,
            "title": title,
            "description": description,
            "stock": stock,
            "has_image": has_image
        }
        db["products"].append(new_item)
        save_db_data(service, db)
        flash('新しいカードを追加しました！', 'success')
    except Exception as e:
        flash(f'カードの追加に失敗しました: {str(e)}', 'error')
        
    return redirect('/admin')

# 📝 【更新】既存商品の画像・在庫数更新
@app.route('/admin/update_product/<item_id>', methods=['POST'])
def update_product(item_id):
    if not session.get('is_admin'): return abort(403)
    
    try: stock = int(request.form.get('stock', 0))
    except: stock = 0
    file = request.files.get('file')
    
    try:
        service = get_drive_service()
        db = get_db_data(service)
        
        for p in db["products"]:
            if p["id"] == item_id:
                p["stock"] = stock
                
                if file and file.filename != '':
                    filename = f"product_{item_id}.png"
                    # 古い画像を削除
                    query = f"name = '{filename}' and '{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
                    results = service.files().list(q=query).execute()
                    for f in results.get('files', []):
                        service.files().delete(fileId=f['id']).execute()
                    
                    # 新しい画像をアップロード
                    file_metadata = {'name': filename, 'parents': [GOOGLE_DRIVE_FOLDER_ID]}
                    media = MediaIoBaseUpload(file.stream, mimetype=file.mimetype, resumable=True)
                    service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                    p["has_image"] = True
                break
                
        save_db_data(service, db)
        flash('カード情報を更新しました。', 'success')
    except Exception as e:
        flash(f'更新に失敗しました: {str(e)}', 'error')
    return redirect('/admin')

# ❌ 【削除】商品削除ルート
@app.route('/admin/delete_product/<item_id>', methods=['POST'])
def delete_product(item_id):
    if not session.get('is_admin'): return abort(403)
    
    try:
        service = get_drive_service()
        db = get_db_data(service)
        
        # 1. リストから削除
        db["products"] = [p for p in db["products"] if p["id"] != item_id]
        
        # 2. ドライブ上の関連画像を削除
        filename = f"product_{item_id}.png"
        query = f"name = '{filename}' and '{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query).execute()
        for f in results.get('files', []):
            service.files().delete(fileId=f['id']).execute()
            
        save_db_data(service, db)
        flash('カードを削除しました。', 'success')
    except Exception as e:
        flash(f'削除に失敗しました: {str(e)}', 'error')
    return redirect('/admin')

# 🖼️ 画像配信エンドポイント
@app.route('/img/<item_id>')
def get_image(item_id):
    try:
        service = get_drive_service()
        filename = f"product_{item_id}.png"
        query = f"name = '{filename}' and '{GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id, mimeType)").execute()
        files = results.get('files', [])
        
        if not files: return abort(404)
            
        file_id = files[0]['id']
        request_media = service.files().get_media(fileId=file_id)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request_media)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        file_stream.seek(0)
        return send_file(file_stream, mimetype=files[0]['mimeType'])
    except Exception:
        return abort(404)

# ✉️ メール送信
# ✉️ メール送信処理（本格版フォーム対応）
@app.route('/send_mail', methods=['POST'])
def send_mail():
    user = session.get('user')
    if not user: return redirect('/') 
    
    # フォームから送られてきたデータを取得
    inquiry_type = request.form.get('inquiry_type')
    message_body = request.form.get('message')
    
    try:
        user_name = user.get('name')
        user_email = user.get('email')
        
        # メールの本文を作成
        mail_text = f"ユーザー: {user_name} ({user_email})\n\n【本文】\n{message_body}"
        msg = MIMEText(mail_text)
        
        # ご要望の件名フォーマット: 【お問い合わせ】(質問) など
        msg['Subject'] = f'【お問い合わせ】({inquiry_type})'
        msg['From'] = GMAIL_ADDRESS
        msg['To'] = GMAIL_ADDRESS 
        
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        flash('お問い合わせを送信しました。', 'success')
    except Exception:
        flash('送信に失敗しました。', 'error')
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='127.0.0.1', port=port, debug=True)