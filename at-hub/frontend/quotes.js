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

function newQuote() {
  currentQuote = { id: null, customer_id: customers[0] ? customers[0].id : null, customer_ref: "", notes: "", valid_until: null, status: "draft", lines: [] };
  drawQuote();
}
async function openQuote(id) { currentQuote = await apiFetch(`/api/quotes/${id}`); drawQuote(); }

function quoteItemOptions(selected) {
  return `<option value="">— not matched: pick our item —</option>` + items.map(i => `<option value="${i.id}" ${i.id === selected ? "selected" : ""}
    data-search="${escapeHtml([i.category, i.barcode].filter(Boolean).join(" "))}">${escapeHtml(i.code)} — ${escapeHtml(i.title)}</option>`).join("");
}

function quoteLineRow(l) {
  const p = l.price;  // suggestion from history (parse / item change)
  return `<tr class="q-line" data-source="${escapeHtml(l.source_text || l.source || "")}">
    <td class="grow"><select class="q-item" data-searchable onchange="quoteItemChanged(this)">${quoteItemOptions(l.item_id)}</select>
      ${l.source_text || l.source ? `<div class="small muted">Asked: “${escapeHtml(l.source_text || l.source)}”${l.match ? ` · ${escapeHtml(l.match)}` : ""}</div>` : ""}
      ${!l.item_id && l.candidates && l.candidates.length ? `<div class="small">Maybe: ${l.candidates.slice(0, 3).map(c => `<a class="link" onclick="pickQuoteCandidate(this, ${c.item_id})">${escapeHtml(c.code)}</a>`).join(" · ")}</div>` : ""}
      <input type="text" class="q-desc" placeholder="Description on the quote (blank = item title)" value="${escapeHtml(l.item_id ? (!l.source && l.description && l.description !== (items.find(i => i.id === l.item_id) || {}).title ? l.description : "") : (l.description || ""))}" style="margin-top:4px;">
      ${lineNoteBox(l.notes, true)}</td>
    <td class="num"><input type="number" min="1" step="1" class="q-qty qty-input" value="${l.quantity || 1}"></td>
    <td class="num">${priceInput("q-price", p ? p.price : (l.unit_price ?? 0))}<div class="q-basis small muted">${p ? quoteBasis(p) : ""}</div></td>
    <td class="num q-amount"></td>
    <td class="line-actions">${lineNoteBtn(l.notes)}${trashBtn("this.closest('tr').remove(); quoteTotals()")}</td></tr>`;
}
function quoteBasis(p) {
  const hist = (p.history || []).map(h => `${h.kind === "sale" ? "Sold" : "Bought"} ${h.doc} · ${h.party} · ${fmtQty(h.qty)} @ ${fmtPrice(h.price)}`).join("\n");
  return `<span title="${escapeHtml(hist || "no history")}">${escapeHtml(p.basis)}${p.cost ? ` · cost ${fmtPrice(p.cost)}` : ""}${p.margin_pct != null ? ` · <strong class="${p.margin_pct < 15 ? "neg" : "pos"}">${p.margin_pct}%</strong>` : ""}</span>`;
}

