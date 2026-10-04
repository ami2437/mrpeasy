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
// One rounding rule everywhere (same as the server's services/money.py): each line rounds to the cent,
// half up, and totals add up the rounded lines -- so the screen, the PDF and the balance always agree.
function lineAmount(qty, price) {
  const v = Number(((qty || 0) * (price || 0)).toPrecision(12));  // drop float noise (2.675 is really 2.67499...)
  return Math.sign(v) * Math.round(Math.abs(v) * 100 + 1e-7) / 100;
}
function sumLines(lines, priceKey = "unit_price") {
  return Math.round(lines.reduce((s, l) => s + lineAmount(l.quantity, l[priceKey]), 0) * 100) / 100;
}

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

// Before invoicing: shipments that went out but aren't marked delivered get a reminder with a date
// to mark them delivered. Never a blocker -- "Invoice Anyway" carries on. Resolves true to go ahead.
async function deliveredCheckBeforeInvoice(shipments) {
  const pending = (shipments || []).filter(s => s && s.status === "shipped" && !s.delivered_at);
  if (!pending.length) return true;
  const today = new Date().toISOString().substring(0, 10);
  const { value, el } = await askDialog({ title: pending.length === 1 ? "Not delivered yet" : `${pending.length} shipments not delivered yet`, tone: "warn",
    body: `<p>${pending.map(s => `<strong>${escapeHtml(s.code)}</strong>${s.ship_date ? ` shipped ${fmtDate(s.ship_date)}` : ""}`).join(", ")}
        ${pending.length === 1 ? "isn't" : "aren't"} marked delivered. Mark ${pending.length === 1 ? "it" : "them"} delivered to complete the order's flow, or invoice anyway.</p>
      <label>Delivered on</label><input type="date" class="ask-delivered" value="${today}" max="${today}" style="max-width:180px;">
      <p class="muted small" style="margin-top:6px;">Uploading a proof of delivery later also marks it delivered.</p>`,
    buttons: [{ label: "Mark Delivered & Invoice", value: "mark", cls: "confirm-btn" }, { label: "Invoice Anyway", value: "skip", cls: "secondary" },
              { label: "Cancel", value: null, cls: "secondary" }] });
  if (value === "skip") return true;
  if (value !== "mark") return false;
  const date = el.querySelector(".ask-delivered").value;
  try {
    for (const s of pending)
      await apiFetch(`/api/shipments/${s.id}/delivered`, { method: "POST", body: JSON.stringify({ delivered_at: date ? `${date}T12:00:00` : null }) });
  } catch (e) { alert(e.message); return false; }
  toast(`Marked ${pending.map(s => s.code).join(", ")} delivered`);
  return true;
}

// A small choice pop-up: resolves to the clicked button's value (null on Esc / click outside),
// with the dialog element so the caller can read any inputs in `body` before it closes.
// askDialog({ title, body: html, buttons: [{ label, value, cls }] }) -> Promise<{ value, el }>
function askDialog({ title, body = "", buttons = [], tone = "" }) {
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-backdrop";
    back.innerHTML = `<div class="modal ask-dialog ${tone}" role="dialog" aria-modal="true"><h3 style="margin:0 0 8px;">${escapeHtml(title)}</h3>
      <div class="ask-body">${body}</div>
      <div class="btn-row" style="margin-top:14px;">${buttons.map((b, i) => `<button type="button" class="${b.cls || ""}" data-i="${i}">${escapeHtml(b.label)}</button>`).join("")}</div></div>`;
    const done = value => { document.removeEventListener("keydown", onKey); back.remove(); resolve({ value, el: back }); };
    const onKey = e => { if (e.key === "Escape") done(null); };
    back.addEventListener("click", e => {
      if (e.target === back) return done(null);
      const btn = e.target.closest("button[data-i]");
      if (btn) { const b = buttons[+btn.dataset.i]; done(b.value); }
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(back);
    decorateIcons(back);
    const first = back.querySelector("input, button[data-i]");
    if (first) first.focus();
  });
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
// Item pickers: archived items and AI-created ones nobody has verified yet can't be picked for new lines.
// They stay in the list (greyed, with the reason) so a line that already has one still shows it.
// forSale: customer orders and quotes -- generic bulk stock (58-NUT) is never sold directly.
function itemPickBlock(i, forSale = false) {
  if (i.is_active === false) return "archived";
  if (i.created_via === "ai-scan" && !i.verified_by) return "needs verifying (Stock Items)";
  if (forSale && i.is_generic) return "generic bulk stock, not sold directly";
  return "";
}
function itemPickAttr(i, forSale = false) { return itemPickBlock(i, forSale) ? `disabled data-blocked="${escapeHtml(itemPickBlock(i, forSale))}"` : ""; }
function itemPickNote(i, forSale = false) { return itemPickBlock(i, forSale) ? ` — ${itemPickBlock(i, forSale)}` : ""; }

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
      ? shown.map((o, i) => `<div class="search-select-option${i === 0 ? " active" : ""}${o.disabled ? " blocked" : ""}" data-i="${i}">${escapeHtml(o.textContent)}${o.dataset.search ? ` <span class="muted small">${escapeHtml(o.dataset.search)}</span>` : ""}</div>`).join("")
        + (matches.length > 50 ? `<div class="muted small" style="padding:6px 10px;">${matches.length - 50} More — Keep Typing To Narrow Down</div>` : "")
      : `<div class="muted small" style="padding:6px 10px;">No Items Match "${escapeHtml(input.value)}"</div>`;
    list.style.display = "block";
  };
  const choose = i => {
    const o = matches[i];
    if (!o) return;
    if (o.disabled) {  // archived / not verified: say why instead of picking it
      list.innerHTML = `<div class="small neg" style="padding:6px 10px;">${escapeHtml(o.textContent.split(" — ")[0])} can't be picked: ${escapeHtml(o.dataset.blocked || "not available")}.</div>`;
      return;
    }
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
  vendor_quote: "Vendor Quote / Confirmation", pod: "Proof Of Delivery", bol: "Bill Of Lading", other: "Other",
  purchase_order: "Purchase Order", invoice: "Our Invoice", packing_list: "Packing List",
};
// Short tag names for the small chips on files and thumbnails
const ATTACHMENT_TAGS = {
  customer_po: "Customer PO", vendor_invoice: "Vendor Invoice", mtr: "MTR", vendor_quote: "Vendor Quote", pod: "POD",
  bol: "BOL", other: "Other", purchase_order: "Purchase Order", invoice: "Our Invoice", packing_list: "Packing List",
};
const MONEY_ATTACHMENTS = ["customer_po", "vendor_invoice", "vendor_quote", "purchase_order", "invoice"];
function attachmentTag(cat) { return `<span class="file-tag ft-${escapeHtml(cat)}">${escapeHtml(ATTACHMENT_TAGS[cat] || cat)}</span>`; }

// Thumbnail strip for the top right of an order / PO: every file on it, newest first, with its tag.
const attachmentThumbUrls = {};
async function renderFileStrip(container, entityType, entityId) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  let files = [];
  try { files = await apiFetch(`/api/attachments/?entity_type=${entityType}&entity_id=${entityId}`); } catch (e) { return; }
  files.sort((a, b) => b.id - a.id);
  el.innerHTML = files.map(f => `<a class="file-thumb" title="${escapeHtml(`${ATTACHMENT_LABELS[f.category] || f.category}: ${f.filename}`)}" onclick="openAttachment(${f.id})">
      <span class="file-thumb-img" data-thumb="${f.id}">${escapeHtml((f.filename.split(".").pop() || "file").slice(0, 4).toUpperCase())}</span>
      ${attachmentTag(f.category)}</a>`).join("")
    + (files.length ? "" : "");
  el.querySelectorAll("[data-thumb]").forEach(async box => {
    const id = box.dataset.thumb;
    try {
      if (!attachmentThumbUrls[id]) {
        const r = await fetch(`/api/attachments/${id}/thumb`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
        if (!r.ok) return;  // no preview (spreadsheet, email...): the file-type label stays
        attachmentThumbUrls[id] = URL.createObjectURL(await r.blob());
      }
      box.innerHTML = `<img src="${attachmentThumbUrls[id]}" alt="">`;
    } catch (e) { /* keep the label */ }
  });
}

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
  if (hidesMoney()) categories = categories.filter(c => !MONEY_ATTACHMENTS.includes(c));
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
              ${attachmentTag(f.category)} <a class="link" onclick="openAttachment(${f.id})">${escapeHtml(f.filename)}</a>
              ${canDelete(f) ? `<select class="att-retag" data-retag="${f.id}" title="What kind of document this is">${categories.map(c => `<option value="${c}" ${c === f.category ? "selected" : ""}>${ATTACHMENT_LABELS[c] || c}</option>`).join("")}</select>` : ""}
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
  el.querySelectorAll("[data-retag]").forEach(sel => sel.onchange = async () => {
    const form = new FormData();
    form.append("category", sel.value);
    try {
      const r = await fetch(`/api/attachments/${sel.dataset.retag}`, { method: "PUT", headers: { Authorization: `Bearer ${AuthGuard.getToken()}` }, body: form });
      if (!r.ok) throw new Error((await r.json()).detail || "Couldn't change the tag");
      renderAttachments(el, entityType, entityId, categories, opts);
      if (opts.onChange) opts.onChange();
    } catch (err) { errorEl.textContent = err.message; }
  });
  el.querySelectorAll("[data-del]").forEach(a => a.onclick = async () => {
    if (!confirm("Delete this file?")) return;
    try {
      await apiFetch(`/api/attachments/${a.dataset.del}`, { method: "DELETE" });
      renderAttachments(el, entityType, entityId, categories, opts);
      if (opts.onChange) opts.onChange();
      undoableDelete("Deleted a file", async () => { renderAttachments(el, entityType, entityId, categories, opts); if (opts.onChange) opts.onChange(); });
    } catch (err) {
      errorEl.textContent = err.message;
    }
  });
}

