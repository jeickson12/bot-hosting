const API_URL = '';

class Auth {
    static async login(username, password) {
        try {
            const response = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });
            
            if (response.ok) {
                const data = await response.json();
                localStorage.setItem('auth_token', data.token);
                localStorage.setItem('user', JSON.stringify(data.user));
                return { success: true };
            }
            
            const error = await response.json();
            return { success: false, error: error.error };
        } catch (error) {
            return { success: false, error: 'Error de conexión' };
        }
    }
    
    static async register(username, email, password) {
        try {
            const response = await fetch('/api/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, email, password })
            });
            
            if (response.ok) {
                const data = await response.json();
                localStorage.setItem('auth_token', data.token);
                localStorage.setItem('user', JSON.stringify(data.user));
                return { success: true };
            }
            
            const error = await response.json();
            return { success: false, error: error.error };
        } catch (error) {
            return { success: false, error: 'Error de conexión' };
        }
    }
}

// Manejar formularios
document.addEventListener('DOMContentLoaded', () => {
    const loginForm = document.getElementById('login-form');
    const registerForm = document.getElementById('register-form');
    const messageDiv = document.getElementById('auth-message');
    
    // Tabs
    const tabBtns = document.querySelectorAll('.tab-btn');
    const forms = document.querySelectorAll('.auth-form');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            forms.forEach(f => f.classList.remove('active'));
            document.getElementById(`${tab}-form`).classList.add('active');
            if (messageDiv) messageDiv.style.display = 'none';
        });
    });
    
    // Login
    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('login-username').value;
            const password = document.getElementById('login-password').value;
            
            const result = await Auth.login(username, password);
            if (result.success) {
                window.location.href = '/dashboard';
            } else {
                showMessage(result.error || 'Credenciales inválidas', 'error');
            }
        });
    }
    
    // Register
    if (registerForm) {
        registerForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('reg-username').value;
            const email = document.getElementById('reg-email').value;
            const password = document.getElementById('reg-password').value;
            const confirm = document.getElementById('reg-confirm').value;
            
            if (password !== confirm) {
                showMessage('Las contraseñas no coinciden', 'error');
                return;
            }
            
            if (password.length < 6) {
                showMessage('La contraseña debe tener al menos 6 caracteres', 'error');
                return;
            }
            
            const result = await Auth.register(username, email, password);
            if (result.success) {
                window.location.href = '/dashboard';
            } else {
                showMessage(result.error || 'Error al registrarse', 'error');
            }
        });
    }
    
    function showMessage(msg, type) {
        if (messageDiv) {
            messageDiv.textContent = msg;
            messageDiv.className = `auth-message ${type}`;
            messageDiv.style.display = 'block';
            setTimeout(() => {
                messageDiv.style.display = 'none';
            }, 3000);
        }
    }
});