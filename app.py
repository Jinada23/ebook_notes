from flask import Flask, render_template, redirect, request, session, url_for, send_from_directory
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from werkzeug.utils import secure_filename
from pdf2image import convert_from_path
import os

# === Flask setup ===
app = Flask(__name__)
app.secret_key = 'your-secret-key'
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # permite HTTP în loc de HTTPS pentru dev

# === OAuth config ===
GOOGLE_CLIENT_SECRETS_FILE = "client_secret.json"
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
REDIRECT_URI = 'https://ebook-notes.onrender.com/oauth2callback'

# === Route: Index ===
@app.route('/')
def index():
    return render_template('index.html')

# === Route: Login cu Google ===
@app.route('/login')
def login():
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

# === Route: Callback după login ===
@app.route('/oauth2callback')
def oauth2callback():
    state = session['state']
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

    return redirect(url_for('list_files'))

# === Convertim PDF în imagini (doar primele 3 pagini) ===
def convert_pdf_to_images(pdf_path, output_folder):
    from PIL import Image
    filename = os.path.splitext(os.path.basename(pdf_path))[0]
    try:
        images = convert_from_path(pdf_path, dpi=125, first_page=1, last_page=1)
    except Exception as e:
        print(f"❌ Eroare conversie PDF: {e}")
        return []

    saved_paths = []
    for i, image in enumerate(images):
        resized = image.resize((992, 1403))  # proporție A4 la 125dpi
        image_path = os.path.join(output_folder, f"{filename}_page_{i+1}.png")
        resized.save(image_path, "PNG")
        saved_paths.append(image_path)

    return saved_paths

# === Route: Lista fișiere ===
@app.route('/list')
def list_files():
    if 'credentials' not in session:
        return redirect('login')

    creds = google_auth_from_session(session['credentials'])
    service = build('drive', 'v3', credentials=creds)
    folder_id = find_folder_id(service, "Exported Notebooks")
    if not folder_id:
    	return "Folderul 'Rakuten Kobo' nu a fost găsit în Google Drive", 404
    results = service.files().list(
    	q=f"'{folder_id}' in parents and mimeType='application/pdf'",
    	pageSize=20,
	fields="files(id, name)"
    ).execute()
    files = results.get('files', [])
    preview_data = []
    print(f"📂 Fișiere găsite în Drive: {len(files)}")

    for file in files:
        file_id = file['id']
        filename = secure_filename(file['name'])
        local_pdf_path = f"static/downloads/{filename}"
        output_folder = "static/previews"
        base_name = os.path.splitext(filename)[0]

        # Descarcă PDF dacă nu există local
        if not os.path.exists(local_pdf_path):
            request = service.files().get_media(fileId=file_id)
            with open(local_pdf_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

        # Verifică dacă imaginile există deja (caching)
        existing_images = [
            os.path.join(output_folder, f)
            for f in os.listdir(output_folder)
            if f.startswith(base_name) and f.endswith(".png")
        ]

        if not existing_images:
            convert_pdf_to_images(local_pdf_path, output_folder)

        # Adaugă imaginile în listă
        image_paths = sorted([
            f"/static/previews/{f}"
            for f in os.listdir(output_folder)
            if f.startswith(base_name) and f.endswith(".png")
        ])

        preview_data.append({
            'name': file['name'],
	    'filename': filename,
            'pages': image_paths
        })

    return render_template("files.html", files=preview_data)

# === Convertim dict-ul din sesiune în Credentials ===
def google_auth_from_session(session_creds):
    return Credentials(
        token=session_creds['token'],
        refresh_token=session_creds.get('refresh_token'),
        token_uri=session_creds['token_uri'],
        client_id=session_creds['client_id'],
        client_secret=session_creds['client_secret'],
        scopes=session_creds['scopes']
    )

# === Servim fișierele PDF dacă e nevoie ===
@app.route('/pdf/<path:filename>')
def serve_pdf(filename):
    return send_from_directory('static/downloads', filename, mimetype='application/pdf')

def find_folder_id(service, folder_name):
    results = service.files().list(
        q=f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}'",
        fields="files(id, name)",
        spaces='drive'
    ).execute()

    folders = results.get('files', [])
    if not folders:
        print(f"❌ Folderul „{folder_name}” nu a fost găsit.")
        return None

    print(f"📁 Găsit folder: {folders[0]['name']} (ID: {folders[0]['id']})")
    return folders[0]['id']

# === Run server ===
if __name__ == '__main__':
    os.makedirs("static/downloads", exist_ok=True)
    os.makedirs("static/previews", exist_ok=True)
    app.run(host="0.0.0.0", port=10000)
