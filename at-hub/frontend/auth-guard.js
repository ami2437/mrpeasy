const API_BASE = "";

// The tab icon, on every page (each one loads this file)
if (document.head && !document.querySelector("link[rel~='icon']")) {
  document.head.insertAdjacentHTML("beforeend", '<link rel="icon" type="image/svg+xml" href="favicon.svg">');
}

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
    // viewing as someone: Logout ends the view and returns to your own account
    if (localStorage.getItem("at_hub_view_as_back")) { viewAsBack(); return; }
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
  // What the user's role allows (Users & Roles -> Roles). The server enforces it; screens only hide what you can't use.
  // A session saved before roles were editable has no list yet: it falls back to the old built-in defaults
  // until the background refresh (refreshSessionUser) brings the real one.
  ROLE_RANK: { employee: 1, manager: 2, admin: 3, super_admin: 4 },
  PERM_DEFAULT: { "customers.view": 2, "customers.edit": 2, "orders.view": 1, "orders.edit": 2, quotes: 3, "shipments.view": 1, "shipments.work": 1,
    "shipments.deliver": 2, "shipments.undo": 2, "pod.upload": 1, "stock.view": 1, "stock.edit": 2, "mtrs.manage": 2, "money.view": 3, invoices: 3,
    "invoices.split": 3, "credit_memos": 3, "invoices.funding": 3, "payments.import": 3, reconcile: 3, purchasing: 3, vendors: 2, po_shipments: 2, "receipts.correct": 2, vendor_payments: 3, landed_costs: 3, reports: 3, insights: 3, imports: 2, ai: 2,
    recycle_bin: 2, golive: 3, simulate: 3, company: 3, "types.manage": 2, templates: 3, tasks: 3, users: 4, backups: 4, "backups.download": 4, file_matcher: 4 },
  can(perm) {
    const user = this.getUser();
    if (!user) return false;
    if (Array.isArray(user.permissions)) return user.permissions.includes(perm);
    return (this.ROLE_RANK[user.role] || 0) >= (this.PERM_DEFAULT[perm] || 99);
  },
  canAny(...perms) { return perms.some(p => this.can(p)); },
  // Page guard: without the permission, back to the user's home page.
  requirePerm(...perms) {
    if (!this.requireLogin()) return null;
    if (!this.canAny(...perms)) {
      alert("Your role doesn't include this page.");
      window.location.href = homePage();
      return null;
    }
    return this.getUser();
  },
};

// ---- several people at once: every record screen remembers the version it loaded and sends it with a
// change (X-Row-Version). If someone else saved in between, the server refuses (409) and the conflict pop-up
// says who and what -- Reload to see it, or Save Mine Anyway.
const recordVersions = {};  // "customer-orders/158" -> row_version
function recordKey(path) {
  const m = String(path).match(/^\/api\/(customer-orders|purchase-orders|shipments|invoices|quotes|customers|vendors)\/(\d+)(?=[/?]|$)/);
  return m ? `${m[1]}/${m[2]}` : null;
}

// ---- Saving is visible: while anything is being saved a thin bar runs along the top of the page, and the button you
// clicked shows a spinner, then a tick. Background calls (presence, Recently Viewed, checks that only read) don't. ----
const SaveIndicator = {
  QUIET: /^\/api\/(presence|auth\/recent|auth\/print-options|auth\/filters|invoices\/\d+\/qty-check|stock-items\/generic-sources)/,
  open: 0, lastBtn: null, lastAt: 0,
  bar() {
    let b = document.getElementById("save-bar");
    if (!b) { b = document.createElement("div"); b.id = "save-bar"; document.body.appendChild(b); }
    return b;
  },
  start(path) {
    if (this.QUIET.test(path)) return null;
    this.open++;
    this.bar().classList.add("on");
    const btn = Date.now() - this.lastAt < 800 && this.lastBtn && this.lastBtn.isConnected ? this.lastBtn : null;
    if (btn && !btn.classList.contains("is-busy")) { btn.classList.remove("is-done"); btn.classList.add("is-busy"); btn.dataset.wasDisabled = btn.disabled ? "1" : ""; btn.disabled = true; }
    return { btn };
  },
  end(h, ok) {
    if (!h) return;
    this.open = Math.max(0, this.open - 1);
    if (!this.open) this.bar().classList.remove("on");
    const btn = h.btn;
    if (btn && btn.classList.contains("is-busy")) {
      btn.classList.remove("is-busy");
      btn.disabled = !!btn.dataset.wasDisabled;
      if (ok && btn.isConnected) { btn.classList.add("is-done"); setTimeout(() => btn.classList.remove("is-done"), 1300); }
    }
  },
};
document.addEventListener("click", e => {
  const b = e.target.closest && e.target.closest("button");
  if (b) { SaveIndicator.lastBtn = b; SaveIndicator.lastAt = Date.now(); }
}, true);
async function apiFetch(path, options = {}) {
  if ((options.method || "GET").toUpperCase() === "GET") return apiFetchRaw(path, options);
  const h = SaveIndicator.start(path);
  try { const r = await apiFetchRaw(path, options); SaveIndicator.end(h, true); return r; }
  catch (e) {
    SaveIndicator.end(h, false);
    // a look-alike to check / a vendor SO # already used: ask, then the same action again
    if (/^(LOOKALIKE|DUPLICATE_SO)\|/.test(e.message || "")) return handleCheckRefusal(e, () => apiFetch(path, options), () => apiFetch(path, withAllowDuplicate(options)));
    throw e;
  }
}
async function apiFetchRaw(path, options = {}) {
  const token = AuthGuard.getToken();
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const method = (options.method || "GET").toUpperCase(), key = recordKey(path);
  if (method !== "GET" && key && recordVersions[key] != null && !headers["X-Force-Save"]) headers["X-Row-Version"] = String(recordVersions[key]);

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, Object.assign({}, options, { headers }));
  } catch (e) {
    throw new Error("Can't reach AT-HUB -- check the internet connection and try again");
  }

  if (response.status === 401) {
    AuthGuard.clearSession();
    window.location.href = "login.html";
    throw new Error("Not authenticated");
  }

  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) {
    // not JSON: a crash page or a proxy's error page
    throw new Error(response.ok ? "The server sent a reply AT-HUB couldn't read" : `Server error (${response.status}) -- try again; if it keeps happening, tell an admin`);
  }

  if (response.status === 409 && data && data.conflict) {
    const choice = await conflictDialog(data.conflict);
    if (choice === "force") return apiFetch(path, Object.assign({}, options, { headers: Object.assign({}, options.headers || {}, { "X-Force-Save": "1" }) }));
    if (choice === "reload") { location.reload(); return new Promise(() => {}); }
    throw new Error("Not saved -- someone else changed it first. Reload to see their changes.");
  }
  if (response.ok && key) {
    const id = parseInt(key.split("/")[1]);
    if (data && !Array.isArray(data) && data.row_version != null && data.id === id) recordVersions[key] = data.row_version;
    else if (method !== "GET") delete recordVersions[key];  // changed, version unknown now: don't check until it's loaded again
  }

  if (!response.ok) {
    const detail = (data && data.detail) ? data.detail : `Request failed (${response.status})`;
    if (Array.isArray(detail)) {
      // Pydantic validation errors: show just the messages, e.g. "Quantity must be a whole number".
      throw new Error([...new Set(detail.map(d => String(d.msg || d).replace(/^Value error, /, "")))].join("; "));
    }
    // an object detail ({code, message, ...}): its message shows; callers can read err.status / err.detail
    const err = new Error(typeof detail === "string" ? detail : detail.message || JSON.stringify(detail));
    err.status = response.status;
    err.detail = detail;
    throw err;
  }
  return data;
}

// "edited Job #, Notes" / "changed a line" / "added a line" -- one activity-log row in words
function activitySummary(r) {
  const nice = k => ({ po_number: "Customer PO #", job_number: "Job #", delivery_date: "delivery date", customer_po_date: "PO date",
    ship_to_address: "ship-to", customer_id: "customer", vendor_id: "vendor", expected_date: "required-by date", vendor_so_number: "vendor SO #",
    unit_price: "price", unit_cost: "cost", quantity: "quantity" }[k] || k.replace(/_/g, " "));
  let fields = [];
  try { fields = Object.keys(JSON.parse(r.detail || "{}")).map(nice); } catch (e) {}
  const what = (r.action || "").replace(/\/\d+/g, "");
  if (!what) return r.method === "PUT" ? `edited ${fields.join(", ") || "the details"}` : r.method.toLowerCase();
  const verb = { POST: "added", PUT: "changed", DELETE: "removed" }[r.method] || r.method.toLowerCase();
  const noun = { lines: "a line", "line-order": "the line order", charges: "a charge", bills: "a vendor invoice", payments: "a payment" }[what] || what.replace(/-/g, " ");
  const done = { "line-order": "re-ordered the lines", confirm: "confirmed it", cancel: "cancelled it", receive: "received items",
    "duplicate-po-ok": "OK'd the duplicate PO #", shipments: "created a shipment", "confirm-booking": "confirmed the bookings",
    "unconfirm-booking": "unconfirmed the bookings", pick: "picked", unpick: "undid the picking", "accept-packing": "accepted the packing",
    unpack: "undid the packing", ship: "shipped it", unship: "undid the shipment", unbook: "unbooked stock", "unbook-all": "unbooked everything",
    boxes: "re-boxed it", "pallet-weights": "set pallet weights", delivered: "marked it delivered", undeliver: "cleared the delivery",
    code: "renamed it", funding: "changed the funding", split: "split the invoice", merge: "combined invoices", "print-options": "changed print options",
    status: (() => { try { return `marked it ${JSON.parse(r.detail || "{}").status || "changed"}`; } catch (e) { return "changed the status"; } })() }[what];
  return done || `${verb} ${noun}${fields.length && r.method === "PUT" ? ` (${fields.join(", ")})` : ""}`;
}

async function conflictDialog(c) {
  let changes = "";
  const kind = { "customer-orders": "customer_order", "purchase-orders": "purchase_order" }[c.collection];
  if (kind) {
    try {
      const rows = await (await fetch(`${API_BASE}/api/activity/${kind}/${c.id}`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } })).json();
      const recent = (rows || []).slice(0, 4);
      if (recent.length) changes = `<div class="small" style="margin-top:6px;"><strong>Their recent changes</strong><ul class="conflict-list">${recent.map(r =>
        `<li>${escapeHtml(r.by || "")} · ${fmtTime(r.at)} · ${escapeHtml(activitySummary(r))}</li>`).join("")}</ul></div>`;
    } catch (e) {}
  }
  const when = c.at ? fmtTime(c.at) : "";
  const { value } = await askDialog({ title: `${c.record} was just changed`, tone: "warn",
    body: `<p><strong>${escapeHtml(c.by)}</strong> saved ${escapeHtml(c.record)}${when ? ` at ${when}` : ""} while you had it open, so <strong>your change wasn't saved</strong> -- nothing was overwritten.</p>${changes}
      <p class="muted small">Reload to see their version and redo your change, or save yours over theirs.</p>`,
    buttons: [{ label: "Reload", value: "reload", cls: "" }, { label: "Save Mine Anyway", value: "force", cls: "secondary" }, { label: "Cancel", value: null, cls: "secondary" }] });
  return value;
}

