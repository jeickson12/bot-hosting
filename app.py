import os
import secrets
import sqlite3
import hashlib
import subprocess
import threading
import time
import shutil
import git
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from flask import Flask, send_from_directory, jsonify, request, redirect, session
from flask_cors import CORS

app = Flask(__name__, static_folder='frontend')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = False  # True en producción con HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
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
GITHUB_REDIRECT_URI = os.environ.get('GITHUB_REDIRECT_URI', '')
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# ==================== BASE DE DATOS ====================
class Database:
    def __init__(self):
        self.init_db()
    
    def get_conn(self):
        return psycopg2.connect(DATABASE_URL)
    
    def init_db(self):
        conn = self.get_conn()
        c = conn.cursor()
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT,
                github_token TEXT,
                github_username TEXT,
                plan TEXT DEFAULT 'free',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS bots (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                repo_name TEXT,
                directory TEXT NOT NULL,
                status TEXT DEFAULT 'stopped',
                pid INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        c.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Base de datos PostgreSQL inicializada")
    
    def hash_password(self, pwd):
        return hashlib.sha256(pwd.encode()).hexdigest()
    
    def register_user(self, username, password, email=None):
        conn = self.get_conn()
        c = conn.cursor()
        try:
            hashed = self.hash_password(password)
            c.execute('INSERT INTO users (username, password, email) VALUES (%s, %s, %s) RETURNING id',
                     (username, hashed, email))
            user_id = c.fetchone()[0]
            conn.commit()
            conn.close()
            return self.get_user(user_id)
        except Exception as e:
            print(f"Error registrando: {e}")
            conn.close()
            return None
    
    def verify_user(self, username, password):
        hashed = self.hash_password(password)
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, hashed))
        user = c.fetchone()
        conn.close()
        return self._user_to_dict(user) if user else None
    
    def get_user(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE id = %s', (user_id,))
        user = c.fetchone()
        conn.close()
        return self._user_to_dict(user) if user else None
    
    def get_user_by_token(self, token):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT user_id FROM sessions WHERE token = %s AND expires_at > CURRENT_TIMESTAMP', (token,))
        session_row = c.fetchone()
        conn.close()
        if session_row:
            return self.get_user(session_row[0])
        return None
    
    def _user_to_dict(self, user):
        if not user:
            return None
        return {
            'id': user[0],
            'username': user[1],
            'email': user[3],
            'github_token': user[4],
            'github_username': user[5],
            'plan': user[6],
            'created_at': user[7].isoformat() if user[7] else None
        }
    
    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = datetime.now() + timedelta(days=7)
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)', 
                 (token, user_id, expires))
        conn.commit()
        conn.close()
        return token
    
    def delete_session(self, token):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM sessions WHERE token = %s', (token,))
        conn.commit()
        conn.close()
    
    def save_github_token(self, user_id, token, username):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('UPDATE users SET github_token = %s, github_username = %s WHERE id = %s', 
                 (token, username, user_id))
        conn.commit()
        conn.close()
        return self.get_user(user_id)
    
    def create_bot(self, user_id, name, repo_url, repo_name, directory):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            INSERT INTO bots (user_id, name, repo_url, repo_name, directory, status) 
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        ''', (user_id, name, repo_url, repo_name, directory, 'running'))
        bot_id = c.fetchone()[0]
        conn.commit()
        conn.close()
        return self.get_bot(bot_id)
    
    def get_bot(self, bot_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM bots WHERE id = %s', (bot_id,))
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
                'created_at': bot[8].isoformat() if bot[8] else None
            }
        return None
    
    def get_user_bots(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM bots WHERE user_id = %s ORDER BY created_at DESC', (user_id,))
        bots = c.fetchall()
        conn.close()
        return [{
            'id': b[0],
            'name': b[2],
            'repo_url': b[3],
            'repo_name': b[4],
            'status': b[6],
            'created_at': b[8].isoformat() if b[8] else None
        } for b in bots]
    
    def update_bot_status(self, bot_id, status, pid=None):
        conn = self.get_conn()
        c = conn.cursor()
        if pid:
            c.execute('UPDATE bots SET status = %s, pid = %s WHERE id = %s', (status, pid, bot_id))
        else:
            c.execute('UPDATE bots SET status = %s WHERE id = %s', (status, bot_id))
        conn.commit()
        conn.close()
    
    def delete_bot(self, bot_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM bots WHERE id = %s', (bot_id,))
        conn.commit()
        conn.close()
db = Database()
print("✅ Base de datos PostgreSQL conectada")

# ==================== GESTOR DE BOTS ====================
class BotManager:
    def __init__(self):
        self.processes = {}
    
    def find_main_file(self, bot_dir):
        main_candidates = ['main.py', 'bot.py', 'app.py', 'run.py', 'telegram_bot.py', 'tg_bot.py']
        
        for candidate in main_candidates:
            path = os.path.join(bot_dir, candidate)
            if os.path.isfile(path):
                return candidate
        
        for file in os.listdir(bot_dir):
            if file.endswith('.py'):
                try:
                    with open(os.path.join(bot_dir, file), 'r', encoding='utf-8') as f:
                        content = f.read()
                        if any(keyword in content.lower() for keyword in ['telegram', 'bot', 'updater']):
                            return file
                except:
                    continue
        return None
    
    def deploy_bot(self, user_id, repo_url, bot_name):
        timestamp = int(time.time())
        safe_name = ''.join(c for c in bot_name if c.isalnum() or c in '_-')
        bot_dir = os.path.join(BOTS_DIR, f'user_{user_id}_{safe_name}_{timestamp}')
        
        try:
            print(f"📦 Clonando: {repo_url}")
            repo = git.Repo.clone_from(repo_url, bot_dir, depth=1)
            repo_name = repo_url.split('/')[-1].replace('.git', '')
            
            main_file = self.find_main_file(bot_dir)
            if not main_file:
                shutil.rmtree(bot_dir)
                return None, "No se encontró un archivo principal (main.py, bot.py, etc.)"
            
            print(f"📄 Archivo principal: {main_file}")
            
            requirements = os.path.join(bot_dir, 'requirements.txt')
            if os.path.exists(requirements):
                print("📦 Instalando dependencias...")
                subprocess.run(['pip', 'install', '-r', 'requirements.txt', '--quiet'], cwd=bot_dir)
            
            bot = db.create_bot(user_id, bot_name, repo_url, repo_name, bot_dir)
            success = self.start_bot(bot['id'], bot_dir, main_file)
            
            if success:
                print(f"✅ Bot {bot_name} desplegado correctamente")
                return bot, None
            else:
                db.delete_bot(bot['id'])
                shutil.rmtree(bot_dir)
                return None, "Error al iniciar el bot"
                
        except Exception as e:
            if os.path.exists(bot_dir):
                shutil.rmtree(bot_dir)
            return None, f"Error: {str(e)[:100]}"
    
    def start_bot(self, bot_id, bot_dir, main_file):
        try:
            log_file = os.path.join(LOGS_DIR, f'bot_{bot_id}.log')
            
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
            self._monitor_process(bot_id, process)
            
            print(f"🚀 Bot {bot_id} iniciado (PID: {process.pid})")
            return True
        except Exception as e:
            print(f"❌ Error: {e}")
            return False
    
    def _monitor_process(self, bot_id, process):
        def monitor():
            try:
                process.wait()
                db.update_bot_status(bot_id, 'stopped')
                if bot_id in self.processes:
                    del self.processes[bot_id]
                print(f"🛑 Bot {bot_id} se detuvo")
            except Exception as e:
                print(f"⚠️ Error: {e}")
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
    
    def get_logs(self, bot_id, lines=100):
        log_file = os.path.join(LOGS_DIR, f'bot_{bot_id}.log')
        if not os.path.exists(log_file):
            return []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                return f.readlines()[-lines:]
        except:
            return []
    
    def restart_bot(self, bot_id):
        bot = db.get_bot(bot_id)
        if not bot:
            return False
        
        if bot_id in self.processes:
            try:
                self.processes[bot_id].terminate()
                self.processes[bot_id].wait(timeout=5)
            except:
                self.processes[bot_id].kill()
            del self.processes[bot_id]
        
        time.sleep(2)
        main_file = self.find_main_file(bot['directory'])
        if not main_file:
            return False
        return self.start_bot(bot_id, bot['directory'], main_file)
    
    def stop_bot(self, bot_id):
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
        bot = db.get_bot(bot_id)
        if not bot:
            return False
        
        self.stop_bot(bot_id)
        
        if os.path.exists(bot['directory']):
            shutil.rmtree(bot['directory'])
        
        log_file = os.path.join(LOGS_DIR, f'bot_{bot_id}.log')
        if os.path.exists(log_file):
            os.remove(log_file)
        
        db.delete_bot(bot_id)
        return True

bot_manager = BotManager()

# ==================== DECORADOR DE AUTENTICACIÓN ====================
def auth_required(f):
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

# ==================== RUTAS FRONTEND ====================
@app.route('/')
def index():
    return send_from_directory('frontend', 'index.html')

@app.route('/login')
def login_page():
    return send_from_directory('frontend', 'login.html')

@app.route('/dashboard')
def dashboard_page():
    return send_from_directory('frontend', 'dashboard.html')

@app.route('/style.css')
def serve_css():
    return send_from_directory('frontend', 'style.css', mimetype='text/css')

@app.route('/auth.js')
def serve_auth_js():
    return send_from_directory('frontend', 'auth.js', mimetype='application/javascript')

@app.route('/dashboard.js')
def serve_dashboard_js():
    return send_from_directory('frontend', 'dashboard.js', mimetype='application/javascript')

@app.route('/<path:filename>')
def serve_static(filename):
    if os.path.exists(os.path.join('frontend', filename)):
        return send_from_directory('frontend', filename)
    return jsonify({'error': 'Not found'}), 404

# ==================== API DE AUTENTICACIÓN ====================
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    
    print(f"📝 Intento de registro: {username}")  # <--- AGREGAR
    
    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña requeridos'}), 400
    
    user = db.register_user(username, password, email)
    print(f"📝 Resultado registro: {user}")  # <--- AGREGAR
    
    if user:
        token = db.create_session(user['id'])
        return jsonify({'token': token, 'user': user}), 201
    return jsonify({'error': 'Usuario ya existe'}), 400

# ==================== API DE GITHUB ====================
@app.route('/api/github/save-token', methods=['POST'])
@auth_required
def api_save_github_token():
    data = request.json
    github_token = data.get('github_token')
    github_username = data.get('github_username')
    
    if not github_token or not github_username:
        return jsonify({'error': 'Token y username requeridos'}), 400
    
    user = db.save_github_token(request.user['id'], github_token, github_username)
    return jsonify({'user': user}), 200

# ==================== API DE BOTS ====================
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

# ==================== API DE REPOSITORIOS GITHUB ====================
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
        formatted = [{
            'name': r['name'],
            'full_name': r['full_name'],
            'clone_url': r['clone_url'],
            'private': r['private'],
            'description': r['description'][:100] if r['description'] else ''
        } for r in repos]
        return jsonify(formatted), 200
    
    return jsonify({'error': 'Error obteniendo repositorios'}), 500

# ==================== GITHUB OAUTH ====================
@app.route('/auth/github')
def github_auth():
    if not GITHUB_CLIENT_ID:
        return "Error: GITHUB_CLIENT_ID no configurado", 500
    
    # Guardar el token de sesión del usuario en la URL
    token = request.args.get('token')
    if token:
        # Guardar en sesión para usarlo después
        session['pending_auth_token'] = token
    
    redirect_uri = GITHUB_REDIRECT_URI
    return redirect(f'https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&redirect_uri={redirect_uri}&scope=repo&state={token or ""}')

@app.route('/auth/github/callback')
def github_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    
    if not code:
        return redirect('/dashboard?error=github_auth_failed')
    
    import requests
    
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
            user_response = requests.get('https://api.github.com/user',
                                        headers={'Authorization': f'token {github_token}'})
            
            if user_response.status_code == 200:
                github_user = user_response.json()
                github_username = github_user.get('login')
                
                # Si tenemos el token del usuario en el state, podemos guardar directamente
                if state:
                    # Buscar usuario por token de sesión
                    user = db.get_user_by_token(state)
                    if user:
                        db.save_github_token(user['id'], github_token, github_username)
                        return redirect(f'/dashboard?github_connected=true')
                
                # Si no, redirigir con parámetros para guardar después
                return redirect(f'/dashboard?github_connected=true&temp_token={github_token}&temp_username={github_username}')
    
    return redirect('/dashboard?error=github_auth_failed')

# ==================== HEALTH CHECK ====================
@app.route('/healthz')
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'bots_running': len(bot_manager.processes)
    }), 200

# ==================== INICIO ====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