function drawQuote() {
  const x = currentQuote, locked = x.status === "converted";
  document.getElementById("form-card").style.display = "none";
  const card = document.getElementById("detail-card");
  card.style.display = "block";
  card.innerHTML = `
    <h3 class="detail-head" style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">${x.id ? `Quote ${escapeHtml(x.code)}` : "New Quote"}
      ${x.id ? `<span class="tag ${QUOTE_TAG[x.status]}">${x.status}</span>` : ""}
      <span style="margin-left:auto;"></span>
      ${x.id ? `<button class="secondary small-btn" onclick="openPdf('/api/quotes/${x.id}/pdf')">PDF / Print</button>` : ""}</h3>
    <div class="field-grid">
      <div class="wide"><label>Customer</label><select id="q-customer" ${locked ? "disabled" : ""}>${customers.map(c => `<option value="${c.id}" ${c.id === x.customer_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}</select></div>
      <div><label>Their reference / RFQ #</label><input type="text" id="q-ref" value="${escapeHtml(x.customer_ref || "")}"></div>
      <div><label>Valid until</label><input type="date" id="q-valid" value="${x.valid_until ? x.valid_until.substring(0, 10) : ""}"></div>
      <div class="wide"><label>Notes on the quote</label><textarea id="q-notes" rows="2">${escapeHtml(x.notes || "")}</textarea></div>
    </div>
    ${locked ? "" : `<details class="fold-section" ${x.lines.length ? "" : "open"} style="margin:10px 0;">
      <summary><strong>Paste the customer's request</strong> <span class="muted small">— email text or rows copied from Excel; each line becomes a quote line</span></summary>
      <textarea id="q-paste" rows="6" placeholder="e.g.\n500 pcs 5/8-11 x 2 A325 HDG hex bolt\n1,000 - 5/8 F436 washer HDG\n15343   250"></textarea>
      <button class="ai-btn" data-icon="sparkles" onclick="readQuoteText()" style="margin-top:6px;">Read Lines</button>
      <span id="q-paste-msg" class="small muted"></span></details>`}
    <h4>Lines</h4>
    <div class="table-scroll"><table class="lines-table fit-table">
      <thead><tr><th class="grow">Item</th><th class="num">Qty</th><th class="num">Unit price</th><th class="num">Amount</th><th></th></tr></thead>
      <tbody id="q-lines" oninput="quoteTotals()">${x.lines.map(quoteLineRow).join("")}</tbody>
      <tfoot><tr><td class="grow">Quote total</td><td></td><td></td><td class="num" id="q-total"></td><td></td></tr></tfoot>
    </table></div>
    ${locked ? "" : `<button class="secondary" onclick="addQuoteLine()" style="margin-top:8px;">+ Add line</button>`}
    <div id="q-error" class="error"></div>
    <div class="btn-row">
      ${locked ? `<span class="muted">Converted to order — <a class="link" onclick="showDetail(${x.order_id})">open it</a>.</span>` : `<button onclick="saveQuote()">Save Quote</button>`}
      ${x.id && !locked ? `${["sent", "accepted", "declined"].filter(s => s !== x.status).map(s => `<button class="secondary" onclick="setQuoteStatus('${s}')">Mark ${s[0].toUpperCase() + s.slice(1)}</button>`).join("")}
        <button onclick="convertQuote()" data-icon="check">Convert to Order</button>
        <button class="danger" onclick="deleteQuote()">Delete</button>` : ""}
      <button class="secondary" onclick="document.getElementById('detail-card').style.display='none'">Close</button>
    </div>`;
  quoteTotals();
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
    const p = await apiFetch(`/api/quotes/price/${id}?customer_id=${document.getElementById("q-customer").value}`);
    tr.querySelector(".q-price").value = p.price;
    tr.querySelector(".q-basis").innerHTML = quoteBasis(p);
    quoteTotals();
  } catch (e) {}
}
function pickQuoteCandidate(a, id) {
  const sel = a.closest("tr").querySelector(".q-item");
  sel.value = id;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
}
async function readQuoteText() {
  const msg = document.getElementById("q-paste-msg");
  msg.textContent = "Reading…";
  try {
    const rows = await apiFetch("/api/quotes/parse", { method: "POST", body: JSON.stringify({
      customer_id: parseInt(document.getElementById("q-customer").value), text: document.getElementById("q-paste").value }) });
    document.getElementById("q-lines").insertAdjacentHTML("beforeend", rows.map(quoteLineRow).join(""));
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
async function deleteQuote() {
  if (!confirm(`Delete quote ${currentQuote.code}?`)) return;
  await apiFetch(`/api/quotes/${currentQuote.id}`, { method: "DELETE" });
  document.getElementById("detail-card").style.display = "none";
  await loadQuotes(); renderOrders();
}
