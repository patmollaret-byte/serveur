import os
import socket
import threading
import hashlib
import json
import time
from datetime import datetime
import sys

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
        
        # Créer le répertoire pour les fichiers s'il n'existe pas
        if not os.path.exists(self.files_dir):
            os.makedirs(self.files_dir)
            
        # Charger les comptes existants
        self.accounts = self.load_accounts()
    
    def is_within_time_window(self):
        """Vérifie si l'heure actuelle est dans la plage autorisée (7h-22h)"""
        current_hour = datetime.now().hour
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
                            message = f"{username} [{datetime.now().strftime('%H:%M:%S')}]: {parts[1]}"
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
    
    def start(self):
        # Vérifier si on est dans la plage horaire autorisée
        if not self.is_within_time_window():
            current_hour = datetime.now().hour
            print(f"[Arrêt] Le serveur n'est pas autorisé à fonctionner en dehors de 7h-22h. Heure actuelle: {current_hour}h")
            print("Le serveur s'arrête.")
            return
        
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.host, self.port))
        server_socket.listen(5)
        
        print(f"[Démarrage] Serveur démarré sur {self.host}:{self.port}")
        print(f"[Plage horaire] Le serveur fonctionne de {self.start_hour}h à {self.end_hour}h")
        print(f"[Heure actuelle] {datetime.now().strftime('%H:%M:%S')}")
        
        try:
            while True:
                # Vérifier à nouveau la plage horaire avant d'accepter de nouvelles connexions
                if not self.is_within_time_window():
                    current_hour = datetime.now().hour
                    print(f"[Arrêt] Heure actuelle: {current_hour}h - Le serveur s'arrête car hors de la plage 7h-22h")
                    break
                
                client_socket, address = server_socket.accept()
                client_thread = threading.Thread(target=self.handle_client, args=(client_socket, address))
                client_thread.daemon = True
                client_thread.start()
                
        except KeyboardInterrupt:
            print("\n[Arrêt] Arrêt du serveur...")
        except Exception as e:
            print(f"\n[Erreur] {e}")
        finally:
            server_socket.close()
            print("Serveur arrêté.")

if __name__ == "__main__":
    server = FileShareServer()
    server.start()
