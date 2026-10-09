// Quotes, inside the Customer Orders page (uses its globals: items, customers, listView, renderOrders).
// A quote is built by hand or from pasted RFQ text; each line's price is suggested from price history.
let quotes = [];
let currentQuote = null;  // the quote open in the editor (null id = new)

async function loadQuotes() {
  try { quotes = await apiFetch("/api/quotes/"); } catch (e) { quotes = []; }
}

const QUOTE_TAG = { draft: "draft", sent: "confirmed", accepted: "shipped", declined: "cancelled", converted: "invoiced" };
function renderQuotes(q) {
  const words = (q || "").toLowerCase().split(/\s+/).filter(Boolean);
  const rows = quotes.filter(x => !words.length || words.every(w => `${x.code} ${x.customer} ${x.customer_ref || ""} ${x.lines.map(l => `${l.item_code || ""} ${l.description}`).join(" ")}`.toLowerCase().includes(w)));
  document.getElementById("quotes-body").innerHTML = rows.map(x => `<tr>
      <td class="nowrap"><a class="link" onclick="openQuote(${x.id})">${escapeHtml(x.code)}</a></td><td>${escapeHtml(x.customer)}</td>
      <td>${escapeHtml(x.customer_ref || "")}</td><td>${fmtDate(x.quote_date)}</td><td>${fmtDate(x.valid_until)}</td>
      <td class="num">${x.lines.length}</td><td class="num">${fmtMoney(x.total)}</td>
      <td><span class="tag ${QUOTE_TAG[x.status] || "draft"}">${x.status}</span></td>
      <td>${x.order_id ? `<a class="link" onclick="showDetail(${x.order_id})">order</a>` : ""}</td></tr>`).join("")
    || `<tr><td colspan="9" class="muted">${quotes.length ? "No quotes match." : "No quotes yet — click + New Quote."}</td></tr>`;
}

async function printQuote(id) {
  const got = await PrintOptions.ask("quote", { title: "Print Quote", buttons: [{ label: "Open PDF", value: "pdf" }] });
  if (got) openPdf(`/api/quotes/${id}/pdf?${PrintOptions.query(got.opts)}`);
}

function newQuote() {
  currentQuote = { id: null, customer_id: customers[0] ? customers[0].id : null, customer_ref: "", notes: "", valid_until: null, status: "draft", lines: [] };
  quoteRfqFile = null;
  drawQuote();
}
async function openQuote(id) { currentQuote = await apiFetch(`/api/quotes/${id}`); quoteRfqFile = null; drawQuote(); }

function quoteItemOptions(selected) {
  return `<option value="">— not matched: pick our item —</option>` + items.map(i => `<option value="${i.id}" ${i.id === selected ? "selected" : itemPickAttr(i, true)}
    data-search="${escapeHtml([i.category, i.barcode].filter(Boolean).join(" "))}">${escapeHtml(i.code)} — ${escapeHtml(i.title)}${itemPickNote(i, true)}</option>`).join("");
}