// Small grey product-group chip shown next to item codes.
function groupTag(item) {
  return (item && item.category ? `<span class="group-tag">${escapeHtml(item.category)}</span>` : "") + aiMadeTag(item);
}
// Items created from a scanned PO carry a small tag until someone has checked them.
function aiMadeTag(item) {
  return item && item.created_via === "ai-scan"
    ? `<span class="ai-made-tag" title="Created from a scanned customer PO${item.created_at ? " on " + new Date(item.created_at).toLocaleDateString() : ""} — check the title, group and price">AI</span>` : "";
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
  { label: null, links: [["dashboard.html", "Dashboard"], ["tasks.html", "Tasks", "admin"], ["ai-desk.html", "AI Desk", "manager"]] },
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
  { label: null, minRole: "manager", links: [["reports.html", "Reports"], ["company.html", "Company Settings", "admin"], ["recycle-bin.html", "Recycle Bin", "manager"]] },
  { label: "MRP Migrate", minRole: "admin", links: [["mrp-payments.html", "PO Payments Import"], ["file-matcher.html", "File Matcher", "super_admin"]] },
  { label: "Admin", minRole: "super_admin", links: [["users.html", "Users & Roles"], ["backups.html", "Backups"]] },
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
      <a class="sidebar-find" onclick="QuickFind.open()" title="Find anything (Ctrl+K)">${icon("search") || "⌕"}<span>Search</span><kbd>Ctrl K</kbd></a>
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
    const filterApi = this.enableFilter(table, ths);
    const viewsKey = `${key}:views`;
    const loadViews = () => { try { return JSON.parse(localStorage.getItem(viewsKey)) || {}; } catch { return {}; } };
    const viewsBtn = document.createElement("a");
    viewsBtn.className = "table-tools-btn";
    viewsBtn.textContent = "Views ▾";
    const viewsMenu = document.createElement("div");
    viewsMenu.className = "table-tools-menu";
    viewsMenu.style.display = "none";
    bar.prepend(viewsBtn, viewsMenu);
    // Export: what you see -- shown columns, rows that pass the filters (incl. ones folded past the first 50)
    const exportBtn = document.createElement("a");
    exportBtn.className = "table-tools-btn";
    exportBtn.textContent = "Export ▾";
    const exportMenu = document.createElement("div");
    exportMenu.className = "table-tools-menu";
    exportMenu.style.display = "none";
    exportMenu.innerHTML = `<a data-x="csv">CSV (.csv)</a><a data-x="xls">Excel (.xls)</a><a data-x="pdf">PDF / Print</a><a data-x="copy">Copy to clipboard</a>`;
    bar.prepend(exportBtn, exportMenu);
    exportBtn.onclick = () => { exportMenu.style.display = exportMenu.style.display === "none" ? "block" : "none"; };
    exportMenu.onclick = e => { const k = e.target.dataset.x; if (!k) return; exportMenu.style.display = "none"; TableTools.exportTable(table, k); };
    document.addEventListener("click", e => { if (!bar.contains(e.target)) exportMenu.style.display = "none"; });
    viewsBtn.onclick = () => {
      if (viewsMenu.style.display !== "none") { viewsMenu.style.display = "none"; return; }
      const views = loadViews();
      viewsMenu.innerHTML = (Object.keys(views).length
          ? Object.keys(views).map(name => `<div class="view-row"><a data-view="${escapeHtml(name)}">${escapeHtml(name)}</a><a class="muted" data-del="${escapeHtml(name)}" title="Delete this view">✕</a></div>`).join("")
          : `<div class="table-tools-hint">No saved views yet. Filter the columns (▾ on a header), then save it here.</div>`)
        + `<div class="table-tools-actions">${filterApi.active() ? `<a data-save="1">Save current filters…</a> · ` : ""}<a data-clear="1">Show everything</a></div>`;
      viewsMenu.style.display = "block";
    };
    viewsMenu.onclick = e => {
      const t = e.target, views = loadViews();
      if (t.dataset.view) { filterApi.set(views[t.dataset.view]); viewsMenu.style.display = "none"; }
      if (t.dataset.del) { delete views[t.dataset.del]; try { localStorage.setItem(viewsKey, JSON.stringify(views)); } catch {} viewsBtn.onclick(); viewsBtn.onclick(); }
      if (t.dataset.clear) { filterApi.set({}); viewsMenu.style.display = "none"; }
      if (t.dataset.save) {
        const name = prompt("Name this view (e.g. \"Unpaid Hudson invoices\"):");
        if (name && name.trim()) { views[name.trim()] = filterApi.get(); try { localStorage.setItem(viewsKey, JSON.stringify(views)); } catch {} }
        viewsMenu.style.display = "none";
      }
    };
    document.addEventListener("click", e => { if (!bar.contains(e.target)) viewsMenu.style.display = "none"; });

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

  // Header filters: a small ▾ on each header. Few distinct values (Status, Customer, Group...) -> tick boxes
  // with counts; many -> a "contains" box. Rows that don't match are hidden; totals follow. Filters
  // re-apply whenever the page re-renders its rows.
  cellText(td) { return td ? ((td.innerText || td.textContent || "").split("\n")[0] || "").trim() : ""; },
  enableFilter(table, ths) {
    const filters = {};  // header index -> {values: Set} | {text: "..."}
    const dataRows = () => Array.from(table.tBodies).flatMap(tb => Array.from(tb.rows)).filter(r => !r.querySelector("td[colspan]"));
    const colOf = i => Array.from(ths[i].parentNode.cells).indexOf(ths[i]);  // columns may have been moved
    const active = () => Object.keys(filters).length > 0;
    let applying = false;
    const apply = () => {
      if (applying) return;
      applying = true;
      const on = active();
      table.classList.toggle("tt-filtering", on);
      dataRows().forEach(r => {
        const ok = Object.entries(filters).every(([i, f]) => {
          const t = this.cellText(r.cells[colOf(+i)]);
          if (f.range) {  // {range: "num"|"date", min, max}
            const v = f.range === "num" ? TableTools.numOf(t) : TableTools.dateOf(t);
            if (v == null) return false;
            return (f.min == null || v >= f.min) && (f.max == null || v <= f.max);
          }
          return f.values ? f.values.has(t) : t.toLowerCase().includes(f.text);
        });
        if (on) r.style.display = ok ? "" : "none";
        else if (r.dataset.ttHid) r.style.display = "";
        r.dataset.ttHid = on && !ok ? "1" : "";
      });
      // "show the other N" / folded-group rows don't make sense while filtering
      Array.from(table.tBodies).forEach(tb => Array.from(tb.rows).forEach(r => {
        if (r.classList.contains("completed-toggle")) r.style.display = on ? "none" : "";
      }));
      ths.forEach((th, i) => th.classList.toggle("filtered", !!filters[i]));
      if (table.tFoot && table.tFoot.className === "totals-row") this.refreshTotals(table);
      applying = false;
    };
    let pop = null;
    const close = () => { if (pop) { pop.remove(); pop = null; } };
    ths.forEach((th, i) => {
      if (!th.dataset.label || th.querySelector("input")) return;
      const btn = document.createElement("span");
      btn.className = "th-filter";
      btn.title = "Filter this column";
      btn.textContent = "▾";
      btn.addEventListener("click", e => {
        e.stopPropagation();
        if (pop && pop.dataset.col === String(i)) { close(); return; }
        close();
        const counts = new Map();
        dataRows().forEach(r => { const t = this.cellText(r.cells[colOf(i)]); counts.set(t, (counts.get(t) || 0) + 1); });
        const values = [...counts.keys()].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
        const f = filters[i];
        const filled = values.filter(Boolean);
        const rangeKind = ths[i].classList.contains("num") ? "num"
          : ths[i].dataset.type === "date" || (filled.length && filled.every(v => TableTools.dateOf(v) != null)) ? "date" : null;
        const listMode = !rangeKind && values.length <= 30;
        pop = document.createElement("div");
        pop.className = "th-filter-pop";
        pop.dataset.col = String(i);
        const iso = ms => ms == null ? "" : new Date(ms).toISOString().substring(0, 10);
        pop.innerHTML = rangeKind
          ? `<div class="th-filter-range">
               <label>${rangeKind === "num" ? "Min" : "From"} <input type="${rangeKind === "num" ? "number" : "date"}" step="any" data-r="min" value="${f && f.range ? (rangeKind === "num" ? f.min ?? "" : iso(f.min)) : ""}"></label>
               <label>${rangeKind === "num" ? "Max" : "To"} <input type="${rangeKind === "num" ? "number" : "date"}" step="any" data-r="max" value="${f && f.range ? (rangeKind === "num" ? f.max ?? "" : iso(f.max)) : ""}"></label></div>
             <div class="th-filter-actions"><a data-a="clear">Clear filter</a></div>`
          : listMode
          ? `<div class="th-filter-list">${values.map(v => `<label><input type="checkbox" value="${escapeHtml(v)}" ${!f || (f.values && f.values.has(v)) ? "checked" : ""}> ${escapeHtml(v || "(blank)")} <span class="muted">${counts.get(v)}</span></label>`).join("")}</div>
             <div class="th-filter-actions"><a data-a="all">All</a> · <a data-a="none">None</a> · <a data-a="clear">Clear filter</a></div>`
          : `<input type="text" placeholder="Contains…" value="${escapeHtml(f && f.text || "")}">
             <div class="th-filter-actions"><a data-a="clear">Clear filter</a></div>`;
        document.body.appendChild(pop);
        const r = th.getBoundingClientRect();
        pop.style.top = `${window.scrollY + r.bottom + 2}px`;
        pop.style.left = `${Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 8))}px`;
        const readList = () => {
          const picked = new Set([...pop.querySelectorAll("input[type=checkbox]:checked")].map(c => c.value));
          if (picked.size === values.length) delete filters[i]; else filters[i] = { values: picked };
          apply();
        };
        pop.addEventListener("click", e2 => e2.stopPropagation());
        pop.addEventListener("change", () => { if (listMode) readList(); });
        if (rangeKind) {
          const readRange = () => {
            const get = k => { const v = pop.querySelector(`[data-r="${k}"]`).value; if (v === "") return null;
              return rangeKind === "num" ? parseFloat(v) : new Date(v + "T00:00:00").getTime() + (k === "max" ? 86399999 : 0); };
            const min = get("min"), max = get("max");
            if (min == null && max == null) delete filters[i]; else filters[i] = { range: rangeKind, min, max };
            apply();
          };
          pop.querySelectorAll("[data-r]").forEach(x => x.addEventListener("input", readRange));
          pop.querySelector("[data-r]").focus();
        }
        const text = pop.querySelector("input[type=text]");
        if (text) {
          text.focus();
          text.addEventListener("input", () => { const v = text.value.trim().toLowerCase(); if (v) filters[i] = { text: v }; else delete filters[i]; apply(); });
        }
        pop.querySelectorAll("[data-a]").forEach(a => a.addEventListener("click", () => {
          if (a.dataset.a === "clear") { delete filters[i]; apply(); close(); return; }
          pop.querySelectorAll("input[type=checkbox]").forEach(c => { c.checked = a.dataset.a === "all"; });
          readList();
        }));
      });
      th.appendChild(btn);
    });
    document.addEventListener("click", close);
    Array.from(table.tBodies).forEach(tb => new MutationObserver(() => { if (active() && !applying) apply(); }).observe(tb, { childList: true }));
    return {
      get: () => Object.fromEntries(Object.entries(filters).map(([i, f]) => [ths[i].dataset.label, f.range ? f : f.values ? { values: [...f.values] } : { text: f.text }])),
      set: saved => {
        Object.keys(filters).forEach(k => delete filters[k]);
        Object.entries(saved || {}).forEach(([label, f]) => {
          const i = ths.findIndex(th => th.dataset.label === label);
          if (i >= 0) filters[i] = f.range ? f : f.values ? { values: new Set(f.values) } : { text: f.text };
        });
        apply();
      },
      active,
    };
  },

  // rows x columns as plain text, the way they're shown
  tableData(table) {
    const head = Array.from(table.tHead.rows[0].cells);
    const cols = head.map((th, i) => [th, i]).filter(([th]) => getComputedStyle(th).display !== "none" && (th.dataset.label || "").trim()
      && !th.classList.contains("sel-col"));
    const rows = Array.from(table.tBodies).flatMap(tb => Array.from(tb.rows))
      .filter(r => !r.querySelector("td[colspan]") && r.dataset.ttHid !== "1" && !r.classList.contains("completed-toggle"));
    const text = td => td ? (td.querySelector("input:not([type=checkbox]), select")
      ? (td.querySelector("select") ? td.querySelector("select").selectedOptions[0]?.textContent || "" : td.querySelector("input:not([type=checkbox])").value)
      : (td.innerText || td.textContent || "")).replace(/\s*\n\s*/g, " · ").trim() : "";
    return {
      header: cols.map(([th]) => th.dataset.label.trim()),
      num: cols.map(([th]) => th.classList.contains("num")),
      rows: rows.map(r => cols.map(([th]) => text(r.cells[Array.from(th.parentNode.cells).indexOf(th)]))),
    };
  },
  exportName() {
    const page = (document.querySelector(".page-title") || {}).textContent || document.title.replace("AT-HUB — ", "") || "export";
    return `${page.trim().replace(/[^\w-]+/g, "-")}-${new Date().toISOString().substring(0, 10)}`;
  },
  exportTable(table, kind) {
    const d = this.tableData(table);
    const download = (blob, ext) => saveBlob(blob, `${this.exportName()}.${ext}`);
    const plain = (v, i) => d.num[i] && /^[-$\d,.()]+$/.test(v.replace(/\s/g, "")) ? v.replace(/[$,\s]/g, "").replace(/^\((.*)\)$/, "-$1") : v;
    if (kind === "csv" || kind === "copy") {
      const q = v => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
      const csv = [d.header, ...d.rows.map(r => r.map(plain))].map(r => r.map(q).join(kind === "copy" ? "\t" : ",")).join("\r\n");
      if (kind === "copy") { navigator.clipboard.writeText(csv.replace(/"/g, "")); toast(`Copied ${d.rows.length} rows`); return; }
      download(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }), "csv");
      toast(`Exported ${d.rows.length} rows to CSV`);
    } else if (kind === "xls") {
      const cell = (v, i) => `<td${d.num[i] ? ' style="mso-number-format:\'#,##0.00###\'"' : ""}>${escapeHtml(plain(v, i))}</td>`;
      const html = `<html><head><meta charset="utf-8"></head><body><table border="1"><tr>${d.header.map(h => `<th>${escapeHtml(h)}</th>`).join("")}</tr>
        ${d.rows.map(r => `<tr>${r.map(cell).join("")}</tr>`).join("")}</table></body></html>`;
      download(new Blob([html], { type: "application/vnd.ms-excel" }), "xls");
      toast(`Exported ${d.rows.length} rows to Excel`);
    } else if (kind === "pdf") {
      const w = window.open("", "_blank");
      const company = (document.querySelector(".brand span") || {}).textContent || "AT-HUB";
      w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(this.exportName())}</title><style>
        @page { size: landscape; margin: 12mm; } body { font: 11px "Segoe UI", Arial, sans-serif; color: #111; }
        h1 { font-size: 16px; margin: 0 0 2px; } .sub { color: #666; margin-bottom: 10px; }
        table { border-collapse: collapse; width: 100%; } th { background: #eef1f6; text-align: left; } th, td { border-bottom: 1px solid #ddd; padding: 4px 6px; vertical-align: top; }
        td.n, th.n { text-align: right; white-space: nowrap; } tr:nth-child(even) td { background: #fafbfc; }</style></head><body>
        <h1>${escapeHtml((document.querySelector(".page-title") || {}).textContent || "Report")}</h1>
        <div class="sub">${escapeHtml(company)} · ${new Date().toLocaleString()} · ${d.rows.length} rows</div>
        <table><thead><tr>${d.header.map((h, i) => `<th class="${d.num[i] ? "n" : ""}">${escapeHtml(h)}</th>`).join("")}</tr></thead>
        <tbody>${d.rows.map(r => `<tr>${r.map((v, i) => `<td class="${d.num[i] ? "n" : ""}">${escapeHtml(v)}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <script>window.onload = () => { window.print(); }<\/script></body></html>`);
      w.document.close();
    }
  },

  numOf(text) {
    const m = (text || "").replace(/[$,\s]/g, "").replace(/^\((.*)\)$/, "-$1").match(/^[-+]?\d*\.?\d+/);
    return m ? parseFloat(m[0]) : null;
  },
  dateOf(text) {
    const t = (text || "").trim();
    let m = t.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
    if (m) return new Date(+m[3], +m[1] - 1, +m[2]).getTime();
    m = t.match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? new Date(+m[1], +m[2] - 1, +m[3]).getTime() : null;
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
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/>',
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
  lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  sliders: '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
  thumbsUp: '<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  notePen: '<path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4"/><path d="M2 6h4M2 10h4M2 14h4M2 18h4"/><path d="M21.38 5.62a1 1 0 0 0-3-3L13 8l-1 4 4-1Z"/>',
};

// ---- Status as a small icon (same look as the order timeline); the word stays in the tooltip and as
// hidden text, so search, header filters and exports still see it. statusIcon("partially_shipped")
const STATUS_ICONS = {
  draft: ["pencil", "Draft"], confirmed: ["thumbsUp", "Confirmed — in progress"], not_booked: ["clock", "Not booked yet"],
  partially_booked: ["half", "Partly booked"], booked: ["package", "Booked into a shipment"],
  partially_shipped: ["half", "Partly shipped"], shipped: ["check", "Shipped"], delivered: ["checkCircle", "Delivered"],
  invoiced: ["receipt", "Invoiced"], paid: ["dollar", "Paid"], cancelled: ["x", "Cancelled"],
  complete: ["dd", "All done — shipped, invoiced & paid"],
};
// Icons or words: a per-user switch (the legend's "Show words" / "Show icons" link)
function statusAsText() { try { return localStorage.getItem("at_hub_status_view") === "text"; } catch (e) { return false; } }
function toggleStatusView(rerender) {
  try { localStorage.setItem("at_hub_status_view", statusAsText() ? "icons" : "text"); } catch (e) {}
  if (typeof window[rerender] === "function") window[rerender]();
}
// A subtle key for the icons in use, with the switch. statusLegend(["draft", "confirmed", ...], "renderOrders")
function statusLegend(keys, rerender) {
  const sw = `<a class="link" onclick="toggleStatusView('${rerender}')">${statusAsText() ? "Show icons" : "Show words"}</a>`;
  if (statusAsText()) return `<div class="status-legend">${sw}</div>`;
  return `<div class="status-legend">${keys.map(k => `<span>${statusIcon(k)} ${escapeHtml((STATUS_ICONS[k] || [, k])[1].replace(/ — .*/, ""))}</span>`).join("")}${sw}</div>`;
}
function statusIcon(status) {
  const [ico, label] = STATUS_ICONS[status] || [null, (status || "").replace(/_/g, " ")];
  if (!ico || statusAsText()) return `<span class="tag ${escapeHtml(status || "")}">${escapeHtml(label)}</span>`;
  return `<span class="st-ico st-${escapeHtml(status)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${ico === "half" ? "" : ico === "dd" ? `<b class="dd">$$</b>` : icon(ico)}<span class="sr-only">${escapeHtml((status || "").replace(/_/g, " "))}</span></span>`;
}

// ---- Item code autocorrect: '15420-NUTS' / '15385 - Nut' -> '15420-NUT', with Undo to keep what was typed.
// Any <input data-autocorrect="item-code">; the page sends keep_code: input.dataset.keep === "1".
function normalizeItemCode(code) { return /\s*-\s*nuts?\s*$/i.test(code) ? code.replace(/\s*-\s*nuts?\s*$/i, "-NUT") : code; }
document.addEventListener("focusout", e => {
  const el = e.target;
  if (!el.matches || !el.matches('input[data-autocorrect="item-code"]') || el.dataset.keep === "1") return;
  const typed = el.value.trim(), fixed = normalizeItemCode(typed);
  el.parentNode.querySelectorAll(".ac-note").forEach(n => n.remove());
  if (fixed === typed) return;
  el.value = fixed;
  const note = document.createElement("div");
  note.className = "ac-note small muted";
  note.innerHTML = `Changed to <strong>${escapeHtml(fixed)}</strong> (nut codes end in -NUT) · <a class="link">Undo</a>`;
  note.querySelector("a").onclick = () => { el.value = typed; el.dataset.keep = "1"; note.innerHTML = `Kept <strong>${escapeHtml(typed)}</strong> as typed.`; };
  el.insertAdjacentElement("afterend", note);
});
document.addEventListener("input", e => { if (e.target.matches && e.target.matches('input[data-autocorrect="item-code"]')) delete e.target.dataset.keep; });

// ---- Line-row icon buttons: one click does it, the hover tooltip says what ----
// trashBtn("removeLine(3, 9)") -> a small bin icon; onclick is plain JS (use single quotes inside).
function trashBtn(onclick, title = "Remove this line") {
  return `<button type="button" class="icon-btn trash-btn no-print" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}" onclick="${onclick}">${icon("trash")}</button>`;
}
// Line notes: a notepad button opens a small note box under the item. The note prints on packing
// lists / invoices / POs unless "Print" is unticked. Pages read .ln-text and .ln-print.
function lineNoteBtn(note) {
  const has = !!(note || "").trim();
  return `<button type="button" class="icon-btn note-btn no-print ${has ? "has-note" : ""}" title="${has ? "Edit this line's note" : "Add a note to this line (prints on packing list, invoice, PO)"}"
    aria-label="Line note" onclick="toggleLineNote(this)">${icon("notePen")}</button>`;
}
function lineNoteBox(note, printNotes, editable = true) {
  const text = (note || "").trim(), print = printNotes !== false;
  if (!editable) return text ? `<div class="line-note-read ${print ? "" : "internal"}" title="${print ? "Prints on documents" : "Internal note: not printed"}">${icon("notePen")}<span>${escapeHtml(text)}</span>${print ? "" : ' <span class="muted small">(not printed)</span>'}</div>` : "";
  return `<div class="line-note" ${text ? "" : 'style="display:none;"'}>
    <textarea class="ln-text" rows="1" placeholder="Note for this line…" title="Shows on packing lists, invoices and printouts">${escapeHtml(text)}</textarea>
    <label class="ln-print-label" title="Untick to keep this note internal (never printed)"><input type="checkbox" class="ln-print" ${print ? "checked" : ""}> Print</label></div>`;
}
// Paperclip beside a record on list pages: loadAttachCounts("customer_order").then(render); clip(o.id)
let ATTACH_COUNTS = {};
async function loadAttachCounts(entityType) {
  try { ATTACH_COUNTS = await apiFetch(`/api/attachments/counts?entity_type=${entityType}`); } catch (e) { ATTACH_COUNTS = {}; }
}
function clip(id) {
  const n = ATTACH_COUNTS[id];
  return n ? `<span class="list-clip" title="${n} file${n === 1 ? "" : "s"} attached">${icon("paperclip")}</span>` : "";
}
function toggleLineNote(btn) {
  const box = btn.closest("tr").querySelector(".line-note");
  if (!box) return;
  const show = box.style.display === "none";
  box.style.display = show ? "" : "none";
  if (show) box.querySelector(".ln-text").focus();
}
function lineNoteValue(tr) {
  const t = tr.querySelector(".ln-text"), pr = tr.querySelector(".ln-print");
  return { notes: t ? t.value.trim() || null : null, print_notes: pr ? pr.checked : true };
}
function icon(name, cls = "") {
  return ICON_PATHS[name] ? `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name]}</svg>` : "";
}
const NAV_ICONS = {
  "dashboard.html": "dashboard", "customers.html": "users", "customer-orders.html": "clipboard", "shipments.html": "truck",
  "pack-shipments.html": "package", "pod.html": "checkCircle", "labels.html": "tag", "invoices.html": "receipt",
  "vendors.html": "factory", "purchase-orders.html": "cart", "landed-costs.html": "anchor", "stock-items.html": "layers",
  "lots.html": "barcode", "mtrs.html": "fileCheck", "reports.html": "chart", "company.html": "building",
  "users.html": "shield", "account.html": "user", "recycle-bin.html": "trash", "file-matcher.html": "paperclip", "tasks.html": "checkCircle", "ai-desk.html": "sparkles", "mrp-payments.html": "dollar", "backups.html": "save",
};
// First matching keyword wins. Buttons are matched on their text, section titles likewise.
const BUTTON_ICONS = [
  [/print/i, "printer"], [/e-?mail|send/i, "mail"], [/\bpdf\b/i, "file"], [/^\s*save/i, "save"], [/upload/i, "upload"],
  [/delete|remove/i, "trash"], [/^\s*(cancel|close|discard)/i, "x"], [/receive/i, "download"], [/reset|undo|unship|unbook|roll ?back/i, "undo"],
  [/preview/i, "eye"], [/\bai\b|read po/i, "sparkles"], [/payment|^\s*pay\b|funding/i, "dollar"], [/^\s*edit/i, "pencil"],
  [/label/i, "tag"], [/profit|report/i, "chart"], [/history/i, "clock"], [/^\s*(add|new|create)\b/i, "plus"],
  [/confirm/i, "thumbsUp"], [/apply|mark|deliver/i, "check"], [/ship|pick|pack/i, "truck"],
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
  items_verify: [["Item", r => actLink("item.html", r.id, r.code)], ["Title", r => escapeHtml(r.title || "")], ["Group", r => escapeHtml(r.group || "")], ["Days Waiting", r => r.days]],
  no_invoice: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => r.customer], ["Shipped", r => actDate(r.ship_date)], ["Amount", r => fmtMoney(r.amount)], ["Days", r => r.days]],
  draft_invoices: [["Invoice", r => actLink("invoices.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => r.customer], ["Amount", r => fmtMoney(r.amount)], ["Days", r => r.days]],
  invoices_overdue: [["Invoice", r => actLink("invoices.html", r.id, r.code)], ["Customer", r => r.customer], ["Balance", r => fmtMoney(r.balance)], ["Due", r => actDate(r.due)], ["Days Overdue", r => r.days]],
  not_booked: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => r.customer], ["Customer PO", r => r.po_number], ["Amount", r => fmtMoney(r.amount)], ["Days", r => r.days]],
  draft_orders: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => r.customer], ["Customer PO", r => r.po_number], ["Amount", r => fmtMoney(r.amount)], ["Days", r => r.days]],
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
    let open = false, recId = null;
    // The record's id goes into the address (?id=…), so Back from a page it links to comes back to it.
    if (typeof window.showDetail === "function") {
      const orig = window.showDetail;
      window.showDetail = function (id, ...rest) { recId = id; if (open) setUrl(id, true); return orig.call(this, id, ...rest); };
    }
    const urlFor = id => { const u = new URL(location.href); if (id) u.searchParams.set("id", id); else u.searchParams.delete("id"); return u.pathname + u.search + u.hash; };
    const setUrl = (id, replace) => history[replace ? "replaceState" : "pushState"]({ record: true, id }, "", urlFor(id));
    window.setRecordId = id => { recId = id; if (open) setUrl(id, true); };  // records drawn without showDetail (a quote)
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
        if (!(history.state && history.state.record)) {
          // opened from the list -- or the page was opened at ?id=…: put the list underneath so Back lands on it
          if (new URL(location.href).searchParams.get("id")) history.replaceState(null, "", urlFor(null));
          setUrl(recId, false);
        }
      } else if (history.state && history.state.record) {
        history.replaceState(null, "", urlFor(null));  // closed with a button: the address drops the id
      }
    };
    new MutationObserver(sync).observe(card, { attributes: true, attributeFilter: ["style"], childList: true });
    window.addEventListener("popstate", e => {
      if (open && !(e.state && e.state.record)) card.style.display = "none";
      else if (!open && e.state && e.state.record && e.state.id && typeof window.showDetail === "function") window.showDetail(e.state.id);  // Forward
    });
    window.closeRecordPage = () => {
      if (history.state && history.state.record) history.back();  // popstate hides the card
      else card.style.display = "none";
    };
    sync();
  });
})();

