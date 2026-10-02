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
    const user = this.getUser();
    // A new or reset account must pick its own password before using the app.
    if (user && user.must_change_password && !location.pathname.endsWith("account.html")) {
      window.location.href = "account.html";
      return null;
    }
    return user;
  },
  // Roles in increasing order of access; each can do everything below it.
  ROLE_RANK: { employee: 1, manager: 2, admin: 3, super_admin: 4 },
  hasRole(minimum) {
    const user = this.getUser();
    return !!user && (this.ROLE_RANK[user.role] || 0) >= this.ROLE_RANK[minimum];
  },
  // Page guard: send users without the role back to the dashboard.
  requireRole(minimum) {
    if (!this.requireLogin()) return null;
    if (!this.hasRole(minimum)) {
      alert(`This page needs the ${minimum.replace("_", " ")} role.`);
      window.location.href = "dashboard.html";
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
    if (Array.isArray(detail)) {
      // Pydantic validation errors: show just the messages, e.g. "Quantity must be a whole number".
      throw new Error([...new Set(detail.map(d => String(d.msg || d).replace(/^Value error, /, "")))].join("; "));
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

// POST a FormData (file upload). apiFetch forces JSON; the browser must set the multipart boundary.
async function apiUpload(path, form) {
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", body: form, headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
  if (response.status === 401) {
    AuthGuard.clearSession();
    window.location.href = "login.html";
    throw new Error("Not authenticated");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `Request failed (${response.status})`);
  }
  return data;
}

// ---- Formatting shared by every page ----
const PRICE_STEP = "0.00001"; // unit prices/costs are kept to 5 decimal places

// Employees work shipments only and never see dollar amounts (the server blanks them too).
function hidesMoney() { const u = AuthGuard.getUser(); return !!u && u.role === "employee"; }

// Unit price/cost: $ with up to 5 decimals, at least 2 ($0.21375, $3.50).
function fmtPrice(n) {
  if (hidesMoney()) return "";
  const v = n || 0;
  return (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 });
}
// Money totals: always 2 decimals with a $ sign.
function fmtMoney(n) {
  if (hidesMoney()) return "";
  const v = n || 0;
  return (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
// Quantities are whole units everywhere.
function fmtQty(n) {
  return Math.round(n || 0).toLocaleString("en-US");
}
// A price input with a "$" in front: priceInput("e-line-price", 0.21375, 'data-line="3"')
function priceInput(cls, value, attrs = "") {
  if (hidesMoney()) return `<input type="hidden" class="${cls}" value="${value ?? 0}" ${attrs}>`;
  return `<span class="price-input"><span>$</span><input type="number" step="${PRICE_STEP}" min="0" class="${cls}" value="${value ?? 0}" ${attrs}></span>`;
}
// Small labelled progress bar, e.g. progressBar("Shipped", 60, "ship", "300 of 500 units").
// kind picks the colour: ship (green), inv (blue), paid (purple), recv (teal).
function progressBar(label, pct, kind, title = "") {
  const v = Math.max(0, Math.min(100, Math.round(pct || 0)));
  return `<div class="progress-line" title="${escapeHtml(title)}">${label ? `<span class="progress-label">${label}</span>` : ""}`
    + `<span class="pct-bar ${kind}"><span style="width:${v}%"></span></span><span class="progress-pct">${v}%</span></div>`;
}
function escapeHtml(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Price history: "vs last" hint + popup, shared by customer and purchase orders ----
// History rows come from /api/stock-items/{id}/price-history (newest first).
const priceHistoryCache = {};
function loadPriceHistory(itemId) {
  if (!priceHistoryCache[itemId]) priceHistoryCache[itemId] = apiFetch(`/api/stock-items/${itemId}/price-history`);
  return priceHistoryCache[itemId];
}

// The most recent other price for this item: kind "sale" or "purchase", skipping the
// current document and cancelled ones.
function lastPrice(history, kind, excludeDocId) {
  return history.find(h => h.kind === kind && h.doc_id !== excludeDocId && h.status !== "cancelled") || null;
}

// "▼ $0.40 vs $2.00 (CO-0003)" under a price. Selling lower than before shows red;
// for purchases, paying more shows red.
function priceDeltaHtml(current, prev, kind, itemId) {
  const link = `onclick="showPriceHistory(${itemId})" title="Click for full price history"`;
  if (!prev) return `<a class="price-delta muted" ${link}>No Previous Price</a>`;
  const diff = current - prev.unit_price;
  if (Math.abs(diff) < 0.000005) return `<a class="price-delta muted" ${link}>= Last ${fmtPrice(prev.unit_price)}</a>`;
  const good = kind === "sale" ? diff > 0 : diff < 0;
  return `<a class="price-delta ${good ? "pos" : "neg"}" ${link}>${diff > 0 ? "▲" : "▼"} ${fmtPrice(Math.abs(diff))} Vs ${fmtPrice(prev.unit_price)} <span class="muted">${escapeHtml(prev.doc_code)}</span></a>`;
}

// Fills every [data-price-delta] element. Attributes: data-item, data-kind (sale|purchase),
// data-doc (current document id), and data-price or data-price-input (selector for a live
// price input in the same row).
async function refreshPriceDeltas(root = document) {
  if (hidesMoney()) return;  // employees: no prices, no price history
  for (const el of root.querySelectorAll("[data-price-delta]")) {
    const itemId = parseInt(el.dataset.item);
    if (!itemId) continue;
    const history = await loadPriceHistory(itemId);
    const row = el.closest("tr") || el.parentElement;
    const input = el.dataset.priceInput ? row.querySelector(el.dataset.priceInput) : null;
    const current = input ? parseFloat(input.value) || 0 : parseFloat(el.dataset.price) || 0;
    el.innerHTML = priceDeltaHtml(current, lastPrice(history, el.dataset.kind, parseInt(el.dataset.doc) || null), el.dataset.kind, itemId);
  }
}

async function showPriceHistory(itemId) {
  let modal = document.getElementById("price-history-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "price-history-modal";
    modal.className = "modal-backdrop";
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.body.appendChild(modal);
  }
  modal.innerHTML = `<div class="modal"><p class="muted">Loading Price History…</p></div>`;
  try {
    const [history, item] = await Promise.all([loadPriceHistory(itemId), apiFetch(`/api/stock-items/${itemId}`)]);
    const section = (kind, title, partyLabel, page) => {
      const rows = history.filter(h => h.kind === kind);
      if (!rows.length) return `<h4>${title}</h4><p class="muted">None Yet.</p>`;
      return `<h4>${title}</h4>
        <table class="compact-table no-table-tools">
          <thead><tr><th>Date</th><th>Document</th><th>${partyLabel}</th><th>Status</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Change</th></tr></thead>
          <tbody>${rows.map((h, i) => {
            const older = rows.slice(i + 1).find(r => r.status !== "cancelled");
            const diff = older ? h.unit_price - older.unit_price : null;
            const good = kind === "sale" ? diff > 0 : diff < 0;
            return `<tr>
              <td>${h.date ? new Date(h.date).toLocaleDateString() : ""}</td>
              <td><a class="link" href="${page}?id=${h.doc_id}">${escapeHtml(h.doc_code)}</a></td>
              <td>${escapeHtml(h.party)}</td><td><span class="tag ${h.status}">${h.status.replace("_", " ")}</span></td>
              <td class="num">${fmtQty(h.quantity)}</td><td class="num">${fmtPrice(h.unit_price)}</td>
              <td class="num">${diff == null || Math.abs(diff) < 0.000005 ? "" : `<span class="${good ? "pos" : "neg"}">${diff > 0 ? "▲" : "▼"} ${fmtPrice(Math.abs(diff))}</span>`}</td>
            </tr>`;
          }).join("")}</tbody>
        </table>`;
    };
    modal.innerHTML = `<div class="modal">
      <div class="row" style="align-items:center;"><h3 style="margin:0;">Price history — ${escapeHtml(item.code)}</h3>
        <div style="flex:0;"><a class="link" onclick="document.getElementById('price-history-modal').remove()">Close</a></div></div>
      <p class="muted" style="margin-top:4px;">${escapeHtml(item.title)}${item.category ? ` · ${escapeHtml(item.category)}` : ""}</p>
      ${section("sale", "Sold (customer orders)", "Customer", "customer-orders.html")}
      ${section("purchase", "Bought (purchase orders)", "Vendor", "purchase-orders.html")}
    </div>`;
  } catch (err) {
    modal.innerHTML = `<div class="modal"><div class="error">${escapeHtml(err.message)}</div></div>`;
  }
}

// ---- Searchable item picker ----
// Any <select data-searchable> becomes a type-to-search box. The select stays in the page
// (hidden) and keeps its value and change events, so existing code reading .value or
// .selectedOptions keeps working. Matches every typed word against the option text plus
// its data-search keywords (product group, barcode).
function makeSearchable(select) {
  if (select.dataset.searchReady) return;
  select.dataset.searchReady = "1";
  const wrap = document.createElement("div");
  wrap.className = "search-select";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Type To Search Code, Title, Group, Barcode…";
  input.autocomplete = "off";
  const list = document.createElement("div");
  list.className = "search-select-list";
  list.style.display = "none";
  select.parentNode.insertBefore(wrap, select);
  wrap.append(input, list, select);
  select.style.display = "none";

  const label = () => select.selectedOptions[0] ? select.selectedOptions[0].textContent.trim() : "";
  input.value = label();
  // Code that sets select.value and fires "change" (e.g. a vendor item # match) updates the box too.
  select.addEventListener("change", () => { if (document.activeElement !== input) input.value = label(); });
  let matches = [], active = 0;

  const render = () => {
    const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    matches = Array.from(select.options).filter(o => {
      const hay = `${o.textContent} ${o.dataset.search || ""}`.toLowerCase();
      return words.every(w => hay.includes(w));
    });
    active = 0;
    const shown = matches.slice(0, 50);
    list.innerHTML = shown.length
      ? shown.map((o, i) => `<div class="search-select-option${i === 0 ? " active" : ""}" data-i="${i}">${escapeHtml(o.textContent)}${o.dataset.search ? ` <span class="muted small">${escapeHtml(o.dataset.search)}</span>` : ""}</div>`).join("")
        + (matches.length > 50 ? `<div class="muted small" style="padding:6px 10px;">${matches.length - 50} More — Keep Typing To Narrow Down</div>` : "")
      : `<div class="muted small" style="padding:6px 10px;">No Items Match "${escapeHtml(input.value)}"</div>`;
    list.style.display = "block";
  };
  const choose = i => {
    const o = matches[i];
    if (!o) return;
    select.value = o.value;
    input.value = label();
    list.style.display = "none";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const highlight = () => list.querySelectorAll(".search-select-option").forEach((el, i) => {
    el.classList.toggle("active", i === active);
    if (i === active) el.scrollIntoView({ block: "nearest" });
  });

  input.addEventListener("focus", () => { input.select(); input.value = ""; render(); });
  input.addEventListener("input", render);
  input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown") { active = Math.min(active + 1, Math.min(matches.length, 50) - 1); highlight(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); highlight(); e.preventDefault(); }
    else if (e.key === "Enter") { choose(active); e.preventDefault(); }
    else if (e.key === "Escape") { input.value = label(); list.style.display = "none"; input.blur(); }
  });
  input.addEventListener("blur", () => setTimeout(() => { list.style.display = "none"; input.value = label(); }, 150));
  list.addEventListener("mousedown", e => {
    const el = e.target.closest(".search-select-option");
    if (el) { e.preventDefault(); choose(parseInt(el.dataset.i)); }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("select[data-searchable]").forEach(makeSearchable);
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.matches("select[data-searchable]")) makeSearchable(n);
      n.querySelectorAll("select[data-searchable]").forEach(makeSearchable);
    }
  }).observe(document.body, { childList: true, subtree: true });
});

