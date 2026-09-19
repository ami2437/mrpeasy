(function () {
    'use strict';

    const API_ORIGIN = 'http://localhost:8000';
    const originalFetch = window.fetch.bind(window);

    function installDashboardButton() {
        if (document.getElementById('shared-dashboard-button')) return;

        const style = document.createElement('style');
        style.textContent = `
            .shared-dashboard-button {
                position: fixed;
                top: 14px;
                left: 16px;
                z-index: 10020;
                display: grid;
                width: 38px;
                height: 38px;
                place-items: center;
                padding: 3px;
                border: 1px solid currentColor;
                border-radius: 50%;
                background: transparent;
                color: inherit;
                text-decoration: none;
            }
            .shared-dashboard-button:hover { background: rgba(255, 255, 255, 0.18); }
            .shared-dashboard-mark {
                display: grid;
                width: 30px;
                height: 30px;
                place-items: center;
                border-radius: 50%;
                background: transparent;
                color: inherit;
                font: 800 11px/1 Georgia, serif;
            }
            .shared-dashboard-button img {
                width: 30px;
                height: 30px;
                border-radius: 50%;
                object-fit: contain;
                background: transparent;
            }
            @media (max-width: 620px) {
                .shared-dashboard-button { top: 10px; left: 10px; }
            }
        `;
        document.head.appendChild(style);

        const link = document.createElement('a');
        link.id = 'shared-dashboard-button';
        link.className = 'shared-dashboard-button';
        link.href = './dashboard.html';
        link.title = 'Open dashboard';
        link.setAttribute('aria-label', 'Open American Traders dashboard');

        const logoUrl = localStorage.getItem('company_logo_url');
        if (logoUrl) {
            const image = document.createElement('img');
            image.src = logoUrl;
            image.alt = 'American Traders logo';
            link.appendChild(image);
        } else {
            const mark = document.createElement('span');
            mark.className = 'shared-dashboard-mark';
            mark.textContent = 'AT';
            link.appendChild(mark);
        }

        document.body.prepend(link);
    }

    function getStoredValue(key) {
        return localStorage.getItem(key) || sessionStorage.getItem(key);
    }

    function getToken() {
        return getStoredValue('access_token');
    }

    function clearSession() {
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        sessionStorage.removeItem('access_token');
        sessionStorage.removeItem('user');
    }

    function redirectToLogin() {
        clearSession();
        window.location.replace('./login.html');
    }

    function isApiRequest(input) {
        const rawUrl = typeof input === 'string' ? input : input.url;
        try {
            const url = new URL(rawUrl, window.location.href);
            return url.origin === API_ORIGIN && url.pathname.startsWith('/api/');
        } catch (error) {
            return false;
        }
    }

    window.fetch = async function authenticatedFetch(input, init = {}) {
        const token = getToken();
        const options = { ...init };
        if (token && isApiRequest(input)) {
            const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
            if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
            options.headers = headers;
        }

        const response = await originalFetch(input, options);
        if (response.status === 401 && !String(typeof input === 'string' ? input : input.url).includes('/api/auth/login')) {
            redirectToLogin();
        }
        return response;
    };

    async function requireRoles(allowedRoles) {
        const token = getToken();
        if (!token) {
            redirectToLogin();
            return null;
        }

        try {
            const response = await window.fetch(`${API_ORIGIN}/api/auth/me`);
            if (!response.ok) {
                if (response.status === 403) window.location.replace('./dashboard.html');
                else redirectToLogin();
                return null;
            }
            const user = await response.json();
            localStorage.removeItem('user');
            sessionStorage.removeItem('user');
            const storage = localStorage.getItem('access_token') ? localStorage : sessionStorage;
            storage.setItem('user', JSON.stringify(user));

            if (allowedRoles.length && !allowedRoles.includes(user.role)) {
                window.location.replace('./dashboard.html');
                return null;
            }
            return user;
        } catch (error) {
            redirectToLogin();
            return null;
        }
    }

    window.AuthGuard = {
        getToken,
        clearSession,
        requireRoles,
        logout: redirectToLogin,
        API_ORIGIN
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', installDashboardButton, { once: true });
    } else {
        installDashboardButton();
    }
})();
