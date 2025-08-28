from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import json, time, os, uuid, mimetypes
import datetime
import email
import email.policy

def should_server_run():
    """Vérifie si le serveur doit tourner (entre 7h et 22h heure française)"""
    now = datetime.datetime.utcnow()
    
    # Ajustement pour la France :
    # - Heure d'été (mars à octobre) : UTC+2 → ajouter 2 heures
    # - Heure d'hiver (octobre à mars) : UTC+1 → ajouter 1 heure
    
    # Déterminer si on est en heure d'été (simplifié)
    is_summer_time = (3 <= now.month <= 10)  # Mars à octobre
    
    hour_offset = 2 if is_summer_time else 1
    french_hour = (now.hour + hour_offset) % 24
    
    return 7 <= french_hour < 22  # De 7h à 22h heure française

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8080))

# Data files and directories
DATA_DIR = "."
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
USERS_FILE = os.path.join(DATA_DIR, "users.txt")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
MESSAGES_FILE = os.path.join(DATA_DIR, "messages.json")
FILES_META_FILE = os.path.join(DATA_DIR, "files.json")
BANNED_FILE = os.path.join(DATA_DIR, "banned.json")

# In-memory stores
users = {}          # {username: password}
sessions = {}       # {session_token: username}
messages = []       # [{user, text, timestamp}]
files_meta = []     # [{id, owner, filename, disk_path, size, uploaded_at}]
banned_users = {}   # {username: ban_expiration_timestamp}

ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# Persistence helpers
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"Failed to load {path}:", e)
        return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"Failed to save {path}:", e)

# Ensure directories and files exist
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)
os.makedirs(os.path.dirname(MESSAGES_FILE), exist_ok=True)
os.makedirs(os.path.dirname(FILES_META_FILE), exist_ok=True)
os.makedirs(os.path.dirname(BANNED_FILE), exist_ok=True)

# Initialize files if they don't exist
for file_path in [SESSIONS_FILE, MESSAGES_FILE, FILES_META_FILE, BANNED_FILE]:
    if not os.path.exists(file_path):
        if file_path == MESSAGES_FILE:
            save_json(file_path, [])
        else:
            save_json(file_path, {})

# Flexible users loader (JSON or plain text user:pass per line)
def parse_users_plain(text: str):
    data = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        sep = None
        for candidate in (':', '=', ',', ' '):
            if candidate in line:
                sep = candidate
                break
        if sep is None:
            continue
        parts = line.split(sep, 1) if sep != ' ' else line.split()
        if len(parts) < 2:
            continue
        user = parts[0].strip().strip('"\'')
        pwd = parts[1].strip().strip('"\'')
        if user:
            data[user] = pwd
    return data

def load_users_file(path: str):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list):
                d = {}
                for u in obj:
                    if isinstance(u, dict) and 'username' in u and 'password' in u:
                        d[u['username']] = u['password']
                if d:
                    return d
        except Exception:
            pass
        return parse_users_plain(content)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print("Failed to load users file:", e)
        return {}

# Load on startup
users = load_users_file(USERS_FILE)
sessions = load_json(SESSIONS_FILE, {})
messages = load_json(MESSAGES_FILE, [])
files_meta = load_json(FILES_META_FILE, [])
banned_users = load_json(BANNED_FILE, {})

# Seed admin if not present
if ADMIN_USER not in users:
    users[ADMIN_USER] = ADMIN_PASS
    save_json(USERS_FILE, users)