// Box breakdown, one size per line, largest first: {500: 4, 400: 1} -> "4 Box × 500<br>1 Box × 400".
function formatBoxCounts(byQty) {
  return Object.entries(byQty).sort((a, b) => b[0] - a[0])
    .map(([qty, n]) => `${n} Box × ${fmtQty(qty)}`).join("<br>");
}

// ---- Attachments: files on orders, purchase orders and shipments ----
const ATTACHMENT_LABELS = {
  customer_po: "Customer PO", vendor_invoice: "Vendor Invoice", mtr: "Material Test Report (MTR)",
  vendor_quote: "Vendor Quote", pod: "Proof Of Delivery", bol: "Bill Of Lading", other: "Other",
};

function fmtFileSize(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

// Attachment files need the auth header, so they're fetched as blobs (cached per id).
const attachmentBlobUrls = {};
async function attachmentUrl(id) {
  if (!attachmentBlobUrls[id]) {
    const r = await fetch(`/api/attachments/${id}/file`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
    if (!r.ok) throw new Error(`Could not open the file (${r.status})`);
    attachmentBlobUrls[id] = URL.createObjectURL(await r.blob());
  }
  return attachmentBlobUrls[id];
}

async function openAttachment(id) {
  const win = window.open("", "_blank");
  try {
    const url = await attachmentUrl(id);
    if (win) win.location.href = url; else window.location.href = url;
  } catch (err) {
    if (win) win.close();
    alert(err.message);
  }
}

// Renders an upload box + the file list into `container` (an element or its id).
// categories: which document types this record takes, first one is the default.
async function renderAttachments(container, entityType, entityId, categories, opts = {}) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  if (hidesMoney()) categories = categories.filter(c => !["customer_po", "vendor_invoice", "vendor_quote"].includes(c));
  const key = `${entityType}-${entityId}`;
  el.innerHTML = `<p class="muted small">Loading Files…</p>`;
  let files = [];
  try {
    files = await apiFetch(`/api/attachments/?entity_type=${entityType}&entity_id=${entityId}`);
  } catch (err) {
    el.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    return;
  }
  const me = AuthGuard.getUser() || {};
  const canDelete = f => f.uploaded_by === me.username || AuthGuard.hasRole("manager");
  const isImage = f => (f.content_type || "").startsWith("image/");
  const groups = categories.map(c => [c, files.filter(f => f.category === c)]).filter(([, list]) => list.length);
  el.innerHTML = `
    <div class="attach-upload">
      <select id="att-cat-${key}">${categories.map(c => `<option value="${c}">${ATTACHMENT_LABELS[c] || c}</option>`).join("")}</select>
      <input type="file" id="att-files-${key}" multiple ${opts.camera ? `accept="image/*,application/pdf" capture="environment"` : ""}>
      <input type="text" id="att-note-${key}" placeholder="${escapeHtml(opts.notePlaceholder || "Note (Optional)")}">
      <button class="secondary" id="att-btn-${key}">Upload</button>
    </div>
    <div id="att-error-${key}" class="error"></div>
    ${groups.length ? groups.map(([cat, list]) => `
      <div class="attach-group">
        <div class="attach-group-label">${ATTACHMENT_LABELS[cat] || cat} <span class="muted">(${list.length})</span></div>
        ${list.map(f => `
          <div class="attach-row">
            ${isImage(f) ? `<img class="attach-thumb" data-att="${f.id}" alt="" onclick="openAttachment(${f.id})">` : `<span class="attach-icon">${(f.filename.split(".").pop() || "file").slice(0, 4).toUpperCase()}</span>`}
            <div class="attach-info">
              <a class="link" onclick="openAttachment(${f.id})">${escapeHtml(f.filename)}</a>
              <div class="muted small">${fmtFileSize(f.size)} · ${escapeHtml(f.uploaded_by || "")} · ${new Date(f.created_at + (f.created_at.endsWith("Z") ? "" : "Z")).toLocaleString()}${f.note ? ` · <span style="color:#1a1a1a;">${escapeHtml(f.note)}</span>` : ""}</div>
            </div>
            ${canDelete(f) ? `<a class="link small" data-del="${f.id}">Delete</a>` : ""}
          </div>`).join("")}
      </div>`).join("") : `<p class="muted small" style="margin:6px 0 0;">No Files Yet.</p>`}
  `;
  el.querySelectorAll("img[data-att]").forEach(async img => {
    try { img.src = await attachmentUrl(img.dataset.att); } catch (e) { img.remove(); }
  });
  const errorEl = document.getElementById(`att-error-${key}`);
  document.getElementById(`att-btn-${key}`).onclick = async () => {
    errorEl.textContent = "";
    const input = document.getElementById(`att-files-${key}`);
    if (!input.files.length) { errorEl.textContent = "Choose A File First."; return; }
    const form = new FormData();
    form.append("entity_type", entityType);
    form.append("entity_id", entityId);
    form.append("category", document.getElementById(`att-cat-${key}`).value);
    form.append("note", document.getElementById(`att-note-${key}`).value);
    Array.from(input.files).forEach(f => form.append("files", f));
    const btn = document.getElementById(`att-btn-${key}`);
    btn.disabled = true;
    btn.textContent = "Uploading…";
    try {
      const r = await fetch("/api/attachments/", { method: "POST", headers: { Authorization: `Bearer ${AuthGuard.getToken()}` }, body: form });
      const data = await r.json();
      if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Upload failed");
      renderAttachments(el, entityType, entityId, categories, opts);
      if (opts.onChange) opts.onChange();
    } catch (err) {
      errorEl.textContent = err.message;
      btn.disabled = false;
      btn.textContent = "Upload";
    }
  };
  el.querySelectorAll("[data-del]").forEach(a => a.onclick = async () => {
    if (!confirm("Delete this file?")) return;
    try {
      await apiFetch(`/api/attachments/${a.dataset.del}`, { method: "DELETE" });
      renderAttachments(el, entityType, entityId, categories, opts);
      if (opts.onChange) opts.onChange();
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });
}

// Small grey product-group chip shown next to item codes.
function groupTag(item) {
  return item && item.category ? `<span class="group-tag">${escapeHtml(item.category)}</span>` : "";
}

// ---- 4x6 labels: same layout and fields as the main portal's labels, plus our logo ----
// Shared by box labels (shipments / batch packing) and the On-Demand Labels page, so the
// on-screen preview and the printout are the same HTML.
let labelCompanyPromise = null;
function labelCompany() {
  if (!labelCompanyPromise) labelCompanyPromise = apiFetch("/api/company/").catch(() => ({}));
  return labelCompanyPromise;
}
function labelDefaultFooter(company) {
  return `${company.name || ""}${company.email ? `\nQuestions? Email ${company.email}` : ""}`;
}
function labelLogoHtml(company, base = "") {
  return company.has_logo
    ? `<div class="label-logo-slot"><img class="label-logo" src="${base}/api/company/logo" alt=""></div>`
    : `<div class="label-logo-slot"></div>`;
}

// Box label. l: { customer, shipment, order, po, job, item_code, item_title, qty, footer? }
function boxLabelHtml(l, company, base = "") {
  const na = v => escapeHtml(v != null && String(v).trim() ? v : "N/A");
  const stripPo = v => String(v || "").replace(/^\s*po\s*#?\s*/i, "");
  const footer = l.footer != null ? l.footer : labelDefaultFooter(company);
  return `
    <div class="label-card">
      ${labelLogoHtml(company, base)}
      <div class="label-customer">${na(l.customer)}</div>
      <div class="label-body">
        <div class="label-field"><div class="label-field-label">Shipment</div><div class="label-field-value">${na(l.shipment)}</div></div>
        <div class="label-field"><div class="label-field-label">Order #</div><div class="label-field-value">${na(l.order)}</div></div>
        <div class="label-field"><div class="label-field-label">PO #</div><div class="label-field-value">${na(stripPo(l.po))}</div></div>
        <div class="label-field"><div class="label-field-label">Job #</div><div class="label-field-value">${na(l.job)}</div></div>
        <div class="label-field item-field" style="grid-column: span 4;">
          <div class="label-field-value item-multiline"><div><strong>ITEM # ${escapeHtml(l.item_code || "N/A")}</strong></div><div>${escapeHtml(l.item_title || "")}</div></div>
        </div>
        <div class="label-field qty-field" style="grid-column: span 4;">
          <div class="label-field-label">Quantity in Box</div><div class="label-field-value large">${l.qty === "" || l.qty == null ? "" : escapeHtml(isNaN(l.qty) ? l.qty : fmtQty(l.qty))}</div>
        </div>
      </div>
      <div class="label-footer">${escapeHtml(footer).replace(/\n/g, "<br>")}</div>
    </div>`;
}

// Free text label. f: { text, size (px), align, bold, border, logo }
function freeLabelHtml(f, company, base = "") {
  return `
    <div class="label-card free-card ${f.border === false ? "no-border" : ""}">
      ${f.logo ? labelLogoHtml(company, base) : ""}
      <div class="free-text" style="font-size:${parseInt(f.size) || 28}px;text-align:${f.align || "center"};font-weight:${f.bold ? 700 : 500};">${escapeHtml(f.text || "")}</div>
    </div>`;
}

// Address label. a: { from, to, attn, ref, note }
function addressLabelHtml(a, company, base = "") {
  const lines = v => escapeHtml(v || "").replace(/\n/g, "<br>");
  return `
    <div class="label-card address-card">
      ${labelLogoHtml(company, base)}
      <div class="addr-from"><div class="label-field-label">From</div>${lines(a.from)}</div>
      <div class="addr-to"><div class="label-field-label">Ship To</div>
        ${a.attn ? `<div class="addr-attn">Attn: ${escapeHtml(a.attn)}</div>` : ""}<div class="addr-to-text">${lines(a.to)}</div></div>
      <div class="addr-bottom">
        ${a.ref ? `<div><span class="label-field-label">Ref</span> ${escapeHtml(a.ref)}</div>` : "<div></div>"}
        ${a.note ? `<div class="addr-note">${escapeHtml(a.note)}</div>` : ""}
      </div>
    </div>`;
}

// Shrinks text that overflows its box (long item descriptions, big free text).
function fitLabelText(root) {
  root.querySelectorAll(".item-multiline").forEach(el => {
    let size = 15;
    el.style.fontSize = size + "px";
    while (el.scrollHeight > el.clientHeight && size > 10) { size -= 0.5; el.style.fontSize = size + "px"; }
  });
  root.querySelectorAll(".free-text, .addr-to-text").forEach(el => {
    const box = el.closest(".label-card");
    let size = parseFloat(el.style.fontSize) || parseFloat(getComputedStyle(el).fontSize);
    while (box.scrollHeight > box.clientHeight + 1 && size > 9) { size -= 1; el.style.fontSize = size + "px"; }
  });
}

// Opens a print tab with the given label cards (HTML) and prints once the logo has loaded.
// Pass `win` when it was opened before an await (pop-up blockers need a synchronous open).
function printLabelCards(cardsHtml, title, win = window.open("", "_blank")) {
  if (!win) { alert("The browser blocked the new tab — allow pop-ups for this site and try again."); return; }
  win.document.open();
  win.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${escapeHtml(title || "Labels")}</title>
    <link rel="stylesheet" href="${location.origin}/labels.css"></head><body>${cardsHtml}</body></html>`);
  win.document.close();
  let tries = 0;
  const wait = () => {
    const ready = win.document.styleSheets.length && Array.from(win.document.images).every(i => i.complete);
    if (ready || ++tries > 40) { fitLabelText(win.document); win.focus(); win.print(); } else setTimeout(wait, 100);
  };
  wait();
}

// labels: [{ customer, shipment, order, po, job, item_code, item_title, qty }]
async function printBoxLabels(labels, title) {
  const win = window.open("", "_blank");
  if (win) win.document.write("<p style='font-family:sans-serif;padding:20px;color:#555'>Preparing labels…</p>");
  const company = await labelCompany();
  printLabelCards(labels.map(l => boxLabelHtml(l, company, location.origin)).join(""), title, win);
}

// Opens a server-rendered PDF (needs the auth header, so it's fetched as a blob).
// The tab is opened synchronously so pop-up blockers allow it.
async function openPdf(path) {
  const win = window.open("", "_blank");
  if (!win) {
    alert("The browser blocked the new tab — allow pop-ups for this site and try again.");
    return;
  }
  win.document.write("<p style='font-family:sans-serif;padding:20px;color:#555'>Preparing PDF…</p>");
  try {
    const response = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
    if (!response.ok) throw new Error(`Could not generate the PDF (${response.status})`);
    win.location.href = URL.createObjectURL(await response.blob());
  } catch (err) {
    win.document.body.innerHTML = `<p style='font-family:sans-serif;padding:20px;color:#dc2626'>${escapeHtml(err.message)}</p>`;
  }
}

// Grouped like MRPeasy's own sidebar: modules are organized under the
// business function they belong to (CRM, Procurement, Warehouse), not a
// flat list of pages.
// A link's third entry is the minimum role that sees it. Employees get the shipping
// side only: orders (to ship from), shipments, POD, stock -- no billing or purchasing.
const NAV_GROUPS = [
  { label: null, links: [["dashboard.html", "Dashboard"]] },
  { label: "CRM", links: [
    ["customers.html", "Customers", "manager"],
    ["customer-orders.html", "Customer Orders"],
    ["shipments.html", "Shipments"],
    ["pack-shipments.html", "Batch Shipments"],
    ["pod.html", "Proof Of Delivery"],
    ["labels.html", "On-Demand Labels"],
    ["invoices.html", "Invoices", "manager"],
  ] },
  { label: "Procurement", minRole: "manager", links: [
    ["vendors.html", "Vendors"],
    ["purchase-orders.html", "Purchase Orders"],
    ["landed-costs.html", "Landed Costs"],
  ] },
  { label: "Warehouse", links: [["stock-items.html", "Stock Items"], ["lots.html", "Lots"], ["mtrs.html", "MTR Library"]] },
  { label: null, minRole: "manager", links: [["reports.html", "Reports"], ["company.html", "Company Settings", "admin"]] },
  { label: "Admin", minRole: "super_admin", links: [["users.html", "Users & Roles"]] },
];

// Where "Home" goes for this user (used by pages without the sidebar, like POD).
function homePage() { return "dashboard.html"; }

const ROLE_LABELS = { super_admin: "Super Admin", admin: "Admin", manager: "Manager", employee: "Employee" };

function renderSidebar(activePage) {
  const user = AuthGuard.getUser();
  const groups = NAV_GROUPS.filter(group => !group.minRole || AuthGuard.hasRole(group.minRole)).map(group => {
    const links = group.links.filter(([, , min]) => !min || AuthGuard.hasRole(min)).map(([href, label]) =>
      `<a href="${href}" class="${href === activePage ? 'active' : ''}">${icon(NAV_ICONS[href])}${label}</a>`
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
      <div class="brand"><img src="/api/company/logo" alt="" class="brand-logo" onerror="this.remove()"><span>AT-HUB</span></div>
      ${groups}
      <div class="sidebar-footer">
        ${user ? `<div class="user-line">${escapeHtml(user.full_name || user.username)}<div class="small">${ROLE_LABELS[user.role] || user.role}</div></div>` : ""}
        <a href="#" onclick="toggleTheme(); return false;" id="theme-toggle">${icon("moon")}<span>${currentTheme() === "dark" ? "Light Mode" : "Dark Mode"}</span></a>
        <a href="account.html" class="${activePage === "account.html" ? "active" : ""}">${icon("user")}My Account</a>
        <a href="#" onclick="AuthGuard.logout(); return false;">${icon("logout")}Logout</a>
      </div>
    </nav>
  `;
}

// Kept as an alias so older pages referencing renderHeader() keep working.
function renderHeader(activePage) {
  return renderSidebar(activePage);
}

// ---- Table tools, applied to every table on every page ----
// Drag a header's right edge to resize, "Columns" to hide/unhide, and a live
// totals footer for headers marked class="sum" (sums only the rows showing, so
// it follows search/filter). Settings persist per page + header set.
const TableTools = {
  seq: 0,

  storageKey(table) {
    const page = location.pathname.split("/").pop() || "index";
    const heads = Array.from(table.tHead.rows[0].cells).map((th, i) => th.dataset.label || th.textContent.trim() || `#${i}`);
    return `at_hub_cols:${page}:${heads.join("|")}`;
  },
  load(key) {
    try { return Object.assign({ hidden: [], widths: {}, order: null }, JSON.parse(localStorage.getItem(key)) || {}); }
    catch { return { hidden: [], widths: {}, order: null }; }
  },
  save(key, state) {
    try { localStorage.setItem(key, JSON.stringify(state)); } catch {}
  },

  enhance(table) {
    if (table.dataset.tt || !table.tHead || !table.tHead.rows.length || table.closest(".no-table-tools")) return;
    const ths = Array.from(table.tHead.rows[0].cells);
    if (ths.length < 2) return;
    const id = String(++this.seq);
    table.dataset.tt = id;
    ths.forEach(th => { th.dataset.label = th.dataset.label || th.textContent.trim(); });
    const key = this.storageKey(table);
    const state = this.load(key);

    // Toolbar: a "Columns" button right-aligned above the table.
    const bar = document.createElement("div");
    bar.className = "table-tools";
    const menuBtn = document.createElement("a");
    menuBtn.className = "table-tools-btn";
    const menu = document.createElement("div");
    menu.className = "table-tools-menu";
    menu.style.display = "none";
    bar.append(menuBtn, menu);
    table.parentNode.insertBefore(bar, table);

    const style = document.createElement("style");
    document.head.appendChild(style);
    // Column order: state.order lists original column indexes in display order. Every row the page
    // renders arrives in the original order, so each new row is rearranged once (rows with a colspan
    // cell or a different cell count -- "Loading…", group rows -- are left as they are).
    const pos = i => (state.order ? state.order.indexOf(i) : i);
    const arrange = row => {
      if (!state.order || row.dataset.ttOrd === String(state.order) || row.cells.length !== ths.length
          || row.querySelector("[colspan]")) return;
      const cells = row.dataset.ttOrd ? row._ttOriginal : Array.from(row.cells);
      row._ttOriginal = cells;
      state.order.forEach(i => row.appendChild(cells[i]));
      row.dataset.ttOrd = String(state.order);
    };
    const applyOrder = () => {
      const head = table.tHead.rows[0];
      (state.order || ths.map((_, i) => i)).forEach(i => head.appendChild(ths[i]));
      Array.from(table.tBodies).forEach(tb => Array.from(tb.rows).forEach(r => {
        if (!state.order && r._ttOriginal) { r._ttOriginal.forEach(c => r.appendChild(c)); delete r.dataset.ttOrd; }
        else arrange(r);
      }));
      if (table.tFoot && table.tFoot.className === "totals-row") this.refreshTotals(table);
    };
    Array.from(table.tBodies).forEach(tb => new MutationObserver(muts => {
      if (state.order) muts.forEach(m => m.addedNodes.forEach(n => { if (n.tagName === "TR") arrange(n); }));
    }).observe(tb, { childList: true }));

    const applyHidden = () => {
      style.textContent = state.hidden.map(i =>
        `table[data-tt="${id}"] > * > tr > :nth-child(${pos(i) + 1}):not([colspan]) { display: none; }`).join("\n");
      menuBtn.classList.toggle("has-hidden", state.hidden.length > 0);
      menuBtn.textContent = state.hidden.length ? `Columns (${state.hidden.length} hidden) ▾` : "Columns ▾";
    };
    const applyWidths = () => {
      table.style.tableLayout = Object.keys(state.widths).length ? "fixed" : "";
      ths.forEach((th, i) => { th.style.width = state.widths[i] ? `${state.widths[i]}px` : ""; });
    };

    menuBtn.onclick = () => {
      if (menu.style.display !== "none") { menu.style.display = "none"; return; }
      menu.innerHTML = ths.map((th, i) => th.dataset.label ? `
        <label><input type="checkbox" data-i="${i}" ${state.hidden.includes(i) ? "" : "checked"}> ${escapeHtml(th.dataset.label)}</label>` : "").join("")
        + `<div class="table-tools-actions"><a data-act="all">Show all</a> · <a data-act="widths">Reset widths</a> · <a data-act="order">Reset order</a></div>`
        + `<div class="table-tools-hint">Drag a column header to move it.</div>`;
      menu.style.display = "block";
    };
    menu.onchange = e => {
      const i = parseInt(e.target.dataset.i);
      state.hidden = e.target.checked ? state.hidden.filter(h => h !== i) : [...state.hidden, i];
      this.save(key, state);
      applyHidden();
    };
    menu.onclick = e => {
      const act = e.target.dataset.act;
      if (act === "all") { state.hidden = []; menu.querySelectorAll("input").forEach(c => { c.checked = true; }); applyHidden(); }
      if (act === "widths") { state.widths = {}; applyWidths(); }
      if (act === "order") { state.order = null; applyOrder(); applyHidden(); }
      if (act) this.save(key, state);
    };
    document.addEventListener("click", e => { if (!bar.contains(e.target)) menu.style.display = "none"; });

    // Resize handles: on the first drag, freeze every column at its current width.
    ths.forEach((th, i) => {
      const grip = document.createElement("span");
      grip.className = "col-resizer";
      grip.addEventListener("mousedown", e => {
        e.preventDefault();
        e.stopPropagation();
        ths.forEach((h, j) => { if (!state.widths[j] && h.offsetWidth) state.widths[j] = h.offsetWidth; });
        applyWidths();
        const startX = e.pageX, startW = th.offsetWidth;
        const move = ev => { state.widths[i] = Math.max(30, startW + ev.pageX - startX); th.style.width = `${state.widths[i]}px`; };
        const up = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); this.save(key, state); };
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
      });
      grip.addEventListener("click", e => e.stopPropagation());
      th.appendChild(grip);
    });

    let dragFrom = null;
    ths.forEach((th, i) => {
      if (!th.dataset.label) return;
      th.draggable = true;
      th.addEventListener("dragstart", e => { dragFrom = i; th.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", th.dataset.label); });
      th.addEventListener("dragend", () => { th.classList.remove("dragging"); ths.forEach(h => h.classList.remove("drop-before", "drop-after")); });
      th.addEventListener("dragover", e => {
        if (dragFrom === null || dragFrom === i) return;
        e.preventDefault();
        const after = e.offsetX > th.offsetWidth / 2;
        th.classList.toggle("drop-after", after); th.classList.toggle("drop-before", !after);
      });
      th.addEventListener("dragleave", () => th.classList.remove("drop-before", "drop-after"));
      th.addEventListener("drop", e => {
        e.preventDefault();
        if (dragFrom === null || dragFrom === i) return;
        const after = th.classList.contains("drop-after");
        const order = (state.order || ths.map((_, j) => j)).filter(j => j !== dragFrom);
        order.splice(order.indexOf(i) + (after ? 1 : 0), 0, dragFrom);
        state.order = order.every((v, j) => v === j) ? null : order;
        dragFrom = null;
        this.save(key, state);
        applyOrder();
        applyHidden();
      });
    });

    applyOrder();
    applyHidden();
    applyWidths();
    this.enableSort(table, ths);

    if (ths.some(th => th.classList.contains("sum"))) {
      table.createTFoot().className = "totals-row";
      this.refreshTotals(table);
      Array.from(table.tBodies).forEach(tb =>
        new MutationObserver(() => this.refreshTotals(table)).observe(tb, { childList: true, subtree: true, characterData: true }));
    }
  },

  // Click a header to sort (ascending, descending, then back to the original order).
  // Rows re-rendered by the page are re-sorted automatically. Rows with a colspan cell
  // ("Loading…", "No items") are left alone, as are headers marked data-nosort.
  sortKey(td) {
    if (!td) return { n: null, s: "" };
    if (td.dataset.sort !== undefined) {
      const n = parseFloat(td.dataset.sort);
      return isNaN(n) ? { n: null, s: td.dataset.sort.toLowerCase() } : { n, s: "" };
    }
    const input = td.querySelector("input:not([type=checkbox]):not([type=hidden]), select");
    const text = (input ? input.value : td.innerText || td.textContent || "").trim();
    const num = text.replace(/[$,\s%]/g, "").replace(/^\((.*)\)$/, "-$1").match(/^[-+]?\d*\.?\d+/);
    if (num && /^[-+$(]?\s*[\d$]/.test(text) && !/^\d{1,2}\/\d{1,2}\/\d{2,4}/.test(text)) return { n: parseFloat(num[0]), s: "" };
    const date = /^\d{1,2}\/\d{1,2}\/\d{2,4}|^\d{4}-\d{2}-\d{2}/.test(text) ? Date.parse(text.replace(/,? .*$/, "") || text) : NaN;
    if (!isNaN(date)) return { n: date, s: "" };
    return { n: null, s: text.toLowerCase() };
  },

  enableSort(table, ths) {
    let col = -1, dir = 0, sorting = false;
    const original = new WeakMap();
    let seq = 0;
    const apply = () => {
      if (sorting) return;
      sorting = true;
      Array.from(table.tBodies).forEach(tb => {
        const rows = Array.from(tb.rows);
        rows.forEach(r => { if (!original.has(r)) original.set(r, seq++); });
        const sortable = rows.filter(r => !r.querySelector("td[colspan]"));
        if (sortable.length < 2) return;
        const sorted = [...sortable].sort((a, b) => {
          if (dir === 0) return original.get(a) - original.get(b);
          const at = Array.from(ths[col].parentNode.cells).indexOf(ths[col]);  // columns may have been moved
          const x = this.sortKey(a.cells[at]), y = this.sortKey(b.cells[at]);
          let c;
          if (x.n != null && y.n != null) c = x.n - y.n;
          else if (x.n != null || y.n != null) c = x.n != null ? -1 : 1;  // numbers before text/blank
          else if (!x.s || !y.s) c = !x.s && !y.s ? 0 : !x.s ? 1 : -1;  // blanks last
          else c = x.s.localeCompare(y.s, undefined, { numeric: true });
          return c * dir || original.get(a) - original.get(b);
        });
        if (sorted.some((r, i) => r !== sortable[i])) sorted.forEach(r => tb.appendChild(r));
      });
      sorting = false;
    };
    ths.forEach((th, i) => {
      if (!th.dataset.label || th.dataset.nosort !== undefined || th.querySelector("input")) return;
      th.classList.add("sortable");
      th.addEventListener("click", e => {
        if (e.target.closest("a, input, button, select, .col-resizer")) return;
        if (col !== i) { col = i; dir = 1; } else dir = dir === 1 ? -1 : dir === -1 ? 0 : 1;
        ths.forEach(h => h.removeAttribute("data-sort-dir"));
        if (dir) th.dataset.sortDir = dir === 1 ? "asc" : "desc";
        apply();
      });
    });
    Array.from(table.tBodies).forEach(tb => new MutationObserver(() => { if (dir && !sorting) apply(); }).observe(tb, { childList: true }));
  },

  // A cell's value is its data-value if set, else the number at the start of its text.
  cellValue(td) {
    if (td.dataset.value !== undefined) return { v: parseFloat(td.dataset.value) || 0, money: td.dataset.money !== undefined };
    const text = (td.firstChild ? td.firstChild.textContent : "").trim();
    if (!text) return null;
    const n = parseFloat(text.replace(/[$,\s]/g, ""));
    return isNaN(n) ? null : { v: n, money: text.includes("$") };
  },

  refreshTotals(table) {
    const ths = Array.from(table.tHead.rows[0].cells);
    const rows = Array.from(table.tBodies).flatMap(tb => Array.from(tb.rows))
      .filter(tr => tr.style.display !== "none" && !tr.querySelector("td[colspan]"));
    const sums = ths.map(() => ({ v: 0, money: false }));
    rows.forEach(tr => Array.from(tr.cells).forEach((td, i) => {
      if (!sums[i] || !ths[i].classList.contains("sum")) return;
      const c = this.cellValue(td);
      if (c) { sums[i].v += c.v; sums[i].money = sums[i].money || c.money; }
    }));
    const cells = ths.map((th, i) => th.classList.contains("sum")
      ? `<td class="num">${sums[i].money ? fmtMoney(sums[i].v) : fmtQty(sums[i].v)}</td>`
      : "<td></td>");
    if (!ths[0].classList.contains("sum")) cells[0] = `<td class="nowrap">Total · ${rows.length} row${rows.length === 1 ? "" : "s"}</td>`;
    const html = `<tr>${cells.join("")}</tr>`;
    if (table.tFoot.innerHTML !== html) table.tFoot.innerHTML = html;
  },

  enhanceAll(root = document) {
    root.querySelectorAll("table").forEach(t => this.enhance(t));
  },
};

// Employees: hide every table column whose header is a dollar amount.
const MONEY_HEADER = /\b(price|cost|amount|total|subtotal|balance|paid|revenue|profit|margin|charge|funding|discount|value|invoices?)\b|\$/i;
let moneySeq = 0;
function hideMoneyColumns(root = document) {
  if (!hidesMoney()) return;
  const tables = root.tagName === "TABLE" ? [root] : Array.from(root.querySelectorAll("table"));
  tables.forEach(table => {
    if (table.dataset.moneyHidden || !table.tHead || !table.tHead.rows.length) return;
    const cols = Array.from(table.tHead.rows[0].cells).map((th, i) => MONEY_HEADER.test(th.textContent) ? i : -1).filter(i => i >= 0);
    table.dataset.moneyHidden = String(++moneySeq);
    if (!cols.length) return;
    const style = document.createElement("style");
    style.textContent = cols.map(i => `table[data-money-hidden="${moneySeq}"] tr > :nth-child(${i + 1}) { display: none; }`).join("\n");
    document.head.appendChild(style);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  if (hidesMoney()) document.body.classList.add("no-money");
  hideMoneyColumns();
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) hideMoneyColumns(n);
  }).observe(document.body, { childList: true, subtree: true });
  TableTools.enhanceAll();
  // Detail panels build their tables on the fly -- pick those up too.
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.tagName === "TABLE") TableTools.enhance(n);
      else if (n.querySelector("table")) TableTools.enhanceAll(n);
    }
  }).observe(document.body, { childList: true, subtree: true });
});

