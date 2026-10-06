// Template Designer (admin and above): invoices, packing lists, POs, quotes and labels.
// A template = page + bands (header on page 1, running header on later pages, the line-items table,
// summary after the table, footer on every page) holding blocks you drag, resize and style. Text uses
// {{fields}}; the canvas fills them with a real record so what you see is what prints (the server's
// renderer draws the same spec -- Preview PDF shows it exactly).
AuthGuard.requirePerm("templates");
document.getElementById("sidebar").innerHTML = renderSidebar("designer.html");

const PX = 96;                 // canvas pixels per inch at 100%
const PT = PX / 72;            // pixels per point
const SNAP = 0.05;             // inches
let TYPES = [], CUSTOMERS = [], templates = [];
let docType = "invoice", tpl = null, spec = null, dirty = false;
let sample = { context: {}, rows: [] }, records = [], recordId = null;
let sel = null;                // { band, id } | { band, table: true } | { band } (band only)
let zoom = 0.85, zoomFit = true, undoStack = [], redoStack = [];
let logoUrl = "/api/company/logo";
let panelTab = "show", lastSel = "";  // right panel: "show" = the show / hide checklist, "edit" = what's selected

const BANDS_DOC = [["header", "Header · page 1"], ["running", "Running header · pages 2+"], ["table", "Line items"], ["summary", "Summary · after the lines"], ["footer", "Footer · every page"]];
const BANDS_LABEL = [["header", "Label"]];
const typeOf = () => TYPES.find(t => t.key === docType) || {};
const isLabel = () => !!typeOf().label_kind;

async function init() {
  [TYPES, CUSTOMERS] = await Promise.all([apiFetch("/api/templates/types"), apiFetch("/api/customers/").catch(() => [])]);
  const want = new URLSearchParams(location.search).get("type");
  if (want && TYPES.some(t => t.key === want)) docType = want;
  drawTabs();
  await loadList();
  document.addEventListener("keydown", onKey);
  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (zoomFit && spec) drawCanvas(); }, 120); });
  window.addEventListener("beforeunload", e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
}