// POST a FormData (file upload). apiFetch forces JSON; the browser must set the multipart boundary.
async function apiUpload(path, form) {
  try { return await apiUploadRaw(path, form); }
  catch (e) {
    if (/^(LOOKALIKE|DUPLICATE_SO)\|/.test(e.message || "")) return handleCheckRefusal(e, () => apiUploadRaw(path, form), () => { form.set("allow_duplicate", "true"); return apiUploadRaw(path, form); });
    throw e;
  }
}
async function apiUploadRaw(path, form) {
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
// Every search box: each word typed must appear somewhere in the record's fields, and spaces / dashes / slashes
// don't matter -- "m246 30a", "M246-30A" and "m24630a" all find job M246-30A; "hudson 4180718" narrows to both.
function searchNorm(v) { return String(v ?? "").toLowerCase(); }
function searchMatch(q, ...values) {
  const words = searchNorm(q).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const hay = values.flat(Infinity).filter(v => v != null && v !== "").map(searchNorm).join(" \u0001 ");
  const loose = hay.replace(/[^a-z0-9\u0001]+/g, " "), tight = hay.replace(/[^a-z0-9\u0001]+/g, "");
  return words.every(w => {
    const wl = w.replace(/[^a-z0-9]+/g, " ").trim(), wt = w.replace(/[^a-z0-9]+/g, "");
    return hay.includes(w) || (wl && loose.includes(wl)) || (wt && tight.includes(wt));
  });
}

function hidesMoney() { return !!AuthGuard.getUser() && !AuthGuard.can("money.view"); }

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
// ---- dates & times, Outlook-style (app/services/clock.py) ----
// Moments (shipped, delivered, created_at...) come from the API in UTC marked "Z" and show in the user's own time zone
// (Account page; default the company's). Calendar dates (delivery / due / invoice dates) come bare and show as that
// same day for everyone.
function userTz() {
  const u = AuthGuard.getUser();
  return (u && u.effective_timezone) || "America/Chicago";
}
const _ZONED = /([zZ]|[+-]\d\d:?\d\d)$/;
// A moment as a Date: zoned -> as is; a bare value with a time -> read as UTC (audit stamps); a Date -> itself.
function utcTime(v) {
  if (!v) return null;
  if (v instanceof Date) return v;
  const s = String(v);
  return new Date(_ZONED.test(s) || !s.includes("T") ? s : s + "Z");
}
function _calendarDay(v) {
  const m = String(v).match(/^(\d{4})-(\d\d)-(\d\d)/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
}
function _isMoment(v) { return v instanceof Date || _ZONED.test(String(v)); }
// The day: a moment in the user's zone, a calendar date as itself. opts: Intl date options (e.g. { month: "short" }).
function fmtDate(v, opts = {}) {
  if (!v) return "";
  try {
    if (_isMoment(v)) return utcTime(v).toLocaleDateString(undefined, { timeZone: userTz(), ...opts });
    const d = _calendarDay(v);
    return d ? d.toLocaleDateString(undefined, opts) : "";
  } catch (e) { return String(v).slice(0, 10); }
}
// Day and time of a moment, in the user's zone (a bare date shows as just the day).
function fmtWhen(v) {
  if (!v) return "";
  if (!(v instanceof Date) && !String(v).includes("T")) return fmtDate(v);
  return utcTime(v).toLocaleString(undefined, { timeZone: userTz(), year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function fmtTime(v) {
  return v ? utcTime(v).toLocaleTimeString(undefined, { timeZone: userTz(), hour: "numeric", minute: "2-digit" }) : "";
}
const fmtDateTime = fmtWhen;
const fmtDay = fmtDate;
// YYYY-MM-DD for a date input: today in the user's zone, or the day of a stored value.
function todayISO() { return new Date().toLocaleDateString("en-CA", { timeZone: userTz() }); }
function dayISO(v) {
  if (!v) return "";
  return _isMoment(v) ? utcTime(v).toLocaleDateString("en-CA", { timeZone: userTz() }) : String(v).slice(0, 10);
}
// Zones offered on the Account / Users pages (any IANA name works through the API).
const TIMEZONE_CHOICES = [
  ["America/Chicago", "Central (Chicago, Texas)"], ["America/New_York", "Eastern (New York)"], ["America/Denver", "Mountain (Denver)"],
  ["America/Phoenix", "Arizona (no DST)"], ["America/Los_Angeles", "Pacific (Los Angeles)"], ["America/Anchorage", "Alaska"], ["Pacific/Honolulu", "Hawaii"],
  ["America/Mexico_City", "Mexico City"], ["America/Toronto", "Toronto"], ["Europe/London", "London"], ["Europe/Berlin", "Central Europe (Berlin)"],
  ["Asia/Dubai", "Dubai"], ["Asia/Kolkata", "India (Kolkata)"], ["Asia/Singapore", "Singapore"], ["Asia/Shanghai", "China (Shanghai)"],
  ["Asia/Tokyo", "Japan (Tokyo)"], ["Australia/Sydney", "Sydney"], ["UTC", "UTC"],
];
function timezoneOptions(selected, companyLabel = "Company default") {
  const have = TIMEZONE_CHOICES.some(([k]) => k === selected);
  return `<option value="" ${!selected ? "selected" : ""}>${escapeHtml(companyLabel)}</option>`
    + (selected && !have ? `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)}</option>` : "")
    + TIMEZONE_CHOICES.map(([k, l]) => `<option value="${k}" ${k === selected ? "selected" : ""}>${escapeHtml(l)}</option>`).join("");
}
// The short name of the user's zone right now (CDT, IST...).
function tzAbbrev(date = new Date()) {
  try { return new Intl.DateTimeFormat(undefined, { timeZone: userTz(), timeZoneName: "short" }).formatToParts(date).find(p => p.type === "timeZoneName").value; }
  catch (e) { return ""; }
}

// A link someone typed, made safe to click: web addresses and AT-HUB pages only (never javascript: / data:). null if not.
function safeHref(url) {
  const u = String(url || "").trim(), bare = u.replace(/[\s\u0000-\u001f]+/g, "");  // browsers drop these inside a URL ("java\nscript:")
  if (!u) return null;
  return /^[a-z][a-z0-9+.-]*:/i.test(bare) && !/^https?:\/\//i.test(bare) ? null : escapeHtml(u);
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
// to mark them delivered. Never a blocker -- "Invoice Anyway" carries on -- unless required (Ready To Invoice:
// delivered first, then the invoice). Resolves true to go ahead.
async function deliveredCheckBeforeInvoice(shipments, { required = false } = {}) {
  const pending = (shipments || []).filter(s => s && s.status === "shipped" && !s.delivered_at);
  if (!pending.length) return true;
  const today = todayISO();
  const { value, el } = await askDialog({ title: pending.length === 1 ? "Not delivered yet" : `${pending.length} shipments not delivered yet`, tone: "warn",
    body: `<p>${pending.map(s => `<strong>${escapeHtml(s.code)}</strong>${s.ship_date ? ` shipped ${fmtDate(s.ship_date)}` : ""}`).join(", ")}
        ${pending.length === 1 ? "isn't" : "aren't"} marked delivered. Mark ${pending.length === 1 ? "it" : "them"} delivered ${required ? "first, then the invoice is created" : "to complete the order's flow, or invoice anyway"}.</p>
      <label>Delivered on</label><input type="date" class="ask-delivered" value="${today}" max="${today}" style="max-width:180px;">
      <p class="muted small" style="margin-top:6px;">Uploading a proof of delivery later also marks it delivered.</p>`,
    buttons: [{ label: "Mark Delivered & Invoice", value: "mark", cls: "confirm-btn" },
              ...(required ? [] : [{ label: "Invoice Anyway", value: "skip", cls: "secondary" }]),
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

// A shipment's delivered date, clickable for anyone who may change it (opens the Delivery Date pop-up).
// The page reloads itself through afterDeliveryChange(shipment) if it defines one.
function deliveredDateHtml(s, text = null) {
  const label = text ?? (s.delivered_at ? fmtDate(s.delivered_at) : "");
  const split = (s.lines || []).some(l => l.delivered_at);  // a line arrived on another day
  if (!AuthGuard.can("shipments.deliver") || !["shipped", "delivered", "invoiced"].includes(s.status)) return escapeHtml(label);
  return `<a class="link date-link" title="${split ? "Some lines arrived on another day -- " : ""}Change the delivery date" onclick="event.stopPropagation(); editDeliveryDate(${s.id})">${escapeHtml(label)}${split ? "*" : ""}</a>`;
}
async function editDeliveryDate(id) {
  const sh = await openDeliveryDates(id);
  if (!sh) return;
  if (typeof afterDeliveryChange === "function") await afterDeliveryChange(sh); else location.reload();
}

// The Delivery Date pop-up: the shipment's date, and (rarely) a line that arrived on another day. Resolves the saved
// shipment, or null when cancelled. A shipment delivered for the first time then gets "bill it now?".
async function openDeliveryDates(id) {
  let sh;
  try { sh = await apiFetch(`/api/shipments/${id}`); } catch (e) { alert(e.message); return null; }
  const known = typeof items !== "undefined" && Array.isArray(items) ? items : [];
  const groups = {};
  sh.lines.forEach(l => {
    const g = groups[l.order_line_id] ??= { order_line_id: l.order_line_id, line_no: l.line_no, item_id: l.item_id, qty: 0, own: null };
    g.qty += l.quantity;
    if (l.delivered_at) g.own = l.delivered_at;
  });
  const rows = Object.values(groups).sort((a, b) => (a.line_no || 0) - (b.line_no || 0));
  const codes = {};
  await Promise.all([...new Set(rows.map(r => r.item_id))].map(async iid => {
    const k = known.find(i => i.id === iid);
    if (k) { codes[iid] = k.code; return; }
    try { codes[iid] = (await apiFetch(`/api/stock-items/${iid}`)).code; } catch { codes[iid] = ""; }
  }));
  const today = todayISO(), shipped = sh.ship_date ? dayISO(sh.ship_date) : "";
  const wasDelivered = !!sh.delivered_at;
  let main = sh.delivered_at ? dayISO(sh.delivered_at) : (shipped > today ? shipped : today);
  let lineDates = Object.fromEntries(rows.map(r => [r.order_line_id, r.own ? dayISO(r.own) : ""]));  // "" = same as the shipment
  let showLines = rows.some(r => r.own), error = "";
  while (true) {
    const { value, el } = await askDialog({ title: `Delivery Date — ${sh.code}`,
      body: `<p class="muted" style="margin-top:0;">${shipped ? `Shipped ${fmtDate(sh.ship_date)}` : ""}${sh.delivered_by ? ` · last set by ${escapeHtml(sh.delivered_by)}` : ""}</p>
        <label>Delivered On</label><input type="date" class="dd-main" value="${main}" ${shipped ? `min="${shipped}"` : ""} max="${today}" style="max-width:180px;">
        <label class="check-label" style="margin-top:10px;"><input type="checkbox" class="dd-split" ${showLines ? "checked" : ""}> Some lines arrived on a different day</label>
        <div class="dd-lines no-table-tools" style="${showLines ? "" : "display:none;"}">
          <table class="fit-table" style="margin-top:6px;"><thead><tr><th>Line</th><th class="grow">Item</th><th class="num">Qty</th><th>Delivered On</th></tr></thead>
          <tbody>${rows.map(r => `<tr><td class="line-no">#${r.line_no ?? ""}</td><td class="grow"><strong>${escapeHtml(codes[r.item_id] || "")}</strong></td>
            <td class="num">${fmtQty(r.qty)}</td>
            <td><input type="date" class="dd-line" data-ol="${r.order_line_id}" ${lineDates[r.order_line_id] ? `data-touched="1"` : ""} value="${lineDates[r.order_line_id] || main}" ${shipped ? `min="${shipped}"` : ""} max="${today}"></td></tr>`).join("")}</tbody></table>
          <p class="muted small">A line left on the shipment's date simply arrived with it.</p></div>
        ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}`,
      buttons: [{ label: "Save", value: "save", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
    if (value !== "save") return null;
    main = el.querySelector(".dd-main").value;
    showLines = el.querySelector(".dd-split").checked;
    el.querySelectorAll(".dd-line").forEach(i => { lineDates[i.dataset.ol] = i.value === main ? "" : i.value; });
    if (!main) { error = "Pick the delivery date."; continue; }
    const lines = rows.map(r => ({ order_line_id: r.order_line_id,
      delivered_at: showLines && lineDates[r.order_line_id] ? `${lineDates[r.order_line_id]}T12:00:00` : null }));
    try {
      const saved = await apiFetch(`/api/shipments/${id}/delivery-dates`, { method: "PUT", body: JSON.stringify({ delivered_at: `${main}T12:00:00`, lines }) });
      toast(`${sh.code} delivered ${fmtDate(saved.delivered_at)}`);
      if (!wasDelivered) offerBilling([saved]);
      return saved;
    } catch (e) { error = e.message; }
  }
}
// The line dates follow the shipment's date until someone changes one.
document.addEventListener("change", e => {
  if (!e.target.classList || !e.target.classList.contains("dd-main")) return;
  const box = e.target.closest(".ask-dialog");
  box.querySelectorAll(".dd-line").forEach(i => { if (!i.dataset.touched) i.value = e.target.value; });
});
document.addEventListener("change", e => {
  if (e.target.classList && e.target.classList.contains("dd-line")) e.target.dataset.touched = "1";
  if (e.target.classList && e.target.classList.contains("dd-split")) e.target.closest(".ask-dialog").querySelector(".dd-lines").style.display = e.target.checked ? "" : "none";
});

// Right after a delivery is recorded: "bill it now?" (managers and up -- invoices are money work). Shipments
// of the same order go on one invoice. Create flies the shipments into the invoice, ticks, then opens it.
// Resolves true when it billed (the page is navigating away), false for "Not now".
async function offerBilling(shipments) {
  const list = (shipments || []).filter(s => s && s.status === "delivered");
  if (!list.length || !AuthGuard.can("invoices")) return false;
  const groups = {};
  list.forEach(s => (groups[s.order_id] = groups[s.order_id] || []).push(s));
  const sets = Object.values(groups), n = sets.length;
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "glass-back";
    back.id = "bill-now";
    const key = e => { if (e.key === "Escape") close(false); };
    const close = v => {
      document.removeEventListener("keydown", key);
      document.body.classList.remove("glass-open");
      back.classList.add("closing");
      setTimeout(() => back.remove(), 180);
      resolve(v);
    };
    back.addEventListener("click", e => { if (e.target === back) close(false); });
    back.innerHTML = `<div class="glass-panel bill-panel" role="dialog" aria-modal="true" aria-labelledby="bn-title">
      <div class="sm-head"><div><h3 id="bn-title">${icon("checkCircle", "pos")} Delivered — bill it now?</h3>
          <div class="muted small">${list.length === 1 ? `${escapeHtml(list[0].code)} is delivered.` : `${list.length} shipments are delivered.`}
            Create the ${n === 1 ? "invoice" : `${n} invoices`} while it's fresh — ${n === 1 ? "it starts" : "they start"} as a draft you review before sending.</div></div>
        <button type="button" class="icon-btn sm-close" aria-label="Close" data-act="later">${icon("x")}</button></div>
      <div class="bn-list">${sets.map(g => `<div class="bn-row">${g.map(s => `<span class="bn-chip" data-id="${s.id}">${icon("truck")}${escapeHtml(s.code)}</span>`).join("")}
          ${g.length > 1 ? `<span class="muted small">same order · one invoice</span>` : ""}</div>`).join("")}</div>
      <div class="sm-foot">
        <div class="sm-box" id="bn-box" aria-hidden="true">${icon("receipt")}<span class="sm-count" id="bn-count">0</span></div>
        <div class="sm-summary"><strong>${n}</strong> draft invoice${n === 1 ? "" : "s"}</div>
        <div class="error" id="bn-error"></div>
        <button type="button" class="secondary" data-act="later">Not now</button>
        <button type="button" class="sm-go" id="bn-go">Create Invoice${n === 1 ? "" : "s"}</button>
      </div></div>`;
    document.body.appendChild(back);
    document.body.classList.add("glass-open");
    document.addEventListener("keydown", key);
    back.querySelectorAll('[data-act="later"]').forEach(b => b.addEventListener("click", () => close(false)));
    const go = back.querySelector("#bn-go");
    go.focus();
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = "Creating…";
      const made = [];
      try {
        for (const g of sets)
          made.push(await apiFetch("/api/invoices/from-shipments", { method: "POST", body: JSON.stringify({ shipment_ids: g.map(s => s.id), shipping_charge: 0 }) }));
      } catch (e) {
        back.querySelector("#bn-error").textContent = e.message;
        if (!made.length) { go.disabled = false; go.textContent = `Create Invoice${n === 1 ? "" : "s"}`; return; }
      }
      await flyIntoBox([...back.querySelectorAll(".bn-chip")], back.querySelector("#bn-box"), back.querySelector("#bn-count"));
      const done = document.createElement("div");
      done.className = "sm-done";
      done.innerHTML = `<div class="sm-done-check">${icon("receipt")}</div><h3>${made.map(i => escapeHtml(i.code)).join(", ")} created</h3>
        <p class="muted">${made.length === 1 ? "Opening the invoice…" : "Opening invoices…"}</p>`;
      back.querySelector(".glass-panel").appendChild(done);
      requestAnimationFrame(() => done.classList.add("show"));
      await new Promise(r => setTimeout(r, 1100));
      resolve(true);
      location.href = made.length === 1 ? `invoices.html?id=${made[0].id}` : "invoices.html";
    });
  });
}

// Chips fly into a box one by one; the box bounces and counts (Create Shipment, Bill Now, batch Ship).
function flyIntoBox(chips, box, count) {
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const t = box.getBoundingClientRect(), tx = t.left + t.width / 2, ty = t.top + t.height / 2;
  box.classList.add("filling");
  return Promise.all(chips.map((src, i) => new Promise(resolve => {
    const from = src.getBoundingClientRect();
    const chip = document.createElement("div");
    chip.className = "sm-chip";
    chip.textContent = src.textContent.trim();
    chip.style.left = `${from.left}px`;
    chip.style.top = `${from.top}px`;
    document.body.appendChild(chip);
    const land = () => { chip.remove(); count.textContent = String(i + 1); box.animate([{ transform: "scale(1)" }, { transform: "scale(1.18)" }, { transform: "scale(1)" }], { duration: 260, easing: "ease-out" }); resolve(); };
    if (reduce) { setTimeout(land, 40 * i); return; }
    src.style.visibility = "hidden";
    const dx = tx - (from.left + chip.offsetWidth / 2), dy = ty - (from.top + chip.offsetHeight / 2);
    chip.animate([
      { transform: "translate(0, 0) scale(1)", opacity: 1 },
      { transform: `translate(${dx * 0.55}px, ${dy * 0.35 - 40}px) scale(.85)`, opacity: 1, offset: 0.55 },
      { transform: `translate(${dx}px, ${dy}px) scale(.25)`, opacity: 0.2 },
    ], { duration: 620, delay: i * 90, easing: "cubic-bezier(.45,.05,.35,1)", fill: "forwards" }).onfinish = land;
  })));
}

// One pasted row -> columns: tab-separated (Excel), else comma, else spaces. Item #s never contain spaces.
function splitPasteRow(row, allowSpaces = true) {
  const t = String(row || "").trim();
  if (t.includes("\t")) return t.split("\t").map(c => c.trim());
  if (t.includes(",")) return t.split(",").map(c => c.trim());
  return allowSpaces ? t.split(/\s+/) : [t];
}

// ---- Status underlays: a record's card takes its status colour, and drafts plus finished / void / cancelled records
// also carry a faint watermark word -- the amber DRAFT order, applied everywhere. setUnderlay(el, null) clears it.
// Tones: amber (draft) · blue (open, waiting) · pink (in progress) · green (done) · red (needs attention) · grey (closed) · teal
const UNDERLAY_TONES = { orange: "#ea580c", amber: "#d97706", blue: "#2563eb", pink: "#db2777", green: "#16a34a", red: "#dc2626", grey: "#64748b", teal: "#0d9488" };
// The watermark is an SVG picture on a card-sized layer, so it never pushes or clips the card's content (a long
// table still scrolls); the word scales with the card. Light and dark versions: the dark one a touch stronger.
function watermarkSvg(word, color, opacity) {
  // The word is fitted to 720 of the picture's 1000 units (textLength makes that exact whatever the font), so once
  // tilted it still sits wholly inside: no letters cut off at either end, top or bottom.
  const n = Math.max(word.length, 1), size = Math.min(160, Math.round(720 / (n * 0.8)));
  const spacing = Math.round(size * .1), natural = n * size * 0.7 + (n - 1) * spacing, width = Math.min(720, Math.round(natural));
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 460"><text x="500" y="${230 + Math.round(size * .36)}" text-anchor="middle"
    font-family="Segoe UI, system-ui, Arial, sans-serif" font-weight="800" font-size="${size}" letter-spacing="${spacing}"
    textLength="${width}" lengthAdjust="spacingAndGlyphs"
    fill="${color}" fill-opacity="${opacity}" transform="rotate(-16 500 230)">${escapeHtml(word)}</text></svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}
function setUnderlay(el, tone, word = "") {
  if (!el) return;
  Object.keys(UNDERLAY_TONES).forEach(t => el.classList.remove(`uw-${t}`));
  el.classList.toggle("uw", !!tone);
  if (tone) el.classList.add(`uw-${tone}`);
  if (tone && word) {
    el.dataset.wm = word;
    el.style.setProperty("--wm-img", watermarkSvg(word, UNDERLAY_TONES[tone], .075));
    el.style.setProperty("--wm-img-dark", watermarkSvg(word, UNDERLAY_TONES[tone], .14));
  } else {
    delete el.dataset.wm;
    el.style.removeProperty("--wm-img");
    el.style.removeProperty("--wm-img-dark");
  }
}
// Status -> [tone, watermark] for each kind of record (one place, so every screen reads the same)
const UNDERLAYS = {
  order(o) {
    if (o.status === "validation") return ["orange", o.ai_source ? "AI READ · VALIDATE" : "VALIDATE"];  // hazard stripes, not the draft's amber
    if (o.status === "draft") return ["amber", "DRAFT"];
    if (o.status === "cancelled") return ["grey", "CANCELLED"];
    if (o.status === "invoiced") return ["green", "COMPLETE"];
    if (o.status === "shipped") return ["green"];
    return (o.lines || []).some(l => l.shipped_quantity > 0 || l.booked_quantity > 0) ? ["pink"] : ["blue"];
  },
  quote(q) {
    if (!q.id) return [null];
    if (q.status === "draft") return ["amber", "DRAFT"];
    if (q.status === "converted") return ["green", "CONVERTED"];
    if (q.status === "accepted") return ["green"];
    if (q.status === "declined") return ["grey", "DECLINED"];
    if (q.valid_until && new Date(q.valid_until) < new Date(new Date().toDateString())) return ["grey", "EXPIRED"];
    return ["blue"];
  },
  po(p) {
    if (p.status === "validation" && p.ai_source) return ["orange", "AI READ · VALIDATE"];
    return { validation: ["orange", "VALIDATE"], draft: ["amber", "DRAFT"], ordered: ["blue"], shipped: ["teal", "IN TRANSIT"], partially_received: ["pink"], received: ["green", "RECEIVED"],
             cancelled: ["grey", "CANCELLED"] }[p.status] || [null];
  },
  invoice(inv) {
    if (inv.status === "void") return ["grey", "VOID"];
    if (inv.status === "draft") return ["amber", "DRAFT"];
    if (inv.status === "paid" || (inv.balance <= 0.005 && (inv.total || 0) > 0)) return ["green", "PAID"];
    if (inv.due_date && new Date(inv.due_date) < new Date(new Date().toDateString())) return ["red", "OVERDUE"];
    return inv.amount_paid > 0 ? ["pink"] : ["blue"];
  },
  shipment(s) {
    return s.status === "cancelled" ? ["grey", "CANCELLED"] : [null];  // open / shipped: .ship-inproc / .ship-done
  },
  active(rec, word = "INACTIVE") { return rec && rec.is_active === false ? ["grey", word] : [null]; },
  lot(l) { return { on_hold: ["amber", "ON HOLD"], rejected: ["red", "REJECTED"] }[l.status] || [null]; },
};
// The same colours as a soft wash on a list row -- only for the tones asked for (attention states, not every row).
function rowUnderlay(kind, rec, tones = ["red"]) {
  const [tone] = UNDERLAYS[kind](rec);
  return tone && tones.includes(tone) ? `row-uw uw-${tone}` : "";
}
function applyUnderlay(el, kind, rec, ...extra) {
  const [tone, word] = UNDERLAYS[kind](rec, ...extra);
  setUnderlay(el, tone, word);
}

// A small choice pop-up: resolves to the clicked button's value (null on Esc / click outside),
// with the dialog element so the caller can read any inputs in `body` before it closes.
// askDialog({ title, body: html, buttons: [{ label, value, cls }] }) -> Promise<{ value, el }>
function askDialog({ title, body = "", buttons = [], tone = "", wide = false }) {
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-backdrop";
    if (document.body.classList.contains("glass-open")) back.classList.add("over-glass");  // asked from inside a glass window: show above it
    back.innerHTML = `<div class="modal ask-dialog ${tone} ${wide ? "ask-wide" : ""}" role="dialog" aria-modal="true"><h3 style="margin:0 0 8px;">${escapeHtml(title)}</h3>
      <div class="ask-body">${body}</div>
      <div class="btn-row" style="margin-top:14px;">${buttons.map((b, i) => `<button type="button" class="${b.cls || ""}" data-i="${i}">${escapeHtml(b.label)}</button>`).join("")}</div></div>`;
    const done = value => { document.removeEventListener("keydown", onKey); back.remove(); resolve({ value, el: back }); };
    const onKey = e => { if (e.key === "Escape") done(null); };
    // only a click that starts AND ends on the backdrop closes it: picking from a dropdown list that hangs
    // below the box hides the list on mousedown, so its click would otherwise land on the backdrop and cancel
    let downOnBack = false;
    back.addEventListener("mousedown", e => { downOnBack = e.target === back; });
    back.addEventListener("click", e => {
      if (e.target === back) { if (downOnBack) done(null); return; }
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
              <td>${h.date ? fmtDate(h.date) : ""}</td>
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
    matches = Array.from(select.options).filter(o => searchMatch(input.value, o.textContent, o.dataset.search));
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
function attachmentTag(cat) { return `<span class="file-tag ft-${escapeHtml(cat)}">${escapeHtml(ATTACHMENT_TAGS[cat] || TypeLists.label("attachment", cat))}</span>`; }
function attachmentLabel(cat) { return (TypeLists.cache.attachment && TypeLists.label("attachment", cat)) || ATTACHMENT_LABELS[cat] || cat; }

// Thumbnail strip for the top right of an order / PO: every file on it, newest first, with its tag.
const attachmentThumbUrls = {};
async function renderFileStrip(container, entityType, entityId) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  let files = [];
  try { files = await apiFetch(`/api/attachments/?entity_type=${entityType}&entity_id=${entityId}`); await TypeLists.load("attachment"); } catch (e) { if (!files.length) return; }
  files.sort((a, b) => b.id - a.id);
  const me = (AuthGuard.getUser() || {}).username;
  const canDel = f => f.uploaded_by === me || AuthGuard.can("money.view");  // same rule as the server
  el.innerHTML = files.map(f => `<span class="file-thumb-wrap"><a class="file-thumb" title="${escapeHtml(`${attachmentLabel(f.category)}: ${f.filename}`)}" onclick="openAttachment(${f.id})">
      <span class="file-thumb-img" data-thumb="${f.id}">${escapeHtml((f.filename.split(".").pop() || "file").slice(0, 4).toUpperCase())}</span>
      ${attachmentTag(f.category)}</a>${canDel(f) ? `<button type="button" class="file-thumb-del" data-del="${f.id}" title="Delete this file">${icon("trash")}</button>` : ""}</span>`).join("");
  el.querySelectorAll(".file-thumb-del").forEach(b => b.onclick = async ev => {
    ev.stopPropagation();
    const f = files.find(x => x.id === parseInt(b.dataset.del));
    if (await deleteAttachmentAsk(f, entityType)) {
      renderFileStrip(el, entityType, entityId);
      document.dispatchEvent(new CustomEvent("files-changed", { detail: { entityType, entityId, from: el } }));
    }
  });
  if (!el._filesListener) {  // a file deleted / added elsewhere on the screen: redraw the strip
    el._filesListener = e => { if (document.body.contains(el) && e.detail.entityType === entityType && e.detail.entityId === entityId && e.detail.from !== el) renderFileStrip(el, entityType, entityId); };
    document.addEventListener("files-changed", el._filesListener);
  }
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
const attachmentBlobUrls = {}, attachmentTypes = {}, attachmentNames = {};
async function attachmentUrl(id) {
  if (!attachmentBlobUrls[id]) {
    const desk = String(id).startsWith("desk-");  // a file on the AI Desk (not attached to anything yet)
    const r = await fetch(desk ? `/api/ai-desk/files/${String(id).slice(5)}/raw` : `/api/attachments/${id}/file`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
    if (!r.ok) throw new Error(`Could not open the file (${r.status})`);
    const blob = await r.blob();
    attachmentTypes[id] = blob.type || "";
    const cd = r.headers.get("content-disposition") || "";
    attachmentNames[id] = decodeURIComponent((cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i) || [])[1] || `file-${id}`);
    attachmentBlobUrls[id] = URL.createObjectURL(blob);
  }
  return attachmentBlobUrls[id];
}

// Spreadsheets as a table, Word / text / email as text (server: services/doc_text.py); null when there's no preview.
async function attachmentPreview(id) {
  const sid = String(id);
  const url = sid.startsWith("desk-") ? `/api/ai-desk/files/${sid.slice(5)}/preview` : /^\d+$/.test(sid) ? `/api/attachments/${sid}/preview` : null;
  if (!url) return null;
  try { return await apiFetchRaw(url); } catch (e) { return null; }
}
function previewHtml(pv) {
  if (pv.type === "text") return `<pre class="fv-text">${escapeHtml(pv.text || "")}</pre>`;
  return `<div class="fv-sheets">${(pv.sheets || []).map(sh => `<div class="fv-sheet">${(pv.sheets.length > 1) ? `<h4>${escapeHtml(sh.name)}</h4>` : ""}
    <table class="compact-table no-table-tools fv-table"><tbody>${sh.rows.map((r, i) => `<tr>${r.map(c => i === 0 ? `<th>${escapeHtml(c)}</th>` : `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`).join("")}</div>`;
}
// Files open in a floating viewer (not a new tab): it sits over the page without blocking it, so you can check
// the document against what's on screen. Drag it by its bar, resize from the corner (both remembered); images
// zoom (wheel / + -), pan (drag) and rotate; PDFs show inside; ‹ › steps through the other files on the screen.
async function openAttachment(id) {
  const ids = [...new Set([...document.querySelectorAll('[onclick*="openAttachment("]')]
    .map(el => parseInt((el.getAttribute("onclick").match(/openAttachment\((\d+)/) || [])[1])).filter(Boolean))];
  fileViewer.show(id, ids.includes(id) ? ids : [id]);
}
const fileViewer = {
  el: null, ids: [], at: 0, zoom: 1, rot: 0, dx: 0, dy: 0,
  geom() { try { return JSON.parse(localStorage.getItem("file_viewer_geom")) || null; } catch { return null; } },
  saveGeom() {
    const r = this.el.getBoundingClientRect();
    try { localStorage.setItem("file_viewer_geom", JSON.stringify({ x: r.left, y: r.top, w: r.width, h: r.height })); } catch {}
  },
  build() {
    const el = document.createElement("div");
    el.className = "file-viewer";
    el.setAttribute("role", "dialog");
    el.innerHTML = `<div class="fv-bar">
        <button class="fv-btn" data-act="prev" title="Previous file">‹</button><button class="fv-btn" data-act="next" title="Next file">›</button>
        <span class="fv-name"></span>
        <span class="fv-img-tools"><button class="fv-btn" data-act="out" title="Zoom out">−</button><button class="fv-btn" data-act="fit" title="Fit">Fit</button>
          <button class="fv-btn" data-act="in" title="Zoom in">+</button><button class="fv-btn" data-act="rot" title="Rotate">⟳</button></span>
        <button class="fv-btn" data-act="max" title="Enlarge to fill the window (again: back beside the form)">⤢</button>
        <button class="fv-btn" data-act="tab" title="Open in a new tab">↗</button><button class="fv-btn" data-act="dl" title="Download">${icon("download")}</button>
        <button class="fv-btn fv-close" data-act="close" title="Close (Esc)">${icon("x")}</button></div>
      <div class="fv-body"></div>`;
    document.body.appendChild(el);
    const g = this.geom(), vw = window.innerWidth, vh = window.innerHeight;
    const w = Math.min(g ? g.w : Math.round(vw * 0.42), vw - 20), h = Math.min(g ? g.h : Math.round(vh * 0.8), vh - 20);
    Object.assign(el.style, { width: `${w}px`, height: `${h}px`, left: `${Math.max(10, Math.min(g ? g.x : vw - w - 20, vw - w - 10))}px`,
      top: `${Math.max(10, Math.min(g ? g.y : 70, vh - 60))}px` });
    el.querySelector(".fv-bar").addEventListener("click", e => { const b = e.target.closest("[data-act]"); if (b) this.act(b.dataset.act); });
    // drag by the bar
    el.querySelector(".fv-bar").addEventListener("pointerdown", e => {
      if (e.target.closest("button")) return;
      const r = el.getBoundingClientRect(), sx = e.clientX, sy = e.clientY;
      const move = ev => { el.style.left = `${Math.max(0, Math.min(window.innerWidth - 80, r.left + ev.clientX - sx))}px`; el.style.top = `${Math.max(0, Math.min(window.innerHeight - 40, r.top + ev.clientY - sy))}px`; };
      const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); this.saveGeom(); };
      addEventListener("pointermove", move); addEventListener("pointerup", up);
    });
    new ResizeObserver(() => { if (this.el) this.saveGeom(); }).observe(el);
    // images: wheel zoom, drag to pan
    const body = el.querySelector(".fv-body");
    body.addEventListener("wheel", e => { if (!body.querySelector("img")) return; e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
    body.addEventListener("pointerdown", e => {
      const img = body.querySelector("img");
      if (!img) return;
      e.preventDefault();
      const sx = e.clientX - this.dx, sy = e.clientY - this.dy;
      const move = ev => { this.dx = ev.clientX - sx; this.dy = ev.clientY - sy; this.paint(); };
      const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); };
      addEventListener("pointermove", move); addEventListener("pointerup", up);
    });
    document.addEventListener("keydown", this.key = e => {
      if (!this.el) return;
      if (e.key === "Escape") this.act("close");
      if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
      if (e.key === "ArrowRight" && e.altKey) this.act("next");
      if (e.key === "ArrowLeft" && e.altKey) this.act("prev");
    });
    this.el = el;
  },
  async show(id, ids) {
    if (!this.el) this.build();
    this.ids = ids;
    this.at = Math.max(0, ids.indexOf(id));
    this.zoom = 1; this.rot = 0; this.dx = 0; this.dy = 0;
    const body = this.el.querySelector(".fv-body");
    body.innerHTML = `<div class="fv-msg">Loading…</div>`;
    this.el.querySelector('[data-act="prev"]').disabled = this.el.querySelector('[data-act="next"]').disabled = ids.length < 2;
    this.el.querySelector(".fv-name").textContent = ids.length > 1 ? `${this.at + 1} of ${ids.length}` : "";
    try {
      const url = await attachmentUrl(id), type = attachmentTypes[id];
      if (this.ids[this.at] !== id) return;  // moved on meanwhile
      this.el.querySelector(".fv-name").textContent = `${attachmentNames[id]}${ids.length > 1 ? ` · ${this.at + 1} of ${ids.length}` : ""}`;
      this.el.querySelector(".fv-name").title = attachmentNames[id];
      const isImg = type.startsWith("image/"), isPdf = type === "application/pdf" || /\.pdf$/i.test(attachmentNames[id]);
      this.el.querySelector(".fv-img-tools").style.display = isImg ? "" : "none";
      const pv = isImg || isPdf ? null : await attachmentPreview(id);
      if (this.ids[this.at] !== id) return;
      body.innerHTML = isImg ? `<img src="${url}" alt="" draggable="false">`
        : isPdf ? `<iframe src="${url}#toolbar=1&view=FitH" title="${escapeHtml(attachmentNames[id])}"></iframe>`
        : pv ? previewHtml(pv)
        : `<div class="fv-msg">This file type can't be shown here.<br><a class="link" onclick="fileViewer.act('dl')">Download it</a> · <a class="link" onclick="fileViewer.act('tab')">open it in a new tab</a></div>`;
      this.paint();
    } catch (e) { body.innerHTML = `<div class="fv-msg error">${escapeHtml(e.message)}</div>`; }
  },
  paint() {
    const img = this.el && this.el.querySelector(".fv-body img");
    if (img) img.style.transform = `translate(${this.dx}px, ${this.dy}px) rotate(${this.rot}deg) scale(${this.zoom})`;
  },
  zoomBy(f) { this.zoom = Math.max(0.2, Math.min(8, this.zoom * f)); this.paint(); },
  act(a) {
    const id = this.ids[this.at];
    if (a === "close") { this.el.remove(); this.el = null; document.removeEventListener("keydown", this.key); return; }
    if (a === "next" || a === "prev") { if (this.ids.length > 1) this.show(this.ids[(this.at + (a === "next" ? 1 : -1) + this.ids.length) % this.ids.length], this.ids); return; }
    if (a === "max") {
      const el = this.el, big = el.classList.toggle("fv-max");
      if (big) { this.prev = { left: el.style.left, top: el.style.top, width: el.style.width, height: el.style.height };
        Object.assign(el.style, { left: "2vw", top: "3vh", width: "96vw", height: "94vh" }); }
      else if (this.prev) Object.assign(el.style, this.prev);
      return;
    }
    if (a === "in") this.zoomBy(1.25);
    if (a === "out") this.zoomBy(1 / 1.25);
    if (a === "fit") { this.zoom = 1; this.dx = 0; this.dy = 0; this.paint(); }
    if (a === "rot") { this.rot = (this.rot + 90) % 360; this.paint(); }
    if (a === "tab") window.open(attachmentBlobUrls[id], "_blank");
    if (a === "dl") { const l = document.createElement("a"); l.href = attachmentBlobUrls[id]; l.download = attachmentNames[id] || "file"; l.click(); }
  },
};

// Renders an upload box + the file list into `container` (an element or its id).
// categories: which document types this record takes, first one is the default.
async function renderAttachments(container, entityType, entityId, categories, opts = {}) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  try { await TypeLists.load("attachment"); } catch {}
  if (TypeLists.cache.attachment) {  // the record kind's types (Company Settings -> Types & Tags), the caller's order first
    const mine = TypeLists.active("attachment", entityType).map(o => o.key);
    categories = [...categories.filter(c => mine.includes(c)), ...mine.filter(c => !categories.includes(c))];
  }
  const moneyKeys = TypeLists.cache.attachment ? TypeLists.cache.attachment.filter(o => o.money).map(o => o.key) : MONEY_ATTACHMENTS;
  if (hidesMoney()) categories = categories.filter(c => !moneyKeys.includes(c));
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
  const canDelete = f => f.uploaded_by === me.username || AuthGuard.can("money.view");
  const isImage = f => (f.content_type || "").startsWith("image/");
  const shown = [...categories, ...[...new Set(files.map(f => f.category))].filter(c => !categories.includes(c))];  // a hidden type's old files still show
  const groups = shown.map(c => [c, files.filter(f => f.category === c)]).filter(([, list]) => list.length);
  const newOpt = AuthGuard.can("types.manage") ? `<option value="__new__">+ New document type…</option>` : "";
  el.innerHTML = `
    <div class="attach-upload">
      <select id="att-cat-${key}">${categories.map(c => `<option value="${escapeHtml(c)}" ${c === opts.selected ? "selected" : ""}>${escapeHtml(attachmentLabel(c))}</option>`).join("")}${newOpt}</select>
      <input type="file" id="att-files-${key}" multiple ${opts.camera ? `accept="image/*,application/pdf" capture="environment"` : ""}>
      <input type="text" id="att-note-${key}" placeholder="${escapeHtml(opts.notePlaceholder || "Note (Optional)")}">
      <button class="secondary" id="att-btn-${key}">Upload</button>
    </div>
    <div id="att-error-${key}" class="error"></div>
    ${groups.length ? groups.map(([cat, list]) => `
      <div class="attach-group">
        <div class="attach-group-label">${escapeHtml(attachmentLabel(cat))} <span class="muted">(${list.length})</span></div>
        ${list.map(f => `
          <div class="attach-row">
            ${isImage(f) ? `<img class="attach-thumb" data-att="${f.id}" alt="" onclick="openAttachment(${f.id})">` : `<span class="attach-icon">${(f.filename.split(".").pop() || "file").slice(0, 4).toUpperCase()}</span>`}
            <div class="attach-info">
              ${attachmentTag(f.category)} <a class="link" onclick="openAttachment(${f.id})">${escapeHtml(f.filename)}</a>
              ${canDelete(f) ? `<select class="att-retag" data-retag="${f.id}" title="What kind of document this is">${(categories.includes(f.category) ? categories : [f.category, ...categories]).map(c => `<option value="${escapeHtml(c)}" ${c === f.category ? "selected" : ""}>${escapeHtml(attachmentLabel(c))}</option>`).join("")}${newOpt}</select>` : ""}
              <div class="muted small">${fmtFileSize(f.size)} · ${escapeHtml(f.uploaded_by || "")} · ${fmtWhen(f.created_at)}${f.note ? ` · <span style="color:#1a1a1a;">${escapeHtml(f.note)}</span>` : ""}</div>
            </div>
            ${canDelete(f) ? `<a class="link small" data-del="${f.id}">Delete</a>` : ""}
          </div>`).join("")}
      </div>`).join("") : `<p class="muted small" style="margin:6px 0 0;">No Files Yet.</p>`}
  `;
  el.querySelectorAll("img[data-att]").forEach(async img => {
    try { img.src = await attachmentUrl(img.dataset.att); } catch (e) { img.remove(); }
  });
  const errorEl = document.getElementById(`att-error-${key}`);
  const catSel = document.getElementById(`att-cat-${key}`);
  let catPrev = catSel.value;
  catSel.onchange = async () => {
    if (catSel.value !== "__new__") { catPrev = catSel.value; return; }
    catSel.value = catPrev;
    const made = await TypeLists.openAdd("attachment", { scope: entityType });
    if (made) renderAttachments(el, entityType, entityId, categories, { ...opts, selected: made.key });
  };
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
    if (sel.value === "__new__") {
      const made = await TypeLists.openAdd("attachment", { scope: entityType });
      if (!made) { renderAttachments(el, entityType, entityId, categories, opts); return; }
      sel.innerHTML += `<option value="${escapeHtml(made.key)}">${escapeHtml(made.label)}</option>`;
      sel.value = made.key;
    }
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
  if (el._filesListener) document.removeEventListener("files-changed", el._filesListener);
  el._filesListener = e => {  // deleted from the thumbnail strip at the top: redraw this list too
    if (document.body.contains(el) && e.detail.entityType === entityType && e.detail.entityId === entityId && e.detail.from !== el) {
      renderAttachments(el, entityType, entityId, categories, Object.assign({}, opts, { onChange: null }));
      if (opts.onChange) opts.onChange();
    }
  };
  document.addEventListener("files-changed", el._filesListener);
}
// The trash on a file thumbnail: say exactly what goes (and what stays), then delete -- Undo puts it back.
async function deleteAttachmentAsk(f, entityType) {
  if (!f) return false;
  const what = { customer_order: "this order", purchase_order: "this PO", shipment: "this shipment", invoice: "this invoice", quote: "this quote" }[entityType] || "this record";
  const extra = f.category === "mtr" ? "<p>Its links to the PO's lines (MTR Library) go with it.</p>"
    : f.category === "vendor_invoice" ? "<p>The vendor invoice entry (number, amount, payments) stays — only the file goes.</p>"
    : f.category === "pod" ? "<p>The shipment stays delivered — only the file goes.</p>" : "";
  const { value } = await askDialog({ title: "Delete This File?", tone: "warn",
    body: `<p style="margin-top:0;"><strong>${escapeHtml(f.filename)}</strong> (${escapeHtml(attachmentLabel(f.category))}) comes off ${what}.</p>${extra}
      <p class="muted small">It goes to the Recycle Bin — Undo (or Ctrl+Z) brings it back.</p>`,
    buttons: [{ label: "Delete File", value: "del", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value !== "del") return false;
  try {
    await apiFetch(`/api/attachments/${f.id}`, { method: "DELETE" });
    undoableDelete(`Deleted ${f.filename}`, async () => document.dispatchEvent(new CustomEvent("files-changed", { detail: { entityType, entityId: f.entity_id, from: null } })));
    return true;
  } catch (e) { alert(e.message); return false; }
}

// ---- Types & tags people can add to (app/services/type_lists.py): document types, S&H types, landed cost types,
// payment methods. TypeLists.load(list) caches a list; TypeLists.openAdd(list) is the "+ New type" pop-up (also the
// shortcut from an attachment / S&H / payment dropdown); TypeLists.renderManager(el) is Company Settings -> Types & Tags.
const TypeLists = {
  cache: {},  // list -> [{id, key, label, scopes, money, builtin, active}]
  async load(list, force = false) {
    if (force || !this.cache[list]) this.cache[list] = (await apiFetch(`/api/types/?list=${list}&all=true`)).options;
    return this.cache[list];
  },
  // active entries for a record kind (document types), in list order
  active(list, scope = null) {
    return (this.cache[list] || []).filter(o => o.active && (!scope || !o.scopes || o.scopes.includes(scope)));
  },
  label(list, key) {
    const o = (this.cache[list] || []).find(x => x.key === key);
    return o ? o.label : key ? String(key).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "";
  },
  // <option>s for a dropdown: the active entries (+ the record's current one even if hidden since), "+ New ..." last
  optionsHtml(list, scope, selected, { blank = null, addNew = true } = {}) {
    const opts = this.active(list, scope);
    if (selected && !opts.some(o => o.key === selected)) opts.push({ key: selected, label: this.label(list, selected) });
    return (blank != null ? `<option value="">${escapeHtml(blank)}</option>` : "")
      + opts.map(o => `<option value="${escapeHtml(o.key)}" ${o.key === selected ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")
      + (addNew && AuthGuard.can("types.manage") ? `<option value="__new__">+ New ${escapeHtml(this.noun(list))}…</option>` : "");
  },
  noun(list) { return { attachment: "document type", charge: "S&H type", landed_cost: "landed cost type", payment_method: "payment method" }[list] || "type"; },
  // The add pop-up: the name as it will be saved, what's already there that looks like it ("use that"), and for
  // document types which records it's for and whether it shows prices. Resolves to the type to use (new or existing).
  openAdd(list, { scope = null, label = "" } = {}) {
    return new Promise(async resolve => {
      await this.load(list);
      const scopes = { customer_order: "Customer orders", purchase_order: "Purchase orders", shipment: "Shipments" };
      const back = document.createElement("div");
      back.className = "modal-backdrop" + (document.body.classList.contains("glass-open") ? " over-glass" : "");
      back.innerHTML = `<div class="modal type-add" role="dialog" aria-modal="true">
        <h3 style="margin:0 0 4px;">New ${escapeHtml(this.noun(list))}</h3>
        <p class="muted small" style="margin:0 0 10px;">Letters and spaces only; saved in Title Case so the list stays tidy.</p>
        <label>Name<input type="text" id="ta-label" maxlength="60" value="${escapeHtml(label)}" placeholder="${list === "attachment" ? "E.g. Vendor Packing List" : list === "charge" ? "E.g. Fuel Surcharge" : list === "payment_method" ? "E.g. Zelle" : "E.g. Drayage"}" autocomplete="off"></label>
        <div class="ta-preview muted small" id="ta-preview"></div>
        <div id="ta-similar"></div>
        ${list === "attachment" ? `<div class="ta-scopes"><span class="small muted">Used on</span>${Object.entries(scopes).map(([k, v]) =>
          `<label class="inline-check"><input type="checkbox" class="ta-scope" value="${k}" ${!scope || scope === k ? "checked" : ""}> ${v}</label>`).join("")}</div>
          <label class="inline-check" title="Like customer POs and vendor invoices: people without 'See prices' can't see or upload these"><input type="checkbox" id="ta-money"> Shows prices (managers only)</label>` : ""}
        <div class="error" id="ta-error"></div>
        <div class="btn-row" style="margin-top:12px;"><button type="button" class="confirm-btn" id="ta-save">Create</button>
          <button type="button" class="secondary" data-close="1">Cancel</button>
          ${AuthGuard.can("company") ? `<a class="link small" href="company.html#types" style="margin-left:auto;">All types &amp; tags →</a>` : ""}</div></div>`;
      const $ = s => back.querySelector(s);
      const done = v => { document.removeEventListener("keydown", onKey, true); back.remove(); resolve(v); };
      const onKey = e => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
      let force = false, timer = null;
      const check = async () => {
        const text = $("#ta-label").value;
        force = false;
        $("#ta-save").textContent = "Create";
        if (text.trim().length < 2) { $("#ta-preview").textContent = ""; $("#ta-similar").innerHTML = ""; return; }
        try {
          const r = await apiFetch(`/api/types/check?list=${list}&label=${encodeURIComponent(text)}`);
          $("#ta-preview").innerHTML = `Saved as <b>${escapeHtml(r.label)}</b>`;
          $("#ta-similar").innerHTML = r.similar.length ? `<div class="ta-similar">${icon("info")}<div><b>Similar ${r.similar.length === 1 ? "type" : "types"} already there</b>
            <div class="ta-sim-list">${r.similar.map(s => `<button type="button" class="small-btn secondary" data-use="${s.id}">Use ${escapeHtml(s.label)}${s.active ? "" : " (hidden)"}</button>`).join("")}</div>
            <div class="muted small">Or create <b>${escapeHtml(r.label)}</b> anyway if it really is different.</div></div></div>` : "";
          if (r.similar.length) $("#ta-save").textContent = "Create Anyway";
          force = r.similar.length > 0;
          decorateIcons(back);
        } catch {}
      };
      $("#ta-label").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 250); });
      back.addEventListener("click", async e => {
        if (e.target === back || e.target.closest("[data-close]")) return done(null);
        const use = e.target.closest("[data-use]");
        if (use) {
          const o = this.cache[list].find(x => x.id === +use.dataset.use) || (await this.load(list, true)).find(x => x.id === +use.dataset.use);
          if (o && !o.active) await apiFetch(`/api/types/${o.id}`, { method: "PUT", body: JSON.stringify({ active: true }) });
          if (o && list === "attachment" && scope && o.scopes && !o.scopes.includes(scope))
            await apiFetch(`/api/types/${o.id}`, { method: "PUT", body: JSON.stringify({ scopes: [...o.scopes, scope] }) });
          await this.load(list, true);
          toast(`Using ${o.label}`);
          return done(this.cache[list].find(x => x.id === o.id));
        }
      });
      $("#ta-save").onclick = async () => {
        $("#ta-error").textContent = "";
        const body = { list, label: $("#ta-label").value, force };
        if (list === "attachment") {
          body.scopes = [...back.querySelectorAll(".ta-scope:checked")].map(x => x.value);
          body.money = $("#ta-money").checked;
        }
        try {
          const made = await apiFetch("/api/types/", { method: "POST", body: JSON.stringify(body) });
          await this.load(list, true);
          toast(`Added ${made.label}`);
          done(made);
        } catch (err) {
          if (err.status === 409 || /similar|already/i.test(err.message)) { await check(); }
          $("#ta-error").textContent = err.detail && err.detail.message ? err.detail.message : err.message;
        }
      };
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(back);
      decorateIcons(back);
      $("#ta-label").focus();
      if (label) check();
    });
  },
  // Company Settings -> Types & Tags: every list, rename / show-hide / scopes inline
  async renderManager(el) {
    const all = await apiFetch("/api/types/?all=true&usage=true");
    Object.entries(all).forEach(([k, v]) => { this.cache[k] = v.options; });
    const can = AuthGuard.can("types.manage");
    const scopes = { customer_order: "Orders", purchase_order: "POs", shipment: "Shipments" };
    el.innerHTML = Object.entries(all).map(([list, v]) => `<div class="types-list" data-list="${list}">
      <div class="types-head"><h4>${escapeHtml(v.title)}</h4><span class="muted small">${escapeHtml(v.help)}</span>
        ${can ? `<button type="button" class="small-btn secondary" data-add="${list}">+ New ${escapeHtml(this.noun(list))}</button>` : ""}</div>
      <table class="fit-table no-table-tools types-table"><thead><tr><th class="grow">Name</th>${list === "attachment" ? "<th>Used on</th><th>Prices</th>" : ""}<th class="num">Used</th><th>Shown</th></tr></thead><tbody>
      ${v.options.map(o => `<tr class="${o.active ? "" : "muted"}">
        <td class="grow">${can ? `<input type="text" class="type-rename" data-id="${o.id}" value="${escapeHtml(o.label)}" data-orig="${escapeHtml(o.label)}">` : escapeHtml(o.label)}
          ${o.builtin ? `<span class="tag draft" title="Comes with AT-HUB -- it can be renamed or hidden, not deleted">built-in</span>` : `<span class="muted small">added by ${escapeHtml(o.created_by || "")}</span>`}</td>
        ${list === "attachment" ? `<td class="nowrap">${Object.entries(scopes).map(([k, n]) => `<label class="inline-check small"><input type="checkbox" class="type-scope" data-id="${o.id}" value="${k}" ${(o.scopes || []).includes(k) ? "checked" : ""} ${can ? "" : "disabled"}> ${n}</label>`).join("")}</td>
          <td>${o.money ? `<span class="tag overdue" title="People without 'See prices' can't see these files">prices</span>` : `<span class="muted small">—</span>`}</td>` : ""}
        <td class="num">${o.used || 0}</td>
        <td>${o.key === "other" ? `<span class="muted small">always</span>` : `<label class="inline-check"><input type="checkbox" class="type-active" data-id="${o.id}" ${o.active ? "checked" : ""} ${can ? "" : "disabled"}> ${o.active ? "Offered" : "Hidden"}</label>`}</td></tr>`).join("")}
      </tbody></table></div>`).join("");
    const save = async (id, body) => {
      try { await apiFetch(`/api/types/${id}`, { method: "PUT", body: JSON.stringify(body) }); toast("Saved"); }
      catch (err) {
        if (err.detail && err.detail.code === "similar") {
          const { value } = await askDialog({ title: "Similar type already there", tone: "warn", body: `<p>${escapeHtml(err.detail.message)}. Rename it to <b>${escapeHtml(err.detail.label)}</b> anyway?</p>`,
            buttons: [{ label: "Rename Anyway", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
          if (value === "go") { await apiFetch(`/api/types/${id}`, { method: "PUT", body: JSON.stringify({ ...body, force: true }) }); toast("Saved"); }
        } else toast(err.detail && err.detail.message ? err.detail.message : err.message);
      }
      this.renderManager(el);
    };
    el.querySelectorAll(".type-rename").forEach(inp => inp.addEventListener("change", () => { if (inp.value.trim() && inp.value !== inp.dataset.orig) save(+inp.dataset.id, { label: inp.value }); }));
    el.querySelectorAll(".type-active").forEach(cb => cb.addEventListener("change", () => save(+cb.dataset.id, { active: cb.checked })));
    el.querySelectorAll(".type-scope").forEach(cb => cb.addEventListener("change", () =>
      save(+cb.dataset.id, { scopes: [...el.querySelectorAll(`.type-scope[data-id="${cb.dataset.id}"]:checked`)].map(x => x.value) })));
    el.querySelectorAll("[data-add]").forEach(b => b.onclick = async () => { if (await this.openAdd(b.dataset.add)) this.renderManager(el); });
  },
};

// Every password box gets an eye button: show what you typed / hide it again (now and in boxes added later).
function addPasswordEye(input) {
  if (input.dataset.eye) return;
  input.dataset.eye = "1";
  const wrap = document.createElement("span");
  wrap.className = "pw-wrap";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pw-eye";
  btn.tabIndex = -1;  // Tab goes from the box to the next field, not to the eye
  const show = on => {
    input.type = on ? "text" : "password";
    btn.innerHTML = icon(on ? "eyeOff" : "eye");
    btn.title = on ? "Hide password" : "Show password";
    btn.setAttribute("aria-label", btn.title);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  };
  btn.addEventListener("mousedown", e => e.preventDefault());  // keep the cursor in the box
  btn.addEventListener("click", () => { show(input.type === "password"); input.focus(); });
  wrap.appendChild(btn);
  show(false);
}
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('input[type="password"]').forEach(addPasswordEye);
  new MutationObserver(muts => muts.forEach(m => m.addedNodes.forEach(n => {
    if (n.nodeType !== 1) return;
    if (n.matches('input[type="password"]')) addPasswordEye(n);
    n.querySelectorAll && n.querySelectorAll('input[type="password"]').forEach(addPasswordEye);
  }))).observe(document.body, { childList: true, subtree: true });
});

// Any <select data-type-list="charge|payment_method|attachment|landed_cost" [data-type-scope]> built with
// TypeLists.optionsHtml(): its "+ New ..." entry opens the pop-up and then selects what was made (page code sees a
// normal change to the new value; Cancel puts the old one back).
document.addEventListener("focusin", e => {
  const s = e.target;
  if (s.matches && s.matches("select[data-type-list]") && s.value !== "__new__") s.dataset.prev = s.value;
});
document.addEventListener("change", async e => {
  const s = e.target;
  if (!s.matches || !s.matches("select[data-type-list]") || s.value !== "__new__") return;
  e.stopImmediatePropagation();
  s.value = s.dataset.prev || "";
  const list = s.dataset.typeList, scope = s.dataset.typeScope || null;
  const made = await TypeLists.openAdd(list, { scope });
  if (!made) return;
  const blank = s.querySelector('option[value=""]');
  s.innerHTML = TypeLists.optionsHtml(list, scope, made.key, { blank: blank ? blank.textContent : null });
  s.value = s.dataset.prev = made.key;
  s.dispatchEvent(new Event("change", { bubbles: true }));
}, true);

// ---- Quick capture: a PO document now, the details later. kind "order" (a customer's PO -> customer order) or "po"
// (a vendor's quote / confirmation -> our purchase order). Who it's from + the file; it waits as "Validation needed". ----
async function quickCapture(kind) {
  const isOrder = kind === "order";
  const parties = await apiFetch(isOrder ? "/api/customers/" : "/api/vendors/");
  if (!isOrder) await TypeLists.load("attachment");
  const active = parties.filter(p => p.is_active !== false).sort((a, b) => a.name.localeCompare(b.name));
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-backdrop";
    back.innerHTML = `<div class="modal capture-modal" role="dialog" aria-modal="true">
      <h3 style="margin:0 0 2px;">${icon("upload")} Quick Capture ${isOrder ? "Customer PO" : "Vendor Document"}</h3>
      <p class="muted small" style="margin:0 0 12px;">Keep the document now, fill it in later. It's saved as <span class="tag validation">Validation Needed</span> —
        ${isOrder ? "it can't be confirmed, booked or shipped" : "it can't be ordered, emailed or received"} until someone checks it and presses <b>Validate</b>.</p>
      <label>${isOrder ? "Customer" : "Vendor"}<select id="qc-party" data-searchable><option value="">— pick ${isOrder ? "the customer" : "the vendor"} —</option>
        ${active.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("")}</select></label>
      <div class="qc-row"><label><span class="lbl">${isOrder ? "Customer PO #" : "Vendor ref / SO #"} <span class="muted small">(optional)</span></span><input type="text" id="qc-ref" autocomplete="off"></label>
        ${isOrder ? "" : `<label>Document type<select id="qc-cat" data-type-list="attachment" data-type-scope="purchase_order">${TypeLists.optionsHtml("attachment", "purchase_order", "vendor_quote")}</select></label>`}</div>
      <label class="qc-drop" id="qc-drop">${icon("upload")}<span id="qc-file-name">Drop the PDF / photo here, or click to choose</span>
        <input type="file" id="qc-files" multiple accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.heif,.xlsx,.xls,.csv,.doc,.docx,.txt,.eml,.msg"></label>
      <label><span class="lbl">Note <span class="muted small">(optional)</span></span><input type="text" id="qc-note" placeholder="E.g. Rush — call Mike about pricing"></label>
      <div class="error" id="qc-error"></div>
      <div class="btn-row" style="margin-top:12px;"><button type="button" class="capture-btn" id="qc-save">${icon("upload")} Capture</button>
        <button type="button" class="secondary" data-close="1">Cancel</button></div></div>`;
    const $ = s => back.querySelector(s);
    const done = v => { document.removeEventListener("keydown", onKey, true); back.remove(); resolve(v); };
    const onKey = e => { if (e.key === "Escape" && !document.querySelector(".type-add")) { e.stopPropagation(); done(null); } };
    back.addEventListener("click", e => { if (e.target === back || e.target.closest("[data-close]")) done(null); });
    const showFiles = () => { const f = $("#qc-files").files; $("#qc-file-name").textContent = f.length ? [...f].map(x => x.name).join(", ") : "Drop the PDF / photo here, or click to choose"; $("#qc-drop").classList.toggle("has", f.length > 0); };
    $("#qc-files").addEventListener("change", showFiles);
    ["dragover", "dragenter"].forEach(ev => $("#qc-drop").addEventListener(ev, e => { e.preventDefault(); $("#qc-drop").classList.add("over"); }));
    ["dragleave", "drop"].forEach(ev => $("#qc-drop").addEventListener(ev, () => $("#qc-drop").classList.remove("over")));
    $("#qc-drop").addEventListener("drop", e => { e.preventDefault(); $("#qc-files").files = e.dataTransfer.files; showFiles(); });
    $("#qc-save").onclick = async () => {
      const err = $("#qc-error"), btn = $("#qc-save");
      err.textContent = "";
      if (!$("#qc-party").value) { err.textContent = `Pick the ${isOrder ? "customer" : "vendor"}.`; return; }
      if (!$("#qc-files").files.length) { err.textContent = "Add the document."; return; }
      const form = new FormData();
      form.append(isOrder ? "customer_id" : "vendor_id", $("#qc-party").value);
      if ($("#qc-ref").value.trim()) form.append(isOrder ? "po_number" : "vendor_so_number", $("#qc-ref").value.trim());
      if (!isOrder) form.append("category", $("#qc-cat").value);
      if ($("#qc-note").value.trim()) form.append("note", $("#qc-note").value.trim());
      [...$("#qc-files").files].forEach(f => form.append("files", f));
      btn.disabled = true;
      try {
        const r = await fetch(isOrder ? "/api/customer-orders/capture" : "/api/purchase-orders/capture",
                              { method: "POST", headers: { Authorization: `Bearer ${AuthGuard.getToken()}` }, body: form });
        const data = await r.json();
        if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Capture failed");
        toast(`${data.code} captured — validate it when you have a minute`);
        done(data);
      } catch (e) { err.textContent = e.message; btn.disabled = false; }
    };
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(back);
    decorateIcons(back);
    $("#qc-party").focus();
  });
}
// The banner on a captured record: what it is, open the document, Validate (and the one-step Validate & ...)
function validationBannerHtml(kind, rec, canEdit) {
  if (rec.status !== "validation") return "";
  const isOrder = kind === "order", noLines = !(rec.lines || []).length, waiting = rec.ai_pending_lines || [];
  const blocked = noLines || waiting.length;
  const why = waiting.length ? `title='Every line from the ${isOrder ? "PO" : "document"} needs its item first'` : noLines ? "title='Add the lines first'" : "";
  const all = typeof items !== "undefined" && Array.isArray(items) ? items : [];
  return `<div class="validation-banner ${rec.ai_source ? "ai-read" : ""}">${icon(rec.ai_source ? "sparkles" : "fileCheck")}<div>
      ${rec.ai_source
        ? `<strong>AI read — check it, then Validate.</strong> Made from <a class="link" onclick="openCapturedDoc('${isOrder ? "customer_order" : "purchase_order"}', ${rec.id})">${escapeHtml(rec.ai_source)}</a>
           ${rec.created_by ? `by ${escapeHtml(rec.created_by)} ` : ""}${fmtDate(rec.created_at)}. Compare every line with the ${isOrder ? "customer's PO" : "vendor's document"}${waiting.length ? `, and give the ${waiting.length} line${waiting.length === 1 ? "" : "s"} below their item` : ""}.`
        : `<strong>Validation needed — quick-captured${rec.created_by ? ` by ${escapeHtml(rec.created_by)}` : ""} ${fmtDate(rec.created_at)}.</strong>
           Check it against the ${isOrder ? "customer's PO" : "vendor's document"}${noLines ? ", add the lines" : ""}, then Validate.
           <a class="link" onclick="openCapturedDoc('${isOrder ? "customer_order" : "purchase_order"}', ${rec.id})">Open the document</a>`}
      ${isOrder ? "It can't be confirmed, booked or shipped before that." : "It can't be ordered, emailed or received before that."}
      ${waiting.length ? `<table class="compact-table no-table-tools ai-wait-table"><thead><tr><th class="grow">The ${isOrder ? "PO" : "Document"} Says</th><th class="num">Qty</th><th class="num">Price</th><th>Our Item</th><th></th></tr></thead><tbody>
        ${waiting.map((w, n) => `<tr><td class="grow"><strong>${escapeHtml(w.item_code || "")}</strong> <span class="small">${escapeHtml(w.description || "")}</span>
            ${w.new_item ? `<div class="small muted">Not in Stock Items yet${w.new_item.looks_like ? ` — closest we have: ${escapeHtml(w.new_item.looks_like.code)}` : ""}</div>` : ""}</td>
          <td class="num">${fmtQty(w.quantity)}</td><td class="num">${fmtPrice(w.unit_price || 0)}</td>
          <td>${canEdit ? `<select class="ai-wait-pick" data-n="${n}" data-searchable style="min-width:220px;"><option value="">— pick our item —</option>${all.map(i =>
              `<option value="${i.id}" ${(w.candidates || [])[0] && w.candidates[0].item_id === i.id && w.candidates[0].score >= 0.85 ? "selected" : ""}>${escapeHtml(i.code)} — ${escapeHtml(i.title)}</option>`).join("")}</select>` : ""}</td>
          <td class="nowrap">${canEdit ? `<button class="small-btn" onclick="aiWaitUse('${kind}', ${rec.id}, ${n}, this)">Use</button>
            ${AuthGuard.can("stock.edit") ? `<button class="small-btn secondary" onclick="aiWaitQuick('${kind}', ${rec.id}, ${n})">Quick Add</button>` : ""}
            <a class="link small neg" onclick="aiWaitDrop('${kind}', ${rec.id}, ${n})">Drop</a>` : ""}</td></tr>`).join("")}</tbody></table>` : ""}</div>
    ${canEdit ? `<div class="vb-actions"><button class="small-btn secondary" onclick="validateCaptured('${kind}', ${rec.id}, false)" ${blocked ? `disabled ${why}` : ""}>Validate</button>
      <button class="small-btn capture-btn" onclick="validateCaptured('${kind}', ${rec.id}, true)" ${blocked ? `disabled ${why}` : ""}>Validate &amp; ${isOrder ? "Confirm" : "Mark Ordered"}</button></div>` : ""}</div>`;
}
// The waiting lines of an AI read: give one its item (picked, or added with Quick Add), or drop it.
async function aiWaitSend(kind, id, n, body, path = "match") {
  try {
    await apiFetch(`/api/${kind === "order" ? "customer-orders" : "purchase-orders"}/${id}/ai-pending/${n}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
    if (typeof showDetail === "function") await showDetail(id);
  } catch (e) { toast(e.message); }
}
function aiWaitUse(kind, id, n, btn) {
  const sel = btn.closest("tr").querySelector(".ai-wait-pick");
  if (!sel || !sel.value) return toast("Pick our item for this line first");
  aiWaitSend(kind, id, n, { item_id: parseInt(sel.value) });
}
async function aiWaitQuick(kind, id, n) {
  const rec = await apiFetch(`/api/${kind === "order" ? "customer-orders" : "purchase-orders"}/${id}`);
  const w = (rec.ai_pending_lines || [])[n];
  if (!w) return;
  let groups = [];
  try { groups = (await apiFetch("/api/stock-items/groups/list")).map(g => g.name); } catch {}
  const all = typeof items !== "undefined" && Array.isArray(items) ? items : [];
  const got = await quickAddItem({ side: kind === "order" ? "customer" : "vendor", heading: "Add This Line's Item",
    said: [w.item_code, w.description, fmtPrice(w.unit_price || 0)].filter(Boolean).join(" · "),
    code: (w.new_item && w.new_item.code) || (kind === "order" ? w.item_code || "" : ""), title: (w.new_item && w.new_item.title) || w.description || "",
    category: w.new_item && w.new_item.category, price: w.unit_price || 0, groups, items: all, candidates: w.candidates,
    looksLike: w.new_item && w.new_item.looks_like });
  if (!got) return;
  if (!all.some(i => i.id === got.item.id)) all.push(got.item);
  aiWaitSend(kind, id, n, { item_id: got.item.id });
}
function aiWaitDrop(kind, id, n) {
  if (confirm("Drop this line? It won't be on the record (the document still shows it).")) aiWaitSend(kind, id, n, null, "discard");
}
async function openCapturedDoc(entityType, id) {
  const files = await apiFetch(`/api/attachments/?entity_type=${entityType}&entity_id=${id}`);
  const f = files.slice().reverse()[0];
  if (f) openAttachment(f.id); else toast("No document on it");
}
async function validateCaptured(kind, id, andGo) {
  const isOrder = kind === "order";
  try {
    const r = await apiFetch(`/api/${isOrder ? "customer-orders" : "purchase-orders"}/${id}/validate`, { method: "POST",
      body: JSON.stringify(isOrder ? { confirm: andGo } : { ordered: andGo }) });
    toast(`${r.code} validated — ${r.status}`);
    if (typeof onValidated === "function") onValidated(r);
  } catch (e) {
    const dup = /^DUPLICATE_PO\|/.test(e.message) ? e.message.split("|") : null;
    toast(dup ? dup[3] + " — fix the PO # (or OK the duplicate on the order) first" : e.message);
  }
}

// Small grey product-group chip shown next to item codes.
function groupTag(item) {
  return (item && item.category ? `<span class="group-tag">${escapeHtml(item.category)}</span>` : "") + aiMadeTag(item);
}
// Items created from a scanned PO carry a small tag until someone has checked them.
function aiMadeTag(item) {
  return item && item.created_via === "ai-scan"
    ? ` <span class="ai-made-tag" title="Created from a scanned customer PO${item.created_at ? " on " + fmtDate(item.created_at) : ""} — check the title, group and price">AI</span>` : "";
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

// Pallet # label (On-Demand Labels > Pallet #): "PALLET", the number huge, "OF 5", and PO # / job # along the bottom.
// p: { number, total, po, job, customer }
function palletNumberLabelHtml(p, company, base = "") {
  const digits = String(p.number || "").length;
  const size = digits <= 2 ? 210 : digits === 3 ? 160 : 110;
  const ref = [p.po && `PO # ${escapeHtml(p.po)}`, p.job && `JOB # ${escapeHtml(p.job)}`].filter(Boolean).join("&nbsp;&nbsp;·&nbsp;&nbsp;");
  return `
    <div class="label-card pallet-num-card">
      ${p.customer ? `<div class="pn-customer">${escapeHtml(p.customer)}</div>` : ""}
      <div class="pn-word">PALLET</div>
      <div class="pn-number" style="font-size:${size}px">${escapeHtml(p.number || "")}</div>
      ${p.total ? `<div class="pn-of">OF ${escapeHtml(p.total)}</div>` : ""}
      ${ref ? `<div class="pn-ref">${ref}</div>` : ""}
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

// labels: [{ customer, customer_id, shipment, order, po, job, item_code, item_title, qty, lot, pallet, ship_to }]
// With a box-label template set as the default (Template Designer) the server draws them as a PDF -- a customer's
// own template first; otherwise the built-in label below.
let templateDefaults = null;
// Labels drawn by the server with the default designed template; false = none applies (print the built-in one).
async function printWithTemplate(docType, labels, win) {
  try {
    templateDefaults = templateDefaults || await apiFetch("/api/templates/defaults");
    if (!templateDefaults[docType]) return false;
    const custs = [...new Set(labels.map(l => l.customer_id).filter(Boolean))];
    const res = await fetch(`${API_BASE}/api/templates/render-labels?as_link=1`, { method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${AuthGuard.getToken()}` },
      body: JSON.stringify({ doc_type: docType, labels, customer_id: custs.length === 1 ? custs[0] : null }) });
    if (!res.ok) return false;
    const url = await pdfUrl(res);  // a real link, so saving keeps the file name (SH...-PO-Labels.pdf)
    if (win) win.location.href = url; else window.open(url, "_blank");
    return true;
  } catch (e) { return false; }
}
async function printBoxLabels(labels, title) {
  const win = window.open("", "_blank");
  if (win) win.document.write("<p style='font-family:sans-serif;padding:20px;color:#555'>Preparing labels…</p>");
  const counts = {}, seen = {};  // "Box 3 of 12" within each shipment
  labels.forEach(l => { counts[l.shipment] = (counts[l.shipment] || 0) + 1; });
  labels = labels.map(l => ({ ...l, box: l.box ?? (seen[l.shipment] = (seen[l.shipment] || 0) + 1), boxes: l.boxes ?? counts[l.shipment] }));
  if (await printWithTemplate("box_label", labels, win)) return;
  const company = await labelCompany();
  printLabelCards(labels.map(l => boxLabelHtml(l, company, location.origin)).join(""), title, win);
}

// A PDF asked for with ?as_link=1 comes back as {url} -- a short-lived real link whose file name is the document's
// (SH215771-M219-30B-4156926-Packing List.pdf), so the browser's PDF viewer saves it under that name.
async function pdfUrl(response) {
  if ((response.headers.get("content-type") || "").includes("application/json")) return API_BASE + (await response.json()).url;
  return URL.createObjectURL(await response.blob());
}
// <record #>-<customer PO #>-<what>: the name a document is saved under (same as the server's filenames.py)
function docFileName(code, po, what) {
  const clean = v => String(v || "").replace(/[\\/:*?"<>|\r\n\t]+/g, "-").replace(/^[\s.-]+|[\s.-]+$/g, "");
  return [clean(code), clean(po)].filter(Boolean).concat(what).join("-");
}

// Change a record's # (shipment / invoice): asks for the new one, saves it with PUT <path>. Resolves to the saved record or null.
async function renameRecord(what, current, path) {
  const { value, el } = await askDialog({ title: `Change ${what} #`,
    body: `<label>New ${escapeHtml(what)} #</label><input type="text" id="rename-code" value="${escapeHtml(current)}" maxlength="40" style="width:100%;">
      <p class="muted small" style="margin:6px 0 0;">Letters, digits and - _ . only. It prints on the documents from now on; ones already sent keep the old #.</p>
      <div class="error" id="rename-error"></div>`,
    buttons: [{ label: "Save", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value !== "go") return null;
  const code = el.querySelector("#rename-code").value.trim();
  if (!code || code === current) return null;
  try {
    const saved = await apiFetch(path, { method: "PUT", body: JSON.stringify({ code }) });
    toast(`${current} is now ${saved.code}`);
    return saved;
  } catch (e) { alert(e.message); return null; }
}

// Opens a server-rendered PDF (needs the auth header, so it's fetched first).
// The tab is opened synchronously so pop-up blockers allow it.
async function openPdf(path) {
  const win = window.open("", "_blank");
  if (!win) {
    alert("The browser blocked the new tab — allow pop-ups for this site and try again.");
    return;
  }
  win.document.write("<p style='font-family:sans-serif;padding:20px;color:#555'>Preparing PDF…</p>");
  try {
    const response = await fetch(`${API_BASE}${path}${path.includes("?") ? "&" : "?"}as_link=1`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
    if (!response.ok) throw new Error(`Could not generate the PDF (${response.status})`);
    win.location.href = await pdfUrl(response);
  } catch (err) {
    win.document.body.innerHTML = `<p style='font-family:sans-serif;padding:20px;color:#dc2626'>${escapeHtml(err.message)}</p>`;
  }
}

// Downloads a server-made file (e.g. a ZIP of one PDF per shipment) -- fetched with the auth header, saved under `filename`.
async function downloadFile(path, filename) {
  const response = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
  if (!response.ok) {
    let detail = `Download failed (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  const url = URL.createObjectURL(await response.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

// Grouped like MRPeasy's own sidebar: modules are organized under the
// business function they belong to (CRM, Procurement, Warehouse), not a
// flat list of pages.
// A link's third entry is the permission that shows it (any of several, space separated) -- set per role on
// Users & Roles. A group with no links left isn't shown.
const NAV_GROUPS = [
  { label: null, links: [["dashboard.html", "Dashboard", "orders.view stock.view invoices purchasing"], ["todo.html", "To-Do", ""], ["notes.html", "Sticky Notes", "orders.view purchasing shipments.view"], ["tasks.html", "Tasks", "tasks"], ["ai-desk.html", "AI Desk", "ai"]] },
  { label: "CRM", links: [
    ["customers.html", "Customers", "customers.view"],
    ["customer-orders.html", "Customer Orders", "orders.view"],
    ["reconcile.html", "Open Lines Check", "reconcile"],
    ["shipments.html", "Shipments", "shipments.view"],
    ["pack-shipments.html", "Bulk Operations", "shipments.work invoices"],
    ["pod.html", "Proof Of Delivery", "pod.upload"],
    ["labels.html", "On-Demand Labels", "shipments.work"],
    ["invoices.html", "Invoices", "invoices"],
  ] },
  { label: "Procurement", links: [
    ["vendors.html", "Vendors", "vendors"],
    ["purchase-orders.html", "Purchase Orders", "purchasing"],
    ["landed-costs.html", "Landed Costs", "landed_costs"],
  ] },
  { label: "Warehouse", links: [["stock-items.html", "Stock Items", "stock.view"], ["lots.html", "Lots", "stock.view"], ["mtrs.html", "MTR Library", "stock.view"]] },
  { label: null, links: [["reports.html", "Reports", "reports"], ["simulate.html", "Simulate", "simulate"], ["company.html", "Company Settings", "company"], ["designer.html", "Template Designer", "templates"], ["recycle-bin.html", "Recycle Bin", "recycle_bin"]] },
  { label: "MRP Migrate", links: [["golive.html", "Go-Live Cleanup", "golive"], ["mrp-payments.html", "PO Payments Import", "payments.import"], ["file-matcher.html", "File Matcher", "file_matcher"]] },
  { label: "Admin", links: [["activity.html", "Activity Log", "recycle_bin"], ["users.html", "Users & Roles", "users"], ["backups.html", "Backups", "backups"]] },
];

// Where "Home" goes for this user: the dashboard, or for a POD-only role (drivers) the POD page.
function homePage() {
  if (!AuthGuard.getUser()) return "login.html";
  if (AuthGuard.canAny("orders.view", "stock.view", "invoices", "purchasing")) return "dashboard.html";
  return AuthGuard.can("pod.upload") ? "pod.html" : AuthGuard.can("shipments.view") ? "shipments.html" : "account.html";
}

const ROLE_LABELS = { super_admin: "Super Admin", admin: "Admin", manager: "Manager", employee: "Employee" };

function renderSidebar(activePage) {
  const user = AuthGuard.getUser();
  const groups = NAV_GROUPS.map(group => {
    const links = group.links.filter(([, , perm]) => !perm || AuthGuard.canAny(...perm.split(" "))).map(([href, label]) =>
      `<a href="${href}" class="${href === activePage ? 'active' : ''} ${PHONE_PAGES.some(([h]) => h === href) ? "m-dup" : ""}">${icon(NAV_ICONS[href])}${label}</a>`
    ).join("");
    if (!links) return "";
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
      ${PhoneNav.groupHtml(activePage)}
      ${groups}
      <div class="sidebar-footer">
        <a href="#" onclick="toggleTheme(); return false;" id="theme-toggle">${icon("moon")}<span>${currentTheme() === "dark" ? "Light Mode" : "Dark Mode"}</span></a>
        <a href="account.html" class="m-only ${activePage === "account.html" ? "active" : ""}">${icon("user")}My Account</a>
        <a href="#" class="m-only" onclick="AuthGuard.logout(); return false;">${icon("logout")}Logout</a>
      </div>
    </nav>
  `;
}

// ---- Phones (the shipping floor): pages that carry the viewport tag get a phone layout under 760px -- the sidebar is a
// drawer behind the top bar's menu button, "On the go" pages first, and tables read as cards ("Label: value" lines,
// like the POD page). Office pages without the tag stay desktop (a phone shows them zoomed out). ----
const PHONE_PAGES = [["shipments.html", "Shipments", "shipments.view"], ["pack-shipments.html", "Bulk Operations", "shipments.work invoices"],
  ["pod.html", "Proof Of Delivery", "pod.upload"], ["labels.html", "On-Demand Labels", "shipments.work"], ["todo.html", "To-Do", ""],
  ["tasks.html", "Tasks", "tasks"], ["dashboard.html", "Dashboard", "orders.view stock.view invoices purchasing"]];
const PhoneNav = {
  media: window.matchMedia("(max-width: 760px)"),
  isPhone() { return this.media.matches && !!document.querySelector('meta[name="viewport"]'); },
  toggle(open = !document.body.classList.contains("nav-open")) {
    document.body.classList.toggle("nav-open", open);
    let scrim = document.getElementById("nav-scrim");
    if (!scrim) {
      scrim = document.createElement("div");
      scrim.id = "nav-scrim";
      scrim.className = "nav-scrim";
      scrim.onclick = () => PhoneNav.toggle(false);
      document.body.appendChild(scrim);
    }
  },
  // the drawer's first group: the pages made for phones that this person may open
  groupHtml(activePage) {
    const links = PHONE_PAGES.filter(([, , perm]) => !perm || AuthGuard.canAny(...perm.split(" ")))
      .map(([href, label]) => `<a href="${href}" class="${href === activePage ? "active" : ""}">${icon(NAV_ICONS[href])}${label}</a>`).join("");
    return links ? `<div class="nav-group m-only"><div class="nav-group-label">On the go</div>${links}</div>
      <div class="nav-group-label m-only m-desk-note">Office screens <span>(best on a computer)</span></div>` : "";
  },
};
document.addEventListener("keydown", e => { if (e.key === "Escape" && document.body.classList.contains("nav-open")) PhoneNav.toggle(false); });

// Tables as cards on phones: each cell gets its column's header as data-label (CSS shows it only under 760px).
// Follows rowspans and colspans, so a merged cell labels the right column. Tables marked .m-keep stay tables.
const PhoneTables = {
  label(table) {
    const head = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
    if (!head) return;
    const names = [];
    [...head.cells].forEach(th => {
      const clone = th.cloneNode(true);
      clone.querySelectorAll(".th-filter, .col-resizer, .sort-ind, button, input, .sr-only").forEach(x => x.remove());
      const text = clone.textContent.replace(/[▾▴⇅]/g, "").replace(/\s+/g, " ").trim();
      for (let k = 0; k < (th.colSpan || 1); k++) names.push(text);
    });
    const sig = names.join("|") + "#" + [...table.tBodies].reduce((n, b) => n + b.rows.length, 0);
    if (table.dataset.mSig === sig) return;  // nothing changed since the last pass
    table.dataset.mSig = sig;
    table.classList.add("m-cards");
    [...table.tBodies].forEach(body => {
      const carry = [];  // rowspans still covering a column: [rows left]
      [...body.rows].forEach(tr => {
        let col = 0;
        [...tr.cells].forEach(td => {
          while (carry[col] > 0) { carry[col]--; col++; }
          if (td.colSpan > 1 || !names[col]) td.removeAttribute("data-label"); else td.dataset.label = names[col];
          if (td.rowSpan > 1) for (let k = 0; k < (td.colSpan || 1); k++) carry[col + k] = td.rowSpan - 1;
          col += td.colSpan || 1;
        });
        while (col < carry.length) { if (carry[col] > 0) carry[col]--; col++; }
      });
    });
  },
  run(root = document) {
    if (!PhoneNav.isPhone()) return;
    root.querySelectorAll("main table:not(.m-keep), .glass-panel table:not(.m-keep), .modal table:not(.m-keep)").forEach(t => this.label(t));
  },
};
(() => {
  let queued = false;
  const later = () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; PhoneTables.run(); }); };
  new MutationObserver(muts => { if (PhoneNav.isPhone() && muts.some(m => m.addedNodes.length)) later(); })
    .observe(document.documentElement, { childList: true, subtree: true });
  PhoneNav.media.addEventListener("change", later);
  document.addEventListener("DOMContentLoaded", later);
})();

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
    exportBtn.className = "table-tools-btn tt-icon";
    exportBtn.innerHTML = icon("download");
    exportBtn.title = "Export — CSV, Excel, PDF or copy";
    exportBtn.setAttribute("aria-label", "Export");
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
        // data-sort-group: rows sort only within their group; groups (with their colspan separator rows) keep their order
        const grouped = rows.some(r => r.dataset.sortGroup !== undefined);
        const grp = r => parseFloat(r.dataset.sortGroup || 0);
        const sorted = [...sortable].sort((a, b) => {
          if (grouped && grp(a) !== grp(b)) return grp(a) - grp(b);
          if (dir === 0) return original.get(a) - original.get(b);
          const at = Array.from(ths[col].parentNode.cells).indexOf(ths[col]);  // columns may have been moved
          const x = this.sortKey(a.cells[at]), y = this.sortKey(b.cells[at]);
          // numbers before text, blanks last -- in both directions (a descending sort shouldn't lead with empty cells)
          if (x.n != null && y.n != null) return (x.n - y.n) * dir || original.get(a) - original.get(b);
          if (x.n != null || y.n != null) return x.n != null ? -1 : 1;
          if (!x.s || !y.s) return (!x.s && !y.s ? 0 : !x.s ? 1 : -1) || original.get(a) - original.get(b);
          return x.s.localeCompare(y.s, undefined, { numeric: true }) * dir || original.get(a) - original.get(b);
        });
        if (grouped) {
          const seps = rows.filter(r => r.querySelector("td[colspan]"));
          const groups = [...new Set(rows.map(grp))].sort((a, b) => a - b);
          const order = groups.flatMap(g => [...seps.filter(r => grp(r) === g), ...sorted.filter(r => grp(r) === g)]);
          if (order.some((r, i) => r !== rows[i])) order.forEach(r => tb.appendChild(r));
        } else if (sorted.some((r, i) => r !== sortable[i])) sorted.forEach(r => tb.appendChild(r));
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
    return `${page.trim().replace(/[^\w-]+/g, "-")}-${todayISO()}`;
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
        <div class="sub">${escapeHtml(company)} · ${fmtWhen(new Date())} · ${d.rows.length} rows</div>
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
      .filter(tr => tr.style.display !== "none" && !tr.querySelector("td[colspan]") && !tr.classList.contains("skeleton-row"));
    if (!rows.length && table.querySelector("tr.skeleton-row")) { table.tFoot.innerHTML = ""; return; }  // still loading
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

// <button data-perm="orders.edit"> (or "a b": any of them) shows only for roles that have it.
function applyPermGates(root = document) {
  const els = root.matches && root.matches("[data-perm]") ? [root] : [];
  (root.querySelectorAll ? root.querySelectorAll("[data-perm]") : []).forEach(el => els.push(el));
  els.forEach(el => { if (!AuthGuard.canAny(...el.dataset.perm.split(" "))) el.style.display = "none"; });
}

// "View as" banner: a super admin looking through someone else's eyes (read-only) -- one click back.
function viewAsBack() {
  try {
    const back = JSON.parse(localStorage.getItem("at_hub_view_as_back"));
    localStorage.removeItem("at_hub_view_as_back");
    if (back && back.token) { AuthGuard.setSession(back.token, back.user); location.href = "users.html"; return; }
  } catch {}
  AuthGuard.clearSession();
  location.href = "login.html";
}
function drawViewAsBanner() {
  let back = null;
  try { back = JSON.parse(localStorage.getItem("at_hub_view_as_back")); } catch {}
  const u = AuthGuard.getUser();
  if (!back || !u || location.pathname.endsWith("login.html")) return;
  const bar = document.createElement("div");
  bar.className = "view-as-bar";
  bar.innerHTML = `${icon("eye")}<span>Viewing as <strong>${escapeHtml(u.full_name || u.username)}</strong> · ${escapeHtml(u.role_name || u.role)} — read-only, nothing you do here is saved</span>
    <button class="small-btn" onclick="viewAsBack()">Back To My Account</button>`;
  document.body.prepend(bar);
  document.body.classList.add("viewing-as");
}

document.addEventListener("DOMContentLoaded", () => {
  drawViewAsBanner();
  if (hidesMoney()) document.body.classList.add("no-money");
  hideMoneyColumns();
  applyPermGates();
  new MutationObserver(muts => {
    for (const m of muts) for (const n of m.addedNodes) if (n.nodeType === 1) { hideMoneyColumns(n); applyPermGates(n); }
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
          <td>${fmtWhen(h.changed_at)}</td><td class="num">${h.previous_pack_size ?? "—"}</td><td class="num"><strong>${h.pack_size ?? "—"}</strong></td>
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
      <span class="muted small"> · ${escapeHtml(r.po_code || "")} · ${escapeHtml(r.vendor || "")} · ${r.po_date ? fmtDate(r.po_date) : ""}${r.heat_number ? ` · Heat ${escapeHtml(r.heat_number)}` : ""}</span></span></label>`;
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
      `Sent ${escapeHtml(e.files || "")} to ${escapeHtml(e.to)} by ${escapeHtml(e.sent_by || "")} · ${fmtWhen(e.sent_at)}`).join("<br>")}</div>` : ""}`;
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
  eyeOff: '<path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c6.5 0 10 8 10 8a17.6 17.6 0 0 1-2.6 3.7"/><path d="M6.6 6.6C3.7 8.5 2 12 2 12s3.5 8 10 8a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m2 2 20 20"/>',
  cornerRight: '<path d="M4 4v7a4 4 0 0 0 4 4h12"/><path d="m15 10 5 5-5 5"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  sticky: '<path d="M15.5 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.5L15.5 3Z"/><path d="M15 3v6h6"/>',
  flask: '<path d="M10 2v7.31"/><path d="M14 9.3V2"/><path d="M8.5 2h7"/><path d="M14 9.3a6.5 6.5 0 1 1-4 0"/><path d="M5.52 16h12.96"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  listTodo: '<rect x="3" y="5" width="6" height="6" rx="1"/><path d="m3 17 2 2 4-4"/><path d="M13 6h8M13 12h8M13 18h8"/>',
  chevLeft: '<path d="m15 18-6-6 6-6"/>',
  chevRight: '<path d="m9 18 6-6-6-6"/>',
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
  scissors: '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
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
  palette: '<circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.65-.75 1.65-1.69 0-.44-.18-.84-.44-1.13-.29-.29-.44-.65-.44-1.13a1.64 1.64 0 0 1 1.67-1.67h2c3.05 0 5.55-2.5 5.55-5.55C21.97 6.01 17.46 2 12 2z"/>',
  sliders: '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
  thumbsUp: '<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  gear: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  notePen: '<path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4"/><path d="M2 6h4M2 10h4M2 14h4M2 18h4"/><path d="M21.38 5.62a1 1 0 0 0-3-3L13 8l-1 4 4-1Z"/>',
};

// ---- Status as a small icon (same look as the order timeline); the word stays in the tooltip and as
// hidden text, so search, header filters and exports still see it. statusIcon("partially_shipped")
const STATUS_ICONS = {
  validation: ["fileCheck", "Validation needed — quick-captured"], draft: ["pencil", "Draft"], confirmed: ["thumbsUp", "Confirmed — in progress"], not_booked: ["clock", "Not booked yet"],
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
  "users.html": "shield", "account.html": "user", "recycle-bin.html": "trash", "file-matcher.html": "paperclip", "tasks.html": "checkCircle", "ai-desk.html": "sparkles", "mrp-payments.html": "dollar", "golive.html": "sliders", "todo.html": "listTodo", "notes.html": "sticky", "simulate.html": "flask", "activity.html": "clock", "backups.html": "save", "designer.html": "palette", "reconcile.html": "list",
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
  not_delivered: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Carrier / Tracking", r => [r.carrier, r.tracking_number].filter(Boolean).join(" · ")], ["Shipped", r => actDate(r.ship_date)], ["Days", r => escapeHtml(r.days ?? "")]],
  missing_pod: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Shipped", r => actDate(r.ship_date)], ["Delivered?", r => r.delivered ? "Yes" : "No"], ["Days", r => escapeHtml(r.days ?? "")]],
  not_shipped: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Status", r => escapeHtml(r.status ?? "")], ["Days Waiting", r => escapeHtml(r.days ?? "")]],
  late_orders: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Customer PO", r => custPoLink(r.po_number, r.id)], ["Due", r => actDate(r.due)], ["Days Late", r => escapeHtml(r.days ?? "")]],
  vendor_shipped: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => escapeHtml(r.vendor ?? "")], ["Vendor Invoices", r => escapeHtml(r.bills ?? "")], ["Received", r => `${fmtQty(r.received_qty)} / ${fmtQty(r.ordered_qty)}`], ["Days Since Invoice", r => escapeHtml(r.days ?? "")]],
  po_overdue: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => escapeHtml(r.vendor ?? "")], ["Expected", r => actDate(r.expected)], ["Received", r => `${fmtQty(r.received_qty)} / ${fmtQty(r.ordered_qty)}`], ["Days Late", r => escapeHtml(r.days ?? "")]],
  bills_due: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => escapeHtml(r.vendor ?? "")], ["Invoice #", r => escapeHtml(r.bill_number ?? "")], ["Balance", r => fmtMoney(r.balance)], ["Due", r => actDate(r.due)], ["Days Overdue", r => escapeHtml(r.days ?? "")]],
  unapplied_payments: [["Payment", r => escapeHtml(r.code ?? "")], ["Vendor", r => escapeHtml(r.vendor ?? "")], ["Paid", r => actDate(r.paid_date)], ["Amount", r => fmtMoney(r.amount)], ["Unapplied", r => fmtMoney(r.unapplied)], ["Days", r => escapeHtml(r.days ?? "")]],
  mtr_unlinked: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["File", r => escapeHtml(r.filename ?? "")], ["Uploaded (Days Ago)", r => escapeHtml(r.days ?? "")]],
  items_verify: [["Item", r => actLink("item.html", r.id, r.code)], ["Title", r => escapeHtml(r.title || "")], ["Group", r => escapeHtml(r.group || "")], ["Days Waiting", r => escapeHtml(r.days ?? "")]],
  no_invoice: [["Shipment", r => actLink("shipments.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Shipped", r => actDate(r.ship_date)], ["Amount", r => fmtMoney(r.amount)], ["Days", r => escapeHtml(r.days ?? "")]],
  draft_invoices: [["Invoice", r => actLink("invoices.html", r.id, r.code)], ["Order", r => actLink("customer-orders.html", r.order_id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Amount", r => fmtMoney(r.amount)], ["Days", r => escapeHtml(r.days ?? "")]],
  invoices_overdue: [["Invoice", r => actLink("invoices.html", r.id, r.code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Balance", r => fmtMoney(r.balance)], ["Due", r => actDate(r.due)], ["Days Overdue", r => escapeHtml(r.days ?? "")]],
  not_booked: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Customer PO", r => custPoLink(r.po_number, r.id)], ["Amount", r => fmtMoney(r.amount)], ["Days", r => escapeHtml(r.days ?? "")]],
  captured_orders: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Customer PO", r => custPoLink(r.po_number, r.id)], ["Captured By", r => escapeHtml(r.by ?? "")], ["Days", r => r.days]],
  captured_pos: [["PO", r => actLink("purchase-orders.html", r.id, r.code)], ["Vendor", r => escapeHtml(r.vendor ?? "")], ["Vendor Ref", r => escapeHtml(r.ref ?? "")], ["Captured By", r => escapeHtml(r.by ?? "")], ["Days", r => r.days]],
  draft_orders: [["Order", r => actLink("customer-orders.html", r.id, r.order_code)], ["Customer", r => escapeHtml(r.customer ?? "")], ["Customer PO", r => custPoLink(r.po_number, r.id)], ["Amount", r => fmtMoney(r.amount)], ["Days", r => escapeHtml(r.days ?? "")]],
};
function actLink(page, id, text) { return id ? `<a class="link" href="${page}?id=${id}">${escapeHtml(text || "")}</a>` : escapeHtml(text || ""); }
function actDate(v) { return fmtDate(v); }

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
      return `<details class="action-section" id="act-${s.key}" ${open.length <= 3 && !PhoneNav.isPhone() ? "open" : ""}>
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
  const cloud = ["vendor_invoice", "vendor_order"].includes(kind) && AuthGuard.can("ai")
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
  picker.accept = "application/pdf,image/*,.xlsx,.csv,.docx,.txt,.eml";  // spreadsheets / Word / emails are read as text
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
  if (!candidates || !candidates.length) return `<div class="ai-cands muted small">Nothing close in Stock Items — pick from the list, or Quick Add it.</div>`;
  return `<div class="ai-cands"><span class="small muted">${pickedId ? "Other close items:" : "Closest we have — pick one:"}</span>${candidates.map(c =>
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
      window.showDetail = function (id, ...rest) {
        recId = id;
        if (open) setUrl(id, true);
        const done = orig.call(this, id, ...rest);
        Promise.resolve(done).then(() => { presence.start(); Recent.track(id); });
        return done;
      };
    }
    const urlFor = id => { const u = new URL(location.href); if (id) u.searchParams.set("id", id); else u.searchParams.delete("id"); return u.pathname + u.search + u.hash; };
    const setUrl = (id, replace) => history[replace ? "replaceState" : "pushState"]({ record: true, id }, "", urlFor(id));
    window.setRecordId = id => { recId = id; if (open) setUrl(id, true); presence.start(); if (id) Recent.track(id); };  // records drawn without showDetail (a quote)
    // presence: who else has this record open, and whether someone else saved it meanwhile
    const COLLECTION = { "customer-orders.html": "customer-orders", "purchase-orders.html": "purchase-orders",
                         "shipments.html": "shipments", "invoices.html": "invoices" }[location.pathname.split("/").pop()];
    const presence = {
      key: null, timer: null,
      start() {
        const key = open && recId && COLLECTION ? `${COLLECTION}/${recId}` : null;
        if (key === this.key) return;
        this.stop();
        this.key = key;
        if (!key) return;
        this.beat();
        this.timer = setInterval(() => { if (!document.hidden) this.beat(); }, 15000);
      },
      stop() {
        if (this.timer) clearInterval(this.timer);
        if (this.key) fetch(`${API_BASE}/api/presence`, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", Authorization: `Bearer ${AuthGuard.getToken()}` },
          body: JSON.stringify({ key: this.key, leave: true }) }).catch(() => {});
        this.timer = this.key = null;
        const bar = card.querySelector(":scope > .presence-bar");
        if (bar) bar.remove();
      },
      async beat() {
        const key = this.key;
        let r;
        try { r = await apiFetch("/api/presence", { method: "POST", body: JSON.stringify({ key }) }); } catch (e) { return; }
        if (key !== this.key) return;
        const me = (AuthGuard.getUser() || {}).username;
        const mine = recordVersions[key];
        const changed = r.version != null && mine != null && r.version !== mine && r.updated_by && r.updated_by !== me;
        let bar = card.querySelector(":scope > .presence-bar");
        if (!r.others.length && !changed) { if (bar) bar.remove(); return; }
        if (!bar) {
          bar = document.createElement("div");
          bar.className = "presence-bar";
          const back = card.querySelector(":scope > .record-backbar");
          back ? back.after(bar) : card.prepend(bar);
        }
        bar.classList.toggle("changed", !!changed);
        bar.innerHTML = (r.others.length ? `<span class="presence-who">${r.others.map(u => `<b class="presence-avatar" title="${escapeHtml(u)}">${escapeHtml(u.slice(0, 2).toUpperCase())}</b>`).join("")}
            ${escapeHtml(r.others.join(", "))} ${r.others.length === 1 ? "is" : "are"} also viewing this</span>` : "")
          + (changed ? `<span class="presence-changed">${icon("info")}<strong>${escapeHtml(r.updated_by)}</strong> saved changes${r.updated_at ? ` at ${fmtTime(r.updated_at)}` : ""}
            <a class="link" onclick="presenceReload()">Reload to see them</a></span>` : "");
      },
    };
    window.presenceReload = () => { if (recId && typeof window.showDetail === "function") window.showDetail(recId); };
    window.addEventListener("beforeunload", () => presence.stop());
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
      presence[open ? "start" : "stop"]();
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

// Lines numbered 1..n on screen in their current order (after a drag, an add or a delete).
function renumberRowsOnScreen(tbody) {
  if (!tbody) return;
  [...tbody.children].filter(tr => tr.dataset.line).forEach((tr, i) => {
    const cell = tr.querySelector(".line-no");
    if (!cell) return;
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const m = n.textContent.match(/^(\s*)(#?)(\d+)(\s*)$/);
      if (m) { n.textContent = `${m[1]}${m[2]}${i + 1}${m[4]}`; break; }
    }
  });
}
// The entry row after its line was saved: emptied, ready for the next one.
function resetEntryRow(tr) {
  if (!tr) return;
  tr.querySelectorAll("select").forEach(sel => { sel.value = ""; sel.dispatchEvent(new Event("change", { bubbles: true })); });
  tr.querySelectorAll("input").forEach(i => {
    if (i.closest(".search-select")) i.value = "";
    else if (i.classList.contains("qty-input")) i.value = 1;
    else if (i.type !== "checkbox") i.value = "";
  });
  tr.querySelectorAll(".vcode-hint, [data-price-delta]").forEach(el => { el.innerHTML = ""; });
}

// ---- order lines: drag to reorder, replace an item, tick several ----
// Drag a line by its ⠿ handle (in the first cell). onReorder(ids) gets every row's data-line in the new order;
// leave it out for a form that isn't saved yet (the rows are simply read in their new order on save).
function enableLineDrag(tbody, onReorder) {
  if (!tbody || tbody.dataset.dragReady) return;
  tbody.dataset.dragReady = "1";
  const rows = () => Array.from(tbody.children).filter(tr => tr.tagName === "TR" && !tr.querySelector("td[colspan]") && !tr.classList.contains("entry-row"));
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
    renumberRowsOnScreen(tbody);  // the # follows the position straight away (the server does the same)
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
  if (!AuthGuard.can("ai")) return "";
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
    const can = (...p) => AuthGuard.canAny(...p);
    const get = (url, ok = true) => ok ? apiFetch(url).catch(() => []) : Promise.resolve([]);
    const [orders, pos, items, customers, vendors, shipments, invoices] = await Promise.all([
      get("/api/customer-orders/", can("orders.view")), get("/api/purchase-orders/", can("purchasing")), get("/api/stock-items/", can("stock.view")),
      get("/api/customers/", can("customers.view")), get("/api/vendors/", can("vendors", "purchasing")), get("/api/shipments/", can("shipments.view")),
      get("/api/invoices/", can("invoices"))]);
    const cname = Object.fromEntries(customers.map(c => [c.id, c.name])), vname = Object.fromEntries(vendors.map(v => [v.id, v.name]));
    this.data = [
      ...orders.map(o => ({ kind: "Order", label: o.code, sub: [cname[o.customer_id], o.po_number && `PO ${o.po_number}`, o.job_number && `Job ${o.job_number}`, o.status].filter(Boolean).join(" · "), href: `customer-orders.html?id=${o.id}`, hay: `${o.code} ${o.po_number || ""} ${o.job_number || ""} ${cname[o.customer_id] || ""}` })),
      ...pos.map(p => ({ kind: "PO", label: p.code, sub: [vname[p.vendor_id], p.vendor_so_number && `SO ${p.vendor_so_number}`, p.status].filter(Boolean).join(" · "), href: `purchase-orders.html?id=${p.id}`, hay: `${p.code} ${p.vendor_so_number || ""} ${vname[p.vendor_id] || ""}` })),
      ...shipments.map(s => { const o = orders.find(x => x.id === s.order_id) || {}; return { kind: "Shipment", label: s.code,
        sub: [s.status, o.po_number && `PO ${o.po_number}`, o.job_number && `Job ${o.job_number}`, s.tracking_number].filter(Boolean).join(" · "),
        href: `shipments.html?id=${s.id}`, hay: `${s.code} ${s.tracking_number || ""} ${o.code || ""} ${o.po_number || ""} ${o.job_number || ""}` }; }),
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
      charges: r.method === "DELETE" ? "removed a charge" : "added a charge",
      receipts: sub === "undo" ? "undid a receipt (stock taken back off)" : sub === "date" ? `changed a receipt's date${d && d.received_date ? ` to ${String(d.received_date).slice(0, 10)}` : ""}`
        : `corrected a received line${d ? `: ${fields(d)}` : ""}`,
      "vendor-shipments": r.method === "DELETE" ? "deleted a vendor shipment" : r.method === "PUT" ? "edited a vendor shipment" : "recorded a vendor shipment (shipped)", payments: "recorded a payment" }[what]) || `${r.method.toLowerCase()} ${escapeHtml(r.action)}`;
  };
  el.innerHTML = rows.length ? `<ul class="activity-list">${rows.map(r => `<li><span class="muted small">${fmtWhen(r.at)}</span>
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
// A page's loading failed (server error, lost connection): say so where the list was going to be,
// instead of leaving the placeholder rows up for ever.
window.addEventListener("unhandledrejection", e => {
  const msg = (e.reason && e.reason.message) || String(e.reason || "unknown error");
  document.querySelectorAll("tbody").forEach(tb => {
    if (!tb.querySelector("tr.skeleton-row")) return;
    tb.innerHTML = `<tr><td colspan="${tb.rows[0].cells.length}" class="error">Couldn't load this list: ${escapeHtml(msg)}
      · <a class="link" onclick="location.reload()">Reload</a></td></tr>`;
  });
});

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
async function printRecord() {
  const card = document.getElementById("detail-card");
  if (!card) { window.print(); return; }
  const groups = printSections(card);
  // last time's choices on this screen (Print Options, saved per person): sections left out, prices, notes
  const key = `record_${location.pathname.split("/").pop().replace(/\.html$/, "").replace(/-/g, "_")}`;
  const last = ((await PrintOptions.load()).other || {})[key] || null;
  const ticked = g => last && Array.isArray(last.off) ? !last.off.includes(g.name) && !(g.money && !(last.on || []).includes(g.name)) : !g.money;
  document.querySelectorAll(".print-dialog").forEach(d => d.remove());
  const dlg = document.createElement("div");
  dlg.className = "qf-backdrop print-dialog";
  dlg.innerHTML = `<div class="qf-box" style="padding:14px 16px;">
    <h3 style="margin:0 0 4px;">Print — what to include?</h3>
    <p class="muted small" style="margin:0 0 10px;">Sections with money start unticked.</p>
    <div class="print-opts">${groups.map((g, i) => `<label><input type="checkbox" data-g="${i}" ${ticked(g) ? "checked" : ""}> ${escapeHtml(g.name)}${g.money ? ' <span class="muted small">($)</span>' : ""}</label>`).join("")}</div>
    <label style="display:block; margin-top:10px;"><input type="checkbox" id="print-prices" ${last && last.prices ? "checked" : ""}> Show prices and totals in the line table</label>
    <label style="display:block; margin-top:4px;" title="Notes marked 'don't print' never print"><input type="checkbox" id="print-notes" ${!last || last.notes !== false ? "checked" : ""}> Show line notes</label>
    <p class="muted small" style="margin:8px 0 0;">Remembered for next time.</p>
    <div style="display:flex; gap:8px; margin-top:14px; justify-content:flex-end;">
      <button class="secondary" data-cancel>Cancel</button><button data-go>Print</button></div></div>`;
  document.body.appendChild(dlg);
  dlg.querySelector("[data-cancel]").onclick = () => dlg.remove();
  dlg.addEventListener("mousedown", e => { if (e.target === dlg) dlg.remove(); });
  dlg.querySelector("[data-go]").onclick = () => {
    const keep = new Set([...dlg.querySelectorAll("[data-g]:checked")].map(c => +c.dataset.g));
    const prices = dlg.querySelector("#print-prices").checked;
    PrintOptions.save(key, { off: groups.filter((g, i) => !keep.has(i)).map(g => g.name), on: groups.filter((g, i) => keep.has(i) && g.money).map(g => g.name),
                             prices, notes: dlg.querySelector("#print-notes").checked });
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
const pkDate = d => fmtDate(d);
const peekStack = [];
function peekGet(url) { return (peekCache[url] ??= apiFetch(url).catch(e => { delete peekCache[url]; throw e; })); }
async function peekItems() { return peekGet("/api/stock-items/"); }
function peekParty(kind, id) { return id ? peekGet(`/api/${kind}/${id}`).catch(() => ({ name: "" })) : Promise.resolve({ name: "" }); }

document.addEventListener("click", e => {
  const a = e.target.closest && e.target.closest("a[href]");
  if (!a || e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  if (a.target === "_blank" || a.hasAttribute("data-nopeek") || a.closest("[data-nopeek]")) return;
  // "#" links are buttons (Light Mode, menus...): resolved against shipments.html?id=208 they'd look like a record link
  if ((a.getAttribute("href") || "").trim().startsWith("#")) return;
  let url;
  try { url = new URL(a.getAttribute("href"), location.href); } catch (err) { return; }
  if (url.origin !== location.origin) return;
  if (url.pathname === location.pathname && url.search === location.search) return;  // the page you're on
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
    return { title: `${escapeHtml(o.code)} ${pk.tag(o.status)}`, sub: `${escapeHtml(cust.name || "")}${o.po_number ? ` · PO ${custPoLink(o.po_number, o.id)}` : ""}${o.job_number ? ` · Job ${escapeHtml(o.job_number)}` : ""}`,
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
      sub: `${o ? `Order ${pk.link("customer-orders.html", o.id, o.code)} · ${escapeHtml(cust.name || "")}${o.po_number ? ` · PO ${custPoLink(o.po_number, o.id)}` : ""}` : ""}`,
      body: pk.facts([["Created", pkDate(s.created_at)], ["Shipped", pkDate(s.ship_date)], ["Delivered", pkDate(s.delivered_at)], ["Carrier", escapeHtml(s.carrier || "")],
          ["Tracking", trackingLink(s.carrier, s.tracking_number)], ["Invoice", s.invoice_id ? invoiceChipFromShipment(s) : ""], ["POD", s.pods && s.pods.length ? `${s.pods.length} file${s.pods.length === 1 ? "" : "s"}` : ""]])
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


// ---- top bar on every screen: date / time in the user's zone, calendar, To-Do and reminders, theme ----
const TopBar = {
  month: null,       // first day of the month shown in the calendar (local Date)
  events: {},        // "YYYY-MM-DD" -> [events]
  picked: null,
  KIND: { delivery: ["Delivery due", "#2563eb"], po: ["PO expected", "#7c3aed"], invoice: ["Invoice due", "#dc2626"],
          shipped: ["Shipped", "#16a34a"], todo: ["To-do", "#ea580c"], note: ["Note reminder", "#ca8a04"] },
  mount() {
    const main = document.querySelector(".app-shell > main");
    if (!main || document.getElementById("topbar") || !AuthGuard.getToken()) return;
    const bar = document.createElement("div");
    bar.id = "topbar";
    bar.className = "topbar";
    const page = (document.title.split("—").pop() || "AT-HUB").trim();
    bar.innerHTML = `<button type="button" class="tb-btn tb-menu m-only" onclick="PhoneNav.toggle()" aria-label="Menu">${icon("list")}</button>
      <span class="tb-page m-only">${escapeHtml(page)}</span>
      <div class="tb-right">
        ${AuthGuard.can("insights") && AuthGuard.can("money.view") ? `<button type="button" class="tb-btn tb-insights" onclick="openQuickInsights(TopBar.insightsFocus())"
          title="Money at a glance: orders shipped / pending, shipments in process / not invoiced, invoices paid / owed, POs received / owed">${icon("chart")}<span>Quick Insights</span></button>` : ""}
        <a class="tb-btn" href="todo.html" title="To-Do and reminders">${icon("listTodo")}<span class="tb-badge" id="tb-todo" hidden></span></a>
        <span class="tb-pop-wrap"><button type="button" class="tb-btn" id="tb-recent" onclick="Recent.toggle(event)" title="Recently viewed records">${icon("clock")}</button>
          <div class="tb-menu-pop tb-recent-pop" id="tb-recent-pop" hidden role="menu"></div></span>
        <button type="button" class="tb-btn" id="tb-notes" onclick="NotesView.toggle()"></button>
        <button type="button" class="tb-btn" onclick="toggleTheme(); TopBar.themeIcon();" title="Light / dark" id="tb-theme"></button>
        <button type="button" class="tb-clock" onclick="TopBar.toggle(event)" title="Calendar">
          ${icon("calendar")}<span class="tb-date" id="tb-date"></span><span class="tb-time" id="tb-time"></span></button>
        <span class="tb-pop-wrap">${AccountMenu.html()}</span>
      </div>
      <div class="tb-cal" id="tb-cal" hidden></div>`;
    main.prepend(bar);
    NotesView.apply();
    this.siteLabel();
    LocalBackup.check();  // a backup to this computer is due every 3 days (backups.download)
    this.tick();
    this.themeIcon();
    setInterval(() => this.tick(), 20000);
    this.badge();
    setInterval(() => this.badge(), 5 * 60000);
    document.addEventListener("click", e => {
      const cal = document.getElementById("tb-cal");
      if (cal && !cal.hidden && !e.target.closest("#tb-cal") && !e.target.closest(".tb-clock")) cal.hidden = true;
    });
    document.addEventListener("keydown", e => { if (e.key === "Escape") { const c = document.getElementById("tb-cal"); if (c) c.hidden = true; } });
  },
  insightsFocus() {  // the Quick Insights card for this screen comes first
    const page = location.pathname.split("/").pop();
    return { "customer-orders.html": "orders", "shipments.html": "shipments", "pack-shipments.html": "shipments",
             "invoices.html": "invoices", "purchase-orders.html": "purchasing", "vendors.html": "purchasing" }[page] || "orders";
  },
  siteLabel() {  // a test copy says so on every page (SITE_LABEL on the server)
    fetch("/api/health").then(r => r.json()).then(h => {
      if (!h.site_label) return;
      const tag = document.createElement("span");
      tag.className = "tb-site-label";
      tag.textContent = h.site_label;
      tag.title = "This is not the real AT-HUB -- changes here don't count";
      document.querySelector("#topbar .tb-right")?.prepend(tag);
      document.title = `[${h.site_label}] ${document.title}`;
    }).catch(() => {});
  },
  themeIcon() { const b = document.getElementById("tb-theme"); if (b) b.innerHTML = icon(currentTheme() === "dark" ? "sun" : "moon"); },
  tick() {
    const now = new Date(), d = document.getElementById("tb-date"), t = document.getElementById("tb-time");
    if (!d) return;
    d.textContent = now.toLocaleDateString(undefined, { timeZone: userTz(), weekday: "short", month: "short", day: "numeric", year: "numeric" });
    t.textContent = `${fmtTime(now)} ${tzAbbrev(now)}`;
  },
  async badge() {
    try {
      const c = await apiFetch("/api/todo/counts");
      const el = document.getElementById("tb-todo");
      if (el) { el.hidden = !c.today; el.textContent = c.today > 99 ? "99+" : c.today; el.title = `${c.today} due today or overdue`; }
    } catch (e) { /* the badge is a nicety */ }
  },
  toggle(e) {
    e.stopPropagation();
    const cal = document.getElementById("tb-cal");
    cal.hidden = !cal.hidden;
    if (!cal.hidden) {
      const t = todayISO();
      if (!this.month) this.month = new Date(+t.slice(0, 4), +t.slice(5, 7) - 1, 1);
      this.picked = this.picked || t;
      this.load();
    }
  },
  shift(n) { this.month = new Date(this.month.getFullYear(), this.month.getMonth() + n, 1); this.load(); },
  goToday() { const t = todayISO(); this.month = new Date(+t.slice(0, 4), +t.slice(5, 7) - 1, 1); this.picked = t; this.load(); },
  iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; },
  async load() {
    const first = new Date(this.month), start = new Date(first);
    start.setDate(1 - first.getDay());
    const end = new Date(start); end.setDate(start.getDate() + 41);
    this.start = start;
    this.render(start, null);
    try {
      const ev = await apiFetch(`/api/calendar?start=${this.iso(start)}&end=${this.iso(end)}`);
      this.events = {};
      ev.forEach(x => (this.events[x.date] = this.events[x.date] || []).push(x));
      this.render(start, true);
    } catch (e) { this.render(start, e.message); }
  },
  render(start, loaded) {
    const cal = document.getElementById("tb-cal"), today = todayISO(), m = this.month.getMonth();
    const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
    const head = this.month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    const dayEv = this.events[this.picked] || [];
    cal.innerHTML = `<div class="tb-cal-head">
        <button type="button" class="tb-nav" onclick="TopBar.shift(-1)" title="Previous month">${icon("chevLeft")}</button>
        <strong>${escapeHtml(head)}</strong>
        <button type="button" class="tb-nav" onclick="TopBar.shift(1)" title="Next month">${icon("chevRight")}</button>
        <button type="button" class="tb-today" onclick="TopBar.goToday()">Today</button></div>
      <div class="tb-grid">${["S", "M", "T", "W", "T", "F", "S"].map(w => `<div class="tb-wd">${w}</div>`).join("")}
        ${days.map(d => { const k = this.iso(d), ev = this.events[k] || [];
          const kinds = [...new Set(ev.map(e => e.kind))].slice(0, 4);
          return `<button type="button" class="tb-day${d.getMonth() !== m ? " out" : ""}${k === today ? " today" : ""}${k === this.picked ? " picked" : ""}"
            onclick="TopBar.pick('${k}')"><span>${d.getDate()}</span><i>${kinds.map(x => `<b style="background:${this.KIND[x][1]}"></b>`).join("")}</i></button>`; }).join("")}</div>
      <div class="tb-legend">${Object.values(this.KIND).map(([l, c]) => `<span><b style="background:${c}"></b>${l}</span>`).join("")}</div>
      <div class="tb-agenda"><div class="tb-agenda-day">${escapeHtml(fmtDate(this.picked, { weekday: "long", month: "long", day: "numeric" }))}</div>
        ${loaded === null ? `<p class="muted small">Loading...</p>` : typeof loaded === "string" ? `<p class="error small">${escapeHtml(loaded)}</p>`
          : dayEv.length ? dayEv.map(e => `<a class="tb-ev" href="${e.link}"><b style="background:${this.KIND[e.kind][1]}"></b>
              <span><strong>${escapeHtml(e.title)}</strong>${e.sub ? `<span class="muted small"> · ${escapeHtml(e.sub)}</span>` : ""}</span></a>`).join("")
          : `<p class="muted small">Nothing on this day.</p>`}
        <a class="link small" href="todo.html?new=${this.picked}">+ To-do on this day</a></div>`;
  },
  pick(k) {
    this.picked = k;
    const first = new Date(+k.slice(0, 4), +k.slice(5, 7) - 1, 1);
    if (first.getMonth() !== this.month.getMonth()) { this.month = first; this.load(); } else this.render(this.start, true);
  },
};
document.addEventListener("DOMContentLoaded", () => TopBar.mount());


// ---- sticky notes on a record (customer order, PO, shipment): the team's reminders, seen by everyone who opens it ----
const STICKY_COLORS = ["yellow", "pink", "green", "blue"];
async function stickyNotes(container, entityType, entityId) {
  const el = typeof container === "string" ? document.getElementById(container) : container;
  if (!el) return;
  let notes = [];
  try { notes = await apiFetch(`/api/notes?entity_type=${entityType}&entity_id=${entityId}`); } catch (e) { el.innerHTML = ""; return; }
  const me = (AuthGuard.getUser() || {}).username;
  const canDelete = n => n.created_by === me || AuthGuard.can("money.view");
  const remind = n => n.remind_date ? `<span class="sticky-when ${!n.done && String(n.remind_date).slice(0, 10) <= todayISO() ? "due" : ""}">${icon("bell")}
      ${fmtDate(n.remind_date, { month: "short", day: "numeric" })}${n.remind_at ? " " + fmtTime(n.remind_at) : ""}</span>` : "";
  const open = notes.filter(n => !n.done), done = notes.filter(n => n.done);
  el.innerHTML = `<div class="sticky-row">
      ${open.map(n => `<div class="sticky-note sticky-${n.color}" data-id="${n.id}">
        <div class="sticky-text">${escapeHtml(n.text)}</div>
        <div class="sticky-foot">${remind(n)}<span class="muted">${escapeHtml(n.updated_by || n.created_by || "")} · ${fmtDate(n.updated_at || n.created_at, { month: "short", day: "numeric" })}</span>
          <span class="sticky-acts"><a title="Done" data-act="done">${icon("check")}</a><a title="Edit" data-act="edit">${icon("pencil")}</a></span></div></div>`).join("")}
      <button type="button" class="sticky-add" title="Stick a note on this record, with a reminder date if you like">${icon("sticky")}${open.length ? "" : "Add Note"}</button>
    </div>
    ${done.length ? `<details class="sticky-done"><summary class="muted small">${done.length} done note${done.length === 1 ? "" : "s"}</summary>
      ${done.map(n => `<div class="small muted" data-id="${n.id}" style="margin:3px 0;">✓ ${escapeHtml(n.text)} <span>· ${escapeHtml(n.done_by || "")} ${fmtDate(n.done_at)}</span>
        <a class="link" data-act="undo">Reopen</a>${canDelete(n) ? ` · <a class="link" data-act="delete">Delete</a>` : ""}</div>`).join("")}</details>` : ""}`;
  const reload = () => { stickyNotes(el, entityType, entityId); if (window.TopBar) TopBar.badge(); };
  const editor = (n = {}) => {
    const box = document.createElement("div");
    let color = n.color || "yellow";
    box.className = `sticky-note sticky-${color} sticky-editing`;
    box.innerHTML = `<textarea placeholder="Reminder for the team...">${escapeHtml(n.text || "")}</textarea>
      <div class="sticky-colors">${STICKY_COLORS.map(c => `<button type="button" class="sw sticky-${c} ${c === color ? "on" : ""}" data-c="${c}" title="${c}"></button>`).join("")}</div>
      <div class="sticky-remind"><label class="small">Remind on</label><input type="date" value="${n.remind_date ? String(n.remind_date).slice(0, 10) : ""}">
        <input type="time" value="${n.remind_at ? utcTime(n.remind_at).toLocaleTimeString("en-GB", { timeZone: userTz(), hour: "2-digit", minute: "2-digit" }) : ""}"></div>
      <div style="display:flex; gap:6px; margin-top:6px;"><button type="button" class="small-btn" data-save>Save</button>
        <button type="button" class="secondary small-btn" data-cancel>Cancel</button>
        ${n.id && canDelete(n) ? `<button type="button" class="secondary small-btn" data-del style="margin-left:auto;">Delete</button>` : ""}</div>`;
    box.querySelectorAll(".sw").forEach(b => b.onclick = () => {
      color = b.dataset.c;
      box.className = `sticky-note sticky-${color} sticky-editing`;
      box.querySelectorAll(".sw").forEach(x => x.classList.toggle("on", x === b));
    });
    box.querySelector("[data-cancel]").onclick = reload;
    const del = box.querySelector("[data-del]");
    if (del) del.onclick = async () => {
      if (!confirm("Delete this note?")) return;
      await apiFetch(`/api/notes/${n.id}`, { method: "DELETE" });
      reload();
    };
    box.querySelector("[data-save]").onclick = async () => {
      const [d, t] = box.querySelectorAll(".sticky-remind input");
      const body = { text: box.querySelector("textarea").value, color, remind_date: d.value, remind_time: d.value ? t.value : "" };
      try {
        await apiFetch(n.id ? `/api/notes/${n.id}` : "/api/notes", { method: n.id ? "PUT" : "POST",
          body: JSON.stringify(n.id ? body : { ...body, entity_type: entityType, entity_id: entityId }) });
        reload();
      } catch (e) { toast(e.message); }
    };
    return box;
  };
  el.querySelector(".sticky-add").onclick = () => {
    const box = editor();
    el.querySelector(".sticky-row").insertBefore(box, el.querySelector(".sticky-add"));
    box.querySelector("textarea").focus();
  };
  el.querySelectorAll("[data-act]").forEach(a => a.onclick = async () => {
    const id = parseInt(a.closest("[data-id]").dataset.id), n = notes.find(x => x.id === id), act = a.dataset.act;
    if (act === "edit") { a.closest(".sticky-note").replaceWith(editor(n)); return; }
    if (act === "delete") {
      if (!confirm("Delete this note for good?")) return;
      await apiFetch(`/api/notes/${id}`, { method: "DELETE" });
    } else await apiFetch(`/api/notes/${id}/done`, { method: "POST" });
    reload();
  });
}
// List pages: a small note marker beside records that have open sticky notes.
let NOTE_COUNTS = {};
async function loadNoteCounts(entityType) {
  try { NOTE_COUNTS = await apiFetch(`/api/notes/counts?entity_type=${entityType}`); } catch (e) { NOTE_COUNTS = {}; }
}
function noteMark(id) {
  const n = NOTE_COUNTS[id];
  return n ? `<span class="list-note" title="${n} open sticky note${n === 1 ? "" : "s"}">${icon("sticky")}</span>` : "";
}


// ---- carrier tracking: a tracking # becomes a link to the carrier's tracking page ----
function trackingUrl(carrier, number) {
  const n = String(number || "").trim().replace(/\s+/g, "");
  if (!n) return null;
  const c = String(carrier || "").toLowerCase(), q = encodeURIComponent(n);
  if (/ups/.test(c) || /^1Z[0-9A-Z]{16}$/i.test(n)) return `https://www.ups.com/track?tracknum=${q}`;
  if (/fedex|fed ex/.test(c)) return `https://www.fedex.com/fedextrack/?trknbr=${q}`;
  if (/usps|postal|post office/.test(c)) return `https://tools.usps.com/go/TrackConfirmAction?tLabels=${q}`;
  if (/dhl/.test(c)) return `https://www.dhl.com/us-en/home/tracking.html?tracking-id=${q}`;
  if (/old dominion|odfl/.test(c)) return `https://www.odfl.com/us/en/tools/trace-track-ltl-freight.html?proNumbers=${q}`;
  if (/estes/.test(c)) return `https://www.estes-express.com/myestes/shipment-tracking/?type=PRO&query=${q}`;
  if (/xpo/.test(c)) return `https://track.xpo.com/?pro=${q}`;
  if (/saia/.test(c)) return `https://www.saia.com/track?pro=${q}`;
  if (/r\+l|r&l|rl carriers/.test(c)) return `https://www2.rlcarriers.com/freight/shipping/shipment-tracing?pro=${q}`;
  return `https://www.google.com/search?q=${encodeURIComponent((carrier ? carrier + " " : "") + "tracking " + n)}`;
}
function trackingLink(carrier, number) {
  const url = trackingUrl(carrier, number);
  return url ? `<a class="link" href="${url}" target="_blank" rel="noopener" title="Track with ${escapeHtml(carrier || "the carrier")}">${escapeHtml(number)} ↗</a>` : "";
}

// ---- Ctrl+S / Cmd+S: press the Save button of what you're working in (a pop-up first, else the card you're in) ----
document.addEventListener("keydown", e => {
  if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "s" || e.shiftKey || e.altKey) return;
  e.preventDefault();
  const visible = b => b.offsetParent !== null && !b.disabled;
  const isSave = b => /^\s*(save|save changes|save order details|save cut-off|save time zone)\b/i.test(b.textContent || "") || b.hasAttribute("data-save");
  const scopes = [];
  const popup = [...document.querySelectorAll(".qf-backdrop, .modal, dialog[open]")].filter(el => el.offsetParent !== null || el.open).pop();
  if (popup) scopes.push(popup);
  const here = document.activeElement && document.activeElement.closest(".card, .td-edit, .sticky-note, .qf-box");
  if (here) scopes.push(here);
  for (const scope of scopes) {
    const btn = [...scope.querySelectorAll("button")].find(b => visible(b) && isSave(b));
    if (btn) { btn.click(); return; }
  }
  const all = [...document.querySelectorAll("main button")].filter(b => visible(b) && isSave(b));
  if (all.length === 1) { all[0].click(); return; }
  toast(all.length ? "Click into the part you're editing, then press Ctrl+S" : "Nothing to save here");
});

// ---- Quick Insights: one pop-up on Customer Orders, Shipments, Invoices and Purchase Orders ----
// The money state of sales, shipping, billing and purchasing (GET /api/insights/). The screen it's opened from
// comes first and is highlighted; each card's bar shows how its headline splits up.
async function openQuickInsights(focus) {
  const back = document.createElement("div");
  back.className = "modal-backdrop";
  back.innerHTML = `<div class="modal qi-modal" role="dialog" aria-modal="true" aria-labelledby="qi-title">
    <div class="qi-head"><h3 id="qi-title">${icon("chart")} Quick Insights</h3><span class="muted small" id="qi-asof"></span>
      <span style="flex:1;"></span><button type="button" class="icon-btn" aria-label="Close" data-close>${icon("x")}</button></div>
    <div class="qi-grid"><div class="muted">Loading…</div></div></div>`;
  const close = () => { document.removeEventListener("keydown", onKey); back.remove(); };
  const onKey = e => { if (e.key === "Escape") close(); };
  back.addEventListener("click", e => { if (e.target === back || e.target.closest("[data-close]")) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(back);
  let data;
  try { data = await apiFetch("/api/insights/"); }
  catch (e) { back.querySelector(".qi-grid").innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`; return; }
  back.querySelector("#qi-asof").textContent = `as of ${fmtDate(data.as_of)} · open work right now`;
  const secs = [...data.sections].sort((a, b) => (b.key === focus) - (a.key === focus));
  const row = f => {
    const inner = `<span class="qi-label"><span class="qi-name">${escapeHtml(f.label)}</span>${f.hint ? `<span class="muted small">${escapeHtml(f.hint)}</span>` : ""}</span>
      <span class="qi-count muted small">${f.count != null ? f.count : ""}</span><span class="qi-amt ${f.tone ? "qi-" + f.tone : ""}">${fmtMoney(f.amount)}</span>`;
    return f.link ? `<a class="qi-row qi-link" href="${f.link}" title="Open">${inner}</a>` : `<div class="qi-row">${inner}</div>`;
  };
  back.querySelector(".qi-grid").innerHTML = secs.map(s => {
    const parts = s.bar.map(k => s.figures.find(f => f.key === k)).filter(f => f && f.amount > 0.005);
    const whole = parts.reduce((t, f) => t + f.amount, 0);
    const bar = whole > 0.005 ? `<div class="qi-bar">${parts.map(f => `<i class="qi-${f.tone || "muted"}" style="flex:${f.amount}" title="${escapeHtml(f.label)}: ${fmtMoney(f.amount)} (${Math.round(f.amount / whole * 100)}%)"></i>`).join("")}</div>` : "";
    return `<section class="qi-card ${s.key === focus ? "qi-focus" : ""}">
      <div class="qi-card-head"><a class="link" href="${s.page}">${escapeHtml(s.title)}</a></div>
      <div class="qi-headline"><span class="qi-big">${fmtMoney(s.headline.amount)}</span>
        <span class="muted small"><span class="qi-name">${escapeHtml(s.headline.label)}</span>${s.headline.count != null ? ` · ${s.headline.count}` : ""}${s.headline.hint ? ` — ${escapeHtml(s.headline.hint)}` : ""}</span></div>
      ${bar}
      <div class="qi-rows">${s.figures.map(row).join("")}</div>
      ${s.after && s.after.length ? `<div class="qi-rows qi-after">${s.after.map(row).join("")}</div>` : ""}
    </section>`;
  }).join("") || `<div class="muted">Nothing to show for your role.</div>`;
}

// ---- Download a backup to this computer (permission "backups.download"; every 3 days it's required) ----
// openLocalBackup() from Backups, or forced on login by LocalBackup.check() when one is due. The browser's Save As
// picker (Edge / Chrome) lets the person choose where it goes; elsewhere it lands in Downloads.
const LocalBackup = {
  async check() {
    if (!AuthGuard.can("backups.download") || localStorage.getItem("at_hub_view_as_back")) return;
    const u = AuthGuard.getUser();
    if (!u || u.must_change_password) return;
    try {
      const s = await apiFetch("/api/local-backup/status");
      if (s.due) openLocalBackup({ forced: true, status: s });
    } catch {}
  },
};

async function openLocalBackup({ forced = false, status = null } = {}) {
  if (document.getElementById("lb-back")) return;
  const s = status || await apiFetch("/api/local-backup/status");
  const mb = n => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
  const last = s.last ? `Last downloaded ${fmtWhen(s.last.at)} by ${escapeHtml(s.last.by)}${s.last.files ? "" : " (database only)"} — ${s.days_since} day${s.days_since === 1 ? "" : "s"} ago.`
    : "No backup has been downloaded to a computer yet.";
  const back = document.createElement("div");
  back.className = "modal-backdrop";
  back.id = "lb-back";
  back.innerHTML = `<div class="modal lb-modal" role="dialog" aria-modal="true" aria-labelledby="lb-title">
    <div class="lb-head"><h3 id="lb-title">${icon("download")} ${forced ? "Time To Download A Backup" : "Download A Backup"}</h3>
      ${forced ? "" : `<button type="button" class="icon-btn" aria-label="Close" data-close>${icon("x")}</button>`}</div>
    ${forced ? `<p class="lb-due"><strong>It's been ${s.last ? `${Math.floor(s.days_since)} days` : "a while"}</strong> since a copy of AT-HUB was saved to a computer.
      Take one now — it only takes a moment — then carry on.</p>` : ""}
    <p class="muted small" style="margin:0 0 12px;">${last} A copy is due every ${s.due_days} days.</p>
    <label class="lb-choice"><input type="radio" name="lb-kind" value="1" checked>
      <span><strong>Everything</strong> <span class="muted small">— database + every attached file (POs, invoices, MTRs, delivery photos) · about ${mb(s.db_bytes / 4 + s.files_bytes)}</span></span></label>
    <label class="lb-choice"><input type="radio" name="lb-kind" value="0">
      <span><strong>Database only</strong> <span class="muted small">— all records, no attached files · about ${mb(s.db_bytes / 4)}</span></span></label>
    <p class="muted small" style="margin:10px 0 0;">Save it somewhere safe and private (an encrypted USB stick, a company drive) — it holds all of the company's records.</p>
    <div class="lb-progress" hidden><div class="lb-bar"><i></i></div><span class="small muted lb-pct"></span></div>
    <div class="error lb-err"></div>
    <div class="lb-foot">
      <button type="button" class="lb-skip secondary" hidden>Continue Without A Backup (This Time)</button>
      ${forced ? "" : `<button type="button" class="secondary" data-close>Cancel</button>`}
      <button type="button" class="lb-go">${icon("download")} Download Backup</button>
    </div></div>`;
  document.body.appendChild(back);
  const close = () => { document.removeEventListener("keydown", onKey); back.remove(); };
  const onKey = e => { if (e.key === "Escape" && !forced) close(); };
  document.addEventListener("keydown", onKey);
  back.addEventListener("click", e => { if (!forced && (e.target === back || e.target.closest("[data-close]"))) close(); });
  const go = back.querySelector(".lb-go"), err = back.querySelector(".lb-err"), prog = back.querySelector(".lb-progress");
  back.querySelector(".lb-skip").addEventListener("click", close);
  go.addEventListener("click", async () => {
    const files = back.querySelector('input[name="lb-kind"]:checked').value === "1";
    const name = `AT-HUB-backup-${todayISO()}-${files ? "everything" : "database"}.zip`;
    err.textContent = "";
    let handle = null;
    if (window.showSaveFilePicker) {  // ask where to save, before the download starts
      try { handle = await window.showSaveFilePicker({ suggestedName: name, types: [{ description: "AT-HUB backup", accept: { "application/zip": [".zip"] } }] }); }
      catch (e) { if (e.name === "AbortError") return; handle = null; }
    }
    go.disabled = true;
    go.innerHTML = `${icon("download")} Preparing…`;
    prog.hidden = false;
    const bar = back.querySelector(".lb-bar i"), pct = back.querySelector(".lb-pct");
    try {
      const res = await fetch(`/api/local-backup/download?files=${files}`, { headers: { Authorization: `Bearer ${AuthGuard.getToken()}` } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `The server said ${res.status}`);
      const total = parseInt(res.headers.get("X-Backup-Size") || res.headers.get("Content-Length") || "0", 10);
      const reader = res.body.getReader(), chunks = [];
      const writable = handle ? await handle.createWritable() : null;
      let got = 0;
      go.innerHTML = `${icon("download")} Downloading…`;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        got += value.length;
        if (writable) await writable.write(value); else chunks.push(value);
        if (total) { bar.style.width = `${Math.min(100, got / total * 100)}%`; pct.textContent = `${mb(got)} of ${mb(total)}`; }
      }
      if (writable) await writable.close();
      else {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob(chunks, { type: "application/zip" }));
        a.download = name;
        document.body.appendChild(a);  // a detached link's download can be cancelled by the browser
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 120000);
      }
      // only a finished save resets the 3-day clock
      await apiFetch("/api/local-backup/saved", { method: "POST", body: JSON.stringify({ files, size: got }) });
      close();
      toast(`Backup saved${handle ? ` as ${handle.name}` : " to your Downloads folder"} (${mb(got)}). Next one due in ${s.due_days} days.`);
    } catch (e) {
      err.textContent = `The download didn't finish: ${e.message}. Try again${forced ? ", or continue for now and try later" : ""}.`;
      go.disabled = false;
      go.innerHTML = `${icon("download")} Try Again`;
      prog.hidden = true;
      if (forced) back.querySelector(".lb-skip").hidden = false;  // never locked out of AT-HUB by a failed download
    }
  });
}

// ---- Print Options: the pop-up behind every PDF / Print button. What's ticked is remembered per person per document
// type on the server (app/services/print_options.py), so emails and Bulk Operations print the same way. ----
const PrintOptions = {
  data: null,
  async load(force = false) {
    if (this.data && !force) return this.data;
    try { this.data = await apiFetch("/api/auth/print-options"); } catch (e) { this.data = { docs: {}, other: {} }; }
    return this.data;
  },
  async get(docType) {  // {key: bool} as last saved
    const d = await this.load();
    return Object.fromEntries((d.docs[docType] || []).map(o => [o.key, o.value]));
  },
  query(opts) { return Object.entries(opts).map(([k, v]) => `${k}=${v ? "true" : "false"}`).join("&"); },
  async save(docType, opts) {
    try {
      await apiFetch(`/api/auth/print-options/${docType}`, { method: "PUT", body: JSON.stringify(opts) });
      const d = await this.load();
      if (d.docs[docType]) d.docs[docType].forEach(o => { if (o.key in opts) o.value = !!opts[o.key]; });
      else d.other[docType] = opts;
    } catch (e) { /* remembering is a nicety; the print still goes ahead */ }
  },
  // Ask what to print. buttons: [{label, value}] (default one "Print"); disabled: {key: "why"} greys an option out.
  // Resolves {action, opts} or null when cancelled. The choices are saved as the new default.
  async ask(docType, { title = "Print Options", note = "", buttons = null, disabled = {} } = {}) {
    const d = await this.load();
    const rows = d.docs[docType] || [];
    const btns = buttons || [{ label: "Print", value: "print" }];
    const { value, el } = await askDialog({ title,
      body: `${note ? `<p class="muted small" style="margin-top:0;">${note}</p>` : ""}
        <div class="print-opts po-list">${rows.map(o => `<label class="check-label po-opt" title="${escapeHtml(disabled[o.key] || o.hint || "")}">
          <input type="checkbox" data-k="${o.key}" ${o.value ? "checked" : ""} ${disabled[o.key] ? "disabled" : ""}>
          <span>${escapeHtml(o.label)}<span class="muted small po-hint">${escapeHtml(disabled[o.key] || o.hint || "")}</span></span></label>`).join("")}</div>
        <p class="muted small" style="margin-bottom:0;">Remembered for next time — emailed and bulk-printed copies use the same choices.</p>`,
      buttons: [...btns.map((b, i) => ({ label: b.label, value: b.value, cls: i === 0 ? "confirm-btn" : "secondary" })), { label: "Cancel", value: null, cls: "secondary" }] });
    if (!value) return null;
    const opts = Object.fromEntries([...el.querySelectorAll("[data-k]")].map(c => [c.dataset.k, c.checked]));
    await this.save(docType, opts);
    return { action: value, opts };
  },
};

// ---- Line notes: "Print All" ticks / unticks every line's own Print box in that table (a select-all), and the
// Hide / Show Notes button in the top bar folds every note away on every screen (remembered on this computer). ----
function printAllNotesHtml() {
  return `<label class="check-label ln-print-all-label no-print" title="Tick or untick Print on every line's note at once (saved with the record)">
    <input type="checkbox" class="ln-print-all"> Print All Line Notes</label>`;
}
function syncPrintAll(root = document) {
  root.querySelectorAll(".ln-print-all").forEach(master => {
    const scope = master.closest("section, .dsec, .card, .modal, form") || document;
    const boxes = [...scope.querySelectorAll(".ln-print")];
    const on = boxes.filter(b => b.checked).length;
    master.checked = boxes.length > 0 && on === boxes.length;
    master.indeterminate = on > 0 && on < boxes.length;
    master.disabled = !boxes.length;
    const label = master.closest("label");
    if (label) label.style.display = boxes.length ? "" : "none";  // no editable notes here: nothing to tick
  });
}
document.addEventListener("change", e => {
  const t = e.target;
  if (!t.classList) return;
  if (t.classList.contains("ln-print-all")) {
    const scope = t.closest("section, .dsec, .card, .modal, form") || document;
    scope.querySelectorAll(".ln-print").forEach(b => { if (b.checked !== t.checked) { b.checked = t.checked; b.dispatchEvent(new Event("input", { bubbles: true })); } });
    t.indeterminate = false;
  } else if (t.classList.contains("ln-print")) syncPrintAll();
});
// a list redrawn with new lines: keep the master in step
new MutationObserver(() => { if (document.querySelector(".ln-print-all")) requestAnimationFrame(() => syncPrintAll()); })
  .observe(document.documentElement, { childList: true, subtree: true });

const NotesView = {
  KEY: "at_hub_notes_hidden",
  hidden() { try { return localStorage.getItem(this.KEY) === "1"; } catch (e) { return false; } },
  apply() {
    document.body.classList.toggle("notes-hidden", this.hidden());
    const b = document.getElementById("tb-notes");
    if (b) {
      b.classList.toggle("on", this.hidden());
      b.title = this.hidden() ? "Notes are hidden on every screen -- click to show them" : "Hide every note (line notes, sticky notes) on every screen";
      b.innerHTML = icon(this.hidden() ? "eyeOff" : "notePen");
    }
  },
  toggle() {
    try { localStorage.setItem(this.KEY, this.hidden() ? "0" : "1"); } catch (e) { /* stays as it is */ }
    this.apply();
    toast(this.hidden() ? "Notes hidden on every screen" : "Notes shown");
  },
};
document.addEventListener("DOMContentLoaded", () => NotesView.apply());

// ---- Who am I: round badge with initials at the right of the top bar; click for name, role, time zone,
// My Account and Sign Out. The colour follows the role. ----
const AccountMenu = {
  COLORS: { super_admin: "#7c3aed", admin: "#2563eb", manager: "#0d9488", employee: "#ea580c" },
  initials(u) {
    const name = (u.full_name || u.username || "?").trim();
    const w = name.split(/\s+/).filter(Boolean);
    return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : (w[0] || "")[1] || "")).toUpperCase();
  },
  html() {
    const u = AuthGuard.getUser();
    if (!u) return "";
    const first = (u.full_name || u.username).trim().split(/\s+/)[0];
    const role = u.role_name || (typeof ROLE_LABELS !== "undefined" && ROLE_LABELS[u.role]) || u.role;
    return `<button type="button" class="tb-account" onclick="AccountMenu.toggle(event)" title="${escapeHtml(u.full_name || u.username)} · ${escapeHtml(role)}" aria-haspopup="menu">
      <span class="tb-avatar" style="background:${this.COLORS[u.role] || "#475569"}">${escapeHtml(this.initials(u))}</span>
      <span class="tb-account-text"><span class="tb-account-name">${escapeHtml(first)}</span><span class="tb-account-role">${escapeHtml(role)}</span></span></button>
      <div class="tb-menu-pop" id="tb-account-pop" hidden role="menu">
        <div class="tb-menu-who"><span class="tb-avatar big" style="background:${this.COLORS[u.role] || "#475569"}">${escapeHtml(this.initials(u))}</span>
          <div><strong>${escapeHtml(u.full_name || u.username)}</strong><div class="muted small">${escapeHtml(u.username)} · ${escapeHtml(role)}</div>
          <div class="muted small">Time zone: ${escapeHtml(userTz())}</div></div></div>
        <a href="account.html" role="menuitem">${icon("user")} My Account</a>
        <a href="#" role="menuitem" onclick="openShortcuts(); return false;">${icon("info")} Keyboard Shortcuts <kbd>?</kbd></a>
        <a href="#" role="menuitem" onclick="AuthGuard.logout(); return false;">${icon("logout")} Sign Out</a>
      </div>`;
  },
  toggle(e) {
    e.stopPropagation();
    const pop = document.getElementById("tb-account-pop");
    if (!pop) return;
    Recent.close();
    pop.hidden = !pop.hidden;
  },
  close() { const pop = document.getElementById("tb-account-pop"); if (pop) pop.hidden = true; },
};
document.addEventListener("click", e => {
  if (!e.target.closest("#tb-account-pop") && !e.target.closest(".tb-account")) AccountMenu.close();
  if (!e.target.closest("#tb-recent-pop") && !e.target.closest("#tb-recent")) Recent.close();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") { AccountMenu.close(); Recent.close(); } });

// ---- Recently Viewed: every record opened (any record page) goes to the top of this person's list, kept on the
// server so it follows them between computers. Top bar clock button + the empty Ctrl+K search. ----
const Recent = {
  rows: null,
  KIND: { "customer-orders.html": "Order", "purchase-orders.html": "PO", "invoices.html": "Invoice", "shipments.html": "Shipment",
          "item.html": "Item", "customers.html": "Customer", "vendors.html": "Vendor", "landed-costs.html": "Landed Cost", "stock-items.html": "Item" },
  async load() {
    if (this.rows) return this.rows;
    try { this.rows = await apiFetch("/api/auth/recent"); } catch (e) { this.rows = []; }
    return this.rows;
  },
  // called when a record page shows a record: label = its heading (first line), read once it has drawn
  track(id) {
    const page = location.pathname.split("/").pop(), kind = this.KIND[page];
    if (!kind || !id) return;
    setTimeout(async () => {
      const head = ["#detail-card h3", "#detail-card h2", "#cc-card h2", "main h1.page-title"].map(q => document.querySelector(q)).find(Boolean);
      let label = head ? head.textContent.replace(/\s+/g, " ").trim() : `${kind} ${id}`;
      label = label.replace(/\b(draft|sent|paid|void|confirmed|shipped|invoiced|cancelled|open|ordered|received)\b.*$/i, "").trim().slice(0, 80) || `${kind} ${id}`;
      try { this.rows = await apiFetch("/api/auth/recent", { method: "POST", body: JSON.stringify({ kind, id, label, url: `${page}?id=${id}` }) }); }
      catch (e) { /* a nicety */ }
    }, 900);
  },
  async toggle(e) {
    e.stopPropagation();
    AccountMenu.close();
    const pop = document.getElementById("tb-recent-pop");
    if (!pop) return;
    if (!pop.hidden) { pop.hidden = true; return; }
    this.rows = null;
    const rows = await this.load();
    pop.innerHTML = `<div class="tb-menu-head">Recently Viewed</div>${rows.length ? rows.map(r => `<a href="${escapeHtml(r.url)}" role="menuitem">
        <span class="qf-kind">${escapeHtml(r.kind)}</span><span class="tb-recent-label">${escapeHtml(r.label)}</span></a>`).join("")
      : `<div class="muted small" style="padding:8px 12px;">Records you open show up here.</div>`}`;
    pop.hidden = false;
  },
  close() { const pop = document.getElementById("tb-recent-pop"); if (pop) pop.hidden = true; },
};

// ---- Saved filters: name what the list is showing (search box + the screen's filters) and bring it back in one
// click. Per person, per screen, on the server. A page can add its own state with savedFilterState / applySavedFilter. ----
const SavedFilters = {
  page: null, rows: [],
  stateNow() {
    const box = document.querySelector(".page-toolbar") || document;
    const fields = {};
    box.querySelectorAll("input[id], select[id]").forEach(el => {
      if (el.type === "file" || el.type === "button") return;
      fields[el.id] = el.type === "checkbox" ? el.checked : el.value;
    });
    return { fields, extra: typeof window.savedFilterState === "function" ? window.savedFilterState() : null };
  },
  apply(state) {
    Object.entries(state.fields || {}).forEach(([id, v]) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!v; else el.value = v;
      el.dispatchEvent(new Event(el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input", { bubbles: true }));
    });
    if (state.extra != null && typeof window.applySavedFilter === "function") window.applySavedFilter(state.extra);
  },
  async mount() {
    const search = document.querySelector(".page-toolbar #search");
    if (!search || document.getElementById("sf-btn")) return;
    this.page = location.pathname.split("/").pop().replace(/\.html$/, "");
    const wrap = document.createElement("span");
    wrap.className = "sf-wrap";
    wrap.innerHTML = `<button type="button" class="secondary sf-btn" id="sf-btn" title="Saved filters: name what this list shows now and bring it back in one click">${icon("sliders")}<span>Saved</span></button>
      <div class="tb-menu-pop sf-pop" id="sf-pop" hidden></div>`;
    search.after(wrap);
    wrap.querySelector("#sf-btn").addEventListener("click", e => { e.stopPropagation(); this.open(); });
    document.addEventListener("click", e => { if (!e.target.closest(".sf-wrap")) { const p = document.getElementById("sf-pop"); if (p) p.hidden = true; } });
  },
  async open() {
    const pop = document.getElementById("sf-pop");
    if (!pop.hidden) { pop.hidden = true; return; }
    try { this.rows = await apiFetch(`/api/auth/filters/${this.page}`); } catch (e) { this.rows = []; }
    pop.innerHTML = `<div class="tb-menu-head">Saved Filters</div>
      ${this.rows.map((r, i) => `<div class="sf-row"><a href="#" onclick="SavedFilters.use(${i}); return false;">${escapeHtml(r.name)}</a>
        <button type="button" class="icon-btn" title="Forget this filter" onclick="SavedFilters.remove(${i})">${icon("x")}</button></div>`).join("")
        || `<div class="muted small" style="padding:6px 12px;">None yet.</div>`}
      <div class="sf-add"><a href="#" onclick="SavedFilters.add(); return false;">${icon("plus")} Save What's Showing Now…</a></div>`;
    pop.hidden = false;
  },
  use(i) { const r = this.rows[i]; if (r) { this.apply(r.state); toast(`Filter: ${r.name}`); } document.getElementById("sf-pop").hidden = true; },
  async add() {
    const name = prompt("Name this filter", (document.querySelector(".page-toolbar #search") || {}).value || "");
    if (!name || !name.trim()) return;
    const rows = this.rows.filter(r => r.name.toLowerCase() !== name.trim().toLowerCase()).concat([{ name: name.trim(), state: this.stateNow() }]);
    try { this.rows = await apiFetch(`/api/auth/filters/${this.page}`, { method: "PUT", body: JSON.stringify(rows) }); toast(`Saved “${name.trim()}”`); }
    catch (e) { toast(e.message); }
    document.getElementById("sf-pop").hidden = true;
  },
  async remove(i) {
    const rows = this.rows.filter((_, n) => n !== i);
    try { this.rows = await apiFetch(`/api/auth/filters/${this.page}`, { method: "PUT", body: JSON.stringify(rows) }); } catch (e) { return toast(e.message); }
    document.getElementById("sf-pop").hidden = true;
    this.open();
  },
};
document.addEventListener("DOMContentLoaded", () => { if (AuthGuard.getToken()) SavedFilters.mount(); });

// ---- "+ New" opens its own screen (like a new quote): the form alone, at the top, with a Back bar and its own
// address (?creating=1) -- the list and any open record step aside. Leaving with unsaved typing asks first. ----
(function newRecordPages() {
  const FORMS = { "customer-orders.html": "form-card", "purchase-orders.html": "form-card", "stock-items.html": "item-form-card",
                  "landed-costs.html": "form-card", "users.html": "form-card" };
  document.addEventListener("DOMContentLoaded", () => {
    const formId = FORMS[location.pathname.split("/").pop()];
    const form = formId && document.getElementById(formId);
    const main = form && form.closest("main");
    if (!form || !main || form.parentElement !== main) return;
    const listName = (document.title.split("—")[1] || "List").trim();
    let open = false, dirty = false;
    form.addEventListener("input", () => { dirty = true; });
    // ?creating=1 (?new= already means "new order for this customer" on the order / PO screens)
    const urlWith = on => { const u = new URL(location.href); u.searchParams.delete("id"); u.searchParams.delete("new"); on ? u.searchParams.set("creating", "1") : u.searchParams.delete("creating"); return u.pathname + u.search; };
    const hide = () => {
      const cancel = [...form.querySelectorAll("button")].find(b => /^\s*cancel\s*$/i.test(b.textContent));
      if (cancel) cancel.click(); else form.style.display = "none";
    };
    window.closeNewRecord = async () => {
      if (dirty) {
        const { value } = await askDialog({ title: "Leave without saving?", tone: "warn", body: "<p>What you've typed on this new record will be lost.</p>",
          buttons: [{ label: "Leave", value: "go", cls: "danger" }, { label: "Keep Editing", value: null, cls: "secondary" }] });
        if (value !== "go") return;
      }
      dirty = false;
      if (history.state && history.state.newRecord) history.back(); else hide();
    };
    const sync = () => {
      const visible = form.style.display !== "none";
      if (visible && !form.querySelector(":scope > .record-backbar"))
        form.insertAdjacentHTML("afterbegin", `<div class="record-backbar no-print"><a class="link" onclick="closeNewRecord()">← Back to ${escapeHtml(listName)}</a></div>`);
      if (visible === open) return;
      open = visible;
      main.classList.toggle("new-mode", open);
      if (open) {
        dirty = false;
        const detail = document.getElementById("detail-card");
        if (detail && detail.style.display !== "none") detail.style.display = "none";  // an open record steps aside
        window.scrollTo({ top: 0 });
        if (!(history.state && history.state.newRecord)) history.pushState({ newRecord: true }, "", urlWith(true));
        const first = form.querySelector("input:not([type=hidden]):not([type=file]):not([type=checkbox]), select, textarea");
        if (first) setTimeout(() => first.focus(), 50);
      } else {
        dirty = false;
        if (history.state && history.state.newRecord) history.replaceState(null, "", urlWith(false));
      }
    };
    new MutationObserver(sync).observe(form, { attributes: true, attributeFilter: ["style"] });
    window.addEventListener("popstate", e => { if (open && !(e.state && e.state.newRecord)) { dirty = false; hide(); } });
    sync();
    if (new URL(location.href).searchParams.get("creating") && !open) {  // reloaded on the new-record screen: open it again
      setTimeout(() => { const b = [...document.querySelectorAll("main button")].find(x => /^\s*\+\s*New\b/i.test(x.textContent)); if (b) b.click(); }, 400);
    }
  });
})();

// ---- Keyboard shortcuts (not while typing): N new record, / search, E edit, ? this list. Ctrl+S saves, Ctrl+K finds. ----
function openShortcuts() {
  AccountMenu.close();
  askDialog({ title: "Keyboard Shortcuts", body: `<table class="fit-table no-table-tools kb-table"><tbody>
      ${[["N", "New record on this screen (order, PO, item…)"], ["/", "Jump to this screen's search box"], ["E", "Edit the record on screen"],
         ["Ctrl + S", "Save what you're working on"], ["Ctrl + K", "Find anything"], ["Esc", "Close a pop-up / menu"], ["?", "This list"]]
        .map(([k, what]) => `<tr><td class="nowrap"><kbd>${k}</kbd></td><td class="grow">${what}</td></tr>`).join("")}</tbody></table>`,
    buttons: [{ label: "Close", value: null, cls: "secondary" }] });
}
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
  const t = e.target;
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
  if (document.querySelector(".modal-backdrop, .qf-backdrop, .glass-back, .print-dialog")) return;
  const visible = el => el && el.offsetParent !== null && !el.disabled;
  const buttons = () => [...document.querySelectorAll("main button, main a.link")].filter(visible);
  if (e.key === "?") { e.preventDefault(); openShortcuts(); }
  else if (e.key === "/") {
    const s = document.querySelector(".page-toolbar #search, #search, #cc-search");
    e.preventDefault();
    if (visible(s)) s.focus(); else QuickFind.open();
  } else if (e.key === "n" || e.key === "N") {
    const b = buttons().find(x => /^\s*\+\s*New\b/i.test(x.textContent));
    if (b) { e.preventDefault(); b.click(); }
  } else if (e.key === "e" || e.key === "E") {
    const b = buttons().find(x => /^\s*(✎\s*)?Edit(\s+(Order|PO|Item|Invoice|Lines))?\s*$/i.test(x.textContent));
    if (b) { e.preventDefault(); b.click(); }
  }
});

// ---- Scanned documents: the file an AI read came from, shown beside the work (the floating file window) before it's
// saved anywhere. scannedDocHtml() is the small card; viewScannedDoc() opens it on the right, Enlarge fills the window. ----
let scannedDocSeq = 0;
function registerScannedDoc(file) {
  if (!file) return null;
  if (file._viewId) return file._viewId;
  const id = `scan-${++scannedDocSeq}`;
  attachmentBlobUrls[id] = URL.createObjectURL(file);
  attachmentTypes[id] = file.type || (/\.pdf$/i.test(file.name) ? "application/pdf" : "");
  attachmentNames[id] = file.name;
  file._viewId = id;
  return id;
}
function viewScannedDoc(idOrFile) {
  const id = typeof idOrFile === "string" || typeof idOrFile === "number" ? idOrFile : registerScannedDoc(idOrFile);
  if (id) fileViewer.show(id, [id]);
}
// The card: a live thumbnail of page 1, the file name, and what happens to it. attachNote: e.g. "Attached to the order when you create it".
function scannedDocHtml(file, attachNote = "") {
  const id = registerScannedDoc(file);
  if (!id) return "";
  const isPdf = attachmentTypes[id] === "application/pdf";
  return `<div class="scan-doc" title="Open it beside the form — drag or resize the window, or Enlarge it">
      <div class="scan-doc-thumb" onclick="viewScannedDoc('${id}')">${isPdf ? `<iframe src="${attachmentBlobUrls[id]}#toolbar=0&navpanes=0&view=FitH" tabindex="-1" title="Preview"></iframe>`
        : (attachmentTypes[id] || "").startsWith("image/") ? `<img src="${attachmentBlobUrls[id]}" alt="">`
        : `<span class="desk-badge other">${escapeHtml((file.name.split(".").pop() || "file").slice(0, 4).toUpperCase())}</span>`}<span class="scan-doc-cover"></span></div>
      <div class="scan-doc-info"><strong title="${escapeHtml(file.name)}">${icon("paperclip")} ${escapeHtml(file.name)}</strong>
        ${attachNote ? `<span class="muted small">${escapeHtml(attachNote)}</span>` : ""}
        <button type="button" class="small-btn secondary" onclick="viewScannedDoc('${id}')">${icon("eye")} View Beside</button></div></div>`;
}

// ---- Quick Add: one line of a scan that isn't an item we know -- create it, or say which item we have it is. ----
// opts: { side: "customer"|"vendor", said, code, title, category, price, groups, items, candidates:[{item_id, code, title, score}],
//         looksLike:{code, score}, note } -> resolves { item, created } or null
async function quickAddItem(opts) {
  const groups = opts.groups || [], all = opts.items || [];
  const canCreate = AuthGuard.can("stock.edit");
  const near = (opts.candidates || []).filter(c => c.item_id);
  const guess = opts.looksLike ? all.find(i => i.code === opts.looksLike.code) : null;
  const pickId = guess ? guess.id : near[0] && near[0].score >= 0.85 ? near[0].item_id : "";
  const priceLabel = opts.side === "vendor" ? "Our Cost" : "Selling Price";
  let error = "", form = { code: opts.code || "", title: opts.title || "", category: opts.category || "", price: opts.price ?? 0, use: pickId };
  while (true) {
    const { value, el } = await askDialog({ title: opts.heading || "Add This Item", wide: true,
      body: `${opts.said ? `<div class="qa-said"><span class="muted small">The ${opts.side === "vendor" ? "vendor's document" : "PO"} says</span><div>${escapeHtml(opts.said)}</div></div>` : ""}
        ${opts.note ? `<p class="warn-text small" style="margin:4px 0 0;">${escapeHtml(opts.note)}</p>` : ""}
        <div class="qa-grid">
          <section class="qa-col"><h4>${icon("plus")} New Item</h4>
            ${canCreate ? `<label>Item #</label><input type="text" class="qa-code" data-autocorrect="item-code" value="${escapeHtml(form.code)}">
              <label>Title</label><input type="text" class="qa-title" value="${escapeHtml(form.title)}">
              <div class="row"><div><label>Group</label><select class="qa-group"><option value="">— pick —</option>${groups.map(g => `<option ${g === form.category ? "selected" : ""}>${escapeHtml(g)}</option>`).join("")}</select></div>
                <div><label>${priceLabel}</label><span class="price-input"><span>$</span><input type="number" class="qa-price" step="0.00001" min="0" value="${form.price || 0}"></span></div></div>`
              : `<p class="muted small">Your role can't add stock items — pick the item we have instead, or ask a manager.</p>`}
          </section>
          <section class="qa-col"><h4>${icon("link")} An Item We Have</h4>
            <label>Same item as</label><select class="qa-use" data-searchable><option value="">— pick our item —</option>${all.map(i => `<option value="${i.id}" ${String(i.id) === String(form.use) ? "selected" : ""}>${escapeHtml(i.code)} — ${escapeHtml(i.title)}</option>`).join("")}</select>
            ${near.length ? `<div class="qa-near"><span class="muted small">Closest we have:</span>${near.slice(0, 4).map(c => `<a class="qa-chip" onclick="const s = this.closest('.qa-col').querySelector('.qa-use'); s.value = '${c.item_id}'; s.dispatchEvent(new Event('change', { bubbles: true }));">
                <b>${escapeHtml(c.code)}</b> ${escapeHtml((c.title || "").slice(0, 34))} <i>${Math.round((c.score || 0) * 100)}%</i></a>`).join("")}</div>` : ""}
            <p class="muted small">Picked here, the ${opts.side === "vendor" ? "vendor's part #" : "customer's wording"} is remembered, so the next read matches it by itself.</p>
          </section></div>
        ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}`,
      buttons: [...(canCreate ? [{ label: "Create New Item", value: "create", cls: "confirm-btn" }] : []), { label: "Use Item We Have", value: "use", cls: canCreate ? "secondary" : "confirm-btn" },
                { label: "Cancel", value: null, cls: "secondary" }] });
    if (!value) return null;
    if (canCreate) form = { code: el.querySelector(".qa-code").value.trim(), title: el.querySelector(".qa-title").value.trim(), category: el.querySelector(".qa-group").value,
                            price: parseFloat(el.querySelector(".qa-price").value) || 0, use: el.querySelector(".qa-use").value };
    else form.use = el.querySelector(".qa-use").value;
    if (value === "use") {
      const item = all.find(i => String(i.id) === String(form.use));
      if (!item) { error = "Pick the item we have on the right."; continue; }
      return { item, created: false };
    }
    if (!form.code || !form.title) { error = "Give the new item an item # and a title."; continue; }
    if (!form.category) { error = "Pick the new item's group."; continue; }
    try {
      const item = await apiFetch("/api/stock-items/", { method: "POST", body: JSON.stringify({ code: form.code, title: form.title, category: form.category,
        [opts.side === "vendor" ? "cost_price" : "selling_price"]: form.price, created_via: "ai-scan" }) });
      toast(`${item.code} added to Stock Items`);
      return { item, created: true };
    } catch (e) { error = e.message; }
  }
}

// ---- Contact pop-up: the round person icon beside a customer / vendor name opens their card (people, phones, emails,
// addresses) right there, with quick Add Address / Add Person. Saved to the same card as the Customers / Vendors pages. ----
const ContactPop = {
  path(kind) { return kind === "vendor" ? "vendors" : "customers"; },
  canEdit(kind) { return AuthGuard.can(kind === "vendor" ? "vendors" : "customers.edit"); },
  iconHtml(kind, idExpr, title = "") {
    return `<button type="button" class="contact-ico no-print" title="${escapeHtml(title || `Open the ${kind}'s contact card`)}" onclick="event.preventDefault(); event.stopPropagation(); ContactPop.open('${kind}', ${idExpr})">${icon("user")}</button>`;
  },
  async load(kind, id) { return apiFetch(`/api/${this.path(kind)}/${id}`); },
  async save(kind, rec, details) {
    return apiFetch(`/api/${this.path(kind)}/${rec.id}`, { method: "PUT", body: JSON.stringify({ details }) });
  },
  // Resolves the (possibly updated) record when closed.
  async open(kind, id, opts = {}) {
    if (!id) return toast(`Pick the ${kind} first`);
    let rec;
    try { rec = await this.load(kind, id); } catch (e) { return toast(e.message); }
    let mode = opts.mode || null, error = "", draft = { label: "", value: opts.prefill || "", name: "", role: "", phone: "", email: "" };
    while (true) {
      const d = rec.details || {}, edit = this.canEdit(kind);
      const rows = (list, f) => (list || []).map(f).join("") || `<div class="muted small">None on file.</div>`;
      const body = mode === "address" ? `<label>Label</label><input type="text" class="cp-label" list="cp-addr-labels" placeholder="e.g. Plant 2, Job site, Tulsa warehouse" value="${escapeHtml(draft.label)}">
            <datalist id="cp-addr-labels"><option>shipping</option><option>billing</option><option>plant</option><option>job site</option><option>warehouse</option></datalist>
            <label>Address</label><textarea class="cp-value" rows="4">${escapeHtml(draft.value)}</textarea>
            ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}`
        : mode === "person" ? `<div class="row"><div><label>Name</label><input type="text" class="cp-name" value="${escapeHtml(draft.name)}"></div>
              <div><label>Role</label><input type="text" class="cp-role" list="cp-roles" value="${escapeHtml(draft.role)}"></div></div>
            <datalist id="cp-roles"><option>buyer</option><option>accounts payable</option><option>receiving</option><option>quality</option><option>sales</option></datalist>
            <div class="row"><div><label>Phone</label><input type="text" class="cp-phone" value="${escapeHtml(draft.phone)}"></div>
              <div><label>Email</label><input type="text" class="cp-email" value="${escapeHtml(draft.email)}"></div></div>
            ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}`
        : `<div class="cp-card">
            ${rec.payment_terms ? `<div class="muted small">Terms: <strong>${escapeHtml(rec.payment_terms)}</strong>${rec.expedited ? ` · <span class="tag overdue">Expedited Shipping</span>` : ""}</div>` : ""}
            <h4>People</h4>${rows(d.people, p => `<div class="cp-row"><strong>${escapeHtml(p.name || "")}</strong> <span class="muted small">${escapeHtml(p.role || "")}</span>
              <div class="small">${[p.phone && `<a class="link" href="tel:${escapeHtml(p.phone)}">${escapeHtml(p.phone)}</a>`, p.email && `<a class="link" href="mailto:${escapeHtml(p.email)}">${escapeHtml(p.email)}</a>`].filter(Boolean).join(" · ")}</div></div>`)}
            <h4>Phones &amp; Emails</h4>${rows([...(d.phones || []).map(x => ({ ...x, t: "tel" })), ...(d.emails || []).map(x => ({ ...x, t: "mailto" }))],
              x => `<div class="cp-row"><span class="muted small cp-lab">${escapeHtml(x.label || "")}</span> <a class="link" href="${x.t}:${escapeHtml(x.value)}">${escapeHtml(x.value)}</a></div>`)}
            <h4>Addresses</h4>${rows(d.addresses, a => `<div class="cp-row"><span class="muted small cp-lab">${escapeHtml(a.label || "")}${a.value === rec.shipping_address ? " · default ship-to" : a.value === rec.address ? " · bill to" : ""}</span>
              <div class="cp-addr">${escapeHtml(a.value)}</div>${opts.onPickAddress ? `<a class="link small" data-pick="${escapeHtml(a.value)}">Use This Address</a>` : ""}</div>`)}
            ${d.notes ? `<h4>Notes</h4><div class="small">${escapeHtml(d.notes)}</div>` : ""}</div>`;
      const buttons = mode ? [{ label: mode === "address" ? "Add Address" : "Add Person", value: "save", cls: "confirm-btn" }, { label: "Back", value: "back", cls: "secondary" }]
        : [...(edit ? [{ label: "+ Address", value: "address", cls: "secondary" }, { label: "+ Person", value: "person", cls: "secondary" }] : []),
           { label: "Open Full Card", value: "full", cls: "secondary" }, { label: "Close", value: null, cls: "secondary" }];
      const pending = askDialog({ title: mode === "address" ? `New Address For ${rec.name}` : mode === "person" ? `New Person At ${rec.name}` : rec.name, body, buttons, wide: !mode });
      // "Use This Address" links close the pop-up with that address
      setTimeout(() => document.querySelectorAll(".ask-dialog [data-pick]").forEach(a => a.addEventListener("click", () => {
        opts.onPickAddress(a.dataset.pick);
        a.closest(".modal-backdrop").querySelector("button[data-i]:last-child").click();
      })), 0);
      const { value, el } = await pending;
      if (!value) return rec;
      if (value === "full") { window.open(`${kind === "vendor" ? "vendors" : "customers"}.html?id=${rec.id}`, "_blank"); continue; }
      if (value === "back") { mode = null; error = ""; continue; }
      if (value === "address" || value === "person") { mode = value; error = ""; continue; }
      // save
      const det = JSON.parse(JSON.stringify(d));
      if (mode === "address") {
        draft.label = el.querySelector(".cp-label").value.trim(); draft.value = el.querySelector(".cp-value").value.trim();
        if (!draft.label || !draft.value) { error = "Give the address a label and the address itself."; continue; }
        det.addresses = [...(det.addresses || []), { label: draft.label, value: draft.value }];
      } else {
        ["name", "role", "phone", "email"].forEach(k => { draft[k] = el.querySelector(`.cp-${k}`).value.trim(); });
        if (!draft.name) { error = "Give the person a name."; continue; }
        det.people = [...(det.people || []), { name: draft.name, role: draft.role, phone: draft.phone, email: draft.email }];
      }
      try {
        rec = await this.save(kind, rec, det);
        toast(mode === "address" ? `Address added to ${rec.name}` : `${draft.name} added to ${rec.name}`);
        if (mode === "address" && opts.onAddressAdded) opts.onAddressAdded(rec, draft.value);
        if (opts.closeAfterSave) return rec;
        mode = null; error = ""; draft = { label: "", value: "", name: "", role: "", phone: "", email: "" };
      } catch (e) { error = e.message; }
    }
  },
};

// ---- Ship-to: pick one of the customer's addresses on file (default first), or type one. A typed address that isn't on
// the card is offered to the card when the order is saved (shipToCheck), so it's there next time. ----
const normAddr = v => (v || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
function customerAddresses(c) {
  if (!c) return [];
  const list = ((c.details || {}).addresses || []).filter(a => (a.value || "").trim());
  const def = c.shipping_address || c.address;
  return list.slice().sort((a, b) => (b.value === def) - (a.value === def));
}
// The little picker under a ship-to box. textareaId: the box; customerFn: () => the customer object.
async function pickShipTo(textareaId, customerFn) {
  const c = customerFn(), box = document.getElementById(textareaId);
  if (!c) return toast("Pick the customer first");
  const list = customerAddresses(c), def = c.shipping_address || c.address;
  const { value, el } = await askDialog({ title: `Ship To — ${c.name}`,
    body: list.length ? `<div class="sp-list">${list.map((a, i) => `<label class="sp-opt"><input type="radio" name="sp" value="${i}" ${normAddr(a.value) === normAddr(box.value) || (!box.value && a.value === def) ? "checked" : ""}>
        <span><strong>${escapeHtml(a.label || "address")}</strong>${a.value === def ? ` <span class="tag confirmed">Default</span>` : ""}<div class="small sp-addr">${escapeHtml(a.value)}</div></span></label>`).join("")}</div>`
      : `<p class="muted">No addresses on ${escapeHtml(c.name)}'s card yet.</p>`,
    buttons: [...(list.length ? [{ label: "Use This Address", value: "use", cls: "confirm-btn" }] : []), { label: "+ New Address", value: "new", cls: "secondary" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value === "use") {
    const pick = el.querySelector('input[name="sp"]:checked');
    if (pick) { box.value = list[+pick.value].value; box.dispatchEvent(new Event("input", { bubbles: true })); }
  } else if (value === "new") {
    await ContactPop.open("customer", c.id, { mode: "address", prefill: normAddr(box.value) && !list.some(a => normAddr(a.value) === normAddr(box.value)) ? box.value : "", closeAfterSave: true,
      onAddressAdded: (rec, v) => { box.value = v; Object.assign(c, rec); box.dispatchEvent(new Event("input", { bubbles: true })); } });
  }
}
// Before saving an order: a typed ship-to that isn't on the customer's card -> add it (with a label), or use it just this once.
async function shipToCheck(text, customer) {
  if (!customer || !normAddr(text) || customerAddresses(customer).some(a => normAddr(a.value) === normAddr(text))) return true;
  if (!AuthGuard.can("customers.edit")) return true;
  const { value, el } = await askDialog({ title: "New Ship-To Address", tone: "warn",
    body: `<p>This address isn't on <strong>${escapeHtml(customer.name)}</strong>'s contact card yet:</p><div class="sp-addr qa-said">${escapeHtml(text)}</div>
      <label>Save it to the card as</label><input type="text" class="ns-label" list="cp-addr-labels2" placeholder="e.g. Plant 2, Job site, Tulsa warehouse">
      <datalist id="cp-addr-labels2"><option>shipping</option><option>plant</option><option>job site</option><option>warehouse</option></datalist>
      <p class="muted small">Saved addresses can be picked on the next order.</p>`,
    buttons: [{ label: "Save To Card & Continue", value: "save", cls: "confirm-btn" }, { label: "Use Once, Don't Save", value: "once", cls: "secondary" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!value) return false;
  if (value === "once") return true;
  const label = el.querySelector(".ns-label").value.trim() || "shipping";
  try {
    const rec = await apiFetch(`/api/customers/${customer.id}`);
    const det = JSON.parse(JSON.stringify(rec.details || {}));
    det.addresses = [...(det.addresses || []), { label, value: text.trim() }];
    Object.assign(customer, await ContactPop.save("customer", rec, det));
    toast(`Address saved to ${customer.name} as “${label}”`);
  } catch (e) { toast(`Not saved to the card: ${e.message}`); }
  return true;
}

// ---- Tab off the last box of the last line: a new blank line (or the blank one already there), ready to type in.
// A lines table opts in with <tbody data-add-line="addLine()">. ----
document.addEventListener("keydown", e => {
  if (e.key !== "Tab" || e.shiftKey || e.ctrlKey || e.altKey) return;
  const t = e.target, body = t.closest && t.closest("tbody[data-add-line]");
  if (!body) return;
  const row = t.closest("tr");
  if (!row || row !== body.lastElementChild) return;
  const visible = el => el.offsetParent !== null && !el.disabled && el.type !== "hidden" && el.type !== "checkbox";
  const fields = [...row.querySelectorAll("input, select, textarea")].filter(visible).filter(el => !el.closest(".line-note"));
  if (!fields.length || fields[fields.length - 1] !== t) return;
  const isBlank = tr => lineRowBlank(tr);
  if (isBlank(row)) return;  // already on an empty line: Tab just moves on
  e.preventDefault();
  const blank = [...body.children].find(tr => tr !== row && isBlank(tr));
  if (!blank) Function(body.dataset.addLine)();  // the page's own add-line
  const target = blank || body.lastElementChild;
  setTimeout(() => {  // after the item box has become its search box
    const first = [...target.querySelectorAll("input, select, textarea")].filter(visible)[0];
    if (!first) return;
    first.focus();
    if (first.closest(".search-select")) first.select();  // the item search box: type over the placeholder
  }, 60);
});

// ---- Line entry like MRPeasy: a blank line always waits at the bottom of a lines table.
// Forms not saved yet (new order / PO, quote, invoice edit): <tbody data-add-line="addLine()"> -- as soon as the last
// line gets something in it, another blank one is added under it (blank lines are skipped on save).
// Saved records (order / PO screens): a <tr class="entry-row" data-commit="addLineToOrder(12)"> -- fill it in and
// press Tab off its last box (or Enter) and the line is saved, then a fresh entry row is ready. ----
function lineRowBlank(tr) {
  return !(tr.querySelector("select") || {}).value && !tr.dataset.itemId && !tr.dataset.orderLineId && !tr.dataset.srcCode
    && ![...tr.querySelectorAll('input[type="text"]')].filter(i => !i.closest(".search-select")).some(i => i.value.trim());
}
function ensureTrailingBlank(body) {
  if (!body || !body.dataset.addLine || !body.isConnected) return;
  const last = body.lastElementChild;
  if (!last || !lineRowBlank(last)) Function(body.dataset.addLine)();
}
document.addEventListener("change", e => { const b = e.target.closest && e.target.closest("tbody[data-add-line]"); if (b) setTimeout(() => ensureTrailingBlank(b), 0); });
new MutationObserver(muts => {
  for (const m of muts) for (const n of m.addedNodes) {
    if (n.nodeType !== 1) continue;
    const bodies = n.matches("tbody[data-add-line]") ? [n] : [...n.querySelectorAll("tbody[data-add-line]")];
    bodies.forEach(b => { if (!b.dataset.blankReady) { b.dataset.blankReady = "1"; setTimeout(() => ensureTrailingBlank(b), 0); } });
  }
}).observe(document.documentElement, { childList: true, subtree: true });

async function commitEntryRow(tr) {
  if (tr.dataset.busy) return;
  const filled = (tr.querySelector("select") || {}).value || [...tr.querySelectorAll('input[type="text"]')].filter(i => !i.closest(".search-select")).some(i => i.value.trim());
  if (!filled) return false;
  tr.dataset.busy = "1";
  tr.classList.add("entry-saving");
  const before = tr;
  let ok = false;
  try { ok = (await Function(`return (${tr.dataset.commit})`)()) !== false; }
  finally { delete tr.dataset.busy; tr.classList.remove("entry-saving"); }
  // cursor back in the (emptied) entry row for the next line
  setTimeout(() => {
    const row = document.querySelector("tr.entry-row") || before;
    const box = row && (row.querySelector(".search-select input") || row.querySelector("input, select"));
    if (box) { box.focus(); if (box.select) box.select(); }
  }, 30);
  return ok;
}
document.addEventListener("keydown", e => {
  const tr = e.target.closest && e.target.closest("tr.entry-row");
  if (!tr) return;
  if (e.key === "Enter" && e.target.tagName !== "TEXTAREA" && !e.target.closest(".search-select")) {
    e.preventDefault();
    commitEntryRow(tr);
    return;
  }
  if (e.key !== "Tab" || e.shiftKey) return;
  const visible = el => el.offsetParent !== null && !el.disabled && el.type !== "hidden";
  const fields = [...tr.querySelectorAll("input, select, textarea")].filter(visible);
  if (fields[fields.length - 1] !== e.target) return;
  if (!(tr.querySelector("select") || {}).value && !tr.querySelector("#new-line-vcode")?.value.trim()) return;  // nothing picked: Tab just moves on
  e.preventDefault();
  commitEntryRow(tr);
});

// ---- Dragging a line near the top or bottom of the window scrolls the page, so a line can be moved anywhere ----
(function dragAutoScroll() {
  let speed = 0, raf = null;
  const step = () => { if (speed) { window.scrollBy(0, speed); raf = requestAnimationFrame(step); } else raf = null; };
  document.addEventListener("dragover", e => {
    if (!document.querySelector(".row-dragging")) return;
    const edge = 110, y = e.clientY, h = window.innerHeight;
    speed = y < edge ? -Math.ceil((edge - y) / 6) : y > h - edge ? Math.ceil((y - (h - edge)) / 6) : 0;
    if (speed && !raf) raf = requestAnimationFrame(step);
  });
  const stop = () => { speed = 0; };
  document.addEventListener("dragend", stop);
  document.addEventListener("drop", stop);
})();

// ---- PO numbers are links, everywhere: the customer's PO # opens its order, our PO # (PO325xxx) opens the purchase order.
// Without the order / PO id the page finds it from the number (customer-orders.html?po=..., purchase-orders.html?code=...).
function custPoLink(po, orderId = null, prefix = "") {
  if (!po) return "";
  const href = orderId ? `customer-orders.html?id=${orderId}` : `customer-orders.html?po=${encodeURIComponent(po)}`;
  return AuthGuard.can("orders.view") ? `${prefix}<a class="link" href="${href}" title="Open the order">${escapeHtml(po)}</a>` : `${prefix}${escapeHtml(po)}`;
}
function vendorPoLink(code, poId = null) {
  if (!code) return "";
  const href = poId ? `purchase-orders.html?id=${poId}` : `purchase-orders.html?code=${encodeURIComponent(code)}`;
  return AuthGuard.can("purchasing") ? `<a class="link" href="${href}" title="Open the purchase order">${escapeHtml(code)}</a>` : escapeHtml(code);
}

// ---- One date-period picker for every screen: Today / 7 / 30 / 90 Days / 6 Months / Year / Any Time / Custom Range...
// PeriodPicker.html("id", { def: "90", onchange: "renderAll()" }) draws it; the choice is remembered per screen.
// PeriodPicker.test("id", date) -> is the date inside; .query("id") -> "days=90" or "date_from=..&date_to=.."; .label("id"). ----
const PeriodPicker = {
  OPTS: [["1", "Today"], ["7", "7 Days"], ["30", "30 Days"], ["90", "90 Days"], ["180", "6 Months"], ["365", "Year"], ["0", "Any Time"]],
  key(id) { return `period:${location.pathname.split("/").pop()}:${id}:${(AuthGuard.getUser() || {}).username || ""}`; },
  saved(id) { try { return localStorage.getItem(this.key(id)); } catch (e) { return null; } },
  remember(id, v) { try { localStorage.setItem(this.key(id), v); } catch (e) { /* a nicety */ } },
  rangeLabel(v) {
    const [, a, b] = v.split(":");
    const f = d => d ? fmtDate(d) : "…";
    return `${f(a)} – ${f(b)}`;
  },
  html(id, { def = "90", onchange = "", opts = null, title = "Which dates to show" } = {}) {
    const v = this.saved(id) || def;
    const list = opts || this.OPTS;
    const custom = v.startsWith("r:") ? `<option value="${escapeHtml(v)}" selected>${escapeHtml(this.rangeLabel(v))}</option>` : "";
    return `<select id="${id}" class="period-pick" data-period data-onchange="${escapeHtml(onchange)}" title="${escapeHtml(title)}">
      ${list.map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${l}</option>`).join("")}${custom}
      <option value="custom">Custom Range…</option></select>`;
  },
  value(id) { const el = document.getElementById(id); return el ? el.value : "0"; },
  range(id) {
    const v = this.value(id);
    if (v.startsWith("r:")) { const [, a, b] = v.split(":"); return { from: a || null, to: b || null }; }
    const days = parseInt(v) || 0;
    if (!days) return { from: null, to: null };
    if (days === 1) return { from: todayISO(), to: null };
    return { from: dayISO(new Date(Date.now() - (days - 1) * 864e5)), to: null };
  },
  // is this date (ISO / Date) inside the period? (no date: only when Any Time)
  test(id, when) {
    const { from, to } = this.range(id);
    if (!from && !to) return true;
    if (!when) return false;
    const d = dayISO(when);
    return (!from || d >= from) && (!to || d <= to);
  },
  query(id) {
    const v = this.value(id);
    if (v.startsWith("r:")) { const [, a, b] = v.split(":"); return `date_from=${a}${b ? `&date_to=${b}` : ""}`; }
    return `days=${parseInt(v) || 3650}`;
  },
  label(id) {
    const v = this.value(id);
    if (v.startsWith("r:")) return this.rangeLabel(v);
    const o = this.OPTS.find(([k]) => k === v);
    return !o ? "" : v === "0" ? "any time" : v === "1" ? "today" : `last ${o[1].toLowerCase()}`;
  },
  set(id, v) {
    const el = document.getElementById(id);
    if (!el) return;
    if (![...el.options].some(o => o.value === v)) el.insertBefore(new Option(this.rangeLabel(v), v), el.querySelector('option[value="custom"]'));
    el.value = v;
    this.remember(id, v);
    if (el.dataset.onchange) Function(el.dataset.onchange)();
  },
};
document.addEventListener("focusin", e => { if (e.target.matches && e.target.matches("select[data-period]")) e.target.dataset.prev = e.target.value; });
document.addEventListener("change", async e => {
  const el = e.target;
  if (!el.matches || !el.matches("select[data-period]")) return;
  if (el.value !== "custom") {
    PeriodPicker.remember(el.id, el.value);
    if (el.dataset.onchange) Function(el.dataset.onchange)();
    return;
  }
  const prev = el.dataset.prev && el.dataset.prev !== "custom" ? el.dataset.prev : "90";
  const cur = prev.startsWith("r:") ? prev.split(":") : [];
  const { value, el: box } = await askDialog({ title: "Custom Date Range",
    body: `<div class="row"><div><label>From</label><input type="date" class="pr-from" value="${cur[1] || ""}"></div>
      <div><label>To</label><input type="date" class="pr-to" value="${cur[2] || todayISO()}"></div></div>
      <p class="muted small">Both days included. Leave To empty for "until today".</p>`,
    buttons: [{ label: "Show", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  const from = value === "go" ? box.querySelector(".pr-from").value : "", to = value === "go" ? box.querySelector(".pr-to").value : "";
  if (!from) { el.value = prev; return; }
  PeriodPicker.set(el.id, `r:${from}:${to && to >= from ? to : ""}`);
});

// ---- A Date filter on a list: which date (Created / Delivery...) + the shared period picker; Any Time by default ----
const PeriodFilters = {
  pages: {},
  mount(prefix, redraw) {
    const slot = document.getElementById(`${prefix}-period-slot`);
    if (!slot) return;
    this.pages[prefix] = redraw;
    slot.innerHTML = PeriodPicker.html(`${prefix}-period`, { def: "0", onchange: redraw, title: "Show only records whose date falls in this period" });
    const by = document.getElementById(`${prefix}-by`);
    try { const v = localStorage.getItem(`periodby:${location.pathname}:${prefix}`); if (v && [...by.options].some(o => o.value === v)) by.value = v; } catch (e) {}
    by.addEventListener("change", () => { try { localStorage.setItem(`periodby:${location.pathname}:${prefix}`, by.value); } catch (e) {} });
  },
  redraw() { Object.values(this.pages).forEach(r => Function(r)()); },
  ok(prefix, dates) {
    const el = document.getElementById(`${prefix}-period`);
    if (!el || el.value === "0") return true;
    const by = (document.getElementById(`${prefix}-by`) || {}).value;
    return PeriodPicker.test(`${prefix}-period`, dates[by]);
  },
};

// ---- Look-alike orders / POs (server: services/lookalike.py): the same customer (vendor) with the same items and
// quantities as another live one. Never refused -- but someone must look and tick "this is separate, not a duplicate"
// before it can be confirmed / validated / ordered. LookAlike.review() is that check; apiFetch calls it by itself when
// the server says LOOKALIKE and retries the action once it's OK'd. ----
const LookAlike = {
  conf(kind) {
    return kind === "vendor" || kind === "po"
      ? { api: "purchase-orders", page: "purchase-orders", word: "PO", Word: "PO", ref: "Vendor SO #", kind: "vendor" }
      : { api: "customer-orders", page: "customer-orders", word: "order", Word: "Order", ref: "Customer PO #", kind: "customer" };
  },
  codes(rec) { return (rec.lookalikes || []).map(x => x.code); },
  // list chip beside the code
  chip(kind, rec) {
    const l = rec.lookalikes || [];
    if (!l.length) return "";
    return ` <span class="la-chip" title="${escapeHtml(`${l.map(x => `${x.code}: ${x.what}`).join("\n")}\nOpen it and click Review to check it isn't a duplicate`)}">${icon("layers")}Looks like ${escapeHtml(l.map(x => x.code).join(", "))}</span>`;
  },
  // banner on the record
  banner(kind, rec) {
    const l = rec.lookalikes || [], c = this.conf(kind);
    if (!l.length) return "";
    return `<div class="la-banner">${icon("layers")}<div><strong>Looks like ${l.map(x => `<a class="link" href="${c.page}.html?id=${x.id}" target="_blank">${escapeHtml(x.code)}</a>`).join(", ")}</strong>
        — ${escapeHtml(l[0].what)}. Check it isn't the same ${c.word} entered twice before it goes on.</div>
      <button class="small-btn" onclick="LookAlike.review('${c.kind}', ${rec.id}).then(ok => ok && typeof afterLookalikeOk === 'function' && afterLookalikeOk(${rec.id}))">Review</button></div>`;
  },
  side(title, sub, lines, money) {
    return `<div class="la-col"><div class="la-col-head"><strong>${title}</strong>${sub ? `<div class="muted small">${sub}</div>` : ""}</div>
      <table class="compact-table no-table-tools"><thead><tr><th>Item</th><th class="num">Qty</th>${money ? `<th class="num">Price</th>` : ""}</tr></thead><tbody>
      ${lines.map(l => `<tr class="${l.match ? "la-same" : "la-diff"}"><td>${escapeHtml(l.item_code)}</td><td class="num">${fmtQty(l.quantity)}</td>${money ? `<td class="num">${fmtPrice(l.price)}</td>` : ""}</tr>`).join("")}
      </tbody></table></div>`;
  },
  // The check: side by side, and an explicit tick. Resolves true once OK'd (the server keeps who and when).
  async review(kind, id) {
    const c = this.conf(kind);
    let rec;
    try { rec = await apiFetchRaw(`/api/${c.api}/${id}`); } catch (e) { alert(e.message); return false; }
    const l = rec.lookalikes || [];
    if (!l.length) { toast("Nothing to check — no look-alike left"); return true; }
    const money = !hidesMoney();
    const mineRef = (c.kind === "customer" ? rec.po_number : rec.vendor_so_number) || "";
    const body = `<p style="margin-top:0;">${escapeHtml(rec.code)} has the same ${l[0].exact ? "items and quantities" : "lines, mostly,"} as
        ${l.length === 1 ? "another" : `${l.length} other`} ${c.word}${l.length === 1 ? "" : "s"}. That's sometimes right (the same things ordered again) —
        but check it isn't the same ${c.word} entered twice.</p>
      ${l.map(x => `<div class="la-pair">
        <div class="la-what">${icon("layers")} <strong>${escapeHtml(x.what)}</strong></div>
        <div class="la-cols">
          ${this.side(`This ${c.word}: ${escapeHtml(rec.code)}`, `${mineRef ? `${c.ref} ${escapeHtml(mineRef)} · ` : ""}${escapeHtml(rec.status)}`, x.mine, money)}
          ${this.side(`<a class="link" href="${c.page}.html?id=${x.id}" target="_blank">${escapeHtml(x.code)} ↗</a>`,
            `${x.ref ? `${c.ref} ${escapeHtml(x.ref)} · ` : `no ${c.ref} · `}${escapeHtml(x.status)} · entered ${fmtDate(x.date)}${x.created_by ? ` by ${escapeHtml(x.created_by)}` : ""}`, x.lines, money)}
        </div></div>`).join("")}
      <p class="muted small" style="margin:8px 0 4px;">Green rows are the same on both; amber rows differ.</p>
      <label class="check-label la-tick"><input type="checkbox" class="la-ok-box"> I checked — <strong>${escapeHtml(rec.code)} is a separate ${c.word}</strong>, not a duplicate of ${escapeHtml(l.map(x => x.code).join(", "))}</label>`;
    const p = askDialog({ title: `Possible Duplicate — ${rec.code}`, wide: true, tone: "warn", body,
      buttons: [{ label: "OK — It's Separate", value: "ok", cls: "confirm-btn la-ok-btn" }, { label: "Not Now", value: null, cls: "secondary" }] });
    // the OK button works only once the box is ticked
    setTimeout(() => {
      const dlg = document.querySelector(".la-ok-box") && document.querySelector(".la-ok-box").closest(".ask-dialog, .modal, div[role=dialog]") || document;
      const box = dlg.querySelector(".la-ok-box"), btn = [...dlg.querySelectorAll("button")].find(b => b.classList.contains("la-ok-btn"));
      if (box && btn) { btn.disabled = true; box.onchange = () => { btn.disabled = !box.checked; }; }
    }, 0);
    const { value, el } = await p;
    if (value !== "ok" || !el.querySelector(".la-ok-box").checked) return false;
    try {
      await apiFetchRaw(`/api/${c.api}/${id}/lookalike-ok`, { method: "POST", body: JSON.stringify({ codes: l.map(x => x.code) }) });
      toast(`${rec.code}: checked — separate from ${l.map(x => x.code).join(", ")}`);
      return true;
    } catch (e) { alert(e.message); return false; }
  },
  // After creating: ask straight away (resolves whether it was OK'd; not OK'd = it stays flagged)
  async afterCreate(kind, rec) {
    if (rec && (rec.lookalikes || []).length) return this.review(kind, rec.id);
    return true;
  },
};
// The server refused something because of a look-alike / a vendor SO # already used: ask, then do it again.
async function handleCheckRefusal(e, retry, retryAllowDuplicate) {
  const msg = e && e.message || "";
  if (msg.startsWith("LOOKALIKE|")) {
    let d = {};
    try { d = JSON.parse(msg.slice("LOOKALIKE|".length)); } catch (x) { /* fall through */ }
    if (d.id && await LookAlike.review(d.kind, d.id)) return retry();
    throw new Error(`Not done yet — ${(d.message || "it looks like another one").split(" -- ")[0]}. Check it (Review on the record) first.`);
  }
  if (msg.startsWith("DUPLICATE_SO|") && retryAllowDuplicate) {
    const [, id, code, text] = msg.split("|");
    const { value } = await askDialog({ title: "Vendor SO # Already Used", tone: "warn",
      body: `<p style="margin-top:0;">${escapeHtml(text)}.</p><p>Is this really a separate purchase order with the same vendor SO #?
        <a class="link" href="purchase-orders.html?id=${parseInt(id)}" target="_blank">Open ${escapeHtml(code)} ↗</a></p>`,
      buttons: [{ label: "Yes, Save It Anyway", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
    if (value === "go") return retryAllowDuplicate();
    throw new Error(`Not saved — vendor SO # is already on ${code}`);
  }
  throw e;
}
function withAllowDuplicate(options) {
  if (options.body instanceof FormData) { options.body.set("allow_duplicate", "true"); return options; }
  try { return Object.assign({}, options, { body: JSON.stringify(Object.assign(JSON.parse(options.body || "{}"), { allow_duplicate: true })) }); }
  catch (x) { return options; }
}

// ---- Icon-only buttons: the gap after a button's icon is for its label; when there's no label, drop it so the icon
// sits in the middle of the button (contact icon, file trash, top-bar icons, the sticky button once notes exist...) ----
function markIconOnly(root) {
  (root.querySelectorAll ? root : document).querySelectorAll("button, a.tb-btn, .seg-tab").forEach(b => {
    const label = [...b.childNodes].filter(n => !(n.nodeType === 1 && /badge/.test(n.getAttribute("class") || ""))).map(n => n.textContent).join("").trim();
    const only = !!b.querySelector(".ico") && !label;  // a count badge ("4") isn't a label
    if (only !== b.classList.contains("icon-only")) b.classList.toggle("icon-only", only);
  });
}
(() => {
  let queued = false;
  const run = () => { queued = false; markIconOnly(document); };
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(run); } };
  document.addEventListener("DOMContentLoaded", run);
  new MutationObserver(later).observe(document.documentElement, { childList: true, subtree: true });
})();
