import os
import secrets
import sqlite3
import hashlib
import subprocess
import threading
import time
import shutil
import git
import json
from datetime import datetime, timedelta
from flask import Flask, send_from_directory, jsonify, request, redirect
from flask_cors import CORS

app = Flask(__name__, static_folder='frontend', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Configuración
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOTS_DIR = os.path.join(BASE_DIR, 'bots')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(BOTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Variables de entorno para GitHub OAuth
GITHUB_CLIENT_ID = os.environ.get('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.environ.get('GITHUB_CLIENT_SECRET', '')
GITHUB_REDIRECT_URI = os.environ.get('GITHUB_REDIRECT_URI', 'https://tu-app.onrender.com/auth/github/callback')

# ==================== BASE DE DATOS ====================
class Database:
    def __init__(self):
        self.db_path = os.path.join(BASE_DIR, 'bots.db')
        self.init_db()
    
    def get_conn(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        conn = self.get_conn()
        c = conn.cursor()
        
        # Usuarios
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT,
                github_token TEXT,
                github_username TEXT,
                plan TEXT DEFAULT 'free',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Bots
        c.execute('''
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                repo_name TEXT,
                directory TEXT NOT NULL,
                status TEXT DEFAULT 'stopped',
                pid INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        # Sesiones
        c.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def hash_password(self, pwd):
        return hashlib.sha256(pwd.encode()).hexdigest()
    
    def register_user(self, username, password, email=None):
        conn = self.get_conn()
        c = conn.cursor()
        try:
            hashed = self.hash_password(password)
            c.execute('INSERT INTO users (username, password, email) VALUES (?, ?, ?)',
                     (username, hashed, email))
            conn.commit()
            user_id = c.lastrowid
            conn.close()
            return self.get_user(user_id)
        except sqlite3.IntegrityError:
            conn.close()
            return None
    
    def verify_user(self, username, password):
        hashed = self.hash_password(password)
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, hashed))
        user = c.fetchone()
        conn.close()
        return self._user_to_dict(user) if user else None
    
    def get_user(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user = c.fetchone()
        conn.close()
        return self._user_to_dict(user) if user else None
    
    def get_user_by_token(self, token):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT user_id FROM sessions WHERE token = ? AND expires_at > CURRENT_TIMESTAMP', (token,))
        session = c.fetchone()
        conn.close()
        if session:
            return self.get_user(session[0])
        return None
    
    def _user_to_dict(self, user):
        return {
            'id': user[0],
            'username': user[1],
            'email': user[3],
            'github_token': user[4],
            'github_username': user[5],
            'plan': user[6],
            'created_at': user[7]
        }
    
    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = datetime.now() + timedelta(days=7)
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)', 
                 (token, user_id, expires))
        conn.commit()
        conn.close()
        return token
    
    def delete_session(self, token):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM sessions WHERE token = ?', (token,))
        conn.commit()
        conn.close()
    
    def save_github_token(self, user_id, token, username):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('UPDATE users SET github_token = ?, github_username = ? WHERE id = ?', 
                 (token, username, user_id))
        conn.commit()
        conn.close()
    
    def create_bot(self, user_id, name, repo_url, repo_name, directory):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            INSERT INTO bots (user_id, name, repo_url, repo_name, directory, status) 
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, name, repo_url, repo_name, directory, 'running'))
        conn.commit()
        bot_id = c.lastrowid
        conn.close()
        return self.get_bot(bot_id)
    
    def get_bot(self, bot_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM bots WHERE id = ?', (bot_id,))
        bot = c.fetchone()
        conn.close()
        if bot:
            return {
                'id': bot[0],
                'user_id': bot[1],
                'name': bot[2],
                'repo_url': bot[3],
                'repo_name': bot[4],
                'directory': bot[5],
                'status': bot[6],
                'pid': bot[7],
                'created_at': bot[8]
            }
        return None
    
    def get_user_bots(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM bots WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
        bots = c.fetchall()
        conn.close()
        return [{
            'id': b[0],
            'name': b[2],
            'repo_url': b[3],
            'repo_name': b[4],
            'status': b[6],
            'created_at': b[8]
        } for b in bots]
    
    def update_bot_status(self, bot_id, status, pid=None):
        conn = self.get_conn()
        c = conn.cursor()
        if pid:
            c.execute('UPDATE bots SET status = ?, pid = ? WHERE id = ?', (status, pid, bot_id))
        else:
            c.execute('UPDATE bots SET status = ? WHERE id = ?', (status, bot_id))
        conn.commit()
        conn.close()
    
    def delete_bot(self, bot_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM bots WHERE id = ?', (bot_id,))
        conn.commit()
        conn.close()

db = Database()

# ==================== GESTOR DE BOTS ====================
class BotManager:
    def __init__(self):
        self.processes = {}
        self.logs_cache = {}
    
    def find_main_file(self, bot_dir):
        """Encuentra el archivo principal del bot"""
        # Archivos comunes de bots de Telegram
        main_candidates = [
            'main.py', 'bot.py', 'app.py', 'run.py',
            'telegram_bot.py', 'tg_bot.py', 'bot_main.py'
        ]
        
        for candidate in main_candidates:
            path = os.path.join(bot_dir, candidate)
            if os.path.isfile(path):
                return candidate
        
        # Buscar cualquier .py que tenga indicios de ser un bot de Telegram
        for file in os.listdir(bot_dir):
            if file.endswith('.py'):
                try:
                    with open(os.path.join(bot_dir, file), 'r', encoding='utf-8') as f:
                        content = f.read()
                        # Buscar patrones de bots de Telegram
                        if any(keyword in content.lower() for keyword in [
                            'telegram', 'bot', 'updater', 'dispatcher', 
                            'messagehandler', 'callbackqueryhandler'
                        ]):
                            return file
                except:
                    continue
        
        return None
    
    def deploy_bot(self, user_id, repo_url, bot_name):
        """Despliega un bot desde GitHub"""
        # Crear directorio único
        timestamp = int(time.time())
        safe_name = ''.join(c for c in bot_name if c.isalnum() or c in '_-')
        bot_dir = os.path.join(BOTS_DIR, f'user_{user_id}_{safe_name}_{timestamp}')
        
        try:
            # 1. Clonar repositorio
            print(f"📦 Clonando: {repo_url}")
            repo = git.Repo.clone_from(repo_url, bot_dir, depth=1)
            
            # Obtener nombre del repo
            repo_name = repo_url.split('/')[-1].replace('.git', '')
            
            # 2. Buscar archivo principal
            main_file = self.find_main_file(bot_dir)
            if not main_file:
                shutil.rmtree(bot_dir)
                return None, "No se encontró un archivo principal (main.py, bot.py, etc.)"
            
            print(f"📄 Archivo principal: {main_file}")
            
            # 3. Instalar dependencias
            requirements = os.path.join(bot_dir, 'requirements.txt')
            if os.path.exists(requirements):
                print("📦 Instalando dependencias...")
                result = subprocess.run(
                    ['pip', 'install', '-r', 'requirements.txt', '--quiet'],
                    cwd=bot_dir,
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    print(f"⚠️ Error en dependencias: {result.stderr[:200]}")
            
            # 4. Guardar en DB
            bot = db.create_bot(user_id, bot_name, repo_url, repo_name, bot_dir)
            
            # 5. Iniciar el bot
            success = self.start_bot(bot['id'], bot_dir, main_file)
            
            if success:
                print(f"✅ Bot {bot_name} desplegado correctamente")
                return bot, None
            else:
                db.delete_bot(bot['id'])
                shutil.rmtree(bot_dir)
                return None, "Error al iniciar el bot"
                
        except git.exc.GitCommandError as e:
            if os.path.exists(bot_dir):
                shutil.rmtree(bot_dir)
            return None, f"Error clonando repositorio: {str(e)[:100]}"
        except Exception as e:
            if os.path.exists(bot_dir):
                shutil.rmtree(bot_dir)
            return None, f"Error: {str(e)[:100]}"
    
    def start_bot(self, bot_id, bot_dir, main_file):
        """Inicia el proceso del bot"""
        try:
            log_file = os.path.join(LOGS_DIR, f'bot_{bot_id}.log')
            
            # Iniciar proceso
            process = subprocess.Popen(
                ['python', main_file],
                cwd=bot_dir,
                stdout=open(log_file, 'a'),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )
            
            self.processes[bot_id] = process
            db.update_bot_status(bot_id, 'running', process.pid)
            
            # Monitorear proceso
            self._monitor_process(bot_id, process)
            
            print(f"🚀 Bot {bot_id} iniciado (PID: {process.pid})")
            return True
            
        except Exception as e:
            print(f"❌ Error iniciando bot {bot_id}: {e}")
            return False
    
    def _monitor_process(self, bot_id, process):
        """Monitorea el proceso en un hilo separado"""
        def monitor():
            try:
                process.wait()
                # El proceso terminó
                db.update_bot_status(bot_id, 'stopped')
                if bot_id in self.processes:
                    del self.processes[bot_id]
                print(f"🛑 Bot {bot_id} se detuvo")
            except Exception as e:
                print(f"⚠️ Error monitoreando bot {bot_id}: {e}")
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
    
    def get_logs(self, bot_id, lines=100):
        """Obtiene los logs del bot"""
        log_file = os.path.join(LOGS_DIR, f'bot_{bot_id}.log')
        
        if not os.path.exists(log_file):
            return []
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
                return all_lines[-lines:]
        except Exception as e:
            return [f"Error leyendo logs: {e}"]
    
    def restart_bot(self, bot_id):
        """Reinicia un bot"""
        bot = db.get_bot(bot_id)
        if not bot:
            return False
        
        # Detener
        if bot_id in self.processes:
            try:
                self.processes[bot_id].terminate()
                self.processes[bot_id].wait(timeout=5)
            except:
                self.processes[bot_id].kill()
            del self.processes[bot_id]
        
        time.sleep(2)
        
        # Iniciar de nuevo
        main_file = self.find_main_file(bot['directory'])
        if not main_file:
            return False
        
        return self.start_bot(bot_id, bot['directory'], main_file)
    
    def stop_bot(self, bot_id):
        """Detiene un bot"""
        if bot_id in self.processes:
            try:
                self.processes[bot_id].terminate()
                self.processes[bot_id].wait(timeout=5)
                del self.processes[bot_id]
            except:
                self.processes[bot_id].kill()
                del self.processes[bot_id]
        
        db.update_bot_status(bot_id, 'stopped')
        return True
    
    def delete_bot(self, bot_id):
        """Elimina un bot completamente"""
        bot = db.get_bot(bot_id)
        if not bot:
            return False
        
        # Detener proceso
        self.stop_bot(bot_id)
        
        # Eliminar directorio
        if os.path.exists(bot['directory']):
            shutil.rmtree(bot['directory'])
        
        # Eliminar logs
        log_file = os.path.join(LOGS_DIR, f'bot_{bot_id}.log')
        if os.path.exists(log_file):
            os.remove(log_file)
        
        # Eliminar de DB
        db.delete_bot(bot_id)
        
        print(f"🗑️ Bot {bot_id} eliminado")
        return True

bot_manager = BotManager()

# ==================== API ENDPOINTS ====================
def auth_required(f):
    """Decorador para verificar autenticación"""
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'No autorizado'}), 401
        
        user = db.get_user_by_token(token)
        if not user:
            return jsonify({'error': 'Sesión inválida'}), 401
        
        request.user = user
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated

@app.route('/')
def index():
    return send_from_directory('frontend', 'index.html')

@app.route('/login')
def login_page():
    return send_from_directory('frontend', 'login.html')

@app.route('/dashboard')
def dashboard_page():
    return send_from_directory('frontend', 'dashboard.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('frontend', path)

# API de autenticación
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    
    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña requeridos'}), 400
    
    user = db.register_user(username, password, email)
    if user:
        token = db.create_session(user['id'])
        return jsonify({'token': token, 'user': user}), 201
    return jsonify({'error': 'Usuario ya existe'}), 400

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    user = db.verify_user(username, password)
    if user:
        token = db.create_session(user['id'])
        return jsonify({'token': token, 'user': user}), 200
    return jsonify({'error': 'Credenciales inválidas'}), 401

@app.route('/api/verify', methods=['POST'])
def api_verify():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'No token'}), 401
    
    user = db.get_user_by_token(token)
    if user:
        return jsonify({'user': user}), 200
    return jsonify({'error': 'Token inválido'}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    token = request.headers.get('Authorization')
    if token:
        db.delete_session(token)
    return jsonify({'message': 'OK'}), 200

# API de bots
@app.route('/api/bots', methods=['GET'])
@auth_required
def api_get_bots():
    bots = db.get_user_bots(request.user['id'])
    return jsonify(bots), 200

@app.route('/api/bots/deploy', methods=['POST'])
@auth_required
def api_deploy_bot():
    data = request.json
    repo_url = data.get('repo_url')
    bot_name = data.get('name')
    
    if not repo_url or not bot_name:
        return jsonify({'error': 'URL y nombre requeridos'}), 400
    
    # Límite de 5 bots para plan free
    if request.user['plan'] == 'free' and len(db.get_user_bots(request.user['id'])) >= 5:
        return jsonify({'error': 'Límite de 5 bots alcanzado'}), 403
    
    bot, error = bot_manager.deploy_bot(request.user['id'], repo_url, bot_name)
    
    if bot:
        return jsonify(bot), 201
    return jsonify({'error': error}), 500

@app.route('/api/bots/<int:bot_id>/logs', methods=['GET'])
@auth_required
def api_get_logs(bot_id):
    logs = bot_manager.get_logs(bot_id, lines=200)
    return jsonify({'logs': logs}), 200

@app.route('/api/bots/<int:bot_id>/restart', methods=['POST'])
@auth_required
def api_restart_bot(bot_id):
    success = bot_manager.restart_bot(bot_id)
    if success:
        return jsonify({'message': 'Bot reiniciado'}), 200
    return jsonify({'error': 'Error al reiniciar'}), 500

@app.route('/api/bots/<int:bot_id>', methods=['DELETE'])
@auth_required
def api_delete_bot(bot_id):
    success = bot_manager.delete_bot(bot_id)
    if success:
        return jsonify({'message': 'Bot eliminado'}), 200
    return jsonify({'error': 'Error al eliminar'}), 500

# API de GitHub
@app.route('/api/github/repos', methods=['GET'])
@auth_required
def api_github_repos():
    if not request.user.get('github_token'):
        return jsonify({'error': 'GitHub no conectado'}), 401
    
    import requests
    headers = {'Authorization': f'token {request.user["github_token"]}'}
    response = requests.get('https://api.github.com/user/repos', headers=headers, params={'per_page': 100})
    
    if response.status_code == 200:
        repos = response.json()
        # Filtrar y formatear
        formatted = [{
            'name': r['name'],
            'full_name': r['full_name'],
            'clone_url': r['clone_url'],
            'private': r['private'],
            'description': r['description'][:100] if r['description'] else ''
        } for r in repos]
        return jsonify(formatted), 200
    
    return jsonify({'error': 'Error obteniendo repositorios'}), 500

@app.route('/auth/github')
def github_auth():
    return redirect(f'https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&redirect_uri={GITHUB_REDIRECT_URI}&scope=repo')

@app.route('/auth/github/callback')
def github_callback():
    code = request.args.get('code')
    if not code:
        return redirect('/dashboard?error=github_auth_failed')
    
    import requests
    
    # Obtener token
    response = requests.post('https://github.com/login/oauth/access_token',
                            data={
                                'client_id': GITHUB_CLIENT_ID,
                                'client_secret': GITHUB_CLIENT_SECRET,
                                'code': code
                            },
                            headers={'Accept': 'application/json'})
    
    if response.status_code == 200:
        data = response.json()
        github_token = data.get('access_token')
        
        if github_token:
            # Obtener usuario de GitHub
            user_response = requests.get('https://api.github.com/user',
                                        headers={'Authorization': f'token {github_token}'})
            
            if user_response.status_code == 200:
                github_user = user_response.json()
                
                # Por simplicidad, redirigir al dashboard
                # En producción deberías asociar el token al usuario autenticado
                return redirect('/dashboard?github_connected=true')
    
    return redirect('/dashboard?error=github_auth_failed')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)