function drawTabs() {
  document.getElementById("type-tabs").innerHTML = TYPES.map(t => `<button class="seg-tab ${t.key === docType ? "active" : ""}" onclick="switchType('${t.key}')">${escapeHtml(t.label)}</button>`).join("");
}
async function switchType(k) {
  if (!(await leaveOk())) return;
  docType = k; tpl = spec = null; sel = null; dirty = false;
  drawTabs();
  await loadList();
}
async function leaveOk() {
  if (!dirty) return true;
  const { value } = await askDialog({ title: "Unsaved changes", tone: "warn", body: `<p>${escapeHtml(tpl ? tpl.name : "This template")} has changes that aren't saved.</p>`,
    buttons: [{ label: "Save", value: "save" }, { label: "Discard", value: "discard", cls: "secondary" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value === "save") { await save(); return true; }
  return value === "discard";
}

async function loadList() {
  templates = (await apiFetch("/api/templates/")).filter(t => t.doc_type === docType);
  records = await apiFetch(`/api/templates/records/${docType}`).catch(() => []);
  recordId = recordId && records.some(r => r.id === recordId) ? recordId : (records[0] || {}).id || null;
  await loadSample();
  drawList();
  if (!tpl && templates.length) openTemplate((templates.find(t => t.is_default && !t.customer_id) || templates[0]).id);
  else drawAll();
}
async function loadSample() {
  try { sample = await apiFetch(`/api/templates/sample/${docType}${recordId ? `?record_id=${recordId}` : ""}`); }
  catch (e) { sample = { context: {}, rows: [] }; }
}
function custName(id) { return (CUSTOMERS.find(c => c.id === id) || {}).name || `customer #${id}`; }
function drawList() {
  const el = document.getElementById("tpl-list");
  el.innerHTML = templates.length ? templates.map(t => `<div class="dz-item ${tpl && tpl.id === t.id ? "on" : ""}" onclick="openTemplate(${t.id})">
      <div class="dz-item-name">${escapeHtml(t.name)}</div>
      <div class="dz-item-meta">${t.is_default ? `<span class="dz-badge">${t.customer_id ? `Default for ${escapeHtml(custName(t.customer_id))}` : "Default"}</span>` : t.customer_id ? `<span class="muted">for ${escapeHtml(custName(t.customer_id))}</span>` : ""}
        <span class="muted">${escapeHtml(t.updated_by || "")}</span></div></div>`).join("")
    : `<p class="muted small">No ${escapeHtml(typeOf().label || "")} templates yet — the built-in layout prints. <a class="link" onclick="openGallery()">Start one</a>.</p>`;
}

async function openTemplate(id) {
  if (tpl && tpl.id === id) return;
  if (!(await leaveOk())) return;
  tpl = await apiFetch(`/api/templates/${id}`);
  spec = tpl.spec;
  undoStack = []; redoStack = []; sel = null; dirty = false;
  drawList();
  drawAll();
}

// ---------- starter gallery (glass) ----------
async function openGallery() {
  if (!(await leaveOk())) return;
  const starters = await apiFetch(`/api/templates/starters/${docType}`);
  const back = document.createElement("div");
  back.className = "glass-back";
  back.id = "gallery";
  back.addEventListener("click", e => { if (e.target === back) back.remove(); });
  back.innerHTML = `<div class="glass-panel gallery-panel"><div class="sm-head"><div><h3>New ${escapeHtml(typeOf().label)} template</h3>
      <div class="muted small">Start from a ready-made design and make it yours, or from blank.</div></div>
      <button class="icon-btn sm-close" onclick="document.getElementById('gallery').remove()">${icon("x")}</button></div>
    <div class="gallery-grid">${starters.map((s, i) => `<button class="gallery-card" onclick="createFrom('${s.key}', ${i})">
        <div class="gallery-thumb" id="thumb-${i}"></div><div class="gallery-name">${escapeHtml(s.name)}</div></button>`).join("")}</div></div>`;
  document.body.appendChild(back);
  window._starters = starters;
  starters.forEach((s, i) => {
    const holder = document.getElementById(`thumb-${i}`);
    const page = s.spec.page || {};
    const scale = Math.min(200 / ((page.w || 8.5) * PX), 250 / ((page.h || 11) * PX));
    holder.appendChild(renderPage(s.spec, scale, false));
  });
}
async function createFrom(key, i) {
  const s = window._starters[i];
  const name = `${s.name}${templates.some(t => t.name === s.name) ? " copy" : ""}`;
  const t = await apiFetch("/api/templates/", { method: "POST", body: JSON.stringify({ doc_type: docType, name, starter: key }) });
  document.getElementById("gallery").remove();
  templates = (await apiFetch("/api/templates/")).filter(x => x.doc_type === docType);
  tpl = null;
  await openTemplate(t.id);
  toast(`Created "${t.name}" — customize it, then Save`);
}

// ---------- the toolbar over the canvas ----------
function drawBar() {
  const bar = document.getElementById("dz-bar");
  if (!tpl) { bar.innerHTML = ""; return; }
  const per = typeOf().per_customer;
  bar.innerHTML = `
    <input type="text" id="tpl-name" value="${escapeHtml(tpl.name)}" oninput="dirty = true; markDirty()" title="Template name">
    <div class="dz-tools">
      ${["text", "field", "kv", "image", "barcode", "qr", "rect", "line"].map(k => `<button class="secondary small-btn" onclick="addBlock('${k}')" title="Add ${{ image: "the logo", kv: "a label / value list", field: "a field" }[k] || "a " + k}">${{ text: "Text", field: "Field", kv: "List", image: "Logo", barcode: "Barcode", qr: "QR", rect: "Box", line: "Line" }[k]}</button>`).join("")}
    </div>
    <span class="dz-sep"></span>
    <button class="icon-btn" onclick="undo()" title="Undo (Ctrl+Z)">${icon("undo")}</button>
    <button class="icon-btn" onclick="redo()" title="Redo (Ctrl+Y)" style="transform:scaleX(-1)">${icon("undo")}</button>
    <select id="zoom" onchange="zoomFit = this.value === 'fit'; if (!zoomFit) zoom = parseFloat(this.value); drawCanvas()" title="Zoom">
      <option value="fit" ${zoomFit ? "selected" : ""}>Fit</option>${[0.5, 0.65, 0.85, 1, 1.25, 1.5, 2].map(z => `<option value="${z}" ${!zoomFit && z === zoom ? "selected" : ""}>${Math.round(z * 100)}%</option>`).join("")}</select>
    <span class="dz-sep"></span>
    ${records.length ? `<select id="rec" onchange="recordId = parseInt(this.value); loadSample().then(drawCanvas)" title="Data shown on the canvas and in the preview">${records.map(r => `<option value="${r.id}" ${r.id === recordId ? "selected" : ""}>${escapeHtml(r.label)}</option>`).join("")}</select>` : `<span class="muted small">sample data</span>`}
    <button class="secondary small-btn" onclick="previewPdf()" data-icon="eye">Preview PDF</button>
    <button class="small-btn" id="save-btn" onclick="save()" data-icon="save">Save</button>
    <div class="dz-default">
      <label class="inline-check"><input type="checkbox" id="is-default" ${tpl.is_default ? "checked" : ""} onchange="setDefault(this.checked)"> Default</label>
      ${per ? `<select id="for-cust" onchange="setCustomer(this.value)" title="Default for everyone, or only for one customer">
          <option value="">for everyone</option>${CUSTOMERS.map(c => `<option value="${c.id}" ${c.id === tpl.customer_id ? "selected" : ""}>for ${escapeHtml(c.name)}</option>`).join("")}</select>` : ""}
    </div>
    <span class="dz-sep"></span>
    <button class="icon-btn" onclick="duplicateTpl()" title="Duplicate this template">${icon("layers")}</button>
    <button class="icon-btn trash-btn" onclick="deleteTpl()" title="Delete this template">${icon("trash")}</button>`;
  decorateIcons(bar);
  markDirty();
}
function markDirty() {
  const b = document.getElementById("save-btn");
  if (b) b.classList.toggle("dirty", dirty);
}

// ---------- spec helpers ----------
function bandsOf() { return isLabel() ? BANDS_LABEL : BANDS_DOC; }
function band(k) { return spec[k] || (spec[k] = { h: k === "running" ? 0.4 : k === "footer" ? 0.35 : 1, blocks: [] }); }
function blockOf(s = sel) { return s && s.id ? (band(s.band).blocks || []).find(b => b.id === s.id) : null; }
function snapshot() { undoStack.push(JSON.stringify(spec)); if (undoStack.length > 100) undoStack.shift(); redoStack = []; }
function changed() { dirty = true; markDirty(); }
function undo() { if (!undoStack.length) return; redoStack.push(JSON.stringify(spec)); spec = JSON.parse(undoStack.pop()); changed(); drawCanvas(); drawProps(); }
function redo() { if (!redoStack.length) return; undoStack.push(JSON.stringify(spec)); spec = JSON.parse(redoStack.pop()); changed(); drawCanvas(); drawProps(); }
const newId = () => "b" + Math.random().toString(36).slice(2, 9);
const snap = v => Math.round(Math.round(v / SNAP) * SNAP * 1000) / 1000;
const r3 = v => Math.round(v * 1000) / 1000;

function addBlock(kind) {
  const bk = sel && sel.band && sel.band !== "table" ? sel.band : "header";
  snapshot();
  const field = (typeOf().fields || [])[0];
  const base = { id: newId(), x: 0.2, y: 0.2, w: 2, h: 0.3, style: { size: 10, color: "#1e293b" } };
  const b = { text: { ...base, type: "text", text: "Your text" }, field: { ...base, type: "text", text: `{{${field ? field.key : "doc.number"}}}`, style: { size: 10, bold: true, color: "#0f172a" } },
    kv: { ...base, type: "kv", w: 2.6, h: 1.0, text: isLabel() ? "PO # | {{label.po}}\nJob # | {{label.job}}" : "Order # | {{order.code}}\nCustomer PO | {{order.po_number}}",
          style: { size: 8.8, label_size: 8, label_w: 0.95, lh: 1.7, color: "#1e293b" } },
    image: { ...base, type: "image", src: "logo", w: 0.9, h: 0.6 }, barcode: { ...base, type: "barcode", value: "{{doc.number}}", w: 2.2, h: 0.5, style: { show_text: true } },
    qr: { ...base, type: "qr", value: "{{doc.number}}", w: 0.9, h: 0.9, style: {} }, rect: { ...base, type: "rect", w: 2, h: 0.8, style: { bg: "#f8fafc", border: 0.6, border_color: "#e2e8f0", radius: 4 } },
    line: { ...base, type: "line", w: 3, h: 0.02, style: { border: 1, color: "#1e293b" } } }[kind];
  if (isLabel() && kind === "barcode") b.value = "{{label.po}}";
  if (isLabel() && kind === "qr") b.value = "{{label.shipment}}";
  band(bk).blocks.push(b);
  sel = { band: bk, id: b.id };
  changed(); drawCanvas(); drawProps();
}

// ---------- show / hide (same rules as template_engine.visible_spec) ----------
const parentOf = grp => grp.split(":")[0].trim();
function isHidden(s, b) {
  const h = s.hidden || [];
  return !!b.hidden || (!!b.group && (h.includes(b.group) || h.includes(parentOf(b.group))));
}
function shownText(b, key) {  // the block's text without the lines of fields that are ticked off
  const hf = b.hide_fields || [], t = String(b[key] || "");
  if (!hf.length) return t;
  return t.split("\n").filter(l => ![...l.matchAll(FIELD_RE)].some(m => hf.includes(m[1]))).join("\n");
}

// ---------- fill {{fields}} like the server does ----------
function lookup(key) {
  if (key === "page") return "1";
  if (key === "pages") return "2";
  return key.split(".").reduce((o, k) => (o && typeof o === "object" ? o[k] : undefined), sample.context) ?? "";
}
function safeMarkup(s) {  // the template's own tags: <b> <i> <u> <br> <font size=N color=X>
  return escapeHtml(s).replace(/&lt;(\/?)(b|i|u|br)\s*\/?&gt;/gi, "<$1$2>")
    .replace(/&lt;font([^&]*)&gt;/gi, (m, a) => { const sz = (a.match(/size=['"]?(\d+(?:\.\d+)?)/) || [])[1], col = (a.match(/color=['"]?(#[0-9a-f]{3,6}|\w+)/i) || [])[1];
      return `<span style="${sz ? `font-size:${sz * PT * zoomNow}px;` : ""}${col ? `color:${col};` : ""}">`; })
    .replace(/&lt;\/font&gt;/gi, "</span>");
}
let zoomNow = 1;
const FIELD_RE = /\{\{\s*([\w.]+)\s*(?:\|([^}]*))?\}\}/g;  // {{field}} or {{field|shown when empty}}
function fillText(text) {
  const out = [];
  String(text || "").split("\n").forEach(line => {
    const found = [...line.matchAll(FIELD_RE)];
    let vals = found.map(m => String(lookup(m[1])));
    if (found.length && !vals.some(v => v.trim()) && !found.some(m => m[2])) return;
    vals = vals.map((v, i) => v.trim() ? v : (found[i][2] || ""));
    let i = 0;
    out.push(line.replace(FIELD_RE, () => "\u0000" + (i++) + "\u0000"));
    out[out.length - 1] = { tpl: out[out.length - 1], vals };
  });
  return out.map(o => safeMarkup(o.tpl).replace(/\u0000(\d+)\u0000/g, (m, n) => escapeHtml(o.vals[+n]).replace(/\n/g, "<br>"))).join("<br>");
}

// ---------- drawing the page ----------
function blockEl(b, scale, interactive, bandKey) {
  const st = b.style || {}, el = document.createElement("div");
  el.className = `blk blk-${b.type}` + (interactive && sel && sel.id === b.id ? " sel" : "") + (isHidden(spec, b) ? " ghost" : "");
  el.dataset.id = b.id;
  el.dataset.band = bandKey;
  Object.assign(el.style, { left: `${b.x * PX * scale}px`, top: `${b.y * PX * scale}px`, width: `${b.w * PX * scale}px`, height: `${b.h * PX * scale}px` });
  const fs = (st.size || 9) * PT * scale;
  if (b.type === "line") {
    const horiz = b.w >= b.h, t = Math.max(1, (st.border || 1) * PT * scale);
    el.innerHTML = `<i style="position:absolute;${horiz ? `left:0;right:0;top:50%;height:${t}px;margin-top:${-t / 2}px` : `top:0;bottom:0;left:50%;width:${t}px;margin-left:${-t / 2}px`};background:${st.color || "#1e293b"}"></i>`;
  } else {
    if (st.bg) el.style.background = st.bg;
    if (st.border) el.style.border = `${Math.max(0.5, st.border * PT * scale)}px solid ${st.border_color || "#cbd5e1"}`;
    if (st.radius) el.style.borderRadius = `${st.radius * PT * scale}px`;
    el.style.padding = `${(st.pad || 0) * PT * scale}px`;
  }
  if (b.type === "text") {
    zoomNow = scale;
    const inner = document.createElement("div");
    inner.className = "blk-text";
    Object.assign(inner.style, { fontSize: `${fs}px`, lineHeight: st.lh || 1.25, fontWeight: st.bold ? 700 : st.semi ? 600 : 400, fontStyle: st.italic ? "italic" : "normal",
      color: st.color || "#1e293b", textAlign: st.align || "left", textTransform: st.upper ? "uppercase" : "none",
      letterSpacing: st.spacing ? `${st.spacing * PT * scale}px` : "normal", justifyContent: { middle: "center", bottom: "flex-end" }[st.valign] || "flex-start" });
    inner.innerHTML = `<div>${fillText(shownText(b, "text"))}</div>`;
    el.appendChild(inner);
  } else if (b.type === "kv") {
    const lw = (st.label_w ?? 0.95) * PX * scale, step = (st.size || 8.8) * (st.lh || 1.7) * PT * scale;
    zoomNow = scale;
    el.innerHTML = shownText(b, "text").split("\n").filter(l => l.includes("|")).map(l => {
      const [k, ...v] = l.split("|");
      let val = fillText(v.join("|").trim());
      if (!val.replace(/<[^>]+>/g, "").trim()) val = escapeHtml(st.empty || "");
      return val ? `<div class="kv-row" style="min-height:${step}px;font-size:${(st.size || 8.8) * PT * scale}px">
        <span style="width:${lw}px;font-size:${(st.label_size || (st.size || 8.8) - 0.8) * PT * scale}px;color:${st.label_color || "#64748b"}">${escapeHtml(k.trim())}</span>
        <b style="font-weight:${st.plain ? 400 : 600};color:${st.color || "#1e293b"}">${val}</b></div>` : "";
    }).join("");
  } else if (b.type === "image") {
    el.innerHTML = `<img src="${logoUrl}" alt="" style="object-position:${st.align === "right" ? "right" : st.align === "center" ? "center" : "left"} top">`;
  } else if (b.type === "barcode") {
    const v = fillText(shownText(b, "value")).replace(/<[^>]+>/g, "");
    el.innerHTML = `<div class="bc-bars"></div>${st.show_text !== false ? `<div class="bc-text" style="font-size:${8 * PT * scale}px">${v}</div>` : ""}`;
  } else if (b.type === "qr") {
    el.innerHTML = `<div class="qr-box"></div>`;
  }
  if (interactive && !isHidden(spec, b)) {
    el.addEventListener("pointerdown", e => startDrag(e, b, bandKey, null));
    if (sel && sel.id === b.id) ["nw", "ne", "sw", "se", "e", "s"].forEach(h => {
      const hd = document.createElement("span");
      hd.className = `hd hd-${h}`;
      hd.addEventListener("pointerdown", e => startDrag(e, b, bandKey, h));
      el.appendChild(hd);
    });
  }
  return el;
}

function tableEl(t, scale, interactive, rowsIn = null, isPallets = false) {
  const cols = (t.columns || []).filter(c => c.key && !c.hidden), st = t.style || {};
  const width = ((spec.page.w || 8.5) - 2 * (spec.page.margin || 0.5)) * PX * scale;
  const fixed = cols.reduce((s, c) => s + (parseFloat(c.w) || 0) * PX * scale, 0), flex = cols.filter(c => !parseFloat(c.w));
  const share = flex.length ? Math.max(0.6 * PX * scale, (width - fixed) / flex.length) : 0;
  const numeric = k => ["qty", "price", "amount", "ordered", "shipped", "backorder"].includes(k);
  const fs = (st.size || 8.6) * PT * scale, hs = (st.header_size || 7.5) * PT * scale, pad = (st.pad || 5) * PT * scale;
  const rows = rowsIn || (sample.rows || []).slice(0, isLabel() ? 0 : 6);
  const cell = (c, r) => c.key === "check" ? "☐" : c.key === "item_code_desc" ? `<b>${escapeHtml(r.item_code || "")}</b><div style="color:#64748b;font-size:${fs * 0.9}px">${escapeHtml(r.description || "")}</div>`
    : escapeHtml(String(r[c.key] === "—" && c.empty !== undefined ? c.empty : r[c.key] ?? "")).replace(/\n/g, "<br>");
  const el = document.createElement("div");
  el.className = "tbl" + (interactive && sel && sel.table && !isPallets ? " sel" : "");
  el.innerHTML = `<table class="no-table-tools no-col-bands" style="width:${width}px;font-size:${fs}px">
    <colgroup>${cols.map(c => `<col style="width:${(parseFloat(c.w) || 0) * PX * scale || share}px">`).join("")}</colgroup>
    <thead><tr style="${st.header_bg ? `background:${st.header_bg};` : ""}${st.top_rule ? `box-shadow:inset 0 ${0.8 * PT * scale}px 0 ${st.top_rule};` : ""}">${cols.map(c =>
      `<th style="padding:${pad}px;font-size:${hs}px;font-weight:600;${st.header_upper ? "text-transform:uppercase;" : ""}color:${st.header_color || "#64748b"};text-align:${c.align || (numeric(c.key) ? "right" : "left")};${st.header_rule ? `border-bottom:${(st.header_rule_w || 1.2) * PT * scale}px solid ${st.header_rule}` : ""}">${escapeHtml(c.header || "")}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr style="${st.zebra && i % 2 ? `background:${st.zebra};` : ""}">${cols.map(c =>
      `<td style="padding:${pad}px;text-align:${c.align || (numeric(c.key) ? "right" : "left")};border-bottom:0.5px solid ${st.row_rule || "#e2e8f0"};${c.key === "amount" ? "font-weight:700;" : ""}">${cell(c, r)}</td>`).join("")}</tr>`).join("")
      || `<tr><td colspan="${cols.length}" style="padding:${pad}px;color:#94a3b8">${isPallets ? "(the shipment's pallets go here -- this record has none)" : "(the record's lines go here)"}</td></tr>`}</tbody></table>`;
  if (interactive && !isPallets) el.addEventListener("pointerdown", e => { e.stopPropagation(); sel = { band: "table", table: true }; drawCanvas(); drawProps(); });
  return el;
}

// The whole page; interactive = the editing canvas (bands labelled, blocks draggable).
function renderPage(s, scale, interactive) {
  const prevSpec = spec;
  spec = s;
  const page = s.page || {}, m = (page.margin ?? 0.5) * PX * scale;
  const sheet = document.createElement("div");
  sheet.className = "sheet" + (interactive ? " editing" : "");
  Object.assign(sheet.style, { width: `${(page.w || 8.5) * PX * scale}px`, minHeight: `${(page.h || 11) * PX * scale}px`, padding: `${m}px`,
    fontFamily: s.font === "ui" ? '"Segoe UI", system-ui, sans-serif' : s.font === "gothic" ? '"Century Gothic", "Trebuchet MS", sans-serif' : 'Arial, "Segoe UI", sans-serif' });
  for (const [k, label] of (isLabelSpec(s) ? BANDS_LABEL : BANDS_DOC)) {
    if (k === "running" && !interactive) continue;
    let bd = document.createElement("div");
    bd.className = `band band-${k}` + (interactive && sel && sel.band === k && !sel.id && !sel.table ? " sel" : "");
    if (k === "table") {
      if (!s.table) continue;
      bd.appendChild(tableEl(s.table, scale, interactive));
      if (s.pallets && (interactive || !s.pallets.hidden)) sheet.appendChild(bd), bd = palletBand(s, scale, interactive);
    } else {
      const bnd = s[k] || { h: 0, blocks: [] };
      bd.style.height = `${(bnd.h || 0) * PX * scale}px`;
      (bnd.blocks || []).filter(b => interactive || !isHidden(s, b)).forEach(b => bd.appendChild(blockEl(b, scale, interactive, k)));
      if (interactive) {
        bd.addEventListener("pointerdown", e => { if (e.target === bd) { sel = { band: k }; drawCanvas(); drawProps(); } });
        const grip = document.createElement("div");
        grip.className = "band-grip";
        grip.title = "Drag to change the band's height";
        grip.addEventListener("pointerdown", e => startBandResize(e, k));
        bd.appendChild(grip);
      }
    }
    if (interactive) {
      const tag = document.createElement("div");
      tag.className = "band-tag";
      tag.textContent = label;
      bd.appendChild(tag);
    }
    if (k === "footer" && !isLabelSpec(s)) bd.classList.add("band-bottom");
    sheet.appendChild(bd);
  }
  spec = prevSpec;
  return sheet;
}
// Packing lists: the pallet table (spec.pallets) after the lines -- on its own page when new_page is set
function palletBand(s, scale, interactive) {
  const p = s.pallets, st = p.style || {}, bd = document.createElement("div");
  bd.className = "band band-pallets" + (p.hidden ? " faded" : "");
  bd.style.marginTop = `${8 * scale}px`;
  if (p.heading) bd.insertAdjacentHTML("beforeend", `<div style="text-align:center;font-weight:700;font-size:${(st.heading_size || 12) * PT * scale}px;color:${st.heading_color || "#1f2d3a"};
    border-bottom:${1.5 * PT * scale}px solid #1f2d3a;padding-bottom:${3 * scale}px;margin-bottom:${8 * scale}px">${escapeHtml(p.heading)}</div>`);
  bd.appendChild(tableEl(p, scale, false, sample.pallet_rows || [], true));
  if (interactive) {
    const tag = document.createElement("div");
    tag.className = "band-tag";
    tag.textContent = `Pallet information${p.new_page ? " · own page" : ""}${p.hidden ? " · hidden" : ""}`;
    bd.appendChild(tag);
  }
  return bd;
}
function isLabelSpec(s) { return !s.table && (s.page || {}).w <= 6.5 && (s.page || {}).h <= 6.5; }

function drawCanvas() {
  const wrap = document.getElementById("canvas-wrap");
  if (!spec) { wrap.innerHTML = `<div class="dz-empty">Pick a template on the left, or <a class="link" onclick="openGallery()">start a new one</a>.</div>`; return; }
  if (zoomFit) {  // the whole page width in view (labels up to 200%)
    const p = spec.page || {};
    zoom = Math.max(0.3, Math.min(isLabel() ? 2 : 1.25, (wrap.clientWidth - 48) / ((p.w || 8.5) * PX)));
  }
  wrap.innerHTML = "";
  wrap.appendChild(renderPage(spec, zoom, true));
}
function drawAll() { drawBar(); drawCanvas(); drawProps(); }

// ---------- drag / resize ----------
function startDrag(e, b, bandKey, handle) {
  e.stopPropagation();
  e.preventDefault();
  if (!sel || sel.id !== b.id) { sel = { band: bandKey, id: b.id }; drawCanvas(); drawProps(); }
  const start = { x: e.clientX, y: e.clientY, bx: b.x, by: b.y, bw: b.w, bh: b.h };
  let moved = false;
  const k = PX * zoom;
  const move = ev => {
    const dx = (ev.clientX - start.x) / k, dy = (ev.clientY - start.y) / k;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 0.02) return;
    if (!moved) { snapshot(); moved = true; }
    const free = ev.altKey ? v => r3(v) : snap;
    if (!handle) { b.x = free(start.bx + dx); b.y = free(start.by + dy); }
    else {
      if (handle.includes("e")) b.w = Math.max(0.05, free(start.bw + dx));
      if (handle.includes("s")) b.h = Math.max(0.02, free(start.bh + dy));
      if (handle.includes("w")) { const nx = free(start.bx + dx); b.w = Math.max(0.05, r3(start.bw + start.bx - nx)); b.x = nx; }
      if (handle.includes("n")) { const ny = free(start.by + dy); b.h = Math.max(0.02, r3(start.bh + start.by - ny)); b.y = ny; }
    }
    const el = document.querySelector(`.blk[data-id="${b.id}"]`);
    if (el) Object.assign(el.style, { left: `${b.x * k}px`, top: `${b.y * k}px`, width: `${b.w * k}px`, height: `${b.h * k}px` });
    syncGeometryInputs(b);
  };
  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    if (moved) { changed(); drawCanvas(); drawProps(); }
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}
function startBandResize(e, k) {
  e.stopPropagation(); e.preventDefault();
  const bnd = band(k), startY = e.clientY, h0 = bnd.h || 0;
  snapshot();
  const move = ev => { bnd.h = Math.max(0, snap(h0 + (ev.clientY - startY) / (PX * zoom))); drawCanvas(); };
  const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); changed(); sel = { band: k }; drawCanvas(); drawProps(); };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
}