// Long lists: show the first 50 rows, then a "show the other N" row. Call after a list's tbody is (re)rendered:
// limitRows(tbody, "lots", renderLots). Rows past the limit are only hidden, so searching still finds them all.
const ROW_LIMIT = 50;
const rowsExpanded = {};
function limitRows(tbody, key, rerender) {
  const rows = Array.from(tbody.children).filter(tr => !tr.classList.contains("completed-toggle") && !tr.querySelector("td[colspan]:only-child.muted"));
  if (rows.length <= ROW_LIMIT + 10) return;  // a few over isn't worth a click
  const cols = (tbody.closest("table").querySelector("thead tr") || {}).children?.length || 1;
  const open = !!rowsExpanded[key];
  if (!open) rows.slice(ROW_LIMIT).forEach(tr => { tr.style.display = "none"; });
  const toggle = document.createElement("tr");
  toggle.className = "completed-toggle";
  toggle.innerHTML = `<td colspan="${cols}"><span class="caret">${open ? "▾" : "▸"}</span> ${open
    ? `Showing all ${rows.length} <span class="muted">· click to show only the first ${ROW_LIMIT}</span>`
    : `Show the other ${rows.length - ROW_LIMIT} <span class="muted">(${rows.length} in all · showing the first ${ROW_LIMIT})</span>`}</td>`;
  toggle.onclick = () => { rowsExpanded[key] = !open; rerender(); };
  rows[Math.min(rows.length, open ? rows.length : ROW_LIMIT) - 1].after(toggle);
}