function quoteLineRow(l) {
  const p = l.price;  // suggestion from history (parse / item change); saved lines get theirs from loadQuoteHints()
  return `<tr class="q-line" data-hint="${p ? escapeHtml(JSON.stringify(p)) : ""}" data-source="${escapeHtml(l.source_text || l.source || "")}">
    <td class="grow"><select class="q-item" data-searchable onchange="quoteItemChanged(this)">${quoteItemOptions(l.item_id)}</select>
      ${l.source_text || l.source ? `<div class="small muted">Asked: “${escapeHtml(l.source_text || l.source)}”${l.match ? ` · ${escapeHtml(l.match)}` : ""}</div>` : ""}
      ${!l.item_id && l.candidates && l.candidates.length ? `<div class="small">Maybe: ${l.candidates.slice(0, 3).map(c => `<a class="link" onclick="pickQuoteCandidate(this, ${c.item_id})">${escapeHtml(c.code)}</a>`).join(" · ")}</div>` : ""}
      <input type="text" class="q-desc" placeholder="Description on the quote (blank = item title)" value="${escapeHtml(l.item_id ? (!l.source && l.description && l.description !== (items.find(i => i.id === l.item_id) || {}).title ? l.description : "") : (l.description || ""))}" style="margin-top:4px;">
      ${lineNoteBox(l.notes, true)}</td>
    <td class="num"><input type="number" min="1" step="1" class="q-qty qty-input" value="${l.quantity || 1}"></td>
    <td class="num"><div class="q-price-cell">${priceInput("q-price", p ? p.price : (l.unit_price ?? 0))}<button type="button" class="icon-btn no-print q-hist-btn"
        title="Earlier quotes of this item" aria-label="Earlier quotes of this item" onclick="showQuotedPrices(this)">${icon("clock")}</button></div>
      <div class="q-basis small muted"></div></td>
    <td class="num q-amount"></td>
    <td class="line-actions">${lineNoteBtn(l.notes)}${trashBtn("this.closest('tr').remove(); quoteTotals()")}</td></tr>`;
}
// Under each price: where the suggestion came from, cost and live margin; ▲/▼ when the price typed
// differs from the suggestion; and any other recent price that disagrees (click it to use it).
function quoteHint(tr) {
  const el = tr && tr.querySelector(".q-basis");
  if (!el || hidesMoney()) return;
  const p = tr.dataset.hint ? JSON.parse(tr.dataset.hint) : null;
  if (!p || !parseInt(tr.querySelector(".q-item").value)) { el.innerHTML = ""; return; }
  const cur = parseFloat(tr.querySelector(".q-price").value) || 0;
  const hist = (p.history || []).map(h => `${h.kind === "sale" ? "Sold" : "Bought"} ${h.doc} · ${h.party} · ${fmtQty(h.qty)} @ ${fmtPrice(h.price)}`).join("\n");
  const margin = cur && p.cost ? Math.round((cur - p.cost) / cur * 1000) / 10 : null;
  const diff = cur - p.price;
  const delta = p.price && Math.abs(diff) >= 0.000005
    ? `<div class="${diff > 0 ? "pos" : "neg"}">${diff > 0 ? "▲" : "▼"} ${fmtPrice(Math.abs(diff))} Vs ${fmtPrice(p.price)} suggested</div>` : "";
  const others = (p.others || []).map(o => `<div class="q-other">Also <a class="link" title="Use this price" onclick="useQuotePrice(this, ${o.price})">${fmtPrice(o.price)}</a> — ${escapeHtml(o.basis)}</div>`).join("");
  el.innerHTML = `<span title="${escapeHtml(hist || "no history")}">${escapeHtml(p.basis)}${p.cost ? ` · cost ${fmtPrice(p.cost)}` : ""}${margin != null ? ` · <strong class="${margin < 15 ? "neg" : "pos"}">${margin}%</strong>` : ""}</span>${delta}${others}`;
}
function useQuotePrice(a, price) {
  const tr = a.closest("tr");
  tr.querySelector(".q-price").value = price;
  quoteHint(tr); quoteTotals();
}
// Saved quote reopened, or customer changed: fetch every line's hint (prices on the quote are left alone).
async function loadQuoteHints() {
  const rows = [...document.querySelectorAll("#q-lines tr")].filter(tr => parseInt(tr.querySelector(".q-item").value));
  if (!rows.length || hidesMoney()) return;
  try {
    const hints = await apiFetch("/api/quotes/prices", { method: "POST", body: JSON.stringify({
      customer_id: parseInt(document.getElementById("q-customer").value), exclude_quote_id: currentQuote.id,
      item_ids: rows.map(tr => parseInt(tr.querySelector(".q-item").value)) }) });
    rows.forEach(tr => {
      const h = hints[tr.querySelector(".q-item").value];
      if (h) { tr.dataset.hint = JSON.stringify(h); quoteHint(tr); }
    });
  } catch (e) {}
}
// The clock icon: every earlier quote of this item (this customer's first), plus a link to full sales/purchase history.
let quotedPriceRow = null;
async function showQuotedPrices(btn) {
  const tr = btn.closest("tr"), itemId = parseInt(tr.querySelector(".q-item").value);
  if (!itemId) { toast("Pick the item first"); return; }
  const custId = parseInt(document.getElementById("q-customer").value);
  let modal = document.getElementById("price-history-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "price-history-modal";
    modal.className = "modal-backdrop";
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.body.appendChild(modal);
  }
  modal.innerHTML = `<div class="modal"><p class="muted">Loading Earlier Quotes…</p></div>`;
  quotedPriceRow = tr;
  try {
    const rows = await apiFetch(`/api/quotes/item-history/${itemId}?customer_id=${custId}${currentQuote.id ? `&exclude_quote_id=${currentQuote.id}` : ""}`);
    const item = items.find(i => i.id === itemId) || {};
    const table = (list, title) => `<h4>${title}</h4>` + (list.length ? `<table class="compact-table no-table-tools">
        <thead><tr><th>Date</th><th>Quote</th><th>Customer</th><th>Status</th><th class="num">Qty</th><th class="num">Price</th><th></th></tr></thead>
        <tbody>${list.map(h => `<tr><td>${fmtDate(h.date)}</td><td>${escapeHtml(h.code)}</td><td>${escapeHtml(h.customer)}</td>
          <td><span class="tag ${QUOTE_TAG[h.status] || "draft"}">${h.status}</span></td><td class="num">${fmtQty(h.qty)}</td>
          <td class="num">${fmtPrice(h.price)}</td><td><a class="link" onclick="useQuotedPrice(${h.price})">Use</a></td></tr>`).join("")}</tbody></table>`
      : `<p class="muted">None Yet.</p>`);
    modal.innerHTML = `<div class="modal">
      <div class="row" style="align-items:center;"><h3 style="margin:0;">Quoted prices — ${escapeHtml(item.code || "")}</h3>
        <div style="flex:0;"><a class="link" onclick="document.getElementById('price-history-modal').remove()">Close</a></div></div>
      <p class="muted" style="margin-top:4px;">${escapeHtml(item.title || "")} · <a class="link" onclick="showPriceHistory(${itemId})">Sales &amp; purchase history</a></p>
      ${table(rows.filter(h => h.mine), "To this customer")}
      ${table(rows.filter(h => !h.mine), "To other customers")}
    </div>`;
  } catch (e) { modal.innerHTML = `<div class="modal"><div class="error">${escapeHtml(e.message)}</div></div>`; }
}
function useQuotedPrice(price) {
  const tr = quotedPriceRow;
  document.getElementById("price-history-modal").remove();
  if (!tr || !tr.isConnected) return;
  tr.querySelector(".q-price").value = price;
  quoteHint(tr); quoteTotals();
}