# --- Templates HTML intégrés ---
def page_template(title, body):
    return f"""
    <!DOCTYPE html>
    <html lang="fr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"> 
        <title>{title}</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css">
        <style>
            :root {{
                --bg-1: #0f2027;
                --bg-2: #203a43;
                --bg-3: #2c5364;
                --primary: #6C63FF;
                --primary-2: #8C7CFF;
                --card-bg: rgba(255,255,255,0.08);
                --border: rgba(255,255,255,0.18);
                --text: #f2f4f8;
                --me: #8b7dff;
                --other: rgba(255,255,255,0.12);
            }}
            * {{ box-sizing: border-box; }}
            html, body {{ height: 100%; }}
            body {{
                margin: 0;
                font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'Apple Color Emoji', 'Segoe UI Emoji';
                color: var(--text);
                background: linear-gradient(135deg, var(--bg-1) 0%, var(--bg-2) 45%, var(--bg-3) 100%);
                background-attachment: fixed;
                position: relative;
            }}
            .app-shell {{
                min-height: 100vh;
                padding: 24px;
                display: grid;
                place-items: center;
            }}
            .glass-card {{
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
                background: var(--card-bg);
                border: 1px solid var(--border);
                border-radius: 20px;
                box-shadow: 0 20px 50px rgba(0,0,0,0.35);
            }}
            .card-elevated {{
                border-radius: 18px;
                box-shadow: 0 16px 40px rgba(0,0,0,0.35);
                background: rgba(255,255,255,0.06);
                border: 1px solid var(--border);
            }}
            .btn-primary {{
                background: linear-gradient(135deg, var(--primary), var(--primary-2));
                border: 0;
                box-shadow: 0 8px 20px rgba(108,99,255,0.35);
            }}
            .btn-primary:disabled {{ opacity: .7; }}
            .btn-outline-light {{ border-color: rgba(255,255,255,0.4); color: #fff; }}
            .btn-outline-light:hover {{ background: rgba(255,255,255,0.12); }}

            /* Chat */
            #chat-list {{
                overflow-y: auto;
                padding: 14px;
                scrollbar-width: thin;
                scrollbar-color: rgba(255,255,255,.3) transparent;
            }}
            #chat-list::-webkit-scrollbar {{ width: 8px; }}
            #chat-list::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,.35); border-radius: 8px; }}

            .chat-bubble {{
                max-width: 78%;
                padding: 10px 12px;
                border-radius: 14px;
                margin: 6px 0;
                word-wrap: break-word;
                background: rgba(255,255,255,0.12);
                border: 1px solid rgba(255,255,255,0.18);
            }}
            .chat-bubble.mine {{
                background: linear-gradient(135deg, rgba(108,99,255,.45), rgba(156,140,255,.45));
                border-color: rgba(108,99,255,.55);
            }}
            .chat-bubble.other {{ background: rgba(255,255,255,0.10); }}

            .brand-title {{ font-weight: 700; letter-spacing: .3px; }}
            .muted {{ color: rgba(255,255,255,.75); }}
            a {{ color: #cbd5ff; }}

            /* Layout to keep input visible */
            .chat-wrapper {{ display: grid; grid-template-rows: auto 1fr auto; height: 85vh; max-height: 900px; }}

            /* Files */
            .file-item {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
                padding: 10px 12px;
                border-radius: 12px;
                background: rgba(255,255,255,0.06);
                border: 1px solid var(--border);
                margin-bottom: 8px;
            }}
            .file-name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
            
            /* File link styling */
            .file-link {{
                color: #8cb4ff;
                text-decoration: underline;
                font-weight: 500;
            }}
        </style>
    </head>
    <body>
        <div class="app-shell">
            <div class="container">
                {body}
            </div>
        </div>
    </body>
    </html>
    """

def page_login(error_message=None):
    return page_template("Connexion", f"""
    <div class="row justify-content-center">
        <div class="col-12 col-sm-10 col-md-7 col-lg-5">
            <div class="glass-card p-4 p-md-5">
                <div class="text-center mb-3">
                    <div class="display-6">🔐</div>
                    <h2 class="brand-title mt-2">Connexion</h2>
                    <p class="muted mb-0">Accédez à votre discussion en toute simplicité</p>
                </div>
                {f'<div class="alert alert-danger py-2 px-3 small mb-2">{error_message}</div>' if error_message else ''}
                <form method="POST" action="/login" class="mt-3">
                    <div class="mb-3">
                        <label class="form-label">Nom d'utilisateur</label>
                        <input class="form-control form-control-lg" name="username" placeholder="Nom d'utilisateur" autocomplete="username">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Mot de passe</label>
                        <input class="form-control form-control-lg" type="password" name="password" placeholder="Mot de passe" autocomplete="current-password">
                    </div>
                    <button class="btn btn-primary btn-lg w-100" type="submit">Se connecter</button>
                </form>
                <div class="text-center mt-3">
                    <a href="/register" class="btn btn-outline-light w-100">Créer un compte</a>
                </div>
            </div>
        </div>
    </div>
    """)

