class DashboardApp {
    constructor() {
        this.token = localStorage.getItem('auth_token');
        this.user = JSON.parse(localStorage.getItem('user') || '{}');
        this.currentBotId = null;
        this.init();
    }
    
    async init() {
        if (!this.token) {
            window.location.href = '/login';
            return;
        }
        
        const isValid = await this.verifyAuth();
        if (!isValid) {
            localStorage.clear();
            window.location.href = '/login';
            return;
        }
        
        // Procesar parámetros de URL (después de GitHub OAuth y PayPal)
        await this.processUrlParams();
        
        this.setupEventListeners();
        this.loadUserInfo();
        this.loadBots();
        this.checkGitHubStatus();
    }
    
    async processUrlParams() {
        const urlParams = new URLSearchParams(window.location.search);
        const githubConnected = urlParams.get('github_connected');
        const tempToken = urlParams.get('temp_token');
        const tempUsername = urlParams.get('temp_username');
        const paymentSuccess = urlParams.get('payment_success');
        const paymentFailed = urlParams.get('payment_failed');
        const paymentCancelled = urlParams.get('payment_cancelled');
        
        // Procesar GitHub OAuth
        if (githubConnected === 'true' && tempToken && tempUsername) {
            try {
                const response = await fetch('/api/github/save-token', {
                    method: 'POST',
                    headers: {
                        'Authorization': this.token,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        github_token: tempToken,
                        github_username: tempUsername
                    })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    this.user = data.user;
                    localStorage.setItem('user', JSON.stringify(this.user));
                    window.history.replaceState({}, document.title, '/dashboard');
                    this.showToast('✅ GitHub conectado correctamente', 'success');
                }
            } catch (error) {
                console.error('Error saving GitHub token:', error);
            }
        } else if (githubConnected === 'true') {
            await this.verifyAuth();
            window.history.replaceState({}, document.title, '/dashboard');
        }
        
        // Procesar pagos de PayPal
        if (paymentSuccess === 'true') {
            this.showToast('✅ Pago exitoso! Ahora eres PRO con bots ilimitados', 'success');
            await this.verifyAuth(); // Recargar usuario con plan PRO
            this.loadUserInfo();
            this.loadAccountInfo();
            window.history.replaceState({}, document.title, '/dashboard');
        } else if (paymentFailed === 'true') {
            this.showToast('❌ Error en el pago. Intenta nuevamente', 'error');
            window.history.replaceState({}, document.title, '/dashboard');
        } else if (paymentCancelled === 'true') {
            this.showToast('❌ Pago cancelado', 'error');
            window.history.replaceState({}, document.title, '/dashboard');
        }
        
        // Verificar si hay error de GitHub
        const error = urlParams.get('error');
        if (error === 'github_auth_failed') {
            this.showToast('❌ Error al conectar GitHub', 'error');
            window.history.replaceState({}, document.title, '/dashboard');
        }
    }
    