// ---- order lines: drag to reorder, replace an item, tick several ----
// Drag a line by its ⠿ handle (in the first cell). onReorder(ids) gets every row's data-line in the new order;
// leave it out for a form that isn't saved yet (the rows are simply read in their new order on save).
function enableLineDrag(tbody, onReorder) {
  if (!tbody || tbody.dataset.dragReady) return;
  tbody.dataset.dragReady = "1";
  const rows = () => Array.from(tbody.children).filter(tr => tr.tagName === "TR" && !tr.querySelector("td[colspan]"));
  const addHandles = () => rows().forEach(tr => {
    if (tr.querySelector(".drag-handle")) return;
    const h = document.createElement("span");
    h.className = "drag-handle";
    h.title = "Drag to move this line";
    h.textContent = "⠿";
    h.addEventListener("mousedown", () => { tr.draggable = true; });
    tr.cells[0] && tr.cells[0].prepend(h);
  });
  addHandles();
  new MutationObserver(addHandles).observe(tbody, { childList: true });
  let dragging = null;
  tbody.addEventListener("dragstart", e => {
    dragging = e.target.closest("tr");
    if (!dragging) return;
    dragging.classList.add("row-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", "line");
  });
  tbody.addEventListener("dragover", e => {
    if (!dragging) return;
    e.preventDefault();
    const over = e.target.closest("tr");
    if (!over || over === dragging || over.parentNode !== tbody) return;
    const after = e.clientY > over.getBoundingClientRect().top + over.offsetHeight / 2;
    if (after) over.after(dragging); else over.before(dragging);
  });
  tbody.addEventListener("dragend", () => {
    if (!dragging) return;
    dragging.classList.remove("row-dragging");
    dragging.draggable = false;
    dragging = null;
    const ids = rows().map(tr => parseInt(tr.dataset.line)).filter(n => !isNaN(n));
    if (onReorder && ids.length) onReorder(ids);
  });
}

// Pop-up next to a line's item: pick another item (search like everywhere else), then onPick(itemId).
function openItemReplace(anchor, currentItemId, onPick) {
  document.querySelectorAll(".replace-pop").forEach(p => p.remove());
  const pop = document.createElement("div");
  pop.className = "replace-pop";
  const id = `replace-sel-${Date.now()}`;
  pop.innerHTML = `<div class="small muted" style="margin-bottom:4px;">Replace this line's item with:</div>
    <select id="${id}" data-searchable>${items.map(i => `<option value="${i.id}" ${itemPickAttr(i)} ${i.id === currentItemId ? "selected" : ""} data-search="${escapeHtml([i.category, i.barcode].filter(Boolean).join(" "))}">${escapeHtml(i.code)} — ${escapeHtml(i.title)}${itemPickNote(i)}</option>`).join("")}</select>
    <div style="margin-top:6px; display:flex; gap:6px;"><button class="small-btn" data-ok>Replace</button><button class="secondary small-btn" data-cancel>Cancel</button></div>
    <div class="error small" data-err></div>`;
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = `${window.scrollY + r.bottom + 4}px`;
  pop.style.left = `${Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - 440))}px`;
  makeSearchable(document.getElementById(id));
  setTimeout(() => pop.querySelector(".search-select input") && pop.querySelector(".search-select input").focus(), 0);
  pop.querySelector("[data-cancel]").onclick = () => pop.remove();
  pop.querySelector("[data-ok]").onclick = async () => {
    const v = parseInt(document.getElementById(id).value);
    if (!v || v === currentItemId) { pop.remove(); return; }
    try { await onPick(v); pop.remove(); } catch (e) { pop.querySelector("[data-err]").textContent = e.message; }
  };
  setTimeout(() => document.addEventListener("mousedown", function away(e) {
    if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("mousedown", away); }
  }), 0);
}