def page_register():
    return page_template("Inscription", """
    <div class="row justify-content-center">
        <div class="col-12 col-sm-10 col-md-7 col-lg-5">
            <div class="glass-card p-4 p-md-5">
                <div class="text-center mb-3">
                    <div class="display-6">📝</div>
                    <h2 class="brand-title mt-2">Créer un compte</h2>
                    <p class="muted mb-0">Rejoignez la discussion en quelques secondes</p>
                </div>
                <form method="POST" action="/register" class="mt-4">
                    <div class="mb-3">
                        <label class="form-label">Nom d'utilisateur</label>
                        <input class="form-control form-control-lg" name="username" placeholder="Nom d'utilisateur" autocomplete="username">
                    </div>
                    <div class="mb-3">
                        <label class="form-label">Mot de passe</label>
                        <input class="form-control form-control-lg" type="password" name="password" placeholder="Mot de passe" autocomplete="new-password">
                    </div>
                    <button class="btn btn-success btn-lg w-100" type="submit">Créer un compte</button>
                </form>
                <div class="text-center mt-3">
                    <a href="/login" class="btn btn-outline-light w-100">Déjà inscrit ? Se connecter</a>
                </div>
            </div>
        </div>
    </div>
    """)

def page_discussion(username):
    return page_template("Discussion", f"""
    <div class="row">
        <div class="col-12">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div class="d-flex align-items-center gap-2">
                    <span class="fs-3">💬</span>
                    <h3 class="brand-title mb-0">Discussion Générale</h3>
                    <span class="badge bg-light text-dark ms-2"><i class="bi bi-person-circle"></i> {username}</span>
                </div>
                <div class="d-flex gap-2">
                    <a class="btn btn-outline-light" href="/export" title="Exporter les messages"><i class="bi bi-download"></i> Exporter</a>
                    <form method="POST" action="/logout" class="m-0">
                        <button class="btn btn-outline-light" title="Déconnexion"><i class="bi bi-box-arrow-right"></i> Déconnexion</button>
                    </form>
                </div>
            </div>
        </div>
        <div class="col-12 col-lg-8">
            <div class="glass-card chat-wrapper p-3 p-md-4 mb-3 mb-lg-0">
                <div id="chat-list" class="card-elevated mb-3">
                    <ul id="messages" class="list-unstyled m-0"></ul>
                </div>
                <div class="input-group">
                    <input id="message-input" class="form-control form-control-lg" placeholder="Écris ton message et appuie sur Entrée…">
                    <button id="send-btn" class="btn btn-primary btn-lg"><i class="bi bi-send"></i> Envoyer</button>
                </div>
            </div>
        </div>
        <div class="col-12 col-lg-4">
            <div class="glass-card p-3 p-md-4">
                <h5 class="brand-title mb-3">📁 Partage de fichiers</h5>
                <div class="mb-3">
                    <input type="file" id="file-input" class="form-control">
                    <button id="upload-btn" class="btn btn-primary w-100 mt-2"><i class="bi bi-upload"></i> Uploader</button>
                </div>
                <p class="small muted">Le fichier sera partagé sous forme de lien dans le chat</p>
            </div>
        </div>
    </div>

    <script>
    const CURRENT_USER = "{username}";
    const ADMIN = "{ADMIN_USER}";

    function escapeHtml(s){{
        return s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    }}
    function two(n){{ return n.toString().padStart(2,'0'); }}
    function formatTime(ts){{
        const d = new Date(ts*1000);
        return two(d.getHours())+":"+two(d.getMinutes());
    }}

    let autoScroll = true;
    const listEl = document.getElementById('messages');
    const scrollBox = document.getElementById('chat-list');

    function renderMessages(msgs){{
        const html = msgs.map(m => {{
            const mine = m.user === CURRENT_USER;
            const admin = m.user === ADMIN;
            
            // Check if message contains a file link
            let messageContent = escapeHtml(m.text);
            if (m.text && m.text.includes('[FILE:')) {{
                // Parse file link format: [FILE:filename|fileid]
                const fileMatch = m.text.match(/\\[FILE:(.*?)\\|(.*?)\\]/);
                if (fileMatch) {{
                    const filename = fileMatch[1];
                    const fileid = fileMatch[2];
                    messageContent = `<a href="/download?fid=${{fileid}}" class="file-link" target="_blank">
                        <i class="bi bi-file-earmark-arrow-down"></i> Télécharger "${{escapeHtml(filename)}}"
                    </a>`;
                }}
            }}
            
            return `
            <li class="d-flex ${{mine ? 'justify-content-end' : 'justify-content-start'}}">
                <div class="chat-bubble ${{mine ? 'mine' : 'other'}}">
                    <div class="small muted mb-1">${{admin ? '👑 ' : ''}}${{escapeHtml(m.user)}}</div>
                    <div>${{messageContent}}</div>
                    <div class="small muted mt-1">${{formatTime(m.timestamp)}}</div>
                </div>
            </li>`;
        }}).join('');
        listEl.innerHTML = html;
        try {{ localStorage.setItem('chat_messages', JSON.stringify(msgs)); }} catch(e) {{}}
        if (autoScroll) {{
            scrollBox.scrollTop = scrollBox.scrollHeight;
        }}
    }}

    async function loadMessages(){{
        try {{
            const res = await fetch('/messages', {{ cache: 'no-store' }});
            const msgs = await res.json();
            renderMessages(msgs);
        }} catch(e) {{
            console.error(e);
        }}
    }}

    function nearBottom(){{
        const delta = scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight;
        return delta < 80;
    }}

    scrollBox.addEventListener('scroll', () => {{
        autoScroll = nearBottom();
    }});

    async function sendMessage(txt){{
        await fetch('/send', {{
            method: 'POST',
            headers: {{ 'Content-Type':'application/x-www-form-urlencoded' }},
            body: 'message=' + encodeURIComponent(txt)
        }});
    }}

    const input = document.getElementById('message-input');
    const sendBtn = document.getElementById('send-btn');

    function canSend(){{ return input.value.trim().length > 0; }}
    function updateBtn(){{ sendBtn.disabled = !canSend(); }}

    sendBtn.addEventListener('click', async () => {{
        const txt = input.value.trim();
        if (!txt) return;
        sendBtn.disabled = true;
        try {{
            await sendMessage(txt);
            input.value = '';
            loadMessages();
        }} finally {{
            updateBtn();
        }}
    }});

    input.addEventListener('input', updateBtn);
    input.addEventListener('keydown', async (e) => {{
        if (e.key === 'Enter' && !e.shiftKey) {{
            e.preventDefault();
            if (canSend()) {{
                sendBtn.click();
            }}
        }}
    }});

    // File upload
    const fileInput = document.getElementById('file-input');
    const uploadBtn = document.getElementById('upload-btn');

    uploadBtn.addEventListener('click', async () => {{
        if (!fileInput.files.length) return;
        
        uploadBtn.disabled = true;
        uploadBtn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Envoi en cours...';
        
        try {{
            const fd = new FormData();
            fd.append('file', fileInput.files[0]);
            
            const response = await fetch('/upload', {{ method:'POST', body: fd }});
            if (response.ok) {{
                fileInput.value = '';
                loadMessages(); // Refresh messages to show the file link
            }} else {{
                alert('Erreur lors de l\\'upload du fichier');
            }}
        }} catch(e) {{
            console.error(e);
            alert('Erreur lors de l\\'upload du fichier');
        }} finally {{
            uploadBtn.disabled = false;
            uploadBtn.innerHTML = '<i class="bi bi-upload"></i> Uploader';
        }}
    }});

    // Render cached messages instantly if available
    try {{ 
        const cached = localStorage.getItem('chat_messages'); 
        if (cached) {{ renderMessages(JSON.parse(cached)); }} 
    }} catch (e) {{}}

    setInterval(loadMessages, 1500);
    loadMessages();
    updateBtn();
    </script>
    """)