function onKey(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (tpl) save(); return; }
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (typing || !spec) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
  const b = blockOf();
  if (!b) return;
  if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); removeBlock(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") { e.preventDefault(); duplicateBlock(); return; }
  const step = e.shiftKey ? 0.1 : 0.01, d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
  if (d) { e.preventDefault(); snapshot(); b.x = r3(b.x + d[0]); b.y = r3(b.y + d[1]); changed(); drawCanvas(); syncGeometryInputs(b); }
}
function removeBlock() {
  const b = blockOf();
  if (!b) return;
  snapshot();
  band(sel.band).blocks = band(sel.band).blocks.filter(x => x.id !== b.id);
  sel = { band: sel.band };
  changed(); drawCanvas(); drawProps();
}
function duplicateBlock() {
  const b = blockOf();
  if (!b) return;
  snapshot();
  const c = JSON.parse(JSON.stringify(b));
  c.id = newId(); c.x = r3(c.x + 0.15); c.y = r3(c.y + 0.15);
  band(sel.band).blocks.push(c);
  sel = { band: sel.band, id: c.id };
  changed(); drawCanvas(); drawProps();
}
function bringTo(front) {
  const b = blockOf();
  if (!b) return;
  snapshot();
  const list = band(sel.band).blocks.filter(x => x.id !== b.id);
  band(sel.band).blocks = front ? [...list, b] : [b, ...list];
  changed(); drawCanvas();
}

