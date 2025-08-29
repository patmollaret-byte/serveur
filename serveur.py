
import os
import socket
import threading
import hashlib
import json
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

class FileShareServer:
    def __init__(self, host='0.0.0.0', port=10000):
        self.host = host
        self.port = int(os.environ.get('PORT', port))
        self.clients = {}
        self.accounts_file = 'accounts.json'
        self.files_dir = 'shared_files'
        self.chat_history = []
        self.start_hour = 7  # 7h
        self.end_hour = 22   # 22h
        self.timezone_offset = 2  # UTC+2 (Heure d'été France)
        
        # Créer le répertoire pour les fichiers s'il n'existe pas
        if not os.path.exists(self.files_dir):
            os.makedirs(self.files_dir)
            
        # Charger les comptes existants
        self.accounts = self.load_accounts()
    
    def get_local_time(self):
        """Retourne l'heure locale avec le décalage de fuseau"""
        return datetime.utcnow() + timedelta(hours=self.timezone_offset)
    
    def is_within_time_window(self):
        """Vérifie si l'heure actuelle est dans la plage autorisée (7h-22h)"""
        current_hour = self.get_local_time().hour
        return self.start_hour <= current_hour < self.end_hour
    
    def load_accounts(self):
        if os.path.exists(self.accounts_file):
            try:
                with open(self.accounts_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_accounts(self):
        with open(self.accounts_file, 'w') as f:
            json.dump(self.accounts, f)
    
    def hash_password(self, password):
        return hashlib.sha256(password.encode()).hexdigest()
    
    def register_account(self, username, password):
        if username in self.accounts:
            return False, "Nom d'utilisateur déjà existant"
        
        self.accounts[username] = {
            'password': self.hash_password(password),
            'created_at': time.time()
        }
        self.save_accounts()
        return True, "Compte créé avec succès"
    
    def authenticate(self, username, password):
        if username not in self.accounts:
            return False, "Nom d'utilisateur incorrect"
        
        if self.accounts[username]['password'] != self.hash_password(password):
            return False, "Mot de passe incorrect"
        
        return True, "Authentification réussie"
    
    def handle_client(self, client_socket, address):
        print(f"[Nouvelle connexion] {address} connecté.")
        username = None
        authenticated = False
        
        try:
            while True:
                # Recevoir la commande du client
                request = client_socket.recv(1024).decode('utf-8')
                if not request:
                    break
                
                parts = request.split(' ', 2)
                command = parts[0]
                
                if command == 'REGISTER':
                    if len(parts) < 3:
                        response = "ERREUR Format: REGISTER username password"
                    else:
                        success, message = self.register_account(parts[1], parts[2])
                        if success:
                            response = f"SUCCES {message}"
                        else:
                            response = f"ERREUR {message}"
                
                elif command == 'LOGIN':
                    if len(parts) < 3:
                        response = "ERREUR Format: LOGIN username password"
                    else:
                        success, message = self.authenticate(parts[1], parts[2])
                        if success:
                            username = parts[1]
                            authenticated = True
                            self.clients[username] = client_socket
                            response = f"SUCCES {message}"
                            # Envoyer l'historique du chat
                            for msg in self.chat_history[-10:]:  # Les 10 derniers messages
                                try:
                                    client_socket.send(f"CHAT_HISTORY {msg}".encode('utf-8'))
                                    time.sleep(0.1)  # Petit délai pour éviter la saturation
                                except:
                                    break
                        else:
                            response = f"ERREUR {message}"
                
                elif command == 'LOGOUT':
                    if username in self.clients:
                        del self.clients[username]
                    authenticated = False
                    username = None
                    response = "SUCCES Déconnecté avec succès"
                
                elif command == 'UPLOAD':
                    if not authenticated:
                        response = "ERREUR Vous devez être connecté pour uploader un fichier"
                    else:
                        # Format: UPLOAD filename filesize filedata
                        if len(parts) < 3:
                            response = "ERREUR Format: UPLOAD filename filesize"
                        else:
                            filename = parts[1]
                            filesize = int(parts[2])
                            
                            # Recevoir les données du fichier
                            file_data = b''
                            while len(file_data) < filesize:
                                packet = client_socket.recv(4096)
                                if not packet:
                                    break
                                file_data += packet
                            
                            # Sauvegarder le fichier
                            filepath = os.path.join(self.files_dir, filename)
                            with open(filepath, 'wb') as f:
                                f.write(file_data)
                            
                            response = f"SUCCES Fichier {filename} uploadé avec succès"
                
                elif command == 'DOWNLOAD':
                    if not authenticated:
                        response = "ERREUR Vous devez être connecté pour télécharger un fichier"
                    else:
                        if len(parts) < 2:
                            response = "ERREUR Format: DOWNLOAD filename"
                        else:
                            filename = parts[1]
                            filepath = os.path.join(self.files_dir, filename)
                            
                            if os.path.exists(filepath):
                                filesize = os.path.getsize(filepath)
                                response = f"SUCCES {filesize}"
                                
                                # Envoyer le fichier
                                with open(filepath, 'rb') as f:
                                    while True:
                                        bytes_read = f.read(4096)
                                        if not bytes_read:
                                            break
                                        client_socket.sendall(bytes_read)
                            else:
                                response = f"ERREUR Fichier {filename} non trouvé"
                
                elif command == 'LIST':
                    if not authenticated:
                        response = "ERREUR Vous devez être connecté pour lister les fichiers"
                    else:
                        files = os.listdir(self.files_dir)
                        if not files:
                            response = "SUCCES Aucun fichier disponible"
                        else:
                            response = "SUCCES " + " ".join(files)
                
                elif command == 'CHAT':
                    if not authenticated:
                        response = "ERREUR Vous devez être connecté pour chatter"
                    else:
                        if len(parts) < 2:
                            response = "ERREUR Format: CHAT message"
                        else:
                            message = f"{username} [{self.get_local_time().strftime('%H:%M:%S')}]: {parts[1]}"
                            self.chat_history.append(message)
                            
                            # Diffuser le message à tous les clients connectés
                            for user, sock in self.clients.items():
                                try:
                                    sock.send(f"CHAT {message}".encode('utf-8'))
                                except:
                                    # En cas d'erreur, supprimer le client
                                    if user in self.clients:
                                        del self.clients[user]
                            
                            response = "SUCCES Message envoyé"
                
                else:
                    response = "ERREUR Commande non reconnue"
                
                # Envoyer la réponse
                client_socket.send(response.encode('utf-8'))
        
        except Exception as e:
            print(f"Erreur avec {address}: {e}")
        
        finally:
            if username and username in self.clients:
                del self.clients[username]
            client_socket.close()
            print(f"[Déconnexion] {address} déconnecté.")

class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            
            local_time = server.get_local_time()
            is_online = server.is_within_time_window()
            status_class = "online" if is_online else "offline"
            status_text = "En ligne" if is_online else "Hors service"
            
            # Calcul du temps jusqu'au prochain changement de statut
            if is_online:
                # Temps jusqu'à 22h
                next_change = local_time.replace(hour=22, minute=0, second=0, microsecond=0)
                if local_time.hour >= 22:
                    next_change += timedelta(days=1)
            else:
                # Temps jusqu'à 7h
                next_change = local_time.replace(hour=7, minute=0, second=0, microsecond=0)
                if local_time.hour >= 7:
                    next_change += timedelta(days=1)
            
            time_until_change = next_change - local_time
            hours, remainder = divmod(time_until_change.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            html = f"""
            <!DOCTYPE html>
            <html lang="fr">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Serveur de Partage de Fichiers</title>
                <style>
                    :root {{
                        --primary: #4361ee;
                        --success: #4cc9f0;
                        --danger: #f72585;
                        --warning: #fca311;
                        --dark: #14213d;
                        --light: #f8f9fa;
                    }}
                    
                    * {{
                        margin: 0;
                        padding: 0;
                        box-sizing: border-box;
                    }}
                    
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: #333;
                        min-height: 100vh;
                        padding: 20px;
                    }}
                    
                    .container {{
                        max-width: 1000px;
                        margin: 0 auto;
                        background: rgba(255, 255, 255, 0.95);
                        border-radius: 15px;
                        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
                        overflow: hidden;
                    }}
                    
                    header {{
                        background: var(--primary);
                        color: white;
                        padding: 2rem;
                        text-align: center;
                    }}
                    
                    h1 {{
                        font-size: 2.5rem;
                        margin-bottom: 0.5rem;
                    }}
                    
                    .subtitle {{
                        font-size: 1.2rem;
                        opacity: 0.9;
                    }}
                    
                    .content {{
                        padding: 2rem;
                    }}
                    
                    .status-container {{
                        display: flex;
                        justify-content: center;
                        margin-bottom: 2rem;
                    }}
                    
                    .status {{
                        padding: 1rem 2rem;
                        border-radius: 50px;
                        font-weight: bold;
                        text-align: center;
                        display: inline-flex;
                        align-items: center;
                        gap: 10px;
                    }}
                    
                    .status.online {{
                        background: #d4edda;
                        color: #155724;
                        box-shadow: 0 4px 15px rgba(76, 201, 240, 0.3);
                    }}
                    
                    .status.offline {{
                        background: #f8d7da;
                        color: #721c24;
                        box-shadow: 0 4px 15px rgba(247, 37, 133, 0.3);
                    }}
                    
                    .status-dot {{
                        width: 12px;
                        height: 12px;
                        border-radius: 50%;
                        display: inline-block;
                    }}
                    
                    .online .status-dot {{
                        background: #28a745;
                        box-shadow: 0 0 10px #28a745;
                    }}
                    
                    .offline .status-dot {{
                        background: #dc3545;
                        box-shadow: 0 0 10px #dc3545;
                    }}
                    
                    .info-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                        gap: 1.5rem;
                        margin-bottom: 2rem;
                    }}
                    
                    .info-card {{
                        background: white;
                        padding: 1.5rem;
                        border-radius: 10px;
                        box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
                        border-left: 4px solid var(--primary);
                    }}
                    
                    .info-card h3 {{
                        color: var(--primary);
                        margin-bottom: 1rem;
                        display: flex;
                        align-items: center;
                        gap: 10px;
                    }}
                    
                    .info-card p {{
                        margin-bottom: 0.5rem;
                        line-height: 1.6;
                    }}
                    
                    .highlight {{
                        font-weight: bold;
                        color: var(--primary);
                    }}
                    
                    .commands {{
                        background: var(--light);
                        padding: 1.5rem;
                        border-radius: 10px;
                        margin-top: 2rem;
                    }}
                    
                    .commands h2 {{
                        color: var(--dark);
                        margin-bottom: 1rem;
                        text-align: center;
                    }}
                    
                    .command-list {{
                        list-style: none;
                    }}
                    
                    .command-list li {{
                        background: white;
                        margin-bottom: 0.5rem;
                        padding: 1rem;
                        border-radius: 8px;
                        border-left: 3px solid var(--success);
                        font-family: 'Courier New', monospace;
                    }}
                    
                    .countdown {{
                        text-align: center;
                        margin-top: 1rem;
                        font-size: 1.1rem;
                        color: var(--dark);
                    }}
                    
                    footer {{
                        text-align: center;
                        padding: 1.5rem;
                        background: var(--dark);
                        color: white;
                        margin-top: 2rem;
                    }}
                    
                    @media (max-width: 768px) {{
                        .info-grid {{
                            grid-template-columns: 1fr;
                        }}
                        
                        h1 {{
                            font-size: 2rem;
                        }}
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <header>
                        <h1>📁 Serveur de Partage de Fichiers</h1>
                        <p class="subtitle">Partagez, discutez, collaborez</p>
                    </header>
                    
                    <div class="content">
                        <div class="status-container">
                            <div class="status {status_class}">
                                <span class="status-dot"></span>
                                {status_text}
                            </div>
                        </div>
                        
                        <div class="info-grid">
                            <div class="info-card">
                                <h3>🕐 Horaires de service</h3>
                                <p><span class="highlight">Ouverture:</span> 7h00</p>
                                <p><span class="highlight">Fermeture:</span> 22h00</p>
                                <p><span class="highlight">Fuseau horaire:</span> UTC+2 (Paris)</p>
                            </div>
                            
                            <div class="info-card">
                                <h3>📊 Statut actuel</h3>
                                <p><span class="highlight">Heure locale:</span> {local_time.strftime('%H:%M:%S')}</p>
                                <p><span class="highlight">Date:</span> {local_time.strftime('%d/%m/%Y')}</p>
                                <p><span class="highlight">Port service:</span> {server.port}</p>
                            </div>
                            
                            <div class="info-card">
                                <h3>📞 Contact</h3>
                                <p>Le serveur est automatiquement géré</p>
                                <p>Fonctionne de 7h à 22h</p>
                                <p>Reconnexion automatique chaque matin</p>
                            </div>
                        </div>
                        
                        <div class="countdown">
                            <p>Prochain changement de statut dans: {hours}h {minutes}m {seconds}s</p>
                        </div>
                        
                        <div class="commands">
                            <h2>💻 Commandes disponibles</h2>
                            <ul class="command-list">
                                <li><code>register [username] [password]</code> - Créer un compte</li>
                                <li><code>login [username] [password]</code> - Se connecter</li>
                                <li><code>upload [filename]</code> - Uploader un fichier</li>
                                <li><code>download [filename]</code> - Télécharger un fichier</li>
                                <li><code>list</code> - Lister les fichiers disponibles</li>
                                <li><code>chat [message]</code> - Envoyer un message</li>
                                <li><code>logout</code> - Se déconnecter</li>
                            </ul>
                        </div>
                    </div>
                    
                    <footer>
                        <p>Serveur de partage de fichiers avec chat | © 2023</p>
                    </footer>
                </div>
                
                <script>
                    // Mise à jour du compte à rebours
                    function updateCountdown() {{
                        const countdownElement = document.querySelector('.countdown p');
                        if (countdownElement) {{
                            const text = countdownElement.textContent;
                            const regex = /(\\d+)h (\\d+)m (\\d+)s/;
                            const match = text.match(regex);
                            
                            if (match) {{
                                let hours = parseInt(match[1]);
                                let minutes = parseInt(match[2]);
                                let seconds = parseInt(match[3]);
                                
                                seconds--;
                                if (seconds < 0) {{
                                    seconds = 59;
                                    minutes--;
                                    if (minutes < 0) {{
                                        minutes = 59;
                                        hours--;
                                        if (hours < 0) {{
                                            // Recharger la page quand le temps est écoulé
                                            location.reload();
                                            return;
                                        }}
                                    }}
                                }}
                                
                                countdownElement.textContent = 
                                    `Prochain changement de statut dans: ${{hours}}h ${{minutes}}m ${{seconds}}s`;
                            }}
                        }}
                    }}
                    
                    // Mettre à jour le compte à rebours chaque seconde
                    setInterval(updateCountdown, 1000);
                </script>
            </body>
            </html>
            """
            
            self.wfile.write(html.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write('Page non trouvée'.encode('utf-8'))

def start_web_server():
    """Démarre le serveur web HTTP sur le port 80/443"""
    web_port = 80
    if 'RENDER' in os.environ:
        web_port = int(os.environ.get('PORT', 10000))
    
    web_server = HTTPServer(('0.0.0.0', web_port), WebHandler)
    print(f"[Web] Interface web démarrée sur le port {web_port}")
    print(f"[Web] Accès: http://localhost:{web_port}")
    web_server.serve_forever()

def start_file_server():
    """Démarre le serveur de fichiers sur le port spécifié"""
    # Vérifier si on est dans la plage horaire autorisée
    if not server.is_within_time_window():
        current_time = server.get_local_time()
        print(f"[Arrêt] Le serveur n'est pas autorisé à fonctionner en dehors de 7h-22h.")
        print(f"[Arrêt] Heure actuelle: {current_time.strftime('%H:%M:%S')}")
        print("Le serveur s'arrête.")
        return
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((server.host, server.port))
    server_socket.listen(5)
    
    current_time = server.get_local_time()
    print(f"[Démarrage] Serveur de fichiers démarré sur {server.host}:{server.port}")
    print(f"[Plage horaire] Le serveur fonctionne de {server.start_hour}h à {server.end_hour}h")
    print(f"[Fuseau horaire] UTC+{server.timezone_offset} (Heure de Paris)")
    print(f"[Heure actuelle] {current_time.strftime('%d/%m/%Y %H:%M:%S')}")
    
    try:
        while True:
            # Vérifier à nouveau la plage horaire avant d'accepter de nouvelles connexions
            if not server.is_within_time_window():
                current_time = server.get_local_time()
                print(f"[Arrêt] Heure actuelle: {current_time.strftime('%H:%M:%S')}")
                print("[Arrêt] Le serveur s'arrête car hors de la plage 7h-22h")
                break
            
            client_socket, address = server_socket.accept()
            client_thread = threading.Thread(target=server.handle_client, args=(client_socket, address))
            client_thread.daemon = True
            client_thread.start()
            
    except KeyboardInterrupt:
        print("\n[Arrêt] Arrêt du serveur...")
    except Exception as e:
        print(f"\n[Erreur] {e}")
    finally:
        server_socket.close()
        print("Serveur de fichiers arrêté.")

if __name__ == "__main__":
    server = FileShareServer()
    
    # Démarrer le serveur web dans un thread séparé
    web_thread = threading.Thread(target=start_web_server)
    web_thread.daemon = True
    web_thread.start()
    
    # Démarrer le serveur de fichiers dans le thread principal
    start_file_server()