    showToast(message, type) {
        const toast = document.createElement('div');
        toast.textContent = message;
        toast.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 24px;
            background: ${type === 'success' ? '#4caf50' : '#f44336'};
            color: white;
            border-radius: 8px;
            z-index: 9999;
            animation: fadeInOut 3s ease;
        `;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 3000);
    }
    
    async verifyAuth() {
        try {
            const response = await fetch('/api/verify', {
                method: 'POST',
                headers: { 'Authorization': this.token }
            });
            if (response.ok) {
                const data = await response.json();
                this.user = data.user;
                localStorage.setItem('user', JSON.stringify(this.user));
                return true;
            }
            return false;
        } catch (error) {
            return false;
        }
    }
    
    setupEventListeners() {
        document.querySelectorAll('.nav-item').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const view = btn.dataset.view;
                if (view) {
                    this.switchView(view);
                } else if (btn.id === 'logout-btn') {
                    this.logout();
                }
            });
        });
        
        const modal = document.getElementById('logs-modal');
        const closeBtn = document.querySelector('.close-btn');
        if (closeBtn) {
            closeBtn.onclick = () => modal.classList.remove('active');
        }
        window.onclick = (e) => {
            if (e.target === modal) modal.classList.remove('active');
        };
    }
    
    switchView(view) {
        document.querySelectorAll('.nav-item').forEach(btn => {
            btn.classList.remove('active');
            if (btn.dataset.view === view) btn.classList.add('active');
        });
        
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.getElementById(`view-${view}`).classList.add('active');
        
        const titles = { bots: 'Mis Bots', github: 'Conectar GitHub', account: 'Mi Cuenta' };
        document.getElementById('page-title').innerText = titles[view];
        
        if (view === 'github') this.loadGitHubRepos();
        if (view === 'account') this.loadAccountInfo();
        if (view === 'bots') this.loadBots();
    }
    
    loadUserInfo() {
        const badge = document.getElementById('user-badge');
        if (badge) {
            badge.innerHTML = `
                <span class="username">${this.escapeHtml(this.user.username)}</span>
                <span class="plan ${this.user.plan}">${this.user.plan === 'free' ? 'Gratis' : 'Pro'}</span>
            `;
        }
        
        const planBadge = document.getElementById('plan-badge');
        if (planBadge) {
            const limitText = this.user.plan === 'free' ? '🎁 Plan Gratis - 2 bots máximo' : '⭐ Plan Pro - Bots ilimitados';
            planBadge.innerText = limitText;
        }
    }
    
    async loadBots() {
        try {
            const response = await fetch('/api/bots', {
                headers: { 'Authorization': this.token }
            });
            
            if (response.ok) {
                const bots = await response.json();
                this.renderBots(bots);
            }
        } catch (error) {
            console.error('Error loading bots:', error);
        }
    }
    
    renderBots(bots) {
        const container = document.getElementById('bots-list');
        if (!container) return;
        
        if (bots.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">🤖</div>
                    <h3>No tienes bots desplegados</h3>
                    <p>Conecta GitHub y despliega tu primer bot</p>
                    <button class="btn btn-primary" onclick="dashboard.switchView('github')">
                        Conectar GitHub
                    </button>
                </div>
            `;
            return;
        }
        
