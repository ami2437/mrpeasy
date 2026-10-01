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

// Grouped like MRPeasy's own sidebar: modules are organized under the
// business function they belong to (CRM, Procurement, Warehouse), not a
// flat list of pages.
const NAV_GROUPS = [
  { label: null, links: [["dashboard.html", "Dashboard"]] },
  { label: "CRM", links: [["customers.html", "Customers"], ["customer-orders.html", "Customer Orders"]] },
  { label: "Procurement", links: [["vendors.html", "Vendors"], ["purchase-orders.html", "Purchase Orders"]] },
  { label: "Warehouse", links: [["stock-items.html", "Stock Items"], ["lots.html", "Lots"]] },
];

function renderSidebar(activePage) {
  const user = AuthGuard.getUser();
  const groups = NAV_GROUPS.map(group => {
    const links = group.links.map(([href, label]) =>
      `<a href="${href}" class="${href === activePage ? 'active' : ''}">${label}</a>`
    ).join("");
    return `
      <div class="nav-group">
        ${group.label ? `<div class="nav-group-label">${group.label}</div>` : ""}
        ${links}
      </div>
    `;
  }).join("");

  return `
    <nav class="sidebar">
      <div class="brand">AT-HUB</div>
      ${groups}
      <div class="sidebar-footer">
        ${user ? `<div class="user-line">${user.username}</div>` : ""}
        <a href="#" onclick="AuthGuard.logout(); return false;">Logout</a>
      </div>
    </nav>
  `;
}

// Kept as an alias so older pages referencing renderHeader() keep working.
function renderHeader(activePage) {
  return renderSidebar(activePage);
}