// Keep the cached user in step with the server: a session saved before a role
// change (or before roles existed) would otherwise hide menus like Users & Roles.
(async function refreshSessionUser() {
  if (!AuthGuard.getToken() || location.pathname.endsWith("login.html")) return;
  try {
    const fresh = await apiFetch("/api/auth/me");
    const cached = AuthGuard.getUser() || {};
    if (JSON.stringify(fresh) === JSON.stringify(cached)) return;
    localStorage.setItem("at_hub_user", JSON.stringify(fresh));
    const sidebar = document.getElementById("sidebar");
    const active = sidebar && sidebar.querySelector(".sidebar a.active");
    if (sidebar) sidebar.innerHTML = renderSidebar(active ? active.getAttribute("href") : location.pathname.split("/").pop());
  } catch (e) { /* apiFetch already handles 401 */ }
})();

// Pack size history popup (Stock Items and Batch Shipments): the current default and
// every earlier size, so an old pack size can still be found after it was replaced.
async function showPackSizeHistory(itemId) {
  let modal = document.getElementById("pack-history-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "pack-history-modal";
    modal.className = "modal-backdrop";
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.body.appendChild(modal);
  }
  modal.innerHTML = `<div class="modal"><p class="muted">Loading…</p></div>`;
  try {
    const [rows, item] = await Promise.all([apiFetch(`/api/stock-items/pack-sizes/history?item_id=${itemId}`), apiFetch(`/api/stock-items/${itemId}`)]);
    modal.innerHTML = `<div class="modal">
      <h3 style="margin-top:0;">${escapeHtml(item.code)} — Pack Sizes</h3>
      <p>Current Default: <strong>${item.default_pack_size ?? "None"}</strong></p>
      ${rows.length ? `<table class="compact-table no-table-tools">
        <thead><tr><th>Changed</th><th class="num">From</th><th class="num">To</th><th>How</th><th>For</th><th>By</th></tr></thead>
        <tbody>${rows.map(h => `<tr>
          <td>${new Date(h.changed_at).toLocaleString()}</td><td class="num">${h.previous_pack_size ?? "—"}</td><td class="num"><strong>${h.pack_size ?? "—"}</strong></td>
          <td>${escapeHtml(h.source || "")}</td><td class="small">${escapeHtml(h.reference || "")}</td><td>${escapeHtml(h.changed_by || "")}</td></tr>`).join("")}</tbody>
      </table>` : `<p class="muted">No Changes Recorded Yet (History Starts From This Update).</p>`}
      <button class="secondary" style="margin-top:12px;" onclick="document.getElementById('pack-history-modal').remove()">Close</button>
    </div>`;
  } catch (err) {
    modal.innerHTML = `<div class="modal"><p class="error">${escapeHtml(err.message)}</p></div>`;
  }
}