        container.innerHTML = bots.map(bot => `
            <div class="bot-card">
                <div class="bot-icon">🤖</div>
                <div class="bot-info">
                    <h3>${this.escapeHtml(bot.name)}</h3>
                    <p class="repo-name">${this.escapeHtml(bot.repo_name || bot.repo_url.split('/').pop())}</p>
                    <span class="status ${bot.status}">
                        ${bot.status === 'running' ? '🟢 Activo' : '🔴 Detenido'}
                    </span>
                </div>
                <div class="bot-actions">
                    <button class="btn-icon" onclick="dashboard.viewLogs(${bot.id})" title="Ver logs">📋</button>
                    <button class="btn-icon" onclick="dashboard.restartBot(${bot.id})" title="Reiniciar">🔄</button>
                    <button class="btn-icon danger" onclick="dashboard.deleteBot(${bot.id})" title="Eliminar">🗑️</button>
                </div>
            </div>
        `).join('');
    }
    
    async viewLogs(botId) {
        this.currentBotId = botId;
        await this.refreshLogs();
        document.getElementById('logs-modal').classList.add('active');
    }
    
    async refreshLogs() {
        if (!this.currentBotId) return;
        
        try {
            const response = await fetch(`/api/bots/${this.currentBotId}/logs`, {
                headers: { 'Authorization': this.token }
            });
            
            if (response.ok) {
                const data = await response.json();
                const logsContent = document.getElementById('logs-content');
                if (logsContent) {
                    logsContent.textContent = data.logs.join('') || 'No hay logs disponibles';
                    logsContent.scrollTop = logsContent.scrollHeight;
                }
            }
        } catch (error) {
            console.error('Error loading logs:', error);
        }
    }
    
    async restartBot(botId) {
        if (!confirm('¿Reiniciar este bot?')) return;
        
        try {
            const response = await fetch(`/api/bots/${botId}/restart`, {
                method: 'POST',
                headers: { 'Authorization': this.token }
            });
            
            if (response.ok) {
                this.showToast('✅ Bot reiniciado correctamente', 'success');
                this.loadBots();
            } else {
                this.showToast('❌ Error al reiniciar el bot', 'error');
            }
        } catch (error) {
            this.showToast('❌ Error de conexión', 'error');
        }
    }
    
    async deleteBot(botId) {
        if (!confirm('¿Eliminar este bot? Esta acción no se puede deshacer.')) return;
        
        try {
            const response = await fetch(`/api/bots/${botId}`, {
                method: 'DELETE',
                headers: { 'Authorization': this.token }
            });
            
            if (response.ok) {
                this.showToast('✅ Bot eliminado correctamente', 'success');
                this.loadBots();
            } else {
                this.showToast('❌ Error al eliminar el bot', 'error');
            }
        } catch (error) {
            this.showToast('❌ Error de conexión', 'error');
        }
    }
    
    async checkGitHubStatus() {
        const container = document.getElementById('github-status');
        if (!container) return;
        
        if (this.user.github_token && this.user.github_token !== 'null') {
            container.innerHTML = `
                <div class="connected">
                    <div class="status-icon">✅</div>
                    <div class="status-text">
                        <strong>GitHub Conectado</strong>
                        <p>Usuario: ${this.escapeHtml(this.user.github_username || 'Conectado')}</p>
                    </div>
                </div>
            `;
            if (document.getElementById('view-github').classList.contains('active')) {
                this.loadGitHubRepos();
            }
        } else {
            container.innerHTML = `
                <div class="disconnected">
                    <div class="status-icon">🔌</div>
                    <div class="status-text">
                        <strong>GitHub No Conectado</strong>
                        <p>Conecta tu cuenta para desplegar bots desde tus repositorios</p>
                        <a href="/auth/github?token=${this.token}" class="btn btn-primary" id="github-connect-btn">Conectar GitHub</a>
                    </div>
                </div>
            `;
        }
    }
    
    async loadGitHubRepos() {
        if (!this.user.github_token) {
            return;
        }
        
        const container = document.getElementById('github-repos');
        if (!container) return;
        
        container.innerHTML = '<div class="loading">Cargando repositorios...</div>';
        
        try {
            const response = await fetch('/api/github/repos', {
                headers: { 'Authorization': this.token }
            });
            
            if (response.ok) {
                const repos = await response.json();
                this.renderGitHubRepos(repos);
            } else {
                const error = await response.json();
                if (error.error === 'GitHub no conectado') {
                    container.innerHTML = '<div class="error">GitHub no está conectado. Conecta tu cuenta primero.</div>';
                    this.checkGitHubStatus();
                } else {
                    container.innerHTML = '<div class="error">Error cargando repositorios</div>';
                }
            }
        } catch (error) {
            container.innerHTML = '<div class="error">Error de conexión</div>';
        }
    }
    
    renderGitHubRepos(repos) {
        const container = document.getElementById('github-repos');
        if (!container) return;
        
        if (repos.length === 0) {
            container.innerHTML = '<div class="empty">No se encontraron repositorios</div>';
            return;
        }
        
        container.innerHTML = `
            <h3>📁 Tus Repositorios</h3>
            <div class="repos-grid">
                ${repos.map(repo => `
                    <div class="repo-card">
                        <div class="repo-header">
                            <span class="repo-icon">📦</span>
                            <span class="repo-name">${this.escapeHtml(repo.name)}</span>
                            ${repo.private ? '<span class="badge private">Privado</span>' : '<span class="badge public">Público</span>'}
                        </div>
                        <p class="repo-desc">${this.escapeHtml(repo.description) || 'Sin descripción'}</p>
                        <button class="btn btn-primary btn-sm" onclick="dashboard.deployBot('${repo.clone_url}', '${repo.name}')">
                            🚀 Desplegar Bot
                        </button>
                    </div>
                `).join('')}
            </div>
        `;
    }
    
    async deployBot(repoUrl, botName) {
        if (!confirm(`¿Desplegar bot desde "${botName}"?`)) return;
        
        const deployBtn = event.target;
        const originalText = deployBtn.innerText;
        deployBtn.innerText = 'Desplegando...';
        deployBtn.disabled = true;
        
        try {
            const response = await fetch('/api/bots/deploy', {
                method: 'POST',
                headers: {
                    'Authorization': this.token,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ repo_url: repoUrl, name: botName })
            });
            
            if (response.ok) {
                this.showToast(`✅ Bot "${botName}" desplegado correctamente`, 'success');
                this.switchView('bots');
                this.loadBots();
            } else {
                const error = await response.json();
                this.showToast(`❌ Error: ${error.error}`, 'error');
            }
        } catch (error) {
            this.showToast('❌ Error de conexión', 'error');
        } finally {
            deployBtn.innerText = originalText;
            deployBtn.disabled = false;
        }
    }
    
    async loadAccountInfo() {
        const container = document.getElementById('account-info');
        if (!container) return;
        
        const botLimit = this.user.plan === 'pro' ? '∞' : '2';
        const currentBots = await this.getBotCount();
        const remaining = this.user.plan === 'pro' ? 'ilimitados' : (2 - currentBots);
        
        container.innerHTML = `
            <div class="info-row">
                <span class="label">Usuario:</span>
                <span class="value">${this.escapeHtml(this.user.username)}</span>
            </div>
            <div class="info-row">
                <span class="label">Email:</span>
                <span class="value">${this.escapeHtml(this.user.email || 'No registrado')}</span>
            </div>
            <div class="info-row">
                <span class="label">Plan:</span>
                <span class="value plan-badge ${this.user.plan}">${this.user.plan === 'pro' ? '⭐ PRO (Bots ilimitados)' : '🎁 GRATIS (2 bots)'}</span>
            </div>
            <div class="info-row">
                <span class="label">GitHub:</span>
                <span class="value">${this.user.github_username ? `Conectado (${this.user.github_username})` : 'No conectado'}</span>
            </div>
            <div class="info-row">
                <span class="label">Bots activos:</span>
                <span class="value">${currentBots} / ${botLimit}</span>
            </div>
            <div class="info-row">
                <span class="label">Bots disponibles:</span>
                <span class="value">${remaining}</span>
            </div>
            <div class="info-row">
                <span class="label">Fecha registro:</span>
                <span class="value">${new Date(this.user.created_at).toLocaleDateString()}</span>
            </div>
        `;
        
        // Botón de upgrade solo para usuarios FREE
        if (this.user.plan !== 'pro') {
            container.innerHTML += `
                <div style="margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #e0e0e0;">
                    <button class="btn btn-primary" onclick="dashboard.upgradeToPro()" style="width: 100%;">
                        ⭐ Actualizar a Pro - $9.99/mes
                    </button>
                    <p style="font-size: 0.7rem; color: #666; text-align: center; margin-top: 0.5rem;">
                        🔒 Bots ilimitados · Soporte prioritario · Pago seguro con PayPal
                    </p>
                </div>
            `;
        }
    }
    
    async getBotCount() {
        try {
            const response = await fetch('/api/bots', {
                headers: { 'Authorization': this.token }
            });
            if (response.ok) {
                const bots = await response.json();
                return bots.length;
            }
        } catch (error) {
            console.error('Error:', error);
        }
        return 0;
    }
    
    async upgradeToPro() {
        try {
            this.showToast('🔄 Redirigiendo a PayPal...', 'info');
            
            const response = await fetch('/api/create-paypal-payment', {
                method: 'POST',
                headers: {
                    'Authorization': this.token,
                    'Content-Type': 'application/json'
                }
            });
            
            if (response.ok) {
                const data = await response.json();
                window.location.href = data.url;
            } else {
                const error = await response.json();
                this.showToast(error.error || '❌ Error al iniciar el pago', 'error');
            }
        } catch (error) {
            this.showToast('❌ Error de conexión', 'error');
        }
    }
    
    async logout() {
        try {
            await fetch('/api/logout', {
                method: 'POST',
                headers: { 'Authorization': this.token }
            });
        } catch (error) {}
        
        localStorage.clear();
        window.location.href = '/';
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Inicializar
let dashboard;
document.addEventListener('DOMContentLoaded', () => {
    dashboard = new DashboardApp();
    window.dashboard = dashboard;
});