def page_admin():
    return page_template("Admin", """
    <div class="row justify-content-center">
        <div class="col-12 col-lg-8">
            <div class="glass-card p-4 p-md-5">
                <div class="d-flex align-items-center justify-content-between mb-3">
                    <div class="d-flex align-items-center gap-2">
                        <span class="fs-3">🛠️</span>
                        <h3 class="brand-title mb-0">Panneau Admin</h3>
                    </div>
                    <form method="POST" action="/logout">
                        <button class="btn btn-outline-light"><i class="bi bi-box-arrow-right"></i> Déconnexion</button>
                    </form>
                </div>
                <form method="POST" action="/ban" class="mt-2">
                    <div class="row g-3 align-items-end">
                        <div class="col-12 col-md-6">
                            <label class="form-label">Utilisateur à bannir</label>
                            <input class="form-control form-control-lg" name="username" placeholder="Nom d'utilisateur">
                        </div>
                        <div class="col-6 col-md-3">
                            <label class="form-label">Durée (min)</label>
                            <input class="form-control form-control-lg" type="number" min="1" name="minutes" placeholder="10">
                        </div>
                        <div class="col-6 col-md-3">
                            <button class="btn btn-warning btn-lg w-100" type="submit"><i class="bi bi-slash-circle"></i> Bannir</button>
                        </div>
                    </div>
                    <p class="muted mt-3 mb-0">Astuce: un ban temporaire se lève automatiquement après la durée indiquée.</p>
                </form>
            </div>
        </div>
    </div>
    """)