// ---- MTRs on a customer order: what shipped, which MTR certifies it, email to the customer ----
// Traced = shipped from a lot received on a PO line the MTR is linked to. "Other" = more MTRs
// on file for the same item (other vendors / receipts), shown so one can still be sent.
function mtrRowHtml(r, checked) {
  return `<label class="check-label" style="white-space:normal;align-items:flex-start;">
    <input type="checkbox" class="om-mtr" value="${r.attachment_id}" ${checked ? "checked" : ""}>
    <span><a class="link" onclick="event.preventDefault(); openAttachment(${r.attachment_id})">${escapeHtml(r.filename)}</a>
      <span class="muted small"> · ${escapeHtml(r.po_code || "")} · ${escapeHtml(r.vendor || "")} · ${r.po_date ? new Date(r.po_date).toLocaleDateString() : ""}${r.heat_number ? ` · Heat ${escapeHtml(r.heat_number)}` : ""}</span></span></label>`;
}

async function renderOrderMtrs(container, orderId) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  el.innerHTML = `<p class="muted small">Loading MTRs…</p>`;
  let d;
  try { d = await apiFetch(`/api/mtrs/customer-order/${orderId}`); }
  catch (err) { el.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`; return; }
  const any = d.lines.some(l => l.traced.length || l.other.length);
  el.innerHTML = `
    <table class="compact-table no-table-tools">
      <thead><tr><th>Item</th><th>Traced MTRs (Shipped From These Receipts)</th><th>Other MTRs For This Item</th></tr></thead>
      <tbody>${d.lines.map(l => `<tr>
        <td><strong>${escapeHtml(l.item_code || "")}</strong><div class="muted small">${escapeHtml(l.item_title || "")}</div></td>
        <td>${l.traced.length ? [...new Map(l.traced.map(r => [r.attachment_id, r])).values()].map(r => mtrRowHtml(r, true)).join("")
              : `<span class="muted small">${l.shipped_lots ? "No MTR Linked To The Lots Shipped" : "Not Shipped Yet"}</span>`}
            ${l.untraced ? `<div class="neg small">Some Units Shipped From A Stock Adjustment Lot (No PO Receipt)</div>` : ""}</td>
        <td>${l.other.length ? [...new Map(l.other.map(r => [r.attachment_id, r])).values()].map(r => mtrRowHtml(r, false)).join("") : `<span class="muted small">None</span>`}</td>
      </tr>`).join("")}</tbody>
    </table>
    ${any ? `
      <div class="panel" style="margin-top:10px;max-width:640px;">
        <div class="row">
          <div><label>To</label><input type="text" id="om-to" value="${escapeHtml(d.customer_email || "")}" placeholder="customer@example.com"></div>
          <div><label>CC</label><input type="text" id="om-cc"></div>
        </div>
        <label>Subject</label><input type="text" id="om-subject" value="Material Test Reports — ${escapeHtml(d.order_code)}${d.po_number ? ` (Your PO ${escapeHtml(d.po_number)})` : ""}">
        <label>Message</label><textarea id="om-body" rows="4">Hello,

Please find attached the material test reports for order ${escapeHtml(d.order_code)}${d.po_number ? ` (your PO ${escapeHtml(d.po_number)})` : ""}.

Thank you.</textarea>
        <div style="margin-top:10px;"><button id="om-send">Email Selected MTRs</button></div>
        <div id="om-error" class="error"></div>
      </div>` : `<p class="muted small">No MTRs On File For These Items Yet.</p>`}
    ${d.emails.length ? `<div class="muted small" style="margin-top:8px;">${d.emails.map(e =>
      `Sent ${escapeHtml(e.files || "")} to ${escapeHtml(e.to)} by ${escapeHtml(e.sent_by || "")} · ${new Date(e.sent_at + "Z").toLocaleString()}`).join("<br>")}</div>` : ""}`;
  const btn = el.querySelector("#om-send");
  if (!btn) return;
  btn.onclick = async () => {
    const err = el.querySelector("#om-error");
    err.textContent = "";
    const ids = [...new Set(Array.from(el.querySelectorAll(".om-mtr:checked")).map(c => parseInt(c.value)))];
    if (!ids.length) { err.textContent = "Tick At Least One MTR."; return; }
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      await apiFetch("/api/mtrs/email", { method: "POST", body: JSON.stringify({
        attachment_ids: ids, order_id: orderId, to: el.querySelector("#om-to").value, cc: el.querySelector("#om-cc").value || null,
        subject: el.querySelector("#om-subject").value, body: el.querySelector("#om-body").value }) });
      renderOrderMtrs(el, orderId);
    } catch (e) { err.textContent = e.message; btn.disabled = false; btn.textContent = "Email Selected MTRs"; }
  };
}

// ---- Icons: a small inline SVG set (Lucide-style strokes), no external library ----
const ICON_PATHS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6M9 16h6"/>',
  truck: '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
  package: '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><path d="m3.3 7 8.7 5 8.7-5"/>',
  checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
  tag: '<path d="M12.59 2.59A2 2 0 0 0 11.17 2H4a2 2 0 0 0-2 2v7.17a2 2 0 0 0 .59 1.41l8.7 8.7a2.43 2.43 0 0 0 3.42 0l6.58-6.58a2.43 2.43 0 0 0 0-3.42z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
  factory: '<path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/>',
  cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
  anchor: '<path d="M12 22V8"/><path d="M5 12H2a10 10 0 0 0 20 0h-3"/><circle cx="12" cy="5" r="3"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  barcode: '<path d="M3 5v14M7 5v14M11 5v14M14 5v14M18 5v14M21 5v14"/>',
  fileCheck: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="m9 15 2 2 4-4"/>',
  chart: '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
  building: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
  user: '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
  printer: '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
  mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 13H8M16 17H8M16 13h-2"/>',
  save: '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7M7 3v4a1 1 0 0 0 1 1h7"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M5 12h14M12 5v14"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5M12 3v12"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
  undo: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  eye: '<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  dollar: '<path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  sparkles: '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2Z"/><path d="M19 3v4M17 5h4"/>',
  pencil: '<path d="M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/>',
  pin: '<path d="M20 10c0 4.99-5.53 10.19-7.4 11.8a1 1 0 0 1-1.2 0C9.53 20.19 4 14.99 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
  type: '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
  paperclip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
};
function icon(name, cls = "") {
  return ICON_PATHS[name] ? `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name]}</svg>` : "";
}
const NAV_ICONS = {
  "dashboard.html": "dashboard", "customers.html": "users", "customer-orders.html": "clipboard", "shipments.html": "truck",
  "pack-shipments.html": "package", "pod.html": "checkCircle", "labels.html": "tag", "invoices.html": "receipt",
  "vendors.html": "factory", "purchase-orders.html": "cart", "landed-costs.html": "anchor", "stock-items.html": "layers",
  "lots.html": "barcode", "mtrs.html": "fileCheck", "reports.html": "chart", "company.html": "building",
  "users.html": "shield", "account.html": "user",
};
// First matching keyword wins. Buttons are matched on their text, section titles likewise.
const BUTTON_ICONS = [
  [/print/i, "printer"], [/e-?mail|send/i, "mail"], [/\bpdf\b/i, "file"], [/^\s*save/i, "save"], [/upload/i, "upload"],
  [/delete|remove/i, "trash"], [/^\s*(cancel|close|discard)/i, "x"], [/receive/i, "download"], [/reset|undo|unship|unbook|roll ?back/i, "undo"],
  [/preview/i, "eye"], [/\bai\b|read po/i, "sparkles"], [/payment|^\s*pay\b|funding/i, "dollar"], [/^\s*edit/i, "pencil"],
  [/label/i, "tag"], [/profit|report/i, "chart"], [/history/i, "clock"], [/^\s*(add|new|create)\b/i, "plus"],
  [/apply|confirm|mark|deliver/i, "check"], [/ship|pick|pack/i, "truck"],
];
const SECTION_ICONS = [
  [/lines|items/i, "list"], [/details|summary/i, "info"], [/attach/i, "paperclip"], [/mtr|test report/i, "fileCheck"],
  [/carrier|tracking/i, "truck"], [/e-?mail/i, "mail"], [/payment|invoice|funding|bill/i, "receipt"], [/landed/i, "anchor"],
  [/receiv/i, "download"], [/pack|box|pallet/i, "package"], [/label/i, "tag"], [/deliver|pod\b/i, "checkCircle"],
  [/history|activity/i, "clock"], [/combine/i, "layers"], [/profit/i, "chart"],
];
function decorateIcons(root = document) {
  if (!root || !root.querySelectorAll) return;
  const add = (el, rules) => {
    if (el.dataset.ico || el.querySelector("svg, img") || el.closest(".no-icons")) return;
    const text = el.textContent.trim();
    if (!text || text.startsWith("+")) return;
    const hit = el.dataset.icon ? [null, el.dataset.icon] : rules.find(([re]) => re.test(text));
    el.dataset.ico = "1";
    if (hit) el.insertAdjacentHTML("afterbegin", icon(hit[1]));
  };
  root.querySelectorAll("button, [data-icon]").forEach(el => add(el, BUTTON_ICONS));
  root.querySelectorAll(".dsec-title, .card > h3, .card > h4").forEach(el => add(el, SECTION_ICONS));
}
document.addEventListener("DOMContentLoaded", () => {
  decorateIcons(document);
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1 && n.tagName.toLowerCase() !== "svg") decorateIcons(n.parentNode || n);
  }).observe(document.body, { childList: true, subtree: true });
});

// ---- Action items (Reports + Dashboard): stuck shipments, missing PODs, vendor follow-ups ----
// Each section is collapsible; sections with nothing to do collapse to a green "all clear" line.
const ACTION_COLUMNS = {
  not_delivered: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => r.customer], ["Carrier / Tracking", r => [r.carrier, r.tracking_number].filter(Boolean).join(" · ")], ["Shipped", r => actDate(r.ship_date)], ["Days", r => r.days]],
  missing_pod: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => r.customer], ["Shipped", r => actDate(r.ship_date)], ["Delivered?", r => r.delivered ? "Yes" : "No"], ["Days", r => r.days]],
  not_shipped: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => r.customer], ["Status", r => r.status], ["Days Waiting", r => r.days]],
  late_orders: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => r.customer], ["Customer PO", r => r.po_number], ["Due", r => actDate(r.due)], ["Days Late", r => r.days]],
  vendor_shipped: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => r.vendor], ["Vendor Invoices", r => r.bills], ["Received", r => `${fmtQty(r.received_qty)} / ${fmtQty(r.ordered_qty)}`], ["Days Since Invoice", r => r.days]],
  po_overdue: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => r.vendor], ["Expected", r => actDate(r.expected)], ["Received", r => `${fmtQty(r.received_qty)} / ${fmtQty(r.ordered_qty)}`], ["Days Late", r => r.days]],
  bills_due: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => r.vendor], ["Invoice #", r => r.bill_number], ["Balance", r => fmtMoney(r.balance)], ["Due", r => actDate(r.due)], ["Days Overdue", r => r.days]],
  unapplied_payments: [["Payment", r => r.code], ["Vendor", r => r.vendor], ["Paid", r => actDate(r.paid_date)], ["Amount", r => fmtMoney(r.amount)], ["Unapplied", r => fmtMoney(r.unapplied)], ["Days", r => r.days]],
  mtr_unlinked: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["File", r => r.filename], ["Uploaded (Days Ago)", r => r.days]],
};
function actLink(page, id, text) { return id ? `<a class="link" href="${page}?id=${id}">${escapeHtml(text || "")}</a>` : escapeHtml(text || ""); }
function actDate(v) { return v ? new Date(v).toLocaleDateString() : ""; }

async function renderActionItems(container, onlyKeys = null) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  let sections;
  try { sections = await apiFetch("/api/reports/action-items"); }
  catch (err) { el.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`; return; }
  if (onlyKeys) sections = sections.filter(s => onlyKeys.includes(s.key));
  const open = sections.filter(s => s.rows.length);
  el.classList.remove("muted");
  el.innerHTML = `
    <div class="action-chips">${sections.map(s => `<a class="action-chip ${s.rows.length ? "has" : "ok"}" href="#act-${s.key}" onclick="document.getElementById('act-${s.key}').open = true">
      <span class="n">${s.rows.length}</span>${escapeHtml(s.title)}</a>`).join("")}</div>
    ${open.length ? "" : `<p class="pos" style="margin:10px 0 0;">${icon("checkCircle")} All Clear — Nothing Needs Follow-Up.</p>`}
    ${open.map(s => {
      const cols = ACTION_COLUMNS[s.key] || [];
      return `<details class="action-section" id="act-${s.key}" ${open.length <= 3 ? "open" : ""}>
        <summary><span class="tag overdue">${s.rows.length}</span> ${escapeHtml(s.title)} <span class="muted small">— ${escapeHtml(s.help)}</span></summary>
        <table class="compact-table"><thead><tr>${cols.map(([h]) => `<th${/days|balance|amount|unapplied/i.test(h) ? ' class="num"' : ""}>${h}</th>`).join("")}</tr></thead>
          <tbody>${s.rows.map(r => `<tr>${cols.map(([h, f]) => `<td${/days|balance|amount|unapplied/i.test(h) ? ' class="num"' : ""}>${f(r) ?? ""}</td>`).join("")}</tr>`).join("")}</tbody></table>
      </details>`;
    }).join("")}`;
}