// ---------- properties panel ----------
function field(label, html, wide = false) { return `<div class="pf ${wide ? "wide" : ""}"><label>${label}</label>${html}</div>`; }
function num(path, v, step = 0.01, min = "") { return `<input type="number" step="${step}" ${min !== "" ? `min="${min}"` : ""} value="${v ?? ""}" data-path="${path}" oninput="setProp(this)">`; }
function col(path, v) { return `<span class="color-pick"><input type="color" value="${v && /^#[0-9a-f]{6}$/i.test(v) ? v : "#000000"}" data-path="${path}" oninput="setProp(this)" ${v ? "" : 'data-empty="1"'}><a class="link small" onclick="clearProp('${path}')">${v ? "clear" : "none"}</a></span>`; }
function chk(path, v, label) { return `<label class="inline-check"><input type="checkbox" data-path="${path}" ${v ? "checked" : ""} onchange="setProp(this)"> ${label}</label>`; }
function sel_(path, v, opts) { return `<select data-path="${path}" onchange="setProp(this)">${opts.map(([k, l]) => `<option value="${k}" ${String(v ?? "") === k ? "selected" : ""}>${l}</option>`).join("")}</select>`; }
function fieldPicker(target) {
  return `<select class="field-pick" onchange="insertField(this, '${target}')"><option value="">+ Insert a field…</option>${(typeOf().fields || []).map(f => `<option value="${f.key}">${escapeHtml(f.label)}</option>`).join("")}</select>`;
}