// ---- "Validate with AI": check a saved order / PO against its attached document with several readers ----
function aiValidateButton(kind, id) {
  if (!AuthGuard.hasRole("manager")) return "";
  return `<button class="ai-validate-btn" onclick="runAiValidate('${kind}', ${id}, this)" title="Read the attached ${kind === "po" ? "vendor quote / confirmation" : "customer PO"} with the exact reader, the local AI and Claude, and compare every line with this ${kind === "po" ? "PO" : "order"}">
    <span class="ai-spark">✦</span> Validate with AI</button>`;
}
async function runAiValidate(kind, id, btn) {
  const panel = document.getElementById("ai-validate-panel");
  if (!panel) return;
  const what = kind === "po" ? "PO" : "order";
  const body = document.getElementById(kind === "po" ? "po-lines-body" : "order-lines-body");
  body && body.querySelectorAll(".ai-line-mark").forEach(m => m.remove());
  btn.disabled = true;
  btn.classList.add("running");
  panel.innerHTML = `<div class="ai-validate-panel"><div class="ai-validate-head"><span class="ai-spark spin">✦</span>
    <strong>Reading the attached document with every reader…</strong> <span class="muted small">then each line of this ${what} is marked below — up to a minute</span></div></div>`;
  try {
    const r = await apiFetch(kind === "po" ? `/api/ai-docs/validate-po/${id}` : `/api/ai-orders/validate/${id}`, { method: "POST" });
    const key = c => { c = (c || "").trim().toUpperCase(); return c.endsWith("-HPC") ? c.slice(0, -4) : c; };
    const val = (field, v) => v == null ? "—" : field === "price" ? fmtPrice(v) : field === "quantity" ? fmtQty(v) : escapeHtml(String(v));
    const ran = r.readers.filter(x => x.ok).map(x => x.name);
    const lineFindings = new Map();
    r.findings.filter(f => f.line && f.kind !== "missing").forEach(f => {
      const k = key(f.line);
      lineFindings.set(k, [...(lineFindings.get(k) || []), f]);
    });
    // one verdict per line, on the line itself
    let ok = 0, bad = 0, check = 0;
    if (body) body.querySelectorAll("tr[data-line]").forEach(tr => {
      const fs = lineFindings.get(key(tr.dataset.code)) || [];
      const cell = tr.querySelector("td.grow") || tr.cells[1];
      const mark = document.createElement("div");
      if (!fs.length && kind !== "po" && parseFloat(tr.dataset.price) === 0) {
        mark.className = "ai-line-mark muted-mark";
        mark.innerHTML = "✦ $0 nut — not on the customer's PO (expected)";
      } else if (!fs.length) {
        ok++;
        mark.className = "ai-line-mark ok";
        mark.innerHTML = `✦ ✓ ${ran.length > 1 ? `all ${ran.length} readers agree` : `${escapeHtml(ran[0] || "reader")} agrees`}`;
      } else {
        const sure = fs.some(f => f.severity === "confirmed");
        sure ? bad++ : check++;
        mark.className = `ai-line-mark ${sure ? "bad" : "check"}`;
        mark.innerHTML = fs.map(f => f.kind === "extra"
          ? `✦ ${sure ? "✕" : "?"} <strong>Not on the document</strong> <span class="muted">— ${f.flagged_by.map(escapeHtml).join(", ")} didn't find this line${f.agrees_with_order.length ? `; ${f.agrees_with_order.map(escapeHtml).join(", ")} did` : ""}</span>`
          : `✦ ${sure ? "✕" : "?"} <strong>${escapeHtml(f.field === "quantity" ? "Qty" : f.field === "price" ? "Price" : f.field)}:</strong> ${what} ${val(f.field, f.saved)}
             · ${Object.entries(f.seen).map(([n, v]) => `${escapeHtml(n)} <strong>${val(f.field, v)}</strong>`).join(" · ")}
             ${f.agrees_with_order.length ? `· <span class="pos">${f.agrees_with_order.map(escapeHtml).join(", ")} ${val(f.field, f.saved)}</span>` : ""}
             <span class="muted">${sure ? "" : "(likely a misread — check)"}</span>`).join("<br>");
      }
      cell.appendChild(mark);
    });
    const missing = r.findings.filter(f => f.kind === "missing");
    const header = r.findings.filter(f => !f.line);
    const readerChips = r.readers.map(x => `<span class="ai-reader ${x.ok ? "ok" : "bad"}" title="${escapeHtml(x.error || `${x.lines} lines read in ${x.seconds}s`)}">${x.ok ? "✓" : "✕"} ${escapeHtml(x.name)}</span>`).join("");
    const state = bad || missing.some(f => f.severity === "confirmed") || header.some(f => f.severity === "confirmed") ? "bad" : check || missing.length || header.length ? "warn" : "clean";
    panel.innerHTML = `<div class="ai-validate-panel ${state}">
      <div class="ai-validate-head"><span class="ai-spark">✦</span>
        <strong>${state === "clean" ? `Every line matches ${escapeHtml(r.document)}` : `Checked against ${escapeHtml(r.document)}`}</strong>
        <span class="small">${ok ? `<span class="pos">${ok} line${ok === 1 ? "" : "s"} ✓</span>` : ""}${bad ? ` · <span class="neg">${bad} to fix</span>` : ""}${check ? ` · <span class="warn-text">${check} to check</span>` : ""}</span>
        <span class="ai-readers" style="margin:0;">${readerChips}</span>
        ${body ? `<a class="link small" onclick="document.getElementById('${body.id}').scrollIntoView({behavior:'smooth', block:'center'})">See the lines ↓</a>` : ""}
        <a class="link small" style="margin-left:auto;" onclick="document.getElementById('ai-validate-panel').innerHTML=''; document.querySelectorAll('.ai-line-mark').forEach(m => m.remove())">Clear</a></div>
      ${header.map(f => `<div class="small ${f.severity === "confirmed" ? "neg" : ""}" style="margin-top:4px;">✦ <strong>${escapeHtml(f.field)}:</strong> ${what} ${val("", f.saved)} · ${Object.entries(f.seen).map(([n, v]) => `${escapeHtml(n)} ${val("", v)}`).join(" · ")}</div>`).join("")}
      ${missing.length ? `<div class="small" style="margin-top:6px;"><strong class="neg">On the document but not on this ${what}:</strong>
        ${missing.map(f => `<div>✦ <strong>${escapeHtml(f.line)}</strong> — ${Object.entries(f.seen).map(([n, v]) => `${escapeHtml(n)}: ${escapeHtml(String(v))}`).join(" · ")}${f.severity === "confirmed" ? "" : " <span class='muted'>(one reader only)</span>"}</div>`).join("")}</div>` : ""}
    </div>`;
  } catch (e) {
    panel.innerHTML = `<div class="ai-validate-panel bad"><div class="ai-validate-head"><span class="ai-spark">✦</span> <strong>Couldn't validate:</strong> ${escapeHtml(e.message)}
      <a class="link small" style="margin-left:auto;" onclick="document.getElementById('ai-validate-panel').innerHTML=''">Close</a></div></div>`;
  } finally {
    btn.disabled = false;
    btn.classList.remove("running");
  }
}

// ---- Ctrl+K: find any order, PO, item, customer, vendor, shipment or invoice from anywhere ----
const QuickFind = {
  data: null, loadedAt: 0, el: null, results: [], active: 0,
  async load() {
    if (this.data && Date.now() - this.loadedAt < 60000) return this.data;
    const mgr = AuthGuard.hasRole("manager");
    const get = (url, ok = true) => ok ? apiFetch(url).catch(() => []) : Promise.resolve([]);
    const [orders, pos, items, customers, vendors, shipments, invoices] = await Promise.all([
      get("/api/customer-orders/"), get("/api/purchase-orders/", mgr), get("/api/stock-items/"), get("/api/customers/", mgr),
      get("/api/vendors/", mgr), get("/api/shipments/"), get("/api/invoices/", mgr)]);
    const cname = Object.fromEntries(customers.map(c => [c.id, c.name])), vname = Object.fromEntries(vendors.map(v => [v.id, v.name]));
    this.data = [
      ...orders.map(o => ({ kind: "Order", label: o.code, sub: [cname[o.customer_id], o.po_number && `PO ${o.po_number}`, o.job_number && `Job ${o.job_number}`, o.status].filter(Boolean).join(" · "), href: `customer-orders.html?id=${o.id}`, hay: `${o.code} ${o.po_number || ""} ${o.job_number || ""} ${cname[o.customer_id] || ""}` })),
      ...pos.map(p => ({ kind: "PO", label: p.code, sub: [vname[p.vendor_id], p.vendor_so_number && `SO ${p.vendor_so_number}`, p.status].filter(Boolean).join(" · "), href: `purchase-orders.html?id=${p.id}`, hay: `${p.code} ${p.vendor_so_number || ""} ${vname[p.vendor_id] || ""}` })),
      ...shipments.map(s => ({ kind: "Shipment", label: s.code, sub: [s.status, s.tracking_number].filter(Boolean).join(" · "), href: `shipments.html?id=${s.id}`, hay: `${s.code} ${s.tracking_number || ""}` })),
      ...invoices.map(i => ({ kind: "Invoice", label: i.code, sub: [cname[i.customer_id], i.status, fmtMoney(i.total)].filter(Boolean).join(" · "), href: `invoices.html?id=${i.id}`, hay: `${i.code} ${cname[i.customer_id] || ""}` })),
      ...items.map(i => ({ kind: "Item", label: i.code, sub: i.title, href: `item.html?id=${i.id}`, hay: `${i.code} ${i.title} ${i.category || ""}` })),
      ...customers.map(c => ({ kind: "Customer", label: c.name, sub: c.contact_name || "", href: `customers.html?id=${c.id}`, hay: `${c.name} ${c.contact_name || ""}` })),
      ...vendors.map(v => ({ kind: "Vendor", label: v.name, sub: v.code || "", href: `vendors.html?id=${v.id}`, hay: `${v.name} ${v.code || ""}` })),
    ];
    this.loadedAt = Date.now();
    return this.data;
  },
  open() {
    if (this.el) { this.el.querySelector("input").focus(); return; }
    this.el = document.createElement("div");
    this.el.className = "qf-backdrop";
    this.el.innerHTML = `<div class="qf-box"><input type="text" placeholder="Find an order, PO, item, customer, vendor, shipment, invoice…" autocomplete="off">
      <div class="qf-list"><div class="qf-empty muted small">Type a code, customer PO #, job #, name or item…</div></div>
      <div class="qf-foot muted small">↑ ↓ to move · Enter to open · Esc to close</div></div>`;
    document.body.appendChild(this.el);
    const input = this.el.querySelector("input");
    this.el.addEventListener("mousedown", e => { if (e.target === this.el) this.close(); });
    input.addEventListener("input", () => this.search(input.value));
    input.addEventListener("keydown", e => {
      if (e.key === "Escape") this.close();
      else if (e.key === "ArrowDown") { this.active = Math.min(this.active + 1, this.results.length - 1); this.paint(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { this.active = Math.max(this.active - 1, 0); this.paint(); e.preventDefault(); }
      else if (e.key === "Enter" && this.results[this.active]) location.href = this.results[this.active].href;
    });
    input.focus();
    this.load();
  },
  close() { if (this.el) { this.el.remove(); this.el = null; } },
  async search(q) {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) { this.results = []; this.paint(); return; }
    const data = await this.load();
    const score = r => {
      const label = r.label.toLowerCase(), hay = r.hay.toLowerCase();
      if (!words.every(w => hay.includes(w))) return -1;
      return (label === words.join(" ") ? 100 : 0) + (label.startsWith(words[0]) ? 20 : 0) + (label.includes(words[0]) ? 10 : 0);
    };
    this.results = data.map(r => [score(r), r]).filter(([s]) => s >= 0).sort((a, b) => b[0] - a[0]).slice(0, 12).map(([, r]) => r);
    this.active = 0;
    this.paint();
  },
  paint() {
    const list = this.el && this.el.querySelector(".qf-list");
    if (!list) return;
    list.innerHTML = this.results.length ? this.results.map((r, i) => `<a class="qf-row ${i === this.active ? "active" : ""}" href="${r.href}">
        <span class="qf-kind">${r.kind}</span><strong>${escapeHtml(r.label)}</strong><span class="muted small">${escapeHtml(r.sub || "")}</span></a>`).join("")
      : `<div class="qf-empty muted small">${this.el.querySelector("input").value ? "Nothing found." : "Type a code, customer PO #, job #, name or item…"}</div>`;
  },
};
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && AuthGuard.getToken()) { e.preventDefault(); QuickFind.open(); }
});