// ---- Theme: light / dark, remembered per browser; follows the OS until chosen ----
function currentTheme() {
  let saved = null;
  try { saved = localStorage.getItem("at_hub_theme"); } catch {}
  return saved || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function applyTheme(theme) { document.documentElement.dataset.theme = theme; }
function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  try { localStorage.setItem("at_hub_theme", next); } catch {}
  applyTheme(next);
  const label = document.querySelector("#theme-toggle span");
  if (label) label.textContent = next === "dark" ? "Light Mode" : "Dark Mode";
}
applyTheme(currentTheme());  // runs in <head>, before the page paints

// ---- AI document scan ----
// aiScanButton("vendor_invoice", "applyInvoiceScan", {poId}) renders a small "AI" chip. Clicking it
// picks a PDF/photo, the private local model reads it, and window[callback](result, file) gets a
// *suggestion* to fill the form with -- the user still reviews and saves.
function aiScanButton(kind, callback, opts = {}) {
  const title = opts.title || "Scan a document with AI and pre-fill this form";
  const cloud = ["vendor_invoice", "vendor_order"].includes(kind) && AuthGuard.hasRole("manager")
    ? ` <button type="button" class="ai-btn cloud" title="For a hard document: read it with Claude (Anthropic, cloud). Your company details and bank numbers are blanked out on this PC first; only that text is sent. About 1-3 cents."
        onclick="aiScan(this, '${kind}', '${callback}', ${opts.poId || "null"}, 'claude')">☁ Ask Claude</button>` : "";
  return `<button type="button" class="ai-btn" data-icon="sparkles" title="${escapeHtml(title)}"
    onclick="aiScan(this, '${kind}', '${callback}', ${opts.poId || "null"})">${escapeHtml(opts.label || "AI Scan")}</button>${cloud}`;
}
function aiScan(btn, kind, callback, poId, engine = "local") {
  if (engine === "claude" && !confirm("Send this document to Claude (Anthropic's cloud) to read?\n\n"
      + "Before it leaves this PC: your company name/addresses, the bill-to/ship-to block and any bank numbers are blanked out. "
      + "Only that text is sent, never the PDF. Scanned images are not sent.")) return;
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = "application/pdf,image/*";
  picker.onchange = async () => {
    const file = picker.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("kind", kind);
    if (engine === "claude") form.append("engine", "claude");
    if (poId) form.append("po_id", poId);
    form.append("file", file);
    const label = btn.lastChild.textContent;
    btn.disabled = true;
    btn.classList.add("busy");
    btn.lastChild.textContent = " Reading…";
    try {
      const result = await apiUpload("/api/ai-docs/extract", form);
      window[callback](result, file);
    } catch (err) {
      alert(`AI scan failed: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.classList.remove("busy");
      btn.lastChild.textContent = label;
    }
  };
  picker.click();
}
// Put the scanned file into a page's <input type=file> so it's saved with the record.
function setFileInput(input, file) {
  if (!input || !file) return;
  try { const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files; } catch {}
}

// ---- Invoice chip: one compact look for an invoice everywhere it's listed ----
// "● INV-0005" with the dot coloured by status; a combined invoice turns dark pink with "⧉ 3"
// (shipments on it). Hover gives the full story. Accepts an invoice object, or a shipment's
// invoice_* fields via invoiceChipFromShipment().
function invoiceChip(inv, opts = {}) {
  if (!inv || !inv.code) return "";
  const ships = inv.shipment_codes || [];
  const combined = inv.is_combined || ships.length > 1;
  const status = inv.status || "draft";
  const tip = [`${inv.code} · ${status[0].toUpperCase()}${status.slice(1)}`,
    combined ? `Combined invoice: ${ships.length} shipments (${ships.join(" + ")})` : (ships[0] ? `Shipment ${ships[0]}` : ""),
    inv.combined_from && inv.combined_from.length ? `Merged in: ${inv.combined_from.join(", ")}` : "",
    opts.here ? `This shipment: ${opts.here}` : ""].filter(Boolean).join("\n");
  return `<a class="inv-chip st-${escapeHtml(status)}${combined ? " combined" : ""}" href="invoices.html?id=${inv.id}"
    onclick="event.stopPropagation()" title="${escapeHtml(tip)}"><i></i>${escapeHtml(inv.code)}${combined ? `<b>⧉ ${ships.length}</b>` : ""}</a>`;
}
function invoiceChipFromShipment(s) {
  if (!s.invoice_code) return "";
  return invoiceChip({ id: s.invoice_id, code: s.invoice_code, status: s.invoice_status,
    shipment_codes: s.invoice_shipment_codes && s.invoice_shipment_codes.length ? s.invoice_shipment_codes : [s.code],
    combined_from: s.invoice_combined_from, is_combined: s.invoice_combined }, { here: s.code });
}


// ---- AI match suggestions ----
// Chips under a scanned line: "15343 · BOLT_HH_5/8-11x1-1/4… 85%". Clicking one picks that item in the
// line's <select> (and fires change). The chosen one is highlighted; nothing is final until the form is saved.
function aiCandidateChips(candidates, selectId, pickedId) {
  if (!candidates || !candidates.length) return `<div class="ai-cands muted small">No similar items found: pick one from the list.</div>`;
  return `<div class="ai-cands"><span class="small muted">${pickedId ? "Other matches:" : "Best matches, pick one:"}</span>${candidates.map(c =>
    `<button type="button" class="ai-cand${c.item_id === pickedId ? " on" : ""}" title="${escapeHtml(`${c.title}\n${c.why}`)}"
      onclick="aiPickCandidate(this, '${selectId}', ${c.item_id})"><b>${escapeHtml(c.code)}</b> ${escapeHtml(c.title.length > 38 ? c.title.slice(0, 38) + "…" : c.title)}
      <i>${Math.round(c.score * 100)}%</i></button>`).join("")}</div>`;
}
function aiPickCandidate(btn, selectId, itemId) {
  const select = document.getElementById(selectId);
  if (!select) return;
  select.value = itemId;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  btn.parentElement.querySelectorAll(".ai-cand").forEach(b => b.classList.toggle("on", b === btn));
  btn.closest("tr") && btn.closest("tr").classList.remove("ai-unmatched");
  if (btn.closest("tr") && btn.closest("tr").aiAfterPick) btn.closest("tr").aiAfterPick();
}
// Vendor chips for a scanned vendor document.
function aiVendorChips(cands, selectId) {
  if (!cands || !cands.length) return "";
  return `<div class="ai-cands"><span class="small muted">Vendor matches:</span>${cands.map(c =>
    `<button type="button" class="ai-cand" title="${escapeHtml(`Matched on ${c.why}`)}" onclick="aiPickCandidate(this, '${selectId}', ${c.vendor_id})">
      <b>${escapeHtml(c.code || "")}</b> ${escapeHtml(c.name)} <i>${Math.round(c.score * 100)}%</i></button>`).join("")}</div>`;
}

// ---- Record pages: opening a record shows it as its own screen, not a panel at the bottom ----
// Every list page shows a record by filling and un-hiding #detail-card. Watching that card here gives
// all of them the same behaviour: the list hides, the record starts at the top of the screen with a
// "Back" bar, and the browser's Back button returns to the list. (Pack Shipments embeds the card in a
// row instead, so it opts out.)
(function recordPages() {
  const NO_PAGE_MODE = ["pack-shipments.html", "pod.html"];
  document.addEventListener("DOMContentLoaded", () => {
    const card = document.getElementById("detail-card");
    const main = card && card.closest("main");
    if (!card || !main || NO_PAGE_MODE.includes(location.pathname.split("/").pop())) return;
    const listName = (document.title.split("—")[1] || "List").trim();
    let open = false;
    const ensureBar = () => {
      if (card.querySelector(":scope > .record-backbar")) return;
      card.insertAdjacentHTML("afterbegin", `<div class="record-backbar"><a class="link" onclick="closeRecordPage()">← Back to ${escapeHtml(listName)}</a></div>`);
    };
    const sync = () => {
      const visible = card.style.display !== "none" && card.innerHTML.trim() !== "";
      if (visible) ensureBar();
      if (visible === open) return;
      open = visible;
      main.classList.toggle("record-mode", open);
      if (open) {
        window.scrollTo({ top: 0 });
        if (!(history.state && history.state.record)) history.pushState({ record: true }, "", location.href);
      }
    };
    new MutationObserver(sync).observe(card, { attributes: true, attributeFilter: ["style"], childList: true });
    window.addEventListener("popstate", () => { if (open) { card.style.display = "none"; } });
    window.closeRecordPage = () => {
      if (history.state && history.state.record) history.back();  // popstate hides the card
      else card.style.display = "none";
    };
    sync();
  });
})();