function drawQuote() {
  const x = currentQuote, locked = x.status === "converted";
  if (window.setRecordId) setRecordId(null);  // the address keeps no order id while a quote is open
  document.getElementById("form-card").style.display = "none";
  const card = document.getElementById("detail-card");
  card.style.display = "block";
  card.classList.remove("order-draft");
  applyUnderlay(card, "quote", x);  // DRAFT · sent blue · CONVERTED · DECLINED / EXPIRED
  card.innerHTML = `
    <h3 class="detail-head" style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">${x.id ? `Quote ${escapeHtml(x.code)}` : "New Quote"}
      ${x.id ? `<span class="tag ${QUOTE_TAG[x.status]}">${x.status}</span>` : ""}
      <span style="margin-left:auto;"></span>
      ${x.id ? `<button class="secondary small-btn" onclick="toggleQuoteEmail()">Email</button>
        <button class="secondary small-btn" onclick="printQuote(${x.id})">PDF / Print</button>` : ""}</h3>
    <div id="q-email-form" style="display:none;"></div>
    <div class="field-grid">
      <div class="wide"><label>Customer</label><select id="q-customer" ${locked ? "disabled" : ""} onchange="loadQuoteHints()">${customers.map(c => `<option value="${c.id}" ${c.id === x.customer_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}</select></div>
      <div><label>Their reference / RFQ #</label><input type="text" id="q-ref" value="${escapeHtml(x.customer_ref || "")}"></div>
      <div><label>Valid until</label><input type="date" id="q-valid" value="${x.valid_until ? x.valid_until.substring(0, 10) : ""}"></div>
      <div class="wide"><label>Notes on the quote</label><textarea id="q-notes" rows="2">${escapeHtml(x.notes || "")}</textarea></div>
    </div>
    ${locked ? "" : `<details class="fold-section" ${x.lines.length ? "" : "open"} style="margin:10px 0;">
      <summary><strong>Fill it from the customer's request</strong> <span class="muted small">— their RFQ as a PDF, or email text / rows copied from Excel</span></summary>
      <div class="row" style="align-items:center; gap:8px; margin:6px 0 8px;">
        <button type="button" class="ai-btn q-scan-btn" style="flex:0 0 auto;" data-icon="sparkles" onclick="scanQuotePdf()" title="Read the customer's RFQ PDF into quote lines">AI Scan PDF</button>
        ${AuthGuard.can("ai") ? `<button type="button" class="ai-btn cloud q-scan-btn" style="flex:0 0 auto;" onclick="scanQuotePdf('claude')" title="For a hard RFQ: read it with Claude (cloud). Names and contact details are removed on this PC first.">☁ Ask Claude</button>` : ""}
        <span class="muted small" style="flex:1 1 auto;">or paste the text below</span></div>
      <div id="q-scan-doc"></div>
      <textarea id="q-paste" rows="6" placeholder="e.g.\n500 pcs 5/8-11 x 2 A325 HDG hex bolt\n1,000 - 5/8 F436 washer HDG\n15343   250"></textarea>
      <button class="ai-btn" data-icon="sparkles" onclick="readQuoteText()" style="margin-top:6px;">Read Lines</button>
      <span id="q-paste-msg" class="small muted"></span></details>`}
    <h4>Lines</h4>
    ${locked ? "" : printAllNotesHtml()}
    <div class="table-scroll"><table class="lines-table fit-table">
      <thead><tr><th class="grow">Item</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th><th></th></tr></thead>
      <tbody id="q-lines" oninput="quoteHint(event.target.closest('tr')); quoteTotals()">${x.lines.map(quoteLineRow).join("")}</tbody>
      <tfoot><tr><td class="grow">Quote total</td><td></td><td></td><td class="num" id="q-total"></td><td></td></tr></tfoot>
    </table></div>
    ${locked ? "" : `<button class="secondary" onclick="addQuoteLine()" style="margin-top:8px;">+ Add line</button>`}
    ${x.id ? `<section class="dsec" style="margin-top:12px;"><h4 class="dsec-title">Files</h4><div id="q-attachments"></div></section>` : ""}
    ${x.emails && x.emails.length ? `<div class="muted small" style="margin-top:8px;">${x.emails.map(e =>
      `Emailed to ${escapeHtml(e.to)} by ${escapeHtml(e.sent_by || "")} · ${fmtWhen(e.sent_at)}`).join("<br>")}</div>` : ""}
    <div id="q-error" class="error"></div>
    <div class="btn-row">
      ${locked ? `<span class="muted">Converted to order — <a class="link" onclick="showDetail(${x.order_id})">open it</a>.</span>` : `<button onclick="saveQuote()">Save Quote</button>`}
      ${x.id && !locked ? `${["sent", "accepted", "declined"].filter(s => s !== x.status).map(s => `<button class="secondary" onclick="setQuoteStatus('${s}')">Mark ${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}
        <button onclick="convertQuote()" data-icon="check">Convert to Order</button>
        <button class="danger" onclick="deleteQuote()">Delete</button>` : ""}
      <button class="secondary" onclick="document.getElementById('detail-card').style.display='none'">Close</button>
    </div>`;
  document.querySelectorAll("#q-lines tr").forEach(quoteHint);
  quoteTotals();
  loadQuoteHints();
  if (x.id) renderAttachments("q-attachments", "quote", x.id, ["customer_rfq"]);
  if (quoteRfqFile && !x.id) document.getElementById("q-scan-doc").innerHTML = scannedDocHtml(quoteRfqFile, "Attached to the quote when you save it");
  card.scrollIntoView({ behavior: "smooth" });
}

function quoteTotals() {
  let total = 0;
  document.querySelectorAll("#q-lines tr").forEach(tr => {
    const amt = lineAmount(parseFloat(tr.querySelector(".q-qty").value) || 0, parseFloat(tr.querySelector(".q-price").value) || 0);
    tr.querySelector(".q-amount").textContent = fmtMoney(amt);
    total += amt;
  });
  const t = document.getElementById("q-total");
  if (t) t.textContent = fmtMoney(total);
}
function addQuoteLine() {
  document.getElementById("q-lines").insertAdjacentHTML("beforeend", quoteLineRow({ quantity: 1, unit_price: 0 }));
  quoteTotals();
}
async function quoteItemChanged(sel) {
  const tr = sel.closest("tr"), id = parseInt(sel.value);
  if (!id) return;
  try {
    const p = await apiFetch(`/api/quotes/price/${id}?customer_id=${document.getElementById("q-customer").value}${currentQuote.id ? `&exclude_quote_id=${currentQuote.id}` : ""}`);
    tr.querySelector(".q-price").value = p.price;
    tr.dataset.hint = JSON.stringify(p);
    quoteHint(tr);
    quoteTotals();
  } catch (e) {}
}
function pickQuoteCandidate(a, id) {
  const sel = a.closest("tr").querySelector(".q-item");
  sel.value = id;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
}
// The customer's RFQ as a PDF: read like a customer PO, each line suggested against our items (with price hints).
let quoteRfqFile = null;
function scanQuotePdf(engine = null) {
  if (engine === "claude" && !confirm("Read this RFQ with Claude (Anthropic's cloud)?\n\nNames, addresses and contact details are removed on this PC first; only that text is sent, never the PDF.")) return;
  const picker = Object.assign(document.createElement("input"), { type: "file", accept: "application/pdf" });
  picker.onchange = async () => {
    const file = picker.files[0];
    if (!file) return;
    const msg = document.getElementById("q-paste-msg");
    msg.innerHTML = `Reading ${escapeHtml(file.name)}… this can take a minute.`;
    document.querySelectorAll(".q-scan-btn").forEach(b => { b.disabled = true; });
    try {
      const form = new FormData();
      form.append("file", file);
      const cid = parseInt(document.getElementById("q-customer").value);
      if (cid && currentQuote.id) form.append("customer_id", cid);
      if (engine) form.append("engine", engine);
      const r = await apiUpload("/api/quotes/ai-read", form);
      // a new quote takes the customer and their reference from the RFQ
      if (!currentQuote.id && r.customer && r.customer.customer_id) { document.getElementById("q-customer").value = r.customer.customer_id; loadQuoteHints(); }
      if (r.reference && !document.getElementById("q-ref").value) document.getElementById("q-ref").value = r.reference;
      const body = document.getElementById("q-lines"), before = body.children.length;
      body.insertAdjacentHTML("beforeend", r.lines.map(quoteLineRow).join(""));
      [...body.children].slice(before).forEach(quoteHint);
      quoteTotals();
      quoteRfqFile = file;
      document.getElementById("q-scan-doc").innerHTML = scannedDocHtml(file, currentQuote.id ? "Attached to the quote when you save it" : "Attached to the quote when you save it");
      const unmatched = r.lines.filter(l => !l.item_id).length;
      msg.innerHTML = `Read ${r.lines.length} line${r.lines.length === 1 ? "" : "s"} from ${escapeHtml(file.name)}${unmatched ? ` · <span class="neg">${unmatched} need${unmatched === 1 ? "s" : ""} you to pick our item</span>` : ""}. Check the lines and prices, then Save Quote.`;
    } catch (e) { msg.innerHTML = `<span class="neg">Couldn't read it: ${escapeHtml(e.message)}</span>`; }
    finally { document.querySelectorAll(".q-scan-btn").forEach(b => { b.disabled = false; }); }
  };
  picker.click();
}

async function readQuoteText() {
  const msg = document.getElementById("q-paste-msg");
  msg.textContent = "Reading…";
  try {
    const rows = await apiFetch("/api/quotes/parse", { method: "POST", body: JSON.stringify({
      customer_id: parseInt(document.getElementById("q-customer").value), text: document.getElementById("q-paste").value }) });
    const body = document.getElementById("q-lines"), before = body.children.length;
    body.insertAdjacentHTML("beforeend", rows.map(quoteLineRow).join(""));
    [...body.children].slice(before).forEach(quoteHint);
    const unmatched = rows.filter(r => !r.item_id).length, noQty = rows.filter(r => !r.qty_found).length;
    msg.innerHTML = `${rows.length} line${rows.length === 1 ? "" : "s"} added${unmatched ? ` · <span class="neg">${unmatched} need our item picked</span>` : ""}${noQty ? ` · <span class="neg">${noQty} had no quantity (set to 1)</span>` : ""}. Your picks are remembered for this customer.`;
    document.getElementById("q-paste").value = "";
    quoteTotals();
  } catch (e) { msg.innerHTML = `<span class="neg">${escapeHtml(e.message)}</span>`; }
}
function quotePayload() {
  return {
    customer_id: parseInt(document.getElementById("q-customer").value),
    customer_ref: document.getElementById("q-ref").value || null,
    valid_until: document.getElementById("q-valid").value || null,
    notes: document.getElementById("q-notes").value || null,
    lines: [...document.querySelectorAll("#q-lines tr")].map(tr => ({
      item_id: parseInt(tr.querySelector(".q-item").value) || null,
      description: tr.querySelector(".q-desc").value.trim() || null,
      quantity: parseFloat(tr.querySelector(".q-qty").value) || 1,
      unit_price: parseFloat(tr.querySelector(".q-price").value) || 0,
      notes: lineNoteValue(tr).notes, source_text: tr.dataset.source || null })),
  };
}
async function saveQuote() {
  const err = document.getElementById("q-error"); err.textContent = "";
  try {
    const x = currentQuote.id ? await apiFetch(`/api/quotes/${currentQuote.id}`, { method: "PUT", body: JSON.stringify(quotePayload()) })
                              : await apiFetch("/api/quotes/", { method: "POST", body: JSON.stringify(quotePayload()) });
    if (quoteRfqFile) {  // the RFQ it was read from goes on the quote
      const form = new FormData();
      form.append("entity_type", "quote"); form.append("entity_id", x.id); form.append("category", "customer_rfq");
      form.append("note", "Read with AI"); form.append("files", quoteRfqFile);
      try { await apiUpload("/api/attachments/", form); quoteRfqFile = null; } catch (e) { alert(`Saved ${x.code}, but attaching the RFQ failed: ${e.message}`); }
    }
    currentQuote = x; await loadQuotes(); renderOrders(); drawQuote(); toast(`Saved ${x.code}`);
  } catch (e) { err.textContent = e.message; }
}
async function setQuoteStatus(s) {
  await saveQuote();
  currentQuote = await apiFetch(`/api/quotes/${currentQuote.id}/status`, { method: "PUT", body: JSON.stringify({ status: s }) });
  await loadQuotes(); renderOrders(); drawQuote();
}
async function convertQuote() {
  const err = document.getElementById("q-error"); err.textContent = "";
  await saveQuote();
  const po = prompt("Customer PO # for the order (leave blank if not known yet):", "");
  if (po === null) return;
  try {
    const r = await apiFetch(`/api/quotes/${currentQuote.id}/convert`, { method: "POST", body: JSON.stringify({ po_number: po || null }) });
    await loadQuotes(); await loadOrders(); toast(`Created order ${r.order}`); showDetail(r.order_id);
  } catch (e) { err.textContent = e.message; }
}
function toggleQuoteEmail() {
  const el = document.getElementById("q-email-form"), x = currentQuote;
  if (el.style.display !== "none") { el.style.display = "none"; return; }
  el.style.display = "block";
  el.innerHTML = `<div class="panel" style="max-width:640px; margin:8px 0;">
      <div class="row">
        <div><label>To</label><input type="text" id="q-email-to" value="${escapeHtml(x.customer_email || "")}" placeholder="buyer@customer.com"></div>
        <div><label>CC</label><input type="text" id="q-email-cc"></div>
      </div>
      <label>Subject</label><input type="text" id="q-email-subject" value="Quotation ${escapeHtml(x.code)}${x.customer_ref ? ` — your ref ${escapeHtml(x.customer_ref)}` : ""}">
      <label>Message</label><textarea id="q-email-body" rows="6">Hello${x.customer_contact ? " " + escapeHtml(x.customer_contact) : ""},

Thank you for your request${x.customer_ref ? ` (${escapeHtml(x.customer_ref)})` : ""}. Please find attached our quotation ${escapeHtml(x.code)}${x.valid_until ? `, valid until ${fmtDate(x.valid_until)}` : ""}.

To place the order, simply reply with your PO. Let us know if you have any questions.

Thank you.</textarea>
      <label class="inline-check" style="margin:8px 0 0;"><input type="checkbox" id="q-email-pdf" checked> Attach the quote PDF</label>
      <div style="margin-top:10px;"><button onclick="sendQuoteEmail()" id="q-email-send">Send</button>
        <span class="muted small">${x.status === "converted" ? "" : "Changes on screen are saved first."}${x.status === "draft" ? " Sending marks this quote as sent." : ""}</span></div>
      <div id="q-email-error" class="error"></div>
    </div>`;
  decorateIcons(el);
}
async function sendQuoteEmail() {
  const err = document.getElementById("q-email-error"), btn = document.getElementById("q-email-send");
  const msg = { to: document.getElementById("q-email-to").value, cc: document.getElementById("q-email-cc").value || null,
    subject: document.getElementById("q-email-subject").value, body: document.getElementById("q-email-body").value,
    attach_pdf: document.getElementById("q-email-pdf").checked };
  err.textContent = ""; btn.disabled = true; btn.textContent = "Sending…";
  try {
    if (currentQuote.status !== "converted")  // the PDF goes out as it's shown, not as it was last saved
      currentQuote = await apiFetch(`/api/quotes/${currentQuote.id}`, { method: "PUT", body: JSON.stringify(quotePayload()) });
    currentQuote = await apiFetch(`/api/quotes/${currentQuote.id}/email`, { method: "POST", body: JSON.stringify(msg) });
    await loadQuotes(); renderOrders(); drawQuote(); toast(`Emailed ${currentQuote.code} to ${msg.to}`);
  } catch (e) { err.textContent = e.message; btn.disabled = false; btn.textContent = "Send"; }
}
async function deleteQuote() {
  if (!confirm(`Delete quote ${currentQuote.code}?`)) return;
  await apiFetch(`/api/quotes/${currentQuote.id}`, { method: "DELETE" });
  document.getElementById("detail-card").style.display = "none";
  await loadQuotes(); renderOrders();
}