# --- Utilitaires ---
def is_authenticated(headers):
    cookie = headers.get("Cookie")
    if cookie and "session=" in cookie:
        token = None
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session="):
                token = part.split("session=")[1]
                break
        if token:
            return sessions.get(token)
    return None

def make_session(username):
    token = uuid.uuid4().hex
    sessions[token] = username
    save_json(SESSIONS_FILE, sessions)
    return token

def parse_multipart_form_data(headers, body):
    """Parse multipart form data manually"""
    content_type = headers.get('Content-Type')
    if not content_type or 'multipart/form-data' not in content_type:
        return None
    
    # Extract boundary from content type
    boundary = content_type.split('boundary=')[1].encode()
    
    parts = body.split(b'--' + boundary)
    files = {}
    
    for part in parts:
        if not part or part == b'--\r\n':
            continue
            
        # Parse headers and content
        header_end = part.find(b'\r\n\r\n')
        if header_end == -1:
            continue
            
        headers_part = part[:header_end]
        content = part[header_end+4:-2]  # Remove \r\n at the end
        
        # Parse headers
        headers_dict = {}
        for header_line in headers_part.split(b'\r\n'):
            if b': ' in header_line:
                key, value = header_line.split(b': ', 1)
                headers_dict[key.decode().lower()] = value.decode()
        
        # Check if this part contains a file
        if 'content-disposition' in headers_dict:
            disposition = headers_dict['content-disposition']
            if 'filename=' in disposition:
                # Extract filename
                filename = disposition.split('filename="')[1].split('"')[0]
                files['file'] = {
                    'filename': filename,
                    'content': content
                }
            elif 'name=' in disposition:
                # Extract form field name
                name = disposition.split('name="')[1].split('"')[0]
                files[name] = content.decode()
    
    return files