// ---- activity history on an order / PO: "admin changed line #2: qty 110 → 111" ----
async function renderActivity(container, entityType, entityId, lineNo = {}) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  let rows = [];
  try { rows = await apiFetch(`/api/activity/${entityType}/${entityId}`); } catch (e) { el.innerHTML = `<span class="muted small">${escapeHtml(e.message)}</span>`; return; }
  const itemCode = id => { const list = typeof items !== "undefined" ? items : []; const it = list.find(i => i.id === id); return it ? it.code : `item ${id}`; };
  const nice = k => ({ po_number: "PO #", customer_po_date: "PO date", job_number: "job #", delivery_date: "delivery date", ship_to_address: "ship-to",
    unit_price: "price", unit_cost: "cost", vendor_item_code: "vendor part #", vendor_description: "vendor description", vendor_so_number: "vendor SO #",
    expected_date: "expected date", item_id: "item", customer_id: "customer", vendor_id: "vendor" }[k] || k.replace(/_/g, " "));
  const val = (k, v) => v == null || v === "" ? "blank" : k === "item_id" ? itemCode(v) : /date/.test(k) ? String(v).substring(0, 10) : String(v).length > 40 ? String(v).slice(0, 40) + "…" : String(v);
  const fields = d => Object.entries(d || {}).filter(([k]) => !["allow_duplicate", "lines", "line_ids"].includes(k)).map(([k, v]) => `${nice(k)} → <strong>${escapeHtml(val(k, v))}</strong>`).join(", ");
  const say = r => {
    let d = null; try { d = r.detail ? JSON.parse(r.detail) : null; } catch {}
    const [what, sub] = r.action.split("/");
    const ln = sub ? (lineNo[sub] != null ? `line #${lineNo[sub]}` : "a line") : "";
    if (!what && r.method === "PUT") return `edited the details: ${fields(d) || "saved"}`;
    if (what === "lines" && r.method === "POST") return d ? `added a line: <strong>${escapeHtml(itemCode(d.item_id))}</strong> × ${fmtQty(d.quantity)}${d.unit_price != null ? ` @ ${fmtPrice(d.unit_price)}` : d.unit_cost != null ? ` @ ${fmtPrice(d.unit_cost)}` : ""}` : "added a line";
    if (what === "lines" && r.method === "PUT") return `changed ${ln}: ${fields(d)}`;
    if (what === "lines" && r.method === "DELETE") return `removed ${ln}`;
    if (what === "line-order") return "reordered the lines";
    return ({ confirm: "confirmed the order", cancel: "cancelled it", "duplicate-po-ok": "OK'd the duplicate customer PO #", shipments: "created a shipment",
      receive: "received stock", "mark-ordered": "marked it ordered", bills: r.method === "DELETE" ? "deleted a vendor invoice" : "added a vendor invoice",
      charges: r.method === "DELETE" ? "removed a charge" : "added a charge", payments: "recorded a payment" }[what]) || `${r.method.toLowerCase()} ${escapeHtml(r.action)}`;
  };
  el.innerHTML = rows.length ? `<ul class="activity-list">${rows.map(r => `<li><span class="muted small">${new Date(r.at).toLocaleString()}</span>
      <strong>${escapeHtml(r.by || "someone")}</strong> ${say(r)}</li>`).join("")}</ul>`
    : `<p class="muted small" style="margin:0;">No changes recorded yet (history starts from today's update).</p>`;
}


// ---- placeholder rows while a list loads (instead of a bare "Loading...") ----
function skeletonizeLoading(root = document) {
  root.querySelectorAll("tbody").forEach(tb => {
    const only = tb.rows.length === 1 ? tb.rows[0] : null;
    const td = only && only.cells.length === 1 ? only.cells[0] : null;
    if (!td || !/^\s*loading/i.test(td.textContent)) return;
    const cols = parseInt(td.getAttribute("colspan")) || (tb.closest("table").tHead ? tb.closest("table").tHead.rows[0].cells.length : 1);
    tb.innerHTML = Array.from({ length: 6 }, (_, r) => `<tr class="skeleton-row">${Array.from({ length: cols }, (_, c) =>
      `<td><span class="skel" style="width:${[60, 80, 45, 70, 55, 90][(r + c) % 6]}%"></span></td>`).join("")}</tr>`).join("");
  });
}
document.addEventListener("DOMContentLoaded", () => skeletonizeLoading());

