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

// ---- Formatting shared by every page ----
const PRICE_STEP = "0.00001"; // unit prices/costs are kept to 5 decimal places

// Unit price/cost: $ with up to 5 decimals, at least 2 ($0.21375, $3.50).
function fmtPrice(n) {
  const v = n || 0;
  return (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 });
}
// Money totals: always 2 decimals with a $ sign.
function fmtMoney(n) {
  const v = n || 0;
  return (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
// Quantities are whole units everywhere.
function fmtQty(n) {
  return Math.round(n || 0).toLocaleString("en-US");
}
// A price input with a "$" in front: priceInput("e-line-price", 0.21375, 'data-line="3"')
function priceInput(cls, value, attrs = "") {
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
  if (!prev) return `<a class="price-delta muted" ${link}>no previous price</a>`;
  const diff = current - prev.unit_price;
  if (Math.abs(diff) < 0.000005) return `<a class="price-delta muted" ${link}>= last ${fmtPrice(prev.unit_price)}</a>`;
  const good = kind === "sale" ? diff > 0 : diff < 0;
  return `<a class="price-delta ${good ? "pos" : "neg"}" ${link}>${diff > 0 ? "▲" : "▼"} ${fmtPrice(Math.abs(diff))} vs ${fmtPrice(prev.unit_price)} <span class="muted">${escapeHtml(prev.doc_code)}</span></a>`;
}

// Fills every [data-price-delta] element. Attributes: data-item, data-kind (sale|purchase),
// data-doc (current document id), and data-price or data-price-input (selector for a live
// price input in the same row).
async function refreshPriceDeltas(root = document) {
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
  modal.innerHTML = `<div class="modal"><p class="muted">Loading price history…</p></div>`;
  try {
    const [history, item] = await Promise.all([loadPriceHistory(itemId), apiFetch(`/api/stock-items/${itemId}`)]);
    const section = (kind, title, partyLabel, page) => {
      const rows = history.filter(h => h.kind === kind);
      if (!rows.length) return `<h4>${title}</h4><p class="muted">None yet.</p>`;
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
  input.placeholder = "Type to search code, title, group, barcode…";
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
        + (matches.length > 50 ? `<div class="muted small" style="padding:6px 10px;">${matches.length - 50} more — keep typing to narrow down</div>` : "")
      : `<div class="muted small" style="padding:6px 10px;">No items match "${escapeHtml(input.value)}"</div>`;
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
  customer_po: "Customer PO", vendor_invoice: "Vendor invoice", mtr: "Material test report (MTR)",
  vendor_quote: "Vendor quote", pod: "Proof of delivery", bol: "Bill of lading", other: "Other",
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
  const key = `${entityType}-${entityId}`;
  el.innerHTML = `<p class="muted small">Loading files…</p>`;
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
      <input type="text" id="att-note-${key}" placeholder="${escapeHtml(opts.notePlaceholder || "Note (optional)")}">
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
      </div>`).join("") : `<p class="muted small" style="margin:6px 0 0;">No files yet.</p>`}
  `;
  el.querySelectorAll("img[data-att]").forEach(async img => {
    try { img.src = await attachmentUrl(img.dataset.att); } catch (e) { img.remove(); }
  });
  const errorEl = document.getElementById(`att-error-${key}`);
  document.getElementById(`att-btn-${key}`).onclick = async () => {
    errorEl.textContent = "";
    const input = document.getElementById(`att-files-${key}`);
    if (!input.files.length) { errorEl.textContent = "Choose a file first."; return; }
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

// ---- 4x6 box labels: same layout and fields as the main portal's labels, plus our logo ----
// labels: [{ customer, shipment, order, po, job, item_code, item_title, qty }]
async function printBoxLabels(labels, title) {
  const win = window.open("", "_blank");
  if (!win) { alert("The browser blocked the new tab — allow pop-ups for this site and try again."); return; }
  win.document.write("<p style='font-family:sans-serif;padding:20px;color:#555'>Preparing labels…</p>");
  let company = {};
  try { company = await apiFetch("/api/company/"); } catch (e) { /* print without company details */ }
  const logo = company.has_logo
    ? `<div class="label-logo-slot"><img class="label-logo" src="${location.origin}/api/company/logo" alt=""></div>`
    : `<div class="label-logo-slot"></div>`;
  const footer = `${escapeHtml(company.name || "")}${company.email ? `<br>Questions? Email ${escapeHtml(company.email)}` : ""}`;
  const na = v => escapeHtml(v != null && String(v).trim() ? v : "N/A");
  const stripPo = v => String(v || "").replace(/^\s*po\s*#?\s*/i, "");
  const cards = labels.map(l => `
    <div class="label-card">
      ${logo}
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
          <div class="label-field-label">Quantity in Box</div><div class="label-field-value large">${fmtQty(l.qty)}</div>
        </div>
      </div>
      <div class="label-footer">${footer}</div>
    </div>`).join("");
  win.document.open();
  win.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${escapeHtml(title || "Labels")}</title>
    <link rel="stylesheet" href="${location.origin}/labels.css"></head><body>${cards}</body></html>`);
  win.document.close();
  // Shrink long item descriptions to fit their box (like the main portal), then print once the logo has loaded.
  const fit = () => {
    win.document.querySelectorAll(".item-multiline").forEach(el => {
      let size = 15;
      el.style.fontSize = size + "px";
      while (el.scrollHeight > el.clientHeight && size > 10) { size -= 0.5; el.style.fontSize = size + "px"; }
    });
    win.focus();
    win.print();
  };
  let tries = 0;
  const wait = () => {
    const ready = win.document.styleSheets.length && Array.from(win.document.images).every(i => i.complete);
    if (ready || ++tries > 40) fit(); else setTimeout(wait, 100);
  };
  wait();
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
const NAV_GROUPS = [
  { label: null, links: [["dashboard.html", "Dashboard"]] },
  { label: "CRM", links: [
    ["customers.html", "Customers"],
    ["customer-orders.html", "Customer Orders"],
    ["shipments.html", "Shipments"],
    ["pack-shipments.html", "Batch Pack Shipments"],
    ["pod.html", "Proof of Delivery"],
    ["invoices.html", "Invoices"],
  ] },
  { label: "Procurement", links: [
    ["vendors.html", "Vendors"],
    ["purchase-orders.html", "Purchase Orders"],
    ["landed-costs.html", "Landed Costs"],
  ] },
  { label: "Warehouse", links: [["stock-items.html", "Stock Items"], ["lots.html", "Lots"]] },
  { label: null, links: [["reports.html", "Reports"], ["company.html", "Company Settings"]] },
  { label: "Admin", minRole: "super_admin", links: [["users.html", "Users & Roles"]] },
];

const ROLE_LABELS = { super_admin: "Super admin", admin: "Admin", manager: "Manager", employee: "Employee" };

function renderSidebar(activePage) {
  const user = AuthGuard.getUser();
  const groups = NAV_GROUPS.filter(group => !group.minRole || AuthGuard.hasRole(group.minRole)).map(group => {
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
      <div class="brand"><img src="/api/company/logo" alt="" class="brand-logo" onerror="this.remove()"><span>AT-HUB</span></div>
      ${groups}
      <div class="sidebar-footer">
        ${user ? `<div class="user-line">${escapeHtml(user.full_name || user.username)}<div class="small">${ROLE_LABELS[user.role] || user.role}</div></div>` : ""}
        <a href="account.html" class="${activePage === "account.html" ? "active" : ""}">My Account</a>
        <a href="#" onclick="AuthGuard.logout(); return false;">Logout</a>
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
    try { return Object.assign({ hidden: [], widths: {} }, JSON.parse(localStorage.getItem(key)) || {}); }
    catch { return { hidden: [], widths: {} }; }
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
    const applyHidden = () => {
      style.textContent = state.hidden.map(i =>
        `table[data-tt="${id}"] > * > tr > :nth-child(${i + 1}):not([colspan]) { display: none; }`).join("\n");
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
        + `<div class="table-tools-actions"><a data-act="all">Show all</a> · <a data-act="widths">Reset widths</a></div>`;
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

    applyHidden();
    applyWidths();

    if (ths.some(th => th.classList.contains("sum"))) {
      table.createTFoot().className = "totals-row";
      this.refreshTotals(table);
      Array.from(table.tBodies).forEach(tb =>
        new MutationObserver(() => this.refreshTotals(table)).observe(tb, { childList: true, subtree: true, characterData: true }));
    }
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

document.addEventListener("DOMContentLoaded", () => {
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