# --- Serveur principal ---
class SimpleChatServer(BaseHTTPRequestHandler):
    def do_GET(self):
        username = is_authenticated(self.headers)
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self.respond(page_login())
        elif path == "/login":
            self.respond(page_login())
        elif path == "/register":
            self.respond(page_register())
        elif path == "/discussion":
            if username:
                # Check if user is banned
                if username in banned_users:
                    ban_expiry = banned_users[username]
                    if time.time() < ban_expiry:
                        remaining = int((ban_expiry - time.time()) / 60)
                        self.respond(page_template("Banni", f"""
                        <div class="text-center">
                            <h3 class="text-danger">⛔ Vous êtes banni temporairement</h3>
                            <p class="muted">Temps restant: {remaining} minutes</p>
                            <a href="/logout" class="btn btn-outline-light">Se déconnecter</a>
                        </div>
                        """))
                        return
                    else:
                        # Ban expired, remove it
                        del banned_users[username]
                        save_json(BANNED_FILE, banned_users)
                
                self.respond(page_discussion(username))
            else:
                self.redirect("/login")
        elif path == "/admin":
            if username == ADMIN_USER:
                self.respond(page_admin())
            else:
                self.redirect("/login")
        elif path == "/messages":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            # Return all messages (persisted)
            self.wfile.write(json.dumps(messages).encode())
        elif path == "/download":
            fid = query.get("fid", [None])[0]
            meta = next((f for f in files_meta if f.get("id") == fid), None)
            if not meta or not os.path.isfile(meta.get("disk_path", "")):
                self.send_error(404, "File not found")
                return
            self.send_response(200)
            mime, _ = mimetypes.guess_type(meta["filename"]) or ("application/octet-stream", None)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Disposition", f"attachment; filename=\"{meta['filename']}\"")
            self.end_headers()
            with open(meta["disk_path"], "rb") as f:
                self.wfile.write(f.read())
        elif path == "/export":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=messages.json")
            self.end_headers()
            self.wfile.write(json.dumps(messages).encode())
        elif path == "/logout":
            cookie = self.headers.get("Cookie")
            if cookie and "session=" in cookie:
                token = None
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("session="):
                        token = part.split("session=")[1]
                        break
                if token:
                    sessions.pop(token, None)
                    save_json(SESSIONS_FILE, sessions)
            self.send_response(302)
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.send_header("Location", "/login")
            self.end_headers()
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        username = is_authenticated(self.headers)

        # For simple form posts
        ctype = self.headers.get('Content-Type')
        if ctype and 'multipart/form-data' in ctype:
            # Handle file upload
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            params = parse_multipart_form_data(self.headers, body)
        else:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b''
            data = raw.decode(errors='ignore') if raw else ''
            params = parse_qs(data) if data else {}
            # Flatten params
            for key in params:
                if isinstance(params[key], list) and len(params[key]) == 1:
                    params[key] = params[key][0]

        if path == "/login":
            user = params.get("username", "")
            pwd = params.get("password", "")
            if (user == ADMIN_USER and pwd == ADMIN_PASS) or (user in users and users[user] == pwd):
                token = make_session(user)
                self.send_response(302)
                self.send_header("Set-Cookie", f"session={token}; Path=/")
                self.send_header("Location", "/admin" if user == ADMIN_USER else "/discussion")
                self.end_headers()
            else:
                self.respond(page_login("Identifiants invalides."))
        elif path == "/register":
            user = params.get("username", "")
            pwd = params.get("password", "")
            if user and pwd and user not in users:
                users[user] = pwd
                save_json(USERS_FILE, users)
                self.redirect("/login")
            else:
                self.respond(page_template("Erreur", "<p>Utilisateur déjà existant.</p>"))
        elif path == "/send" and username:
            msg = params.get("message", "")
            # Check if user is banned
            if username in banned_users and time.time() < banned_users[username]:
                self.send_response(403)
                self.end_headers()
                return
                
            if msg:
                messages.append({"user": username, "text": msg, "timestamp": time.time()})
                save_json(MESSAGES_FILE, messages)
            self.send_response(200)
            self.end_headers()
        elif path == "/ban" and username == ADMIN_USER:
            user = params.get("username", "")
            minutes = int(params.get("minutes", 0))
            if user in users:
                banned_users[user] = time.time() + minutes * 60
                save_json(BANNED_FILE, banned_users)
            self.redirect("/admin")
        elif path == "/logout":
            cookie = self.headers.get("Cookie")
            if cookie and "session=" in cookie:
                token = None
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("session="):
                        token = part.split("session=")[1]
                        break
                if token:
                    sessions.pop(token, None)
                    save_json(SESSIONS_FILE, sessions)
            self.send_response(302)
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            self.send_header("Location", "/login")
            self.end_headers()
        elif path == "/upload" and username:
            # Check if user is banned
            if username in banned_users and time.time() < banned_users[username]:
                self.send_response(403)
                self.end_headers()
                return
                
            if not params or 'file' not in params:
                self.send_error(400, "No file provided")
                return
                
            try:
                file_data = params['file']
                filename = file_data['filename']
                content = file_data['content']
                
                # Sauvegarder le fichier
                original_name = os.path.basename(filename)
                fid = uuid.uuid4().hex
                disk_name = f"{fid}__{original_name}"
                disk_path = os.path.join(UPLOAD_DIR, disk_name)
                
                # Créer le dossier uploads s'il n'existe pas
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                
                with open(disk_path, 'wb') as out:
                    out.write(content)
                
                size = os.path.getsize(disk_path)
                files_meta.append({
                    "id": fid,
                    "owner": username,
                    "filename": original_name,
                    "disk_path": disk_path,
                    "size": size,
                    "uploaded_at": time.time(),
                })
                save_json(FILES_META_FILE, files_meta)
                
                # Ajouter un message avec le lien du fichier dans le chat
                file_link = f"[FILE:{original_name}|{fid}]"
                messages.append({
                    "user": username, 
                    "text": f"a partagé un fichier: {file_link}", 
                    "timestamp": time.time()
                })
                save_json(MESSAGES_FILE, messages)
                
                self.send_response(200)
                self.end_headers()
                
            except Exception as e:
                print(f"Upload error: {e}")
                self.send_error(500, "Internal Server Error")
        else:
            self.send_error(404, "Not found")

    # --- helpers ---
    def respond(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

if __name__ == "__main__":
    if not should_server_run():
        print("Horaire du serveur (7h-22h). Arrêt du serveur.")
        exit(0)
    
    print(f"Server running at http://{HOST}:{PORT}/")
    server = HTTPServer((HOST, PORT), SimpleChatServer)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Server stopped by user")
    except Exception as e:
        print(f"Server error: {e}")
