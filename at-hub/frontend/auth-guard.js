const API_BASE = "";

const AuthGuard = {
  getToken() {
    return localStorage.getItem("at_hub_token");
  },
  getUser() {
    const raw = localStorage.getItem("at_hub_user");
    return raw ? JSON.parse(raw) : null;
  },
  setSession(token, user) {
    localStorage.setItem("at_hub_token", token);
    localStorage.setItem("at_hub_user", JSON.stringify(user));
  },
  clearSession() {
    localStorage.removeItem("at_hub_token");
    localStorage.removeItem("at_hub_user");
  },
  logout() {
    this.clearSession();
    window.location.href = "login.html";
  },
  requireLogin() {
    const token = this.getToken();
    if (!token) {
      window.location.href = "login.html";
      return null;
    }
    return this.getUser();
  },
};

async function apiFetch(path, options = {}) {
  const token = AuthGuard.getToken();
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(`${API_BASE}${path}`, Object.assign({}, options, { headers }));

  if (response.status === 401) {
    AuthGuard.clearSession();
    window.location.href = "login.html";
    throw new Error("Not authenticated");
  }

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const detail = (data && data.detail) ? data.detail : `Request failed (${response.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function renderHeader(activePage) {
  const user = AuthGuard.getUser();
  const nav = [
    ["dashboard.html", "Dashboard"],
    ["stock-items.html", "Stock Items"],
    ["lots.html", "Lots"],
    ["customer-orders.html", "Customer Orders"],
    ["purchase-orders.html", "Purchase Orders"],
    ["customers.html", "Customers"],
    ["vendors.html", "Vendors"],
  ];
  const links = nav.map(([href, label]) =>
    `<a href="${href}" class="${href === activePage ? 'active' : ''}">${label}</a>`
  ).join("");

  return `
    <header>
      <h1>AT-HUB</h1>
      <nav>${links}<a href="#" onclick="AuthGuard.logout(); return false;">Logout${user ? ` (${user.username})` : ""}</a></nav>
    </header>
  `;
}