// ---- print the record on screen (order / PO / invoice): the browser's print, laid out for paper ----
// Print asks what to include: every section of the record on screen, money ones unticked by default.
function printSections(card) {
  const groups = [];
  let cur = null;
  Array.from(card.children).forEach(el => {
    if (el.matches("h3.detail-head, #ai-validate-panel, .btn-row, script, style")) return;
    const own = h => h ? Array.from(h.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join("").trim() || h.textContent : "";
    const title = el.matches("h4") ? own(el)
      : el.matches(".dsec") ? (el.querySelector(".dsec-title, h4") || {}).textContent
      : el.matches(".timeline") ? "Progress timeline"
      : el.matches(".money-tiles") ? "Money summary"
      : el.matches("details.fold-section") ? (el.querySelector("summary h4") || el.querySelector("summary") || {}).textContent
      : null;
    if (title) {
      const name = title.replace(/\(\d+\)/, "").replace(/—.*$/, "").trim();
      cur = { name, els: [el], money: el.matches(".money-tiles") || /money|invoice|payment|profit|landed|cost/i.test(name) };
      groups.push(cur);
    } else if (cur) cur.els.push(el);
  });
  return groups;
}
function printRecord() {
  const card = document.getElementById("detail-card");
  if (!card) { window.print(); return; }
  const groups = printSections(card);
  document.querySelectorAll(".print-dialog").forEach(d => d.remove());
  const dlg = document.createElement("div");
  dlg.className = "qf-backdrop print-dialog";
  dlg.innerHTML = `<div class="qf-box" style="padding:14px 16px;">
    <h3 style="margin:0 0 4px;">Print — what to include?</h3>
    <p class="muted small" style="margin:0 0 10px;">Sections with money start unticked.</p>
    <div class="print-opts">${groups.map((g, i) => `<label><input type="checkbox" data-g="${i}" ${g.money ? "" : "checked"}> ${escapeHtml(g.name)}${g.money ? ' <span class="muted small">($)</span>' : ""}</label>`).join("")}</div>
    <label style="display:block; margin-top:10px;"><input type="checkbox" id="print-prices"> Show prices and totals in the line table</label>
    <label style="display:block; margin-top:4px;" title="Notes marked 'don't print' never print"><input type="checkbox" id="print-notes" checked> Show line notes</label>
    <div style="display:flex; gap:8px; margin-top:14px; justify-content:flex-end;">
      <button class="secondary" data-cancel>Cancel</button><button data-go>Print</button></div></div>`;
  document.body.appendChild(dlg);
  dlg.querySelector("[data-cancel]").onclick = () => dlg.remove();
  dlg.addEventListener("mousedown", e => { if (e.target === dlg) dlg.remove(); });
  dlg.querySelector("[data-go]").onclick = () => {
    const keep = new Set([...dlg.querySelectorAll("[data-g]:checked")].map(c => +c.dataset.g));
    const prices = dlg.querySelector("#print-prices").checked;
    document.body.classList.toggle("print-no-notes", !dlg.querySelector("#print-notes").checked);
    dlg.remove();
    const hidden = [], opened = [];
    groups.forEach((g, i) => g.els.forEach(el => {
      if (!keep.has(i)) { el.classList.add("print-hide"); hidden.push(el); }
      el.querySelectorAll ? [el, ...el.querySelectorAll("details")].forEach(d => { if (d.tagName === "DETAILS" && !d.open) { d.open = true; opened.push(d); } }) : null;
    }));
    let style = null;
    if (!prices) {  // money columns of the line tables
      style = document.createElement("style");
      const rules = [];
      card.querySelectorAll("table.lines-table").forEach((t, n) => {
        t.dataset.printT = n;
        Array.from(t.tHead.rows[0].cells).forEach((th, i) => { if (MONEY_HEADER.test(th.textContent)) rules.push(`table[data-print-t="${n}"] tr > :nth-child(${i + 1}) { display: none !important; }`); });
        t.querySelectorAll("tfoot, [data-price-delta]").forEach(x => x.classList.add("print-hide"));
      });
      style.textContent = `@media print { ${rules.join(" ")} }`;
      document.head.appendChild(style);
    }
    document.body.classList.add("printing-record");
    setTimeout(() => {
      window.print();
      setTimeout(() => {
        document.body.classList.remove("printing-record", "print-no-notes");
        hidden.forEach(el => el.classList.remove("print-hide"));
        card.querySelectorAll(".print-hide").forEach(el => el.classList.remove("print-hide"));
        opened.forEach(d => { d.open = false; });
        if (style) style.remove();
      }, 400);
    }, 50);
  };
}

// Show / hide a list table together with its Views / Columns bar.
function showTable(table, show) {
  if (!table) return;
  table.style.display = show ? "" : "none";
  const bar = table.previousElementSibling;
  if (bar && bar.classList.contains("table-tools")) bar.style.display = show ? "" : "none";
}


// Small notice at the bottom of the screen ("Exported 52 rows", "Undid: ...") with an optional action link.
function toast(message, action = null, ms = 4500) {
  let host = document.getElementById("toast-host");
  if (!host) { host = document.createElement("div"); host.id = "toast-host"; document.body.appendChild(host); }
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = `<span>${escapeHtml(message)}</span>${action ? `<a>${escapeHtml(action.label)}</a>` : ""}`;
  if (action) t.querySelector("a").onclick = () => { t.remove(); action.run(); };
  host.appendChild(t);
  setTimeout(() => t.classList.add("out"), ms);
  setTimeout(() => t.remove(), ms + 400);
}

// ---- Import CSV (items / customers / vendors): pick a file, see what will happen, then apply ----
function openImport(kind, onDone) {
  const nouns = { items: "stock items", customers: "customers", vendors: "vendors" };
  document.querySelectorAll(".import-dialog").forEach(d => d.remove());
  const dlg = document.createElement("div");
  dlg.className = "qf-backdrop import-dialog";
  dlg.innerHTML = `<div class="qf-box" style="padding:14px 16px; width:min(860px, calc(100vw - 32px));">
    <h3 style="margin:0 0 4px;">Import ${nouns[kind]} from CSV</h3>
    <p class="muted small" style="margin:0 0 10px;">Columns are matched by name (e.g. ${kind === "items" ? "Code, Title, Group, Price, Cost, Reorder point, Pack size" : "Name, Contact, Email, Phone, Address, Shipping address"}).
      Rows update the ${kind === "items" ? "item with the same code" : "record with the same name"}, otherwise they're created. Nothing is saved until you click Import.</p>
    <input type="file" accept=".csv,text/csv,.txt">
    <div class="import-preview" style="margin-top:10px;"></div>
    <div style="display:flex; gap:8px; margin-top:12px; justify-content:flex-end;">
      <button class="secondary" data-cancel>Close</button><button data-apply disabled>Import</button></div></div>`;
  document.body.appendChild(dlg);
  const file = dlg.querySelector("input[type=file]"), box = dlg.querySelector(".import-preview"), go = dlg.querySelector("[data-apply]");
  dlg.querySelector("[data-cancel]").onclick = () => dlg.remove();
  const send = async step => {
    const form = new FormData();
    form.append("file", file.files[0]);
    const r = await fetch(`/api/import/${kind}/${step}`, { method: "POST", headers: { Authorization: `Bearer ${AuthGuard.getToken()}` }, body: form });
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Import failed");
    return data;
  };
  file.onchange = async () => {
    go.disabled = true;
    box.innerHTML = `<p class="muted small">Reading…</p>`;
    try {
      const p = await send("preview");
      const tag = a => ({ create: '<span class="tag confirmed">new</span>', update: '<span class="tag on_hold">update</span>', same: '<span class="tag draft">no change</span>', error: '<span class="tag cancelled">problem</span>' }[a]);
      box.innerHTML = `<p class="small" style="margin:0 0 6px;"><strong>${p.counts.create}</strong> new · <strong>${p.counts.update}</strong> to update ·
          ${p.counts.same} unchanged · <span class="${p.counts.error ? "neg" : ""}"><strong>${p.counts.error}</strong> with problems (skipped)</span>
          <span class="muted"> — columns used: ${Object.entries(p.columns).map(([f, h]) => `${escapeHtml(h)}`).join(", ")}${p.ignored.length ? `; ignored: ${p.ignored.map(escapeHtml).join(", ")}` : ""}</span></p>
        <div class="table-scroll" style="max-height:46vh;"><table class="compact-table no-table-tools"><thead><tr><th>Row</th><th></th><th>${kind === "items" ? "Code" : "Name"}</th><th>Changes / problems</th></tr></thead>
        <tbody>${p.rows.map(r => `<tr><td>${r.row}</td><td>${tag(r.action)}</td><td><strong>${escapeHtml(r.key)}</strong></td>
          <td class="small">${r.errors.length ? `<span class="neg">${escapeHtml(r.errors.join("; "))}</span>` : escapeHtml(Object.entries(r.changes).map(([f, v]) => `${f.replace(/_/g, " ")}: ${v}`).join(" · "))}</td></tr>`).join("")}</tbody></table></div>`;
      go.disabled = !(p.counts.create + p.counts.update);
    } catch (e) { box.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`; }
  };
  go.onclick = async () => {
    go.disabled = true;
    try {
      const r = await send("apply");
      dlg.remove();
      toast(`Imported: ${r.created} new, ${r.updated} updated${r.skipped ? `, ${r.skipped} skipped` : ""}`);
      if (onDone) onDone();
    } catch (e) { box.insertAdjacentHTML("afterbegin", `<div class="error">${escapeHtml(e.message)}</div>`); go.disabled = false; }
  };
}

// ---- Undo (Ctrl+Z): actions register how to reverse themselves; Ctrl+Z (outside a text box) reverses the last one ----
const Undo = {
  stack: [],
  push(label, reverse) {
    this.stack.push({ label, reverse });
    if (this.stack.length > 30) this.stack.shift();
    toast(label, { label: "Undo", run: () => this.run() });
  },
  async run() {
    const last = this.stack.pop();
    if (!last) { toast("Nothing to undo"); return; }
    try { await last.reverse(); toast(`Undone: ${last.label}`); }
    catch (e) { toast(`Couldn't undo: ${e.message}`); }
  },
};
document.addEventListener("keydown", e => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z" || e.shiftKey) return;
  const t = e.target;
  if (t && (t.matches("input, textarea, select") || t.isContentEditable)) return;  // let the box undo its own typing
  if (!Undo.stack.length) return;
  e.preventDefault();
  Undo.run();
});
// After a delete / remove: undo = restore the newest recycle-bin entry (what this action just put there).
async function undoableDelete(label, after) {
  let entry = null;
  try { entry = (await apiFetch("/api/recycle-bin")).find(e => !e.restored_at); } catch (e) { return; }
  if (!entry) return;
  Undo.push(label, async () => { await apiFetch(`/api/recycle-bin/${entry.id}/restore`, { method: "POST" }); if (after) await after(); });
}


// Save a file the browser made (exports): the link is attached to the page while clicked -- some browsers cancel otherwise.
function saveBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 60000);
}

// ---- Peek: a link to another record opens it in a glass pop-up to glance at, with "Open full page" ----
// Any <a href="customer-orders.html?id=…"> (or purchase-orders / shipments / invoices / item) anywhere in the
// app opens here instead of leaving the page. Ctrl/⌘/Shift-click, middle-click, target="_blank" and
// data-nopeek still go to the page. Links inside the pop-up open in it too (‹ goes back).
const PEEK_PAGES = { "customer-orders.html": "order", "purchase-orders.html": "po", "shipments.html": "shipment", "invoices.html": "invoice", "item.html": "item" };
const peekCache = {};
const pkDate = d => d ? new Date(d).toLocaleDateString() : "";
const peekStack = [];
function peekGet(url) { return (peekCache[url] ??= apiFetch(url).catch(e => { delete peekCache[url]; throw e; })); }
async function peekItems() { return peekGet("/api/stock-items/"); }
function peekParty(kind, id) { return id ? peekGet(`/api/${kind}/${id}`).catch(() => ({ name: "" })) : Promise.resolve({ name: "" }); }

document.addEventListener("click", e => {
  const a = e.target.closest && e.target.closest("a[href]");
  if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  if (a.target === "_blank" || a.hasAttribute("data-nopeek") || a.closest("[data-nopeek]")) return;
  let url;
  try { url = new URL(a.getAttribute("href"), location.href); } catch (err) { return; }
  if (url.origin !== location.origin) return;
  const kind = PEEK_PAGES[url.pathname.split("/").pop()], id = parseInt(url.searchParams.get("id"));
  if (!kind || !id || [...url.searchParams.keys()].some(k => k !== "id")) return;
  e.preventDefault();
  e.stopPropagation();  // a row's own click (open / select) shouldn't fire as well
  peek(kind, id, url.pathname.split("/").pop() + url.search, !!a.closest("#peek"));
}, true);

async function peek(kind, id, href, nested = false) {
  let back = document.getElementById("peek");
  if (!back) {
    peekStack.length = 0;
    back = document.createElement("div");
    back.id = "peek";
    back.className = "glass-back peek-back";
    back.addEventListener("click", e => { if (e.target === back) closePeek(); });
    back.innerHTML = `<div class="glass-panel peek-panel" role="dialog" aria-modal="true"></div>`;
    document.body.appendChild(back);
    document.addEventListener("keydown", peekKey);
  }
  if (nested && back.dataset.current) peekStack.push(back.dataset.current);
  back.dataset.current = JSON.stringify([kind, id, href]);
  const panel = back.querySelector(".peek-panel");
  panel.innerHTML = `<div class="peek-head"><div class="peek-title"><span class="muted">Loading…</span></div>${peekButtons(href)}</div>`;
  try {
    const { title, sub, body } = await PEEK_RENDER[kind](id);
    panel.innerHTML = `<div class="peek-head"><div class="peek-title">${peekStack.length ? `<button type="button" class="icon-btn peek-prev" title="Back" onclick="peekPrev()">‹</button>` : ""}
        <div><h3>${title}</h3>${sub ? `<div class="muted small">${sub}</div>` : ""}</div></div>${peekButtons(href)}</div>
      <div class="peek-body">${body}</div>`;
    panel.classList.remove("peek-swap");
    void panel.offsetWidth;
    panel.classList.add("peek-swap");
    decorateIcons(panel);
  } catch (err) {
    panel.querySelector(".peek-title").innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
  }
}
function peekButtons(href) {
  return `<div class="peek-actions"><a class="peek-open" href="${escapeHtml(href)}" data-nopeek>Open full page →</a>
    <button type="button" class="icon-btn peek-close" aria-label="Close" onclick="closePeek()">${icon("x")}</button></div>`;
}
function peekPrev() {
  const prev = peekStack.pop();
  if (!prev) return;
  const [kind, id, href] = JSON.parse(prev);
  document.getElementById("peek").dataset.current = "";
  peek(kind, id, href);
}
function peekKey(e) { if (e.key === "Escape") closePeek(); }
function closePeek() {
  const back = document.getElementById("peek");
  document.removeEventListener("keydown", peekKey);
  if (!back) return;
  back.classList.add("closing");
  setTimeout(() => back.remove(), 170);
}