function drawProps() {
  const el = document.getElementById("props");
  if (!spec) { el.innerHTML = `<p class="muted small" style="margin:0;">What you select on the page — a block, a band, the line items — is edited here.</p>`; return; }
  const selKey = sel ? JSON.stringify(sel) : "";
  if (selKey !== lastSel) { lastSel = selKey; if (sel) panelTab = "edit"; }
  const tabs = `<div class="dz-ptabs"><button class="${panelTab === "show" ? "on" : ""}" onclick="panelTab = 'show'; drawProps()">Show / hide</button>
    <button class="${panelTab === "edit" ? "on" : ""}" onclick="panelTab = 'edit'; drawProps()">${sel ? "Selected" : "Page"}</button></div>`;
  if (panelTab === "show") { el.innerHTML = tabs + showHideHtml(); decorateIcons(el); return; }
  drawEditProps(el);
  el.insertAdjacentHTML("afterbegin", tabs);
}
function drawEditProps(el) {
  const b = blockOf();
  if (b) {
    const st = b.style || {};
    const kind = { text: "Text", kv: "Label / value list", image: "Logo", barcode: "Barcode", qr: "QR code", rect: "Box", line: "Line" }[b.type];
    el.innerHTML = `<div class="props-head"><h3>${kind}</h3><span class="muted small">${escapeHtml(bandsOf().find(x => x[0] === sel.band)?.[1] || "")}</span></div>
      ${b.type === "text" ? field("Text — {{fields}} fill in; a line whose fields are empty is left out", `<textarea id="p-text" rows="5" data-path="text" oninput="setProp(this)">${escapeHtml(b.text || "")}</textarea>${fieldPicker("text")}
          <div class="muted small">Tags: &lt;b&gt;bold&lt;/b&gt; &lt;i&gt;italic&lt;/i&gt; &lt;font size=14&gt;big&lt;/font&gt; · {{field|—}} shows — when empty</div>`, true) : ""}
      ${b.type === "kv" ? field("One row per line: <b>Label | {{field}}</b> — a row whose field is empty is left out", `<textarea id="p-text" rows="6" data-path="text" oninput="setProp(this)">${escapeHtml(b.text || "")}</textarea>${fieldPicker("text")}`, true)
          + `<div class="pgrid">${field("Text size", num("style.size", st.size || 8.8, 0.2, 5))}${field("Label size", num("style.label_size", st.label_size || 8, 0.2, 5))}
             ${field("Label column (in)", num("style.label_w", st.label_w ?? 0.95, 0.05, 0.2))}${field("Row spacing", num("style.lh", st.lh || 1.7, 0.05, 1))}
             ${field("Value color", col("style.color", st.color))}${field("Label color", col("style.label_color", st.label_color))}</div>
             ${field("Show for an empty value (blank = leave the row out)", `<input type="text" value="${escapeHtml(st.empty || "")}" data-path="style.empty" oninput="setProp(this)" placeholder="e.g. —">`)}
             <div class="pchecks">${chk("style.plain", st.plain, "Values not bold")}</div>` : ""}
      ${["barcode", "qr"].includes(b.type) ? field("Value", `<input type="text" id="p-value" value="${escapeHtml(b.value || "")}" data-path="value" oninput="setProp(this)">${fieldPicker("value")}`, true) : ""}
      <div class="pgrid">${field("X (in)", num("x", b.x))}${field("Y (in)", num("y", b.y))}${field("Width", num("w", b.w, 0.01, 0.02))}${field("Height", num("h", b.h, 0.01, 0.01))}</div>
      ${b.type === "text" ? `<div class="pgrid">${field("Size (pt)", num("style.size", st.size || 9, 0.5, 4))}${field("Color", col("style.color", st.color))}
          ${field("Align", sel_("style.align", st.align || "left", [["left", "Left"], ["center", "Center"], ["right", "Right"]]))}${field("Vertical", sel_("style.valign", st.valign || "top", [["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]]))}
          ${field("Line height", num("style.lh", st.lh || 1.25, 0.05, 0.8))}${field("Letter spacing", num("style.spacing", st.spacing || 0, 0.5, 0))}</div>
          <div class="pchecks">${chk("style.bold", st.bold, "Bold")}${chk("style.semi", st.semi, "Semibold")}${chk("style.italic", st.italic, "Italic")}${chk("style.upper", st.upper, "UPPERCASE")}${chk("style.fit_wrap", st.fit === "wrap", "Don't shrink to fit")}</div>` : ""}
      ${field("Section in Show / hide (blocks with the same name show and hide together)", `<input type="text" list="grp-list" value="${escapeHtml(b.group || "")}" data-path="group" oninput="setProp(this)" placeholder="e.g. Signatures">
          <datalist id="grp-list">${[...new Set(allBlocks().map(x => x.block.group).filter(Boolean))].map(n => `<option value="${escapeHtml(n)}">`).join("")}</datalist>`)}
      ${b.type === "image" ? field("Align", sel_("style.align", st.align || "left", [["left", "Left"], ["center", "Center"], ["right", "Right"]])) + `<p class="muted small">Shows your company logo (Company Settings).</p>` : ""}
      ${b.type === "barcode" ? `<div class="pchecks">${chk("style.show_text", st.show_text !== false, "Print the value under the bars")}</div>${field("Align", sel_("style.align", st.align || "left", [["left", "Left"], ["center", "Center"], ["right", "Right"]]))}` : ""}
      ${b.type === "line" ? `<div class="pgrid">${field("Thickness (pt)", num("style.border", st.border || 1, 0.1, 0.1))}${field("Color", col("style.color", st.color))}</div>` : ""}
      ${b.type !== "line" ? `<h4>Box</h4><div class="pgrid">${field("Fill", col("style.bg", st.bg))}${field("Border (pt)", num("style.border", st.border || 0, 0.1, 0))}
          ${field("Border color", col("style.border_color", st.border_color))}${field("Corner radius", num("style.radius", st.radius || 0, 1, 0))}${field("Padding (pt)", num("style.pad", st.pad || 0, 1, 0))}</div>` : ""}
      <div class="props-actions"><button class="secondary small-btn" onclick="duplicateBlock()">Duplicate</button>
        <button class="secondary small-btn" onclick="bringTo(true)">To front</button><button class="secondary small-btn" onclick="bringTo(false)">To back</button>
        <button class="danger small-btn" onclick="removeBlock()">Delete</button></div>
      <p class="muted small">Drag to move · corners to resize · arrow keys nudge (Shift = more) · Alt while dragging = no snapping · Ctrl+D duplicate · Del removes</p>`;
  } else if (sel && sel.table && spec.table) {
    const t = spec.table, st = t.style || {}, opts = (typeOf().columns || []).map(c => [c.key, c.label]);
    el.innerHTML = `<div class="props-head"><h3>Line items</h3></div>
      <p class="muted small">Columns left to right. Width 0 = share the space left.</p>
      <div class="cols">${(t.columns || []).map((c, i) => `<div class="colrow">
          ${sel_(`table.columns.${i}.key`, c.key, opts)}<input type="text" value="${escapeHtml(c.header || "")}" data-path="table.columns.${i}.header" oninput="setProp(this)" placeholder="Heading">
          <input type="number" step="0.05" min="0" value="${c.w || 0}" data-path="table.columns.${i}.w" oninput="setProp(this)" title="Width (in)">
          ${sel_(`table.columns.${i}.align`, c.align || "", [["", "Auto"], ["left", "Left"], ["center", "Center"], ["right", "Right"]])}
          <span class="colbtns"><a class="link" onclick="moveCol(${i}, -1)" title="Move left">↑</a><a class="link" onclick="moveCol(${i}, 1)" title="Move right">↓</a><a class="link neg" onclick="removeCol(${i})" title="Remove">✕</a></span></div>`).join("")}</div>
      <button class="secondary small-btn" onclick="addCol()" data-icon="plus">Add column</button>
      <h4>Look</h4><div class="pgrid">${field("Text size", num("table.style.size", st.size || 8.6, 0.2, 5))}${field("Heading size", num("table.style.header_size", st.header_size || 7.5, 0.2, 5))}
        ${field("Heading fill", col("table.style.header_bg", st.header_bg))}${field("Heading text", col("table.style.header_color", st.header_color))}
        ${field("Line under heading", col("table.style.header_rule", st.header_rule))}${field("Line above heading", col("table.style.top_rule", st.top_rule))}
        ${field("Row lines", col("table.style.row_rule", st.row_rule))}${field("Stripe rows", col("table.style.zebra", st.zebra))}
        ${field("Grid lines", col("table.style.grid", st.grid))}${field("Cell padding", num("table.style.pad", st.pad || 5, 0.5, 0))}</div>`;
  } else if (sel && sel.band) {
    const bnd = band(sel.band);
    el.innerHTML = `<div class="props-head"><h3>${escapeHtml(bandsOf().find(x => x[0] === sel.band)?.[1] || sel.band)}</h3></div>
      ${field("Height (in)", num(`${sel.band}.h`, bnd.h, 0.05, 0))}
      <p class="muted small">${{ header: "Printed at the top of page 1.", running: "Printed at the top of every page after the first.", summary: "Printed right after the line items (totals, notes, signatures) and kept together.", footer: "Printed at the bottom of every page — {{page}} and {{pages}} give page numbers." }[sel.band] || ""}
        Click a block to edit it, or add one from the toolbar (it goes into this band).</p>`;
  } else {
    const p = spec.page || (spec.page = {});
    el.innerHTML = `<div class="props-head"><h3>Page</h3></div>
      ${field("Font", sel_("font", spec.font || "sans", [["ui", "Segoe UI (as in the samples)"], ["sans", "Arial (built-in documents)"], ["gothic", "Century Gothic (old portal)"]]))}
      <div class="pgrid">${field("Width (in)", num("page.w", p.w || 8.5, 0.1, 1))}${field("Height (in)", num("page.h", p.h || 11, 0.1, 1))}${field("Margin (in)", num("page.margin", p.margin ?? 0.5, 0.05, 0))}</div>
      ${isLabel() ? `<p class="muted small">Labels print one page per box. 6 × 4 in fits most thermal label printers.</p>` : ""}
      <p class="muted small">Click a band (Header, Line items, Summary, Footer) or a block to edit it.</p>`;
  }
  decorateIcons(el);
}
// ---------- the show / hide checklist ----------
const DECOR = ["rect", "line"];
function allBlocks() { return bandsOf().flatMap(([k]) => ((spec[k] || {}).blocks || []).map(block => ({ band: k, block }))); }
function sectionTree() {
  const secs = [], by = {};
  for (const { block: b } of allBlocks()) {
    if (!b.group) continue;
    const name = parentOf(b.group), part = b.group.includes(":") ? b.group.slice(b.group.indexOf(":") + 1).trim() : null;
    let sec = by[name];
    if (!sec) { sec = by[name] = { name, parts: [], fields: [], blocks: [] }; secs.push(sec); }
    if (part) { if (!sec.parts.includes(part)) sec.parts.push(part); continue; }
    sec.blocks.push(b);
    for (const m of `${b.text || ""}\n${b.value || ""}`.matchAll(FIELD_RE))
      if (!sec.fields.includes(m[1]) && !["page", "pages"].includes(m[1])) sec.fields.push(m[1]);
  }
  return secs;
}
function showHideHtml() {
  const secs = sectionTree(), hidden = spec.hidden || [], fl = Object.fromEntries((typeOf().fields || []).map(f => [f.key, f.label]));
  window._secs = secs;
  const box = (on, call, label, dis = false) => `<label class="sh-row ${dis ? "dis" : ""}"><input type="checkbox" ${on ? "checked" : ""} ${dis ? "disabled" : ""} onchange="${call}"> <span>${label}</span></label>`;
  const fieldOff = (sec, key) => sec.blocks.filter(b => (`${b.text || ""}\n${b.value || ""}`).includes(key)).every(b => (b.hide_fields || []).includes(key));
  const others = allBlocks().filter(({ block: b }) => !b.group && !DECOR.includes(b.type));
  const kind = { text: "Text", kv: "List", image: "Logo", barcode: "Barcode", qr: "QR code" };
  return `<p class="muted small sh-help">Untick anything you don't want printed. It disappears from the page and the PDF (shown faded here); tick it to bring it back.</p>
    ${secs.length ? `<div class="sh-list">${secs.map((sec, i) => {
      const on = !hidden.includes(sec.name);
      const subs = sec.parts.map(p => box(on && !hidden.includes(`${sec.name}: ${p}`), `toggleSection(${i}, '${"p" + sec.parts.indexOf(p)}', this.checked)`, escapeHtml(p), !on)).join("")
        + (sec.fields.length > 1 ? sec.fields.map((f, j) => box(on && !fieldOff(sec, f), `toggleField(${i}, ${j}, this.checked)`, escapeHtml(fl[f] || f), !on)).join("") : "");
      return `<div class="sh-sec">${box(on, `toggleSection(${i}, '', this.checked)`, `<b>${escapeHtml(sec.name)}</b>`)}${subs ? `<div class="sh-sub">${subs}</div>` : ""}</div>`;
    }).join("")}</div>` : `<p class="muted small">This template has no sections yet — give blocks a Section name (select a block) to list them here.</p>`}
    ${spec.table ? `<h4>Line-item columns</h4><div class="sh-list sh-cols">${(spec.table.columns || []).map((c, i) => box(!c.hidden, `toggleColumn(${i}, this.checked)`, escapeHtml(c.header || ((typeOf().columns || []).find(x => x.key === c.key) || {}).label || c.key))).join("")}</div>` : ""}
    ${spec.pallets ? `<h4>Pallet information</h4><div class="sh-list">${box(!spec.pallets.hidden, "togglePallets(this.checked)", "<b>Print the pallet table</b> <span class='muted'>(when the shipment has pallets)</span>")}</div>
      <div class="sh-list sh-cols">${(spec.pallets.columns || []).map((c, i) => box(!c.hidden && !spec.pallets.hidden, `togglePalletColumn(${i}, this.checked)`, escapeHtml(c.header || c.key), !!spec.pallets.hidden)).join("")}</div>` : ""}
    ${others.length ? `<h4>Other blocks</h4><div class="sh-list">${others.map(({ band: bk, block: b }) => box(!b.hidden, `toggleBlock('${bk}', '${b.id}', this.checked)`,
        `${kind[b.type] || b.type}: ${escapeHtml(String(b.text || b.value || "").replace(/<[^>]+>/g, "").slice(0, 28))}`)).join("")}</div>` : ""}`;
}
function toggleSection(i, part, on) {
  const sec = window._secs[i], name = part ? `${sec.name}: ${sec.parts[+part.slice(1)]}` : sec.name;
  snapshot();
  const h = new Set(spec.hidden || []);
  on ? h.delete(name) : h.add(name);
  spec.hidden = [...h];
  changed(); drawCanvas(); drawProps();
}
function toggleField(i, j, on) {
  const sec = window._secs[i], key = sec.fields[j];
  snapshot();
  for (const b of sec.blocks) {
    if (!(`${b.text || ""}\n${b.value || ""}`).includes(key)) continue;
    const hf = new Set(b.hide_fields || []);
    on ? hf.delete(key) : hf.add(key);
    b.hide_fields = [...hf];
  }
  changed(); drawCanvas(); drawProps();
}
function togglePallets(on) { snapshot(); spec.pallets.hidden = !on; changed(); drawCanvas(); drawProps(); }
function togglePalletColumn(i, on) { snapshot(); spec.pallets.columns[i].hidden = !on; changed(); drawCanvas(); drawProps(); }
function toggleColumn(i, on) { snapshot(); spec.table.columns[i].hidden = !on; changed(); drawCanvas(); drawProps(); }
function toggleBlock(bk, id, on) { snapshot(); const b = band(bk).blocks.find(x => x.id === id); if (b) b.hidden = !on; changed(); drawCanvas(); drawProps(); }

function syncGeometryInputs(b) {
  ["x", "y", "w", "h"].forEach(k => { const i = document.querySelector(`#props input[data-path="${k}"]`); if (i) i.value = b[k]; });
}

function target(path) {  // "style.size" on the block, or "table.columns.2.w" / "page.w" / "header.h" on the spec
  const parts = path.split(".");
  const root = ["table", "page", "header", "running", "summary", "footer", "font"].includes(parts[0]) ? spec : blockOf();
  let o = root;
  for (const p of parts.slice(0, -1)) o = o[p] ?? (o[p] = /^\d+$/.test(p) ? [] : {});
  return [o, parts[parts.length - 1]];
}
let typingSnap = null;
function setProp(input) {
  const path = input.dataset.path;
  if (path === "style.fit_wrap") { snapshot(); const [o] = target("style.x"); o.fit = input.checked ? "wrap" : "shrink"; changed(); drawCanvas(); return; }
  if (typingSnap !== path) { snapshot(); typingSnap = path; setTimeout(() => { typingSnap = null; }, 800); }
  const [o, k] = target(path);
  let v = input.type === "checkbox" ? input.checked : input.value;
  if (input.type === "number") v = input.value === "" ? 0 : parseFloat(input.value);
  o[k] = v;
  if (input.type === "color" && input.nextElementSibling) input.nextElementSibling.textContent = "clear";
  changed();
  drawCanvas();
}
function clearProp(path) { snapshot(); const [o, k] = target(path); delete o[k]; changed(); drawCanvas(); drawProps(); }
function insertField(selEl, targetKey) {
  if (!selEl.value) return;
  const box = document.getElementById(targetKey === "text" ? "p-text" : "p-value");
  const ins = `{{${selEl.value}}}`;
  const at = box.selectionStart ?? box.value.length;
  box.value = box.value.slice(0, at) + ins + box.value.slice(box.selectionEnd ?? at);
  selEl.value = "";
  setProp(box);
  box.focus();
}
function addCol() { snapshot(); spec.table.columns.push({ key: (typeOf().columns || [])[0].key, header: "", w: 0.8 }); changed(); drawCanvas(); drawProps(); }
function removeCol(i) { snapshot(); spec.table.columns.splice(i, 1); changed(); drawCanvas(); drawProps(); }
function moveCol(i, d) {
  const c = spec.table.columns, j = i + d;
  if (j < 0 || j >= c.length) return;
  snapshot(); [c[i], c[j]] = [c[j], c[i]]; changed(); drawCanvas(); drawProps();
}

// ---------- save / preview / default ----------
async function save() {
  if (!tpl) return;
  try {
    tpl = await apiFetch(`/api/templates/${tpl.id}`, { method: "PUT", body: JSON.stringify({ name: document.getElementById("tpl-name").value, spec }) });
    spec = tpl.spec;
    dirty = false;
    markDirty();
    templates = (await apiFetch("/api/templates/")).filter(t => t.doc_type === docType);
    drawList();
    toast("Saved");
  } catch (e) { alert(e.message); }
}
async function previewPdf() {
  const res = await fetch("/api/templates/preview", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${AuthGuard.getToken()}` },
    body: JSON.stringify({ doc_type: docType, spec, record_id: recordId }) });
  if (!res.ok) { alert((await res.json().catch(() => ({}))).detail || "Preview failed"); return; }
  window.open(URL.createObjectURL(await res.blob()), "_blank");
}
async function setDefault(on) {
  if (dirty) await save();
  await apiFetch(`/api/templates/${tpl.id}/default`, { method: "POST", body: JSON.stringify({ on }) });
  tpl.is_default = on;
  templates = (await apiFetch("/api/templates/")).filter(t => t.doc_type === docType);
  drawList();
  toast(on ? `"${tpl.name}" now prints ${tpl.customer_id ? `for ${custName(tpl.customer_id)}` : "for every " + typeOf().label.toLowerCase()}` : "Back to the built-in layout");
}
async function setCustomer(v) {
  tpl = await apiFetch(`/api/templates/${tpl.id}`, { method: "PUT", body: JSON.stringify(v ? { customer_id: parseInt(v) } : { clear_customer: true }) });
  spec = tpl.spec;
  templates = (await apiFetch("/api/templates/")).filter(t => t.doc_type === docType);
  drawList();
}
async function duplicateTpl() {
  if (dirty) await save();
  const t = await apiFetch("/api/templates/", { method: "POST", body: JSON.stringify({ doc_type: docType, name: `${tpl.name} copy`, spec }) });
  templates = (await apiFetch("/api/templates/")).filter(x => x.doc_type === docType);
  tpl = null;
  await openTemplate(t.id);
}
async function deleteTpl() {
  const { value } = await askDialog({ title: `Delete "${tpl.name}"?`, tone: "warn", body: tpl.is_default ? "<p>It's a default — that document goes back to the built-in layout.</p>" : "",
    buttons: [{ label: "Delete", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value !== "go") return;
  await apiFetch(`/api/templates/${tpl.id}`, { method: "DELETE" });
  tpl = spec = null; dirty = false;
  templates = (await apiFetch("/api/templates/")).filter(t => t.doc_type === docType);
  drawList(); drawAll();
}

init();