// small building blocks
const pk = {
  tag: s => `<span class="tag ${escapeHtml(s || "")}">${escapeHtml((s || "").replace(/_/g, " "))}</span>`,
  facts: rows => `<div class="peek-facts">${rows.filter(r => r && r[1] !== undefined && r[1] !== null && r[1] !== "").map(([k, v]) => `<div><span>${escapeHtml(k)}</span><strong>${v}</strong></div>`).join("")}</div>`,
  link: (page, id, text) => `<a class="link" href="${page}?id=${id}">${escapeHtml(text)}</a>`,
  money: v => hidesMoney() ? "" : fmtMoney(v || 0),
  price: v => hidesMoney() ? "" : fmtPrice(v || 0),
  table: (head, rows) => `<div class="peek-table-wrap"><table class="peek-table no-table-tools no-col-bands"><thead><tr>${head.map(h => `<th class="${h.startsWith("#") ? "num" : ""}">${escapeHtml(h.replace(/^#/, ""))}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("") || `<tr><td colspan="${head.length}" class="muted">None</td></tr>`}</tbody></table></div>`,
  section: (title, html) => `<h4 class="peek-h">${escapeHtml(title)}</h4>${html}`,
};
const itemCell = (items, id) => { const i = items.find(x => x.id === id); return i ? `${pk.link("item.html", i.id, i.code)}<div class="muted small">${escapeHtml(i.title)}</div>` : `#${id}`; };

const PEEK_RENDER = {
  async order(id) {
    const o = await apiFetch(`/api/customer-orders/${id}`);
    const [items, cust] = await Promise.all([peekItems(), peekParty("customers", o.customer_id)]);
    const total = o.lines.reduce((s, l) => s + lineAmount(l.quantity, l.unit_price), 0);
    const ships = {};
    o.lines.forEach(l => (l.shipments || []).forEach(s => { ships[s.shipment_id] = s.code || s.shipment_code || `#${s.shipment_id}`; }));
    return { title: `${escapeHtml(o.code)} ${pk.tag(o.status)}`, sub: `${escapeHtml(cust.name || "")}${o.po_number ? ` · PO ${escapeHtml(o.po_number)}` : ""}${o.job_number ? ` · Job ${escapeHtml(o.job_number)}` : ""}`,
      body: pk.facts([["Created", pkDate(o.created_at || o.order_date)], ["Delivery", pkDate(o.delivery_date)], ["Customer PO date", pkDate(o.customer_po_date)], ["Order total", pk.money(total)]])
        + pk.table(["Line", "Item", "#Qty", "#Shipped", "#Booked", ...(hidesMoney() ? [] : ["#Price", "#Amount"])], o.lines.map(l => `<tr><td>#${l.line_no ?? ""}</td><td>${itemCell(items, l.item_id)}</td>
            <td class="num">${fmtQty(l.quantity)}</td><td class="num">${fmtQty(l.shipped_quantity)}</td><td class="num">${fmtQty(l.booked_quantity)}</td>
            ${hidesMoney() ? "" : `<td class="num">${pk.price(l.unit_price)}</td><td class="num">${pk.money(lineAmount(l.quantity, l.unit_price))}</td>`}</tr>`))
        + (Object.keys(ships).length ? pk.section("Shipments", `<div class="peek-chips">${Object.entries(ships).map(([sid, code]) => pk.link("shipments.html", sid, code)).join("")}</div>`) : "")
        + (o.notes ? pk.section("Notes", `<p class="peek-notes">${escapeHtml(o.notes)}</p>`) : "") };
  },
  async po(id) {
    const o = await apiFetch(`/api/purchase-orders/${id}`);
    const [items, vend] = await Promise.all([peekItems(), peekParty("vendors", o.vendor_id)]);
    return { title: `${escapeHtml(o.code)} ${pk.tag(o.status)}`, sub: `${escapeHtml(vend.name || "")}${o.vendor_so_number ? ` · Vendor SO ${escapeHtml(o.vendor_so_number)}` : ""}`,
      body: pk.facts([["Ordered", pkDate(o.order_date || o.created_at)], ["Expected", pkDate(o.expected_date)], ["Order total", pk.money(o.order_total)], ["Paid", pk.money(o.amount_paid)]])
        + pk.table(["Item", "Vendor part #", "#Qty", "#Received", ...(hidesMoney() ? [] : ["#Unit cost"])], o.lines.map(l => `<tr><td>${itemCell(items, l.item_id)}</td>
            <td class="small">${escapeHtml(l.vendor_item_code || "")}</td><td class="num">${fmtQty(l.quantity)}</td><td class="num">${fmtQty(l.received_quantity)}</td>
            ${hidesMoney() ? "" : `<td class="num">${pk.price(l.unit_cost)}</td>`}</tr>`))
        + (o.notes ? pk.section("Notes", `<p class="peek-notes">${escapeHtml(o.notes)}</p>`) : "") };
  },
  async shipment(id) {
    const s = await apiFetch(`/api/shipments/${id}`);
    const [items, o] = await Promise.all([peekItems(), apiFetch(`/api/customer-orders/${s.order_id}`).catch(() => null)]);
    const cust = o ? await peekParty("customers", o.customer_id) : { name: "" };
    const lots = {};
    await Promise.all([...new Set(s.lines.map(l => l.lot_id).filter(Boolean))].map(lid => peekGet(`/api/lots/${lid}`).then(l => { lots[lid] = l.lot_code; }).catch(() => {})));
    return { title: `${escapeHtml(s.code)} ${pk.tag(s.status)}`,
      sub: `${o ? `Order ${pk.link("customer-orders.html", o.id, o.code)} · ${escapeHtml(cust.name || "")}${o.po_number ? ` · PO ${escapeHtml(o.po_number)}` : ""}` : ""}`,
      body: pk.facts([["Created", pkDate(s.created_at)], ["Shipped", pkDate(s.ship_date)], ["Delivered", pkDate(s.delivered_at)], ["Carrier", escapeHtml(s.carrier || "")],
          ["Tracking", escapeHtml(s.tracking_number || "")], ["Invoice", s.invoice_id ? invoiceChipFromShipment(s) : ""], ["POD", s.pods && s.pods.length ? `${s.pods.length} file${s.pods.length === 1 ? "" : "s"}` : ""]])
        + pk.table(["Line", "Item", "Lot", "#Booked", "#Picked"], s.lines.map(l => `<tr><td>#${l.line_no ?? ""}</td><td>${itemCell(items, l.item_id)}</td>
            <td>${escapeHtml(lots[l.lot_id] || "")}</td><td class="num">${fmtQty(l.quantity)}</td><td class="num">${fmtQty(l.picked_quantity)}</td></tr>`))
        + (s.notes ? pk.section("Notes", `<p class="peek-notes">${escapeHtml(s.notes)}</p>`) : "") };
  },
  async invoice(id) {
    const v = await apiFetch(`/api/invoices/${id}`);
    const cust = await peekParty("customers", v.customer_id);
    return { title: `${escapeHtml(v.code)} ${pk.tag(v.status)}`, sub: escapeHtml(cust.name || ""),
      body: (v.status === "void" && v.void_reason ? `<div class="peek-void">Voided${v.voided_at ? ` ${pkDate(v.voided_at)}` : ""}${v.voided_by ? ` by ${escapeHtml(v.voided_by)}` : ""}: ${escapeHtml(v.void_reason)}</div>` : "")
        + pk.facts([["Invoice date", pkDate(v.invoice_date)], ["Due", pkDate(v.due_date)], ["Total", pk.money(v.total)], ["Paid", pk.money(v.amount_paid)], ["Balance", pk.money(v.balance)],
          ["Order", v.order_id ? pk.link("customer-orders.html", v.order_id, "open") : ""]])
        + pk.table(["Description", "#Qty", ...(hidesMoney() ? [] : ["#Price", "#Amount"])], v.lines.map(l => `<tr><td class="small">${escapeHtml(l.description || "")}</td><td class="num">${fmtQty(l.quantity)}</td>
            ${hidesMoney() ? "" : `<td class="num">${pk.price(l.unit_price)}</td><td class="num">${pk.money(l.amount)}</td>`}</tr>`))
        + ((v.shipment_ids || []).length ? pk.section("Shipments", `<div class="peek-chips">${v.shipment_ids.map((sid, i) => pk.link("shipments.html", sid, (v.shipment_codes || [])[i] || `#${sid}`)).join("")}</div>`) : "") };
  },
  async item(id) {
    const [i, m] = await Promise.all([apiFetch(`/api/stock-items/${id}`), apiFetch(`/api/stock-items/${id}/movements?limit=8`).catch(() => null)]);
    return { title: `${escapeHtml(i.code)}${i.is_generic ? ` <span class="tag source-generic">generic bulk</span>` : ""}`, sub: `${escapeHtml(i.title)}${i.category ? ` · ${escapeHtml(i.category)}` : ""}`,
      body: pk.facts([["On hand", fmtQty(i.on_hand)], ["Booked", fmtQty(i.booked)], ["Available", `<span class="${i.available < 0 ? "neg" : ""}">${fmtQty(i.available)}</span>`],
          ["Cost", pk.price(i.cost_price)], ["Selling price", pk.price(i.selling_price)]])
        + (m ? pk.section("Recent movements", pk.table(["Date", "Type", "#In / out", "Lot", "Documents"], m.movements.map(t => `<tr><td class="nowrap">${pkDate(t.date)}</td><td>${movementTypeTag(t.type)}</td>
            <td class="num ${t.quantity < 0 ? "neg" : "pos"}">${t.quantity > 0 ? "+" : ""}${fmtQty(t.quantity)}</td><td>${escapeHtml(t.lot || "")}</td><td>${movementLinks(t)}</td></tr>`))) : "") };
  },
};

// Stock movements (item page + peek): the type, and its documents as links -- from / to item, shipment,
// customer order, the customer's PO #, purchase order, invoice.
function movementTypeTag(t) {
  return `<span class="tag ${t === "receipt" ? "shipped" : t === "shipment" ? "confirmed" : t === "transfer" ? "transfer" : "draft"}">${escapeHtml(t.replace(/_/g, " "))}</span>`;
}
const MOVE_PAGE = { shipment: "shipments.html", customer_order: "customer-orders.html", purchase_order: "purchase-orders.html", invoice: "invoices.html" };
function movementLinks(t) {
  const parts = [];
  if (t.from_item_code && t.quantity > 0) parts.push(`From ${pk.link("item.html", t.from_item_id, t.from_item_code)}${t.from_lot ? ` <span class="muted">lot ${escapeHtml(t.from_lot)}</span>` : ""}`);
  else if (t.type === "transfer" && t.quantity < 0 && t.ref_item_id) parts.push(`To ${pk.link("item.html", t.ref_item_id, t.reference)}`);
  (t.links || []).forEach(l => parts.push(`<a class="doc-chip k-${l.kind}${l.po ? " cust-po" : ""}" href="${MOVE_PAGE[l.kind]}?id=${l.id}">${escapeHtml(l.label)}</a>`));
  if (!parts.length && t.reference) parts.push(escapeHtml(t.reference));
  return `<div class="move-links">${parts.join("")}</div>`;
}
