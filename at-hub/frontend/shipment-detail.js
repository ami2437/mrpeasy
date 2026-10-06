// Shipment detail shared by the Shipments page and Bulk Operations. The main screen (showDetail) only SHOWS a shipment:
// items, timeline, accepted packing, carrier, POD, delivery, invoicing. Picking, packing and shipping -- and undoing them --
// happen in one pop-up, Process Shipment (openProcess); once shipped it opens as Modify Shipment.
// The page provides the data globals (shipments, orders, items, customers, lots,
// shipmentsById, currentShipmentId) and reloadList(); it can set SHIPMENT_DETAIL_CONTAINER
// and onShipmentDetailClose. Only one detail is open at a time (element ids are fixed).
let SHIPMENT_DETAIL_CONTAINER = "detail-card";
let onShipmentDetailClose = null;
// Pack-size memory: {shipment id: {order line id: {size, source, label}}} from /api/shipments/pack-suggestions
// (the customer's / last packed size or the item default, by the rule chosen on Bulk Operations).
const packSuggest = {};
async function loadPackSuggestions(ids) {
  ids = [...new Set(ids)].filter(Boolean);
  if (!ids.length) return;
  try { Object.assign(packSuggest, await apiFetch(`/api/shipments/pack-suggestions?ids=${ids.join(",")}`)); } catch {}
}

function detailContainer() { return document.getElementById(SHIPMENT_DETAIL_CONTAINER); }
function closeShipmentDetail() {
  const el = detailContainer();
  if (el) { el.style.display = "none"; el.innerHTML = ""; }
  if (onShipmentDetailClose) onShipmentDetailClose();
}

function orderCode(id) { const o = orders.find(o => o.id === id); return o ? o.code : id; }
function order(id) { return orders.find(o => o.id === id); }
function itemLabel(id) { const i = items.find(i => i.id === id); return i ? `${i.code} — ${i.title}` : id; }
function itemCode(id) { const i = items.find(i => i.id === id); return i ? i.code : id; }
function itemObj(id) { return items.find(i => i.id === id); }
function customerName(id) { const c = customers.find(c => c.id === id); return c ? c.name : id; }


function lotCode(id) { const lot = lots.find(l => l.id === id); return lot ? lot.lot_code : ""; }

// ---- One source of truth: the main screen SHOWS a shipment; only the Process pop-up (openProcess) changes it ----
const SHIPPED = ["shipped", "delivered", "invoiced"];
let detailShownId = null;  // the shipment the main screen is showing (redrawn after changes made in the pop-up)

// Booked -> Picked -> Packed -> Shipped -> Delivered -> Invoiced, each step done / current / to do.
function shipTimelineHtml(sh, invoice = null, compact = false) {
  const picked = sh.lines.reduce((t, l) => t + (l.picked_quantity || 0), 0), total = sh.lines.reduce((t, l) => t + l.quantity, 0);
  const shipped = SHIPPED.includes(sh.status), delivered = !!sh.delivered_at || ["delivered", "invoiced"].includes(sh.status);
  const steps = [
    ["Booked", true, fmtDate(sh.created_at)],
    ["Picked", total > 0 && picked >= total - 1e-9, picked > 0 && picked < total ? `${fmtQty(picked)} of ${fmtQty(total)}` : ""],
    ["Packed", !!sh.packed_at || shipped, sh.packed_at ? `${fmtDate(sh.packed_at)}${sh.packed_by ? ` · ${sh.packed_by}` : ""}` : ""],
    ["Shipped", shipped, sh.ship_date ? fmtDate(sh.ship_date) : ""],
    ["Delivered", delivered, sh.delivered_at ? fmtDate(sh.delivered_at) : ""],
    ["Invoiced", sh.status === "invoiced", invoice ? invoice.code : ""],
  ].slice(0, compact ? 4 : 6);
  const current = steps.findIndex(s => !s[1]);
  if (sh.status === "cancelled") return `<div class="ship-timeline"><span class="tl-step cancelled">${icon("x")} Cancelled</span></div>`;
  return `<ol class="ship-timeline ${compact ? "compact" : ""}">${steps.map(([label, done, note], i) =>
    `<li class="tl-step ${done ? "done" : i === current ? "current" : ""}"><span class="tl-dot">${done ? icon("check") : i + 1}</span>
      <span class="tl-label">${label}${note && !compact ? `<span class="tl-note">${escapeHtml(note)}</span>` : ""}</span></li>`).join("")}</ol>`;
}

// Items on the main screen: read-only. Per order line: ordered, shipped before, this shipment (by lot), picked, left after.
function linesSectionHtml(shipment) {
  const isShipped = SHIPPED.includes(shipment.status);
  const ord = order(shipment.order_id);
  const sorted = shipment.lines.slice().sort((a, b) => (a.line_no || 0) - (b.line_no || 0) || a.id - b.id);
  const group = {};
  sorted.forEach(l => { const g = group[l.order_line_id] ??= { first: l.id, n: 0, qty: 0 }; g.n++; g.qty += l.quantity; });
  const olOf = id => (ord && ord.lines.find(x => x.id === id)) || null;
  return `
    <h4 class="dsec-title">Items</h4>
    <table class="fit-table ship-lines">
      <thead><tr><th title="Order line">Line</th><th class="grow">Item</th><th>Lot</th>
        <th class="num" title="Quantity on the order line">Ordered</th>
        <th class="num" title="Shipped on earlier shipments of this order">Shipped before</th>
        <th class="num" title="Booked into this shipment, one row per lot">Booked (by lot)</th>
        <th class="num" title="The whole order line on this shipment -- every lot added up">Line total</th><th class="num">Picked</th>
        <th class="num" title="Still to ship on the order line once this shipment has gone">Left after this</th></tr></thead>
      <tbody>
        ${sorted.map(l => {
          const g = group[l.order_line_id], ol = olOf(l.order_line_id), first = g.first === l.id;
          const before = ol ? Math.max(0, ol.shipped_quantity - (isShipped ? g.qty : 0)) : null;
          const after = ol ? Math.max(0, ol.quantity - before - g.qty) : null;
          const span = g.n > 1 ? ` rowspan="${g.n}"` : "";
          return `<tr>
            ${first ? `<td class="line-no"${span}>#${l.line_no ?? ""}</td><td class="grow"${span}>${itemLabel(l.item_id)}</td>` : ""}
            <td>${lotCode(l.lot_id)}</td>
            ${first ? `<td class="num"${span}>${ol ? fmtQty(ol.quantity) : ""}</td><td class="num muted"${span}>${before != null ? fmtQty(before) : ""}</td>` : ""}
            <td class="num">${g.n > 1 ? fmtQty(l.quantity) : `<strong>${fmtQty(l.quantity)}</strong>`}</td>
            ${first ? `<td class="num"${span}><strong>${fmtQty(g.qty)}</strong>${g.n > 1 ? `<div class="muted small">${g.n} lots</div>` : ""}</td>` : ""}
            <td class="num">${fmtQty(l.picked_quantity)}${l.picked_quantity >= l.quantity ? " ✓" : ""}</td>
            ${first ? `<td class="num"${span}>${after == null ? "" : after > 0 ? `<strong>${fmtQty(after)}</strong>` : `<span class="pos">0 ✓</span>`}</td>` : ""}
          </tr>`;
        }).join("")}
      </tbody>
    </table>`;
}

// Packing on the main screen: what's saved / accepted, read-only. Changes happen in Process Shipment.
function packingReadOnlyHtml(sh) {
  const n = sh.boxes.length;
  const tag = sh.packed_at ? `<span class="tag shipped">Packing accepted · ${n} box${n === 1 ? "" : "es"}</span>`
    : n ? `<span class="tag draft">Proposed · ${n} box${n === 1 ? "" : "es"} · not accepted</span>`
    : `<span class="tag draft">Not packed yet</span>`;
  const rows = shippedByLine(sh).map(e => {
    const saved = sh.boxes.filter(b => b.order_line_id === e.order_line_id);
    const byQty = {};
    saved.forEach(b => { byQty[b.quantity_in_box] = (byQty[b.quantity_in_box] || 0) + 1; });
    return `<tr><td class="line-no">#${e.line_no ?? ""}</td><td class="grow">${itemLabel(e.item_id)}</td><td class="num">${fmtQty(e.qty)}</td>
      <td class="num">${saved.length ? packSizeFor(sh, e) : `<span class="muted" title="Not packed yet -- what Process Shipment will pre-fill">${packSizeFor(sh, e)}</span>`}</td>
      <td>${saved.length ? formatBoxCounts(byQty) : `<span class="muted">—</span>`}</td><td>${escapeHtml(linePallet(sh, e.order_line_id)) || `<span class="muted">—</span>`}</td></tr>`;
  }).join("");
  const used = [...new Set(sh.boxes.map(b => b.pallet_number).filter(Boolean))].sort(byPalletNo);
  const po = (order(sh.order_id) || {}).po_number || "";
  const pallets = used.map(pn => {
    const p = sh.pallets.find(x => x.pallet_number === pn) || {};
    const boxes = sh.boxes.filter(b => b.pallet_number === pn);
    const missing = `<span class="neg small">missing</span>`;
    return `<tr><td><strong>${escapeHtml(pn)}</strong></td><td class="grow">${escapeHtml([...new Set(boxes.map(b => itemCode(b.item_id)))].join(", "))}</td>
      <td class="num">${boxes.length}</td><td>${p.weight != null ? `${fmtQty(p.weight)} lbs` : missing}</td><td>${p.dimensions ? escapeHtml(p.dimensions) : missing}</td><td>${escapeHtml(po) || "—"}</td></tr>`;
  }).join("");
  return `<h4 class="dsec-title">Packing ${tag}</h4>
    ${sh.packed_at ? `<p class="muted small" style="margin-top:0;">Accepted ${fmtDate(sh.packed_at)}${sh.packed_by ? ` by ${escapeHtml(sh.packed_by)}` : ""}. To change it, use <b>Process Shipment</b>.</p>`
      : `<p class="muted small" style="margin-top:0;">Packing is done in <b>Process Shipment</b>.${n ? "" : " Grey sizes are what it will pre-fill."}</p>`}
    <table class="fit-table no-table-tools">
      <thead><tr><th>Line</th><th class="grow">Item</th><th class="num">Qty</th><th class="num">Pack size</th><th>Boxes</th><th>Pallet #</th></tr></thead>
      <tbody>${rows}</tbody></table>
    ${used.length ? `<h5 class="dsub-title">Pallets</h5><table class="fit-table no-table-tools">
      <thead><tr><th>Pallet #</th><th class="grow">Items</th><th class="num">Boxes</th><th>Weight</th><th>Dimensions (L x W x H in)</th><th>Customer PO #</th></tr></thead>
      <tbody>${pallets}</tbody></table>` : ""}
    ${n ? `<div style="margin-top:12px;">
      <button class="secondary" onclick="printLabels(${sh.id})">Print Labels</button>
      ${used.length ? `<button class="secondary" onclick="printPalletLabels(${sh.id})" title="4x6 shipment pallet labels: PO #, job # and every pallet with its customer item #s" data-icon="layers">Pallet Labels</button>` : ""}
      <button class="secondary" onclick="location.href='labels.html?shipment_id=${sh.id}'" title="Edit a label before printing, print-only">Custom Label</button>
      <button class="secondary" onclick="printPackingList(${sh.id})">Packing List PDF</button>
      <button class="secondary" onclick="exportPackingList(${sh.id}, 'xlsx')" title="The packing list as an Excel workbook: header, lines, boxes and pallets">Excel</button>
      <button class="secondary" onclick="exportPackingList(${sh.id}, 'csv')" title="The packing list lines as a CSV file (opens in Excel, imports anywhere)">CSV</button>
      <span class="muted small" style="margin-left:6px;">Print on packing list:</span>
      <label class="inline-check"><input type="checkbox" id="pl-boxes" checked> Box details</label>
      <label class="inline-check"><input type="checkbox" id="pl-pallets" ${used.length ? "checked" : ""}> Pallet info</label>
      <label class="inline-check" title="How many boxes ride on each pallet, in the pallet table (off unless needed)"><input type="checkbox" id="pl-pallet-boxes"> Boxes per pallet</label>
      <label class="inline-check"><input type="checkbox" id="pl-lots"> Lot #</label>
      <label class="inline-check" title="Line notes from the order (a note marked 'don't print' never prints)"><input type="checkbox" id="pl-notes" checked> Line notes</label>
    </div>` : ""}`;
}

function carrierReadOnlyHtml(sh) {
  const v = x => x ? escapeHtml(String(x)) : `<span class="muted">—</span>`;
  return `<h4 class="dsec-title">Carrier</h4>
    <div class="carrier-grid ro">
      <div><label>Carrier</label><div>${v(sh.carrier)}</div></div>
      <div><label>Tracking Number</label><div>${sh.tracking_number ? trackingLink(sh.carrier, sh.tracking_number) : v(sh.tracking_number)}</div></div>
      ${hidesMoney() ? "" : `<div class="money-field"><label>Shipping Cost</label><div>${sh.shipping_cost != null ? fmtMoney(sh.shipping_cost) : `<span class="muted">—</span>`}</div></div>`}
    </div>
    ${sh.notes ? `<div class="carrier-notes"><label>Notes</label><div>${escapeHtml(sh.notes)}</div></div>` : ""}
    <p class="muted small">Carrier and tracking are entered in <b>${SHIPPED.includes(sh.status) ? "Modify Shipment" : "Process Shipment"}</b>.</p>`;
}

// Any change to a shipment, from the pop-up or elsewhere: refresh the pop-up (if open), the list, and the main screen.
async function afterShipmentChange(id, step = null) {
  if (proc && proc.id === id) {
    const sh = await apiFetch(`/api/shipments/${id}`);
    shipmentsById[id] = sh;
    if (sh.status === "cancelled") { closeProcess(true); toast(`${sh.code} is cancelled — nothing left on it`); }
    else {
      proc.dirty = false;
      proc.step = step && procCanOpen(sh, step) ? step : procCanOpen(sh, proc.step) ? proc.step : procNextStep(sh);
      renderProc();
    }
  }
  await reloadList();
  const card = detailContainer();
  if (detailShownId === id && card && card.style.display !== "none") await showDetail(id);
}

async function shipmentAction(id, action, body, step = null) {
  const errorEl = document.getElementById("proc-error") || document.getElementById("lifecycle-error");
  if (errorEl) errorEl.textContent = "";
  try {
    await apiFetch(`/api/shipments/${id}/${action}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
    await afterShipmentChange(id, step);
  } catch (err) {
    if (errorEl) errorEl.textContent = err.message; else toast(err.message);
  }
}

// Undo packing on a shipment that hasn't shipped: boxes, pallets and "packing accepted" go; picking stays.
async function confirmUnpack(codes) {
  const many = codes.length > 1;
  const { value } = await askDialog({ title: many ? `Unpack ${codes.length} shipments?` : `Unpack ${codes[0]}?`, tone: "warn",
    body: `<p>Boxes and pallets are cleared${many ? "" : ` on <b>${escapeHtml(codes[0])}</b>`}. Picking stays.</p>`,
    buttons: [{ label: "Unpack", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  return value === "go";
}
// Undo picking on a shipment that hasn't shipped: every line back to 0 picked; bookings and packing stay.
async function confirmUnpick(codes) {
  const many = codes.length > 1;
  const { value } = await askDialog({ title: many ? `Unpick ${codes.length} shipments?` : `Unpick ${codes[0]}?`, tone: "warn",
    body: `<p>Picked quantities go back to 0. Bookings and boxes stay; the packing is re-checked and accepted again after picking.</p>`,
    buttons: [{ label: "Unpick", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  return value === "go";
}
// Undo Confirm Bookings: back to New (stock stays booked). Only while nothing is picked.
async function confirmUnconfirm(codes) {
  const many = codes.length > 1;
  const { value } = await askDialog({ title: many ? `Unconfirm bookings on ${codes.length} shipments?` : `Unconfirm bookings on ${codes[0]}?`, tone: "warn",
    body: `<p>Back to <b>New</b>. Stock stays booked.</p>`,
    buttons: [{ label: "Unconfirm", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  return value === "go";
}
async function unconfirmShipment(id) {
  const sh = shipmentsById[id] || shipments.find(s => s.id === id);
  if (!(await confirmUnconfirm([sh ? sh.code : `#${id}`]))) return;
  await shipmentAction(id, "unconfirm-booking");
}
async function unpickShipment(id) {
  const sh = shipmentsById[id] || shipments.find(s => s.id === id);
  if (!(await confirmUnpick([sh ? sh.code : `#${id}`]))) return;
  await shipmentAction(id, "unpick");
}
async function unpackShipment(id) {
  const sh = shipmentsById[id] || shipments.find(s => s.id === id);
  if (!(await confirmUnpack([sh ? sh.code : `#${id}`]))) return;
  await shipmentAction(id, "unpack");
}

function allPicked(shipment) { return shipment.lines.every(l => (l.picked_quantity || 0) >= l.quantity - 1e-9); }

// ---- Process Shipment: the ONE place a shipment is picked, packed and shipped. Once shipped it opens as
// Modify Shipment (carrier / tracking, Undo Ship). Each finished step can be undone here, and only here.
let proc = null;  // { id, step: pick | pack | ship | shipped, dirty }
const PROC_STEPS = [["pick", "Pick"], ["pack", "Pack"], ["ship", "Ship"]];
function procNextStep(sh) {
  if (SHIPPED.includes(sh.status)) return "shipped";
  if (!allPicked(sh)) return "pick";
  return sh.packed_at ? "ship" : "pack";
}
function procCanOpen(sh, step) {
  if (SHIPPED.includes(sh.status)) return step === "shipped";
  if (step === "ship") return allPicked(sh) && !!sh.packed_at;
  if (step === "pack") return allPicked(sh);  // pick first, then pack: the stage tabs (To pick / To pack / Ready to ship) stay clean
  return step === "pick";
}
function procDone(sh, step) { return step === "pick" ? allPicked(sh) : step === "pack" ? !!sh.packed_at : SHIPPED.includes(sh.status); }

async function openProcess(id, step = null) {
  const [sh] = await Promise.all([apiFetch(`/api/shipments/${id}`), packSuggest[id] ? null : loadPackSuggestions([id])]);
  if (sh.status === "cancelled") { toast(`${sh.code} is cancelled`); return; }
  shipmentsById[id] = sh;
  currentShipmentId = id;
  let back = document.getElementById("proc");
  if (!back) {
    back = document.createElement("div");
    back.className = "glass-back";
    back.id = "proc";
    back.addEventListener("click", e => { if (e.target === back) closeProcess(); });
    document.body.appendChild(back);
    document.body.classList.add("glass-open");
    document.addEventListener("keydown", procKey);
  }
  proc = { id, step: step && procCanOpen(sh, step) ? step : procNextStep(sh), dirty: false };
  renderProc();
}
function procKey(e) { if (e.key === "Escape" && proc && !document.querySelector(".modal-backdrop")) closeProcess(); }

// Close: unsaved packing edits are only dropped after asking. The screen behind is redrawn from what's saved.
async function closeProcess(force = false) {
  if (!proc) return;
  if (!force && proc.dirty) {
    const { value } = await askDialog({ title: "Discard unsaved changes?", tone: "warn",
      body: "<p>The packing you changed hasn't been accepted.</p>",
      buttons: [{ label: "Discard", value: "go", cls: "danger" }, { label: "Keep Editing", value: null, cls: "secondary" }] });
    if (value !== "go") return;
  }
  const id = proc.id, back = document.getElementById("proc");
  proc = null;
  document.removeEventListener("keydown", procKey);
  document.body.classList.remove("glass-open");
  if (back) { back.id = ""; back.classList.add("closing"); setTimeout(() => back.remove(), 180); }
  await reloadList();
  const card = detailContainer();
  if (detailShownId === id && card && card.style.display !== "none") await showDetail(id);
}

async function procGo(step) {
  if (!proc || step === proc.step) return;
  if (proc.dirty) {
    const { value } = await askDialog({ title: "Leave without accepting?", tone: "warn", body: "<p>Your packing changes will be lost.</p>",
      buttons: [{ label: "Discard Changes", value: "go", cls: "danger" }, { label: "Stay", value: null, cls: "secondary" }] });
    if (value !== "go") return;
  }
  proc.step = step;
  proc.dirty = false;
  renderProc();
}

function renderProc() {
  const sh = shipmentsById[proc.id], back = document.getElementById("proc"), shipped = SHIPPED.includes(sh.status);
  const ord = order(sh.order_id) || {};
  back.innerHTML = `<div class="glass-panel proc-panel ${shipped ? "ship-done" : "ship-inproc"}" role="dialog" aria-modal="true" aria-labelledby="proc-title">
    <div class="sm-head"><div>
        <h3 id="proc-title">${shipped ? "Modify" : "Process"} ${escapeHtml(sh.code)}</h3>
        <div class="muted small">${escapeHtml(ord.code || "")} · ${escapeHtml(customerName(ord.customer_id) || "")}${ord.po_number ? ` · PO ${escapeHtml(ord.po_number)}` : ""}</div>
        ${shipTimelineHtml(sh, null, true)}</div>
      <button type="button" class="icon-btn sm-close" aria-label="Close" onclick="closeProcess()">${icon("x")}</button></div>
    ${shipped ? "" : `<div class="proc-tabs" role="tablist">${PROC_STEPS.map(([k, label], i) => `<button type="button" role="tab" aria-selected="${proc.step === k}"
        class="proc-tab ${proc.step === k ? "on" : ""} ${procDone(sh, k) ? "done" : ""}" ${procCanOpen(sh, k) ? "" : "disabled"} onclick="procGo('${k}')"
        title="${!procCanOpen(sh, k) ? (k === "pack" ? "Pick everything first" : "Pick everything and accept the packing first") : ""}">${procDone(sh, k) ? icon("check") : `<span class="proc-n">${i + 1}</span>`}${label}</button>`).join("")}</div>`}
    <div class="proc-body" id="proc-body">${{ pick: procPickHtml, pack: procPackHtml, ship: procShipHtml, shipped: procShippedHtml }[proc.step](sh)}</div>
    <div class="error" id="proc-error"></div>
    <div class="sm-foot proc-foot">${{ pick: procPickFoot, pack: procPackFoot, ship: procShipFoot, shipped: procShippedFoot }[proc.step](sh)}</div></div>`;
  decorateIcons(back);
  if (proc.step === "pack") {
    palletStash = {};
    refreshBoxSummary();
    renderPalletTable();
    const body = document.getElementById("proc-body");
    const mark = () => { proc.dirty = true; };
    body.addEventListener("input", mark);
    body.addEventListener("change", mark);
  }
}

// ---- step 1: Pick (and the bookings: unbook, unbook all, unconfirm) ----
function procPickHtml(sh) {
  const sorted = sh.lines.slice().sort((a, b) => (a.line_no || 0) - (b.line_no || 0) || a.id - b.id);
  return `<p class="muted small" style="margin-top:0;">${sh.status === "new" ? "Booked, not confirmed — picking confirms the bookings." : "Bookings confirmed."}
      Pick now starts at everything left; lower it to pick part of a line (0 = none) — whatever isn't picked is unbooked (back to stock, still open on the order).
      Unbook sends unpicked stock back to the shelf.</p>
    <table class="fit-table no-table-tools">
      <thead><tr><th>Line</th><th class="grow">Item</th><th>Lot</th><th class="num">Booked</th><th class="num">Picked</th><th>Pick now</th><th>Unbook</th></tr></thead>
      <tbody>${sorted.map(l => { const left = Math.max(0, l.quantity - (l.picked_quantity || 0)); return `<tr>
        <td class="line-no">#${l.line_no ?? ""}</td><td class="grow">${itemLabel(l.item_id)}</td><td>${lotCode(l.lot_id)}</td>
        <td class="num">${fmtQty(l.quantity)}</td><td class="num">${fmtQty(l.picked_quantity)}${left <= 0 ? " ✓" : ""}</td>
        <td>${left > 0 ? pickQtyInput(sh, l) : ""}</td>
        <td class="unbook-cell">${left > 0 ? `<input type="number" step="1" min="1" max="${left}" placeholder="${left}" id="unbook-${l.id}" class="qty-input unbook-qty" title="Blank = all ${left}">
          <button class="small-btn secondary" onclick="unbookLine(${sh.id}, ${l.id})">Unbook</button>` : `<span class="muted small">Picked</span>`}</td></tr>`; }).join("")}</tbody>
    </table>`;
}
function procPickFoot(sh) {
  const picked = sh.lines.some(l => (l.picked_quantity || 0) > 0), unpicked = sh.lines.some(l => l.quantity - (l.picked_quantity || 0) > 1e-9);
  return `${picked ? `<button type="button" class="danger" onclick="unpickShipment(${sh.id})" title="Picked quantities back to 0 (bookings and packing stay)">Unpick</button>` : ""}
    ${sh.status === "ready" && !picked ? `<button type="button" class="danger" onclick="unconfirmShipment(${sh.id})" title="Back to New -- stock stays booked">Unconfirm Bookings</button>` : ""}
    ${unpicked ? `<button type="button" class="danger" onclick="unbookAll(${sh.id})" title="Every unpicked unit back to stock in one go">Unbook All</button>` : ""}
    <span class="spacer"></span>
    ${unpicked ? `<button type="button" class="next-step" onclick="procPick()" title="Records the Pick now quantities">Pick</button>`
      : `<button type="button" class="next-step" onclick="procGo('${sh.packed_at ? "ship" : "pack"}')">Next: ${sh.packed_at ? "Ship" : "Pack"} →</button>`}`;
}
// Pick now box (single and bulk Process): starts at everything left; lowered turns amber
function pickQtyInput(sh, l) {
  const left = Math.max(0, l.quantity - (l.picked_quantity || 0));
  return `<input type="number" step="1" min="0" max="${left}" class="pick-qty qty-input" data-sh="${sh.id}" data-line="${l.id}" data-left="${left}" value="${left}"
    oninput="this.classList.toggle('pick-short', (parseFloat(this.value) || 0) < ${left} - 1e-9)" title="Booked and not yet picked: ${fmtQty(left)}">`;
}
// Read the Pick now boxes under `scope`, grouped per shipment. Returns { error } or { picks: [{ sh, lines, short }] }.
function readPickInputs(scope, shOf) {
  const per = new Map();
  for (const el of scope.querySelectorAll(".pick-qty")) {
    const sh = shOf(parseInt(el.dataset.sh)), line = sh.lines.find(l => l.id === parseInt(el.dataset.line));
    const left = parseFloat(el.dataset.left), qty = el.value.trim() === "" ? 0 : Number(el.value);
    if (!Number.isInteger(qty) || qty < 0 || qty > left + 1e-9) return { error: `${sh.code} line #${line.line_no ?? ""}: pick a whole number from 0 to ${fmtQty(left)}.` };
    if (!per.has(sh.id)) per.set(sh.id, { sh, lines: [], short: [] });
    const p = per.get(sh.id);
    if (qty > 0) p.lines.push({ shipment_line_id: line.id, quantity: qty });
    if (qty < left - 1e-9) p.short.push({ line, left, qty });
  }
  for (const p of per.values())
    if (!p.lines.length && !(p.short.length && p.sh.lines.some(l => (l.picked_quantity || 0) > 0)))
      return { error: `${p.sh.code}: nothing to pick — enter a quantity, or Unbook All to release it.` };
  return { picks: [...per.values()] };
}
// Picking less than booked: the short qty is unbooked (back to stock, still open on the order) -- list it and confirm first
async function confirmShortPick(picks) {
  const short = picks.flatMap(p => p.short.map(r => ({ ...r, sh: p.sh })));
  if (!short.length) return true;
  const many = picks.length > 1, units = short.reduce((t, r) => t + r.left - r.qty, 0);
  const { value } = await askDialog({ title: `Picking less than booked on ${short.length} line${short.length === 1 ? "" : "s"}`, tone: "warn",
    body: `<table class="fit-table no-table-tools"><thead><tr>${many ? "<th>Shipment</th>" : ""}<th>Line</th><th class="grow">Item</th><th class="num">To pick</th><th class="num">Picking</th><th class="num">Unbooked</th></tr></thead>
      <tbody>${short.map(r => `<tr>${many ? `<td class="nowrap">${escapeHtml(r.sh.code)}</td>` : ""}<td class="line-no">#${r.line.line_no ?? ""}</td><td class="grow">${itemLabel(r.line.item_id)}</td>
        <td class="num">${fmtQty(r.left)}</td><td class="num">${fmtQty(r.qty)}</td><td class="num"><b>${fmtQty(r.left - r.qty)}</b></td></tr>`).join("")}</tbody></table>
      <p>The <b>${fmtQty(units)}</b> not picked is <b>unbooked</b>: it goes back to stock, ${many ? "each shipment" : escapeHtml(short[0].sh.code)} moves on with what's picked,
      and the customer order keeps it open so it can be booked on a later shipment.</p>`,
    buttons: [{ label: "Pick & Unbook the Rest", value: "go", cls: "confirm-btn" }, { label: "Go Back", value: null, cls: "secondary" }] });
  return value === "go";
}
// Confirm (if new) and record one shipment's picks; whatever wasn't picked is unbooked
async function savePick(p) {
  if (p.sh.status === "new") await apiFetch(`/api/shipments/${p.sh.id}/confirm-booking`, { method: "POST" });  // picking confirms the bookings
  return apiFetch(`/api/shipments/${p.sh.id}/pick`, { method: "POST", body: JSON.stringify({ lines: p.lines, unbook_rest: p.short.length > 0 }) });
}
const shortUnits = picks => picks.reduce((t, p) => t + p.short.reduce((u, r) => u + r.left - r.qty, 0), 0);

async function procPick() {
  const sh = shipmentsById[proc.id], err = document.getElementById("proc-error");
  err.textContent = "";
  const read = readPickInputs(document.getElementById("proc-body"), () => sh);
  if (read.error) { err.textContent = read.error; return; }
  if (!(await confirmShortPick(read.picks))) return;
  try {
    const after = await savePick(read.picks[0]), units = shortUnits(read.picks);
    if (units) toast(`${units.toLocaleString()} unbooked — back in stock, still open on the order`);
    await afterShipmentChange(sh.id, allPicked(after) ? (after.packed_at ? "ship" : "pack") : "pick");  // all picked: on to packing
    if (allPicked(after) && !after.packed_at && after.boxes.length) await askDialog({ title: "Re-check the packing", tone: "warn",
      body: `<p>${escapeHtml(after.code)} was packed before it went back a step. Check the boxes and pallets, then <b>Accept Packaging</b>.</p>`,
      buttons: [{ label: "Review Packing", value: "ok", cls: "confirm-btn" }] });
  } catch (e) { err.textContent = e.message; }
}

// ---- step 2: Pack (pack sizes, boxes, pallets) ----
function procPackHtml(sh) {
  return `${sh.boxes.length && !sh.packed_at ? `<div class="notice small"><b>Re-check needed</b> — these boxes were saved earlier
      (packed before the shipment went back a step, or proposed in bulk). Check them, then <b>Accept Packaging</b>.</div>` : ""}
    <p class="muted small" style="margin-top:0;">Each order line is split into boxes by its pack size — pre-filled from what was packed before
      (see the note under each size). Lines are packed separately even when they're the same item.</p>
    <div class="pack-tools-row">
      <div><label>Pallet # for every line (optional)</label>
        <input type="text" id="default-pallet" placeholder="E.g. PLT-1" oninput="applyPalletToAll(this.value)"></div>
      <details class="pack-paste" id="pack-paste-box">
        <summary class="link" style="cursor:pointer;">Paste pack sizes</summary>
        <textarea id="pack-paste" rows="4" style="font-family:monospace;margin-top:6px;" placeholder="Item #   Pack size&#10;16713   50&#10;15420   100"></textarea>
        <div class="muted small">Item # and pack size, one per line — space, tab or comma (straight from Excel works). Header row optional.</div>
        <div><button class="secondary" onclick="applyPastedPackSizes()" style="margin-top:6px;">Apply Pack Sizes</button>
          <span id="pack-paste-status" class="muted small"></span></div>
      </details>
    </div>
    <table class="fit-table">
      <thead><tr><th>Line</th><th class="grow">Item</th><th class="num">Qty</th><th>Pack size</th><th>Boxes</th><th>Pallet #</th></tr></thead>
      <tbody>${packSizeRowsHtml(sh)}</tbody>
    </table>
    <details id="box-details" style="margin-top:12px;">
      <summary class="link" style="cursor:pointer;">Edit individual boxes (<span id="box-count">0</span>) — uneven splits, lot code or pallet per box</summary>
      <table class="lines-table" style="margin-top:8px;">
        <thead><tr><th>Order line</th><th>Box #</th><th>Qty in box</th><th>Lot code</th><th>Pallet #</th><th></th></tr></thead>
        <tbody id="box-rows" oninput="refreshBoxSummary(); renderPalletTable()" onchange="refreshBoxSummary(); renderPalletTable()">${boxRowsHtml(sh)}</tbody>
      </table>
      <button class="secondary" onclick="addBoxRow()" style="margin-top:8px;">+ Add box</button>
    </details>
    <h5 class="dsub-title">Pallets <span class="muted small">(optional)</span></h5>
    <p class="muted small">Which line goes on which pallet: the <b>Pallet #</b> at the end of each line above; each pallet's weight and dimensions below.
      Or paste both at once — <strong>Item # · Pallet # · Weight · Dimensions</strong> (or just <strong>Pallet # · Weight · Dimensions</strong>), space, tab or comma.</p>
    <details style="margin-bottom:10px;">
      <summary class="link" style="cursor:pointer;">Paste pallet data</summary>
      <textarea id="pallet-paste" rows="4" style="font-family:monospace;margin-top:6px;max-width:520px;" placeholder="${PALLET_PASTE_HINT}"></textarea>
      <div><button class="secondary" onclick="applyPastedPallets()" style="margin-top:6px;">Apply Pasted</button>
        <span id="pallet-paste-status" class="muted small"></span></div>
    </details>
    <div id="pallet-table"></div>`;
}
function procPackFoot(sh) {
  return `${sh.packed_at || sh.boxes.length ? `<button type="button" class="danger" onclick="unpackShipment(${sh.id})" title="Clear the boxes, pallets and accepted packing (picking stays)">Unpack</button>` : ""}
    ${sh.boxes.length ? `<button type="button" class="secondary" onclick="printLabels(${sh.id})" title="Labels for the saved boxes">Labels</button>
      <button type="button" class="secondary" onclick="printPackingList(${sh.id})" title="Packing list of the saved packing">Packing List</button>` : ""}
    <span class="spacer"></span>
    <button type="button" class="next-step" onclick="procAccept()">${sh.packed_at ? "Accept Changes" : "Accept Packaging"}</button>`;
}
async function procAccept() {
  const id = proc.id, err = document.getElementById("proc-error");
  err.textContent = "";
  if (!(await confirmPalletGaps(palletGaps(palletValues()), "Accept"))) return;
  try {
    await apiFetch(`/api/shipments/${id}/boxes`, { method: "PUT", body: JSON.stringify({ boxes: collectBoxes() }) });
    await apiFetch(`/api/shipments/${id}/pallet-weights`, { method: "PUT", body: JSON.stringify({ pallets: collectPallets() }) });
    const sh = await apiFetch(`/api/shipments/${id}/accept-packing`, { method: "POST" });
    proc.dirty = false;
    toast(`${sh.code}: packing accepted · ${sh.boxes.length} boxes`);
    await afterShipmentChange(id, allPicked(sh) ? "ship" : "pick");  // picked: on to Ship; else back to picking
  } catch (e) { err.textContent = e.message; }
}

// ---- step 3: Ship (carrier, tracking, the last checks) ----
function carrierInputsHtml(sh) {
  return `<div class="carrier-grid">
      <div><label>Carrier</label><input type="text" id="s-carrier" value="${escapeHtml(sh.carrier || "")}" placeholder="E.g. UPS, FedEx Freight"></div>
      <div><label>Tracking Number</label><input type="text" id="s-tracking" value="${escapeHtml(sh.tracking_number || "")}"></div>
      ${hidesMoney() ? `<input type="hidden" id="s-cost" value="">` : `<div class="money-field" title="What we pay the carrier"><label>Shipping Cost</label><input type="number" step="0.01" min="0" id="s-cost" value="${sh.shipping_cost ?? ""}"></div>`}
    </div>
    <div class="carrier-notes"><label>Notes</label><textarea id="s-notes" rows="2">${escapeHtml(sh.notes || "")}</textarea></div>`;
}
function procShipHtml(sh) {
  const gaps = savedPalletGaps(sh), pallets = new Set(sh.boxes.map(b => b.pallet_number).filter(Boolean)).size;
  return `<div class="proc-summary">${icon("checkCircle")} Picked · packed: <b>${sh.boxes.length} box${sh.boxes.length === 1 ? "" : "es"}</b>${pallets ? ` on <b>${pallets} pallet${pallets === 1 ? "" : "s"}</b>` : ""}</div>
    ${gaps.length ? `<div class="notice small"><b>Missing pallet data</b> — ${gaps.map(escapeHtml).join(" · ")}. Fix it under <a class="link" onclick="procGo('pack')">Pack</a>, or ship anyway.</div>` : ""}
    ${carrierInputsHtml(sh)}
    <p class="muted small">Ship Now takes the stock off on-hand. Carrier and tracking go on the packing list and the POD email.</p>`;
}
function procShipFoot(sh) {
  return `<span class="spacer"></span><button type="button" class="ship-now lit" id="proc-ship" onclick="procShip()">${icon("truck")} Ship Now</button>`;
}
async function saveCarrierFromPopup(id) {
  const cost = document.getElementById("s-cost").value;
  await apiFetch(`/api/shipments/${id}`, { method: "PUT", body: JSON.stringify({
    carrier: document.getElementById("s-carrier").value.trim() || null, tracking_number: document.getElementById("s-tracking").value.trim() || null,
    shipping_cost: cost ? parseFloat(cost) : null, notes: document.getElementById("s-notes").value || null }) });
}
async function procShip() {
  const id = proc.id, err = document.getElementById("proc-error"), btn = document.getElementById("proc-ship");
  err.textContent = "";
  try {
    if (!(await confirmPalletGaps(savedPalletGaps(shipmentsById[id]), "Ship"))) return;
    btn.disabled = true;
    await saveCarrierFromPopup(id);
    const sh = await apiFetch(`/api/shipments/${id}/ship`, { method: "POST" });
    const panel = document.querySelector("#proc .glass-panel"), done = document.createElement("div");
    done.className = "sm-done";
    done.innerHTML = `<div class="sm-done-check ship-truck">${icon("truck")}</div><h3>${escapeHtml(sh.code)} shipped</h3><p class="muted">Stock has left on-hand</p>`;
    panel.appendChild(done);
    requestAnimationFrame(() => done.classList.add("show"));
    await new Promise(r => setTimeout(r, 1200));
    await closeProcess(true);
  } catch (e) { err.textContent = e.message; if (btn) btn.disabled = false; }
}

// ---- once shipped: Modify (carrier / tracking, Undo Ship) ----
function procShippedHtml(sh) {
  return `<div class="proc-summary">${icon("truck")} Shipped ${fmtDate(sh.ship_date)} · ${sh.boxes.length} box${sh.boxes.length === 1 ? "" : "es"}
      ${sh.delivered_at ? ` · delivered ${fmtDate(sh.delivered_at)}` : ""}${sh.status === "invoiced" ? " · invoiced" : ""}</div>
    ${carrierInputsHtml(sh)}
    <p class="muted small"><b>Undo Ship</b> puts the stock back (still booked) and takes it back to picking${sh.status === "invoiced" ? " — its invoice is voided with it" : ""}.
      Delivery and invoicing are on the shipment screen.</p>`;
}
function procShippedFoot(sh) {
  return `${AuthGuard.can("shipments.undo") ? `<button type="button" class="danger" onclick="unshipShipment(${sh.id})">Undo Ship</button>` : ""}
    <span class="spacer"></span>
    <button type="button" class="next-step" onclick="procSaveCarrier()">Save Carrier Info</button>`;
}
async function procSaveCarrier() {
  const id = proc.id, err = document.getElementById("proc-error");
  err.textContent = "";
  try { await saveCarrierFromPopup(id); toast("Carrier info saved"); await afterShipmentChange(id); }
  catch (e) { err.textContent = e.message; }
}

async function unbookLine(shipmentId, lineId) {
  const box = document.getElementById(`unbook-${lineId}`);
  const qty = box.value.trim() === "" ? parseFloat(box.placeholder) : parseFloat(box.value);
  if (!Number.isInteger(qty) || qty <= 0) {
    (document.getElementById("proc-error") || document.getElementById("lifecycle-error")).textContent = "Unbook quantity must be a whole number greater than 0.";
    return;
  }
  await shipmentAction(shipmentId, "unbook", { shipment_line_id: lineId, quantity: qty });
}


async function unbookAll(id) {
  const sh = shipmentsById[id];
  const units = sh.lines.reduce((t, l) => t + Math.max(0, l.quantity - (l.picked_quantity || 0)), 0);
  const nonePicked = sh.lines.every(l => !(l.picked_quantity > 0));
  const { value } = await askDialog({ title: `Unbook all on ${sh.code}?`, tone: "warn",
    body: `<p>${fmtQty(units)} units go back to stock.${nonePicked ? " Nothing is picked, so the shipment is <b>cancelled</b>." : " Picked units stay."}</p>`,
    buttons: [{ label: "Unbook All", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value === "go") await shipmentAction(id, "unbook-all");
}

function cancelShipment(id) {
  if (!confirm("Cancel this shipment? Its bookings are released back to stock and its packing list is cleared.")) return;
  shipmentAction(id, "cancel");
}

// Delivered date: set by a POD upload, or by hand (managers and up). It's printed on the invoice.
function deliverySectionHtml(shipment) {
  if (!["shipped", "delivered", "invoiced"].includes(shipment.status)) return `<p class="muted">Available once the shipment has shipped.</p>`;
  const canMark = AuthGuard.can("shipments.deliver");
  const today = todayISO();
  return `
    ${shipment.delivered_at
      ? `<p><span class="tag delivered">Delivered</span> <strong>${fmtDate(shipment.delivered_at)}</strong>
          <span class="muted small">Recorded By ${escapeHtml(shipment.delivered_by || "")}</span></p>`
      : `<p class="muted">Not delivered yet — uploading a proof of delivery marks it delivered.</p>`}
    ${canMark ? `
      <div class="row" style="max-width:520px; align-items:flex-end;">
        <div><label>Delivered on</label><input type="date" id="delivered-date" value="${shipment.delivered_at ? dayISO(shipment.delivered_at) : today}" max="${today}"></div>
        <div style="flex:0;"><button class="secondary" style="white-space:nowrap;" onclick="markDelivered(${shipment.id})">${shipment.delivered_at ? "Change date" : "Mark Delivered"}</button></div>
        ${shipment.delivered_at ? `<div style="flex:0;"><a class="link small" style="white-space:nowrap;" onclick="clearDelivered(${shipment.id})">Clear Delivery</a></div>` : ""}
      </div>` : ""}
    <div id="delivery-error" class="error"></div>`;
}

async function markDelivered(id) {
  const errorEl = document.getElementById("delivery-error");
  errorEl.textContent = "";
  const value = document.getElementById("delivered-date").value;
  try {
    const sh = await apiFetch(`/api/shipments/${id}/delivered`, { method: "POST", body: JSON.stringify({ delivered_at: value ? `${value}T12:00:00` : null }) });
    await reloadList();
    showDetail(id);
    offerBilling([sh]);  // delivered -> bill it now?
  } catch (err) {
    errorEl.textContent = err.message;
  }
}

// Lifecycle "Mark Delivered (no POD)": today, then "bill it now?"
async function deliverNow(id) {
  await shipmentAction(id, "delivered", { delivered_at: null });
  if ((shipmentsById[id] || {}).status === "delivered") offerBilling([shipmentsById[id]]);
}

async function clearDelivered(id) {
  if (!confirm("Clear the delivery date? The shipment goes back to Shipped.")) return;
  try {
    await apiFetch(`/api/shipments/${id}/undeliver`, { method: "POST" });
    await reloadList();
    showDetail(id);
  } catch (err) {
    document.getElementById("delivery-error").textContent = err.message;
  }
}

// Undo Ship: stock goes back and stays booked; the shipment's invoice is voided with it. A draft
// invoice just gets a heads-up; a sent one walks through the steps first (payments, customer, reason).
async function unshipShipment(id) {
  let plan;
  try { plan = await apiFetch(`/api/shipments/${id}/undo-plan`); }
  catch (e) { (document.getElementById("proc-error") || document.getElementById("lifecycle-error")).textContent = e.message; return; }
  const what = `Stock goes back to its lots and stays booked; ${plan.code} returns to New so you can unbook, change, cancel or delete it.`
    + (plan.pods ? ` Its proof of delivery (${plan.pods} file${plan.pods === 1 ? "" : "s"}) stays on record.` : "");
  if (!plan.invoice || !plan.steps.length) {
    const { value } = await askDialog({ title: `Undo ${plan.code}?`, tone: plan.invoice ? "warn" : "",
      body: `<p>${what}</p>${plan.invoice ? `<p><strong>Draft invoice ${escapeHtml(plan.invoice.code)}</strong> (${fmtMoney(plan.invoice.total)}) is voided with it — it was never sent.</p>` : ""}`,
      buttons: [{ label: plan.invoice ? `Void ${plan.invoice.code} & Undo Shipment` : "Undo Shipment", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
    if (value === "go") shipmentAction(id, "unship", {});
    return;
  }
  undoWizard(id, plan, what);
}

let undoWhat = "", undoPaidRemoved = false;
function undoWizard(id, plan, what, paymentsRemoved = false) {
  undoWhat = what;
  undoPaidRemoved = paymentsRemoved;
  const inv = plan.invoice, has = k => plan.steps.includes(k);
  let back = document.getElementById("undo-wizard");
  if (!back) {
    back = document.createElement("div");
    back.id = "undo-wizard";
    back.className = "modal-backdrop";
    back.onclick = e => { if (e.target === back) back.remove(); };
    document.body.appendChild(back);
  }
  const sentTo = inv.emails.length ? `emailed to ${escapeHtml(inv.emails[inv.emails.length - 1].to)} on ${fmtDate(inv.emails[inv.emails.length - 1].sent_at)}` : `marked ${escapeHtml(inv.status)}`;
  const mail = inv.customer_email ? `mailto:${encodeURIComponent(inv.customer_email)}?subject=${encodeURIComponent(`Invoice ${inv.code} cancelled`)}&body=${encodeURIComponent(`Hello,

Please disregard invoice ${inv.code} (${fmtMoney(inv.total)}). It has been cancelled and a corrected invoice will follow once the shipment is re-sent.

Thank you.`)}` : "";
  let n = 0;
  const step = (done, title, body) => `<li class="undo-step ${done ? "done" : ""}"><span class="undo-num">${done ? "✓" : ++n}</span><div><strong>${title}</strong>${body}</div></li>`;
  back.innerHTML = `<div class="modal ask-dialog warn" style="max-width:620px;">
    <h3 style="margin:0 0 6px;">Undo ${escapeHtml(plan.code)} — invoice ${escapeHtml(inv.code)} was sent</h3>
    <p class="muted small" style="margin-top:0;">${what} ${escapeHtml(inv.code)} (${fmtMoney(inv.total)}) was ${sentTo}, so it's voided with the undo — finish these steps first.</p>
    <ol class="undo-steps">
      ${has("remove_payments") || paymentsRemoved ? step(!inv.payments.length, "Remove the payments recorded on it",
        inv.payments.length ? `<div class="small">${inv.payments.map(p => `${fmtMoney(p.amount)} ${p.method ? escapeHtml(p.method) : ""} ${p.reference ? `· ${escapeHtml(p.reference)}` : ""} ${fmtDate(p.paid_date)}
          <a class="link" onclick="undoRemovePayment(${id}, ${inv.id}, ${p.id})">Remove</a>`).join("<br>")}
          <div class="muted">Refund or re-apply the money to the corrected invoice outside AT-HUB as needed.</div></div>` : `<div class="small muted">Done.</div>`) : ""}
      ${has("notify_customer") ? step(false, "Tell the customer it's cancelled",
        `<div class="small">${mail ? `<a class="link" href="${mail}">Email ${escapeHtml(inv.customer_email)}</a> · ` : ""}<a class="link" href="invoices.html?id=${inv.id}" target="_blank">Open ${escapeHtml(inv.code)}</a></div>
         <label class="inline-check small undo-check" style="margin:4px 0 0;"><input type="checkbox" id="undo-told" onchange="undoCheck()"> The customer has been told (or will be) to disregard ${escapeHtml(inv.code)}</label>`) : ""}
      ${has("reason") ? step(false, "Why it's being cancelled", `<input type="text" id="undo-reason" placeholder="E.g. wrong quantity shipped -- re-shipping" oninput="undoCheck()" style="margin-top:4px;">`) : ""}
      ${has("combined") ? step(false, `It also bills ${escapeHtml(inv.combined_with.join(", "))}`,
        `<label class="inline-check small undo-check" style="margin:4px 0 0;"><input type="checkbox" id="undo-combined" onchange="undoCheck()"> Un-invoice ${escapeHtml(inv.combined_with.join(", "))} too (they can go on a new invoice)</label>`) : ""}
    </ol>
    <div class="btn-row" style="margin-top:14px;">
      <button class="danger" id="undo-go" disabled onclick="undoGo(${id})">Void ${escapeHtml(inv.code)} &amp; Undo Shipment</button>
      <button class="secondary" onclick="document.getElementById('undo-wizard').remove()">Cancel</button></div>
    <div id="undo-error" class="error"></div></div>`;
  back.dataset.payments = inv.payments.length;
  decorateIcons(back);
  undoCheck();
}
function undoCheck() {
  const box = id => { const el = document.getElementById(id); return !el || el.checked; };
  const reason = document.getElementById("undo-reason");
  const ok = document.getElementById("undo-wizard").dataset.payments === "0" && box("undo-told") && box("undo-combined") && (!reason || reason.value.trim());
  document.getElementById("undo-go").disabled = !ok;
}
async function undoRemovePayment(id, invoiceId, paymentId) {
  if (!confirm("Remove this payment from the invoice?")) return;
  try {
    await apiFetch(`/api/invoices/${invoiceId}/payments/${paymentId}`, { method: "DELETE" });
    const plan = await apiFetch(`/api/shipments/${id}/undo-plan`);
    undoWizard(id, plan, undoWhat, true);
  } catch (e) { document.getElementById("undo-error").textContent = e.message; }
}
async function undoGo(id) {
  const val = el => document.getElementById(el);
  try {
    await apiFetch(`/api/shipments/${id}/unship`, { method: "POST", body: JSON.stringify({
      reason: val("undo-reason") ? val("undo-reason").value.trim() : null,
      customer_notified: val("undo-told") ? val("undo-told").checked : false,
      combined_ok: val("undo-combined") ? val("undo-combined").checked : false }) });
    document.getElementById("undo-wizard").remove();
    toast("Shipment undone — its invoice is void");
    await afterShipmentChange(id, "pick");
  } catch (e) { val("undo-error").textContent = e.message; }
}

async function deleteShipment(id) {
  if (!confirm("Delete this shipment permanently? Any bookings are released back to stock. You can then create a new shipment from the order.")) return;
  try {
    await apiFetch(`/api/shipments/${id}`, { method: "DELETE" });
    detailContainer().style.display = "none";
    await reloadList();
  } catch (err) {
    document.getElementById("lifecycle-error").textContent = err.message;
  }
}

// Packing is per order line: the same item on two order lines is boxed separately.
// { order_line_id: { line_no, item_id, qty } }, ordered by line number.
function shippedByLine(shipment) {
  const byLine = {};
  shipment.lines.forEach(l => {
    byLine[l.order_line_id] = byLine[l.order_line_id] || { order_line_id: l.order_line_id, line_no: l.line_no, item_id: l.item_id, qty: 0 };
    byLine[l.order_line_id].qty += l.quantity;
  });
  return Object.values(byLine).sort((a, b) => (a.line_no || 0) - (b.line_no || 0));
}

function lineLabel(entry) {
  const i = itemObj(entry.item_id);
  return `#${entry.line_no ?? "?"} ${i ? `${i.code} — ${i.title}` : entry.item_id}`;
}

function boxRowsHtml(shipment) {
  const boxes = shipment.boxes.length ? shipment.boxes : (() => {
    // Nothing saved yet: split each line by its item's default pack size.
    let n = 1;
    return shippedByLine(shipment).flatMap(e => calculateBoxes(e.qty, packSizeFor(shipment, e)).map(b => ({
      order_line_id: e.order_line_id, item_id: e.item_id, box_number: n++, quantity_in_box: b.quantity_in_box,
      lot_code: lotCodeForLine(e.order_line_id), pallet_number: "",
    })));
  })();
  return boxRowHtmlList(boxes);
}

function lineOptions(selectedLineId) {
  return shippedByLine(shipmentsById[currentShipmentId]).map(e =>
    `<option value="${e.order_line_id}" data-item="${e.item_id}" ${e.order_line_id === selectedLineId ? "selected" : ""}>${escapeHtml(lineLabel(e))}</option>`
  ).join("");
}

function boxRowHtmlList(boxes) {
  return boxes.map(b => `
    <tr>
      <td><select class="box-line">${lineOptions(b.order_line_id)}</select></td>
      <td><input type="number" step="1" class="box-number" value="${b.box_number}" style="width:60px;"></td>
      <td><input type="number" step="1" min="1" class="box-qty qty-input" value="${b.quantity_in_box}"></td>
      <td><input type="text" class="box-lot" value="${b.lot_code || ""}" style="width:110px;"></td>
      <td><input type="text" class="box-pallet" value="${b.pallet_number || ""}" style="width:90px;"></td>
      <td class="line-actions">${trashBtn("this.closest('tr').remove(); refreshBoxSummary();", "Remove this box row")}</td>
    </tr>
  `).join("");
}

// Pack size shown per line: the largest saved box, else the item's default, else the whole quantity.
function packSizeFor(shipment, entry) {
  const saved = shipment.boxes.filter(b => b.order_line_id === entry.order_line_id);
  if (saved.length) return saved.find(b => b.pack_size)?.pack_size || Math.max(...saved.map(b => b.quantity_in_box));
  const sug = (packSuggest[shipment.id] || {})[entry.order_line_id];
  if (sug && sug.size) return sug.size;
  const item = itemObj(entry.item_id);
  return item && item.default_pack_size ? item.default_pack_size : entry.qty;
}

// Reads the (possibly collapsed) box table and shows "3 × 100, 1 × 20" per line, flagging mismatches.
function refreshBoxSummary() {
  const rows = Array.from(document.querySelectorAll("#box-rows tr"));
  document.querySelectorAll(".pack-size-input").forEach(input => {
    const lineId = parseInt(input.dataset.line);
    const qty = parseFloat(input.dataset.qty);
    const byQty = {};
    let boxed = 0;
    rows.filter(tr => parseInt(tr.querySelector(".box-line").value) === lineId).forEach(tr => {
      const q = parseFloat(tr.querySelector(".box-qty").value) || 0;
      boxed += q;
      byQty[q] = (byQty[q] || 0) + 1;
    });
    const summary = formatBoxCounts(byQty) || "—";
    const off = Math.abs(boxed - qty) > 1e-6;
    document.querySelector(`.box-summary[data-line="${lineId}"]`).innerHTML =
      summary + (off ? ` <span style="color:#dc2626;font-weight:600;">(boxed ${fmtQty(boxed)} of ${fmtQty(qty)})</span>` : "");
  });
  const count = document.getElementById("box-count");
  if (count) count.textContent = rows.length;
}

function applyPalletToAll(value) {
  document.querySelectorAll("#box-rows .box-pallet, .line-pallet").forEach(el => { el.value = value; });
  renderPalletTable();
}

// Pallet #s in number order: 1, 2, 10 (and P2 before P10)
const byPalletNo = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });

// Normalises "48x54x21", "48 X 54 X 21", "48*54*21", "48in x 54in x 21in" to "48 x 54 x 21" (same as the main portal).
function normalizeDimensions(raw) {
  const text = String(raw || "").trim();
  const m = text.match(/^([\d.]+)\s*(?:in\.?|")?\s*[xX×*]\s*([\d.]+)\s*(?:in\.?|")?\s*[xX×*]\s*([\d.]+)\s*(?:in\.?|")?$/);
  return m ? `${+m[1]} x ${+m[2]} x ${+m[3]}` : text;
}

// Pallet weight/dimension rows follow the pallet #s currently typed on the boxes.
// Values already typed are kept; otherwise the saved ones are shown.
function renderPalletTable(pending = {}) {
  const container = document.getElementById("pallet-table");
  if (!container) return;
  const shipment = shipmentsById[currentShipmentId];
  const typed = {};
  container.querySelectorAll("tr[data-pallet]").forEach(tr => {
    typed[tr.dataset.pallet] = { weight: tr.querySelector(".pallet-weight").value, dimensions: tr.querySelector(".pallet-dimensions").value };
  });
  const byPallet = {};
  document.querySelectorAll("#box-rows tr").forEach(tr => {
    const pn = tr.querySelector(".box-pallet").value.trim();
    if (!pn) return;
    const entry = byPallet[pn] = byPallet[pn] || { boxes: 0, items: new Set() };
    entry.boxes++;
    const opt = tr.querySelector(".box-line").selectedOptions[0];
    if (opt) entry.items.add(itemCode(parseInt(opt.dataset.item)));
  });
  const names = Object.keys(byPallet).sort(byPalletNo);
  if (!names.length) {
    container.innerHTML = `<p class="muted">No pallets yet. Type a pallet # on a line above to enter its weight and dimensions.</p>`;
    return;
  }
  container.innerHTML = `
    <table class="fit-table no-table-tools pallet-grid">
      <thead><tr><th>Pallet #</th><th>Weight (lbs)</th><th>Dimensions (L x W x H in)</th></tr></thead>
      <tbody>${names.map(pn => {
        const saved = shipment.pallets.find(p => p.pallet_number === pn) || {};
        const v = Object.assign({ weight: saved.weight ?? "", dimensions: saved.dimensions ?? "" }, palletStash[pn] || {}, typed[pn] || {}, pending[pn] || {});
        delete palletStash[pn];
        return `<tr data-pallet="${escapeHtml(pn)}">
          <td><strong>${escapeHtml(pn)}</strong></td>
          <td><input type="number" step="0.1" min="0" class="pallet-weight" style="width:100px;" value="${escapeHtml(v.weight)}"></td>
          <td><input type="text" class="pallet-dimensions" style="width:150px;" placeholder="48 x 40 x 50" value="${escapeHtml(v.dimensions)}"
                onblur="this.value = normalizeDimensions(this.value)"></td>
        </tr>`;
      }).join("")}</tbody>
    </table>`;
}

// Paste rows of: Item # · Pack size (space, tab or comma; header optional). Fills the pack size on every line of that item
// and re-splits its boxes -- nothing is saved until Accept Packaging.
function applyPastedPackSizes() {
  const status = document.getElementById("pack-paste-status"), shipment = shipmentsById[currentShipmentId];
  const norm = c => String(c || "").trim().toLowerCase().replace(/-nuts$/, "-nut");
  let lines = 0;
  const unknown = [], changed = [];
  document.getElementById("pack-paste").value.split(/\r?\n/).map(l => l.trim()).filter(Boolean).forEach(row => {
    const cols = splitPasteRow(row), code = cols[0], size = parseInt(String(cols[cols.length - 1] || "").replace(/[^\d]/g, ""));
    if (!code || !size || cols.length < 2) return;  // header / blank
    const entries = shippedByLine(shipment).filter(e => norm(itemCode(e.item_id)) === norm(code));
    if (!entries.length) { unknown.push(code); return; }
    entries.forEach(e => {
      const input = document.querySelector(`.pack-size-input[data-line="${e.order_line_id}"]`);
      if (!input) return;
      if (parseInt(input.value) !== size) changed.push(`${itemCode(e.item_id)}: ${input.value} → ${size}`);
      input.value = size;
      splitByPackSize(e.order_line_id);
      lines++;
    });
  });
  if (proc && lines) proc.dirty = true;
  status.innerHTML = `Applied to ${lines} line${lines === 1 ? "" : "s"}${changed.length ? ` (changed: ${escapeHtml(changed.join(", "))})` : ""}.`
    + (unknown.length ? ` <span class="neg">Not on this shipment: ${escapeHtml(unknown.join(", "))}.</span>` : "") + " Accept Packaging to keep it.";
}

// Pallet paste (single and bulk), one box for both kinds of row -- tab (Excel), comma or spaces; header optional:
//   Item # · Pallet # · Weight · Dimensions   puts that item's line(s) on the pallet, and gives the pallet its weight / size
//   Pallet # · Weight · Dimensions            just the pallet's weight / size
// A row is an item row when its first value is an item on the shipment. Rows that give one pallet different weights or
// sizes are asked about (pickPalletConflicts); anything it would replace on screen is listed first.
const PALLET_PASTE_HINT = "Item #   Pallet #   Weight   Dimensions&#10;15420   1   250   48x48x48&#10;15420-NUT   1&#10;16713   2   300   48x48x24";
const DIMS_AT_END = /(\d+(?:\.\d+)?\s*(?:in\.?|")?\s*[xX×*]\s*\d+(?:\.\d+)?\s*(?:in\.?|")?\s*[xX×*]\s*\d+(?:\.\d+)?\s*(?:in\.?|")?)\s*$/;
const palletItemKey = c => String(c || "").trim().toLowerCase().replace(/-nuts$/, "-nut");
// -> { rows: [{ item, lineIds, pallet, weight, dimensions }], unknown: [item #s not on the shipment] }
function parsePalletPaste(text, linesOfItem) {
  const rows = [], unknown = [];
  String(text || "").split(/\r?\n/).map(l => l.trim()).filter(Boolean).forEach(row => {
    let dims = "", toks;
    if (row.includes("\t") || row.includes(",")) {
      toks = splitPasteRow(row).filter(t => t !== "");
      if (toks.length && DIMS_AT_END.test(toks[toks.length - 1])) dims = toks.pop();
    } else {
      const m = row.match(DIMS_AT_END);
      if (m) { dims = m[1]; row = row.slice(0, m.index).trim(); }
      toks = row ? row.split(/\s+/) : [];
    }
    if (!toks.length || /^(item|pallet)/i.test(toks[0])) return;  // header / blank
    const lineIds = linesOfItem(toks[0]);
    let item = null, pallet, weight;
    if (lineIds.length) [item, pallet, weight] = toks;
    else if (toks.length >= 3) { unknown.push(toks[0]); return; }  // item # . pallet . weight, but not an item on it
    else [pallet, weight] = toks;
    if (!pallet) { if (item) unknown.push(`${item} (no pallet #)`); return; }
    rows.push({ item, lineIds, pallet, weight: weight ? (parseFloat(weight.replace(/[^\d.]/g, "")) || "") : "", dimensions: dims ? normalizeDimensions(dims) : "" });
  });
  return { rows, unknown };
}
// One weight / size per pallet. Rows that disagree: ask which to keep. -> {pallet #: {weight?, dimensions?}}, or null if cancelled.
async function pickPalletConflicts(rows) {
  const byPallet = {}, order = [];
  rows.forEach(r => {
    const key = r.pallet.toLowerCase();
    if (!byPallet[key]) { byPallet[key] = { pallet: r.pallet, opts: [] }; order.push(key); }
    if (r.weight === "" && !r.dimensions) return;
    const g = byPallet[key], same = g.opts.find(o => o.weight === r.weight && o.dimensions === r.dimensions);
    if (same) { if (r.item) same.items.push(r.item); } else g.opts.push({ weight: r.weight, dimensions: r.dimensions, items: r.item ? [r.item] : [] });
  });
  const out = {}, clash = [];
  order.forEach(key => {
    const g = byPallet[key], ws = [...new Set(g.opts.map(o => o.weight).filter(w => w !== ""))], ds = [...new Set(g.opts.map(o => o.dimensions).filter(Boolean))];
    if (ws.length > 1 || ds.length > 1) { clash.push(g); return; }
    const v = {};
    if (ws.length) v.weight = ws[0];
    if (ds.length) v.dimensions = ds[0];
    out[g.pallet] = v;
  });
  if (!clash.length) return out;
  const optLabel = o => `${o.weight !== "" ? `${o.weight} lbs` : "no weight"} · ${o.dimensions || "no size"}${o.items.length ? ` <span class="muted">(${escapeHtml(o.items.join(", "))})</span>` : ""}`;
  const { value, el } = await askDialog({ title: "Same pallet, different weight or size", tone: "warn",
    body: `<p class="small" style="margin-top:0;">These rows give one pallet more than one weight or size. Which is right?</p>
      ${clash.map((g, gi) => `<div class="pal-clash"><b>Pallet ${escapeHtml(g.pallet)}</b>${g.opts.map((o, oi) => `
        <label class="pal-clash-opt"><input type="radio" name="pal-clash-${gi}" value="${oi}" ${oi === g.opts.length - 1 ? "checked" : ""}> ${optLabel(o)}</label>`).join("")}</div>`).join("")}`,
    buttons: [{ label: "Use These", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (value !== "go") return null;
  clash.forEach((g, gi) => {
    const o = g.opts[+(el.querySelector(`input[name="pal-clash-${gi}"]:checked`) || {}).value || 0];
    const v = {};
    if (o.weight !== "") v.weight = o.weight;
    if (o.dimensions) v.dimensions = o.dimensions;
    out[g.pallet] = v;
  });
  return out;
}
// What a paste would replace: a line's pallet #, a pallet's weight or size. linePallets: {lineId: current value}; now: {pallet #: {weight, dimensions}}
function palletPasteChanges(rows, pallets, linePallets, now, lineName) {
  const changes = [];
  rows.forEach(r => r.lineIds.forEach(id => { const cur = linePallets[id] || "";
    if (cur && cur !== r.pallet) changes.push(`${lineName(id)}: pallet ${cur} → ${r.pallet}`); }));
  Object.entries(pallets).forEach(([pn, v]) => {
    const was = now[pn] || Object.entries(now).find(([k]) => k.toLowerCase() === pn.toLowerCase())?.[1] || {};
    if (v.weight !== undefined && was.weight && String(was.weight) !== String(v.weight)) changes.push(`Pallet ${pn} weight: ${was.weight} → ${v.weight} lbs`);
    if (v.dimensions && was.dimensions && was.dimensions !== v.dimensions) changes.push(`Pallet ${pn} size: ${was.dimensions} → ${v.dimensions}`);
  });
  return [...new Set(changes)];
}
async function confirmPalletReplace(changes) {
  if (!changes.length) return true;
  const { value } = await askDialog({ title: "Replace pallet data already entered?", tone: "warn",
    body: `<ul class="small" style="margin:0;padding-left:18px;">${changes.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`,
    buttons: [{ label: "Replace", value: "go", cls: "danger" }, { label: "Cancel", value: null, cls: "secondary" }] });
  return value === "go";
}
function pastedPalletsMsg(lines, filled, unused, unknown) {
  return [lines ? `${lines} line${lines === 1 ? "" : "s"} put on a pallet.` : "",
    filled ? `${filled} pallet${filled === 1 ? "" : "s"} filled.` : "",
    unused.length ? `<span class="neg">${escapeHtml(unused.join(", "))} ${unused.length === 1 ? "isn't" : "aren't"} on any line yet — give a line that Pallet # and it fills in.</span>` : "",
    unknown.length ? `<span class="neg">Not on this shipment: ${escapeHtml(unknown.join(", "))}.</span>` : ""].filter(Boolean).join(" ") || "Nothing applied.";
}
// Pasting into a shipment's Pack screen; the screen-specific parts are passed in.
// io: { lineIds(code), linePallet(id), setLinePallet(id, pn), lineName(id), palletValues(), render(pending), stash }
async function runPalletPaste(text, io) {
  const { rows, unknown } = parsePalletPaste(text, io.lineIds);
  if (!rows.length) return { msg: unknown.length ? pastedPalletsMsg(0, 0, [], unknown) : "Nothing to apply — Item # · Pallet # · Weight · Dimensions, or Pallet # · Weight · Dimensions." };
  const pallets = await pickPalletConflicts(rows);
  if (!pallets) return { msg: "Nothing changed." };
  const cur = {};
  rows.forEach(r => r.lineIds.forEach(id => { cur[id] = io.linePallet(id); }));
  if (!(await confirmPalletReplace(palletPasteChanges(rows, pallets, cur, io.palletValues(), io.lineName)))) return { msg: "Nothing changed." };
  let lines = 0;
  rows.forEach(r => r.lineIds.forEach(id => { io.setLinePallet(id, r.pallet); lines++; }));
  io.render(pallets);
  const shown = Object.keys(io.palletValues()).map(k => k.toLowerCase()), unused = [];
  Object.entries(pallets).forEach(([pn, v]) => { if (!shown.includes(pn.toLowerCase())) { io.stash[pn] = v; unused.push(pn); } });
  return { msg: pastedPalletsMsg(lines, Object.keys(pallets).length - unused.length, unused, unknown), changed: true };
}
let palletStash = {};  // pasted pallets no line is on yet: {pallet #: {weight, dimensions}}
async function applyPastedPallets() {
  const status = document.getElementById("pallet-paste-status"), sh = shipmentsById[currentShipmentId], entries = shippedByLine(sh);
  const input = id => document.querySelector(`.line-pallet[data-line="${id}"]`);
  const r = await runPalletPaste(document.getElementById("pallet-paste").value, {
    lineIds: code => entries.filter(e => palletItemKey(itemCode(e.item_id)) === palletItemKey(code)).map(e => e.order_line_id),
    linePallet: id => (input(id) || {}).value || "",
    setLinePallet: (id, pn) => { const i = input(id); if (i) i.value = pn; setLinePallet(id, pn); },
    lineName: id => { const e = entries.find(x => x.order_line_id === id); return `#${e.line_no ?? ""} ${itemCode(e.item_id)}`; },
    palletValues, render: pending => renderPalletTable(pending), stash: palletStash,
  });
  if (r.changed && proc) proc.dirty = true;
  status.innerHTML = r.msg + (r.changed ? " Accept Packaging to keep it." : "");
}
// The pallet table as it stands: {pallet #: {weight, dimensions}} (typed, else saved).
function palletValues() {
  const out = {};
  document.querySelectorAll("#pallet-table tr[data-pallet]").forEach(tr => {
    out[tr.dataset.pallet] = { weight: tr.querySelector(".pallet-weight").value.trim(), dimensions: tr.querySelector(".pallet-dimensions").value.trim() };
  });
  return out;
}
// "Pallet 2: no weight" -- pallets in use without a weight or dimensions (from the screen, or from a saved shipment).
function palletGaps(values) {
  return Object.entries(values).flatMap(([pn, v]) => {
    const miss = [!v.weight && "weight", !v.dimensions && "dimensions"].filter(Boolean);
    return miss.length ? [`Pallet ${pn}: no ${miss.join(" or ")}`] : [];
  });
}
function savedPalletGaps(sh) {
  const used = [...new Set((sh.boxes || []).map(b => b.pallet_number).filter(Boolean))];
  return palletGaps(Object.fromEntries(used.map(pn => { const p = (sh.pallets || []).find(x => x.pallet_number === pn) || {};
    return [pn, { weight: p.weight, dimensions: p.dimensions }]; })));
}
// Ask before going on with pallets missing a weight or size. Resolves true to go ahead.
async function confirmPalletGaps(gaps, action) {
  if (!gaps.length) return true;
  const { value } = await askDialog({ title: "Pallet weight / size missing", tone: "warn",
    body: `<ul class="small" style="margin:0 0 6px;padding-left:18px;">${gaps.map(g => `<li>${escapeHtml(g)}</li>`).join("")}</ul>
      <p class="muted small" style="margin:0;">They print blank on the packing list.</p>`,
    buttons: [{ label: `${action} Anyway`, value: "go", cls: "danger" }, { label: "Go Back", value: null, cls: "secondary" }] });
  return value === "go";
}

function collectPallets() {
  return Array.from(document.querySelectorAll("#pallet-table tr[data-pallet]")).map(tr => ({
    pallet_number: tr.dataset.pallet,
    weight: tr.querySelector(".pallet-weight").value ? parseFloat(tr.querySelector(".pallet-weight").value) : null,
    dimensions: normalizeDimensions(tr.querySelector(".pallet-dimensions").value) || null,
  }));
}

// Mirrors the existing portal's calculate_boxes(): full boxes of pack_size, then one remainder box.
function calculateBoxes(quantity, packSize) {
  if (!packSize || packSize <= 0) packSize = quantity || 1;
  const fullBoxes = Math.floor(quantity / packSize);
  const remaining = Math.round(quantity - fullBoxes * packSize);
  const boxes = [];
  for (let i = 0; i < fullBoxes; i++) boxes.push({ box_number: i + 1, quantity_in_box: packSize });
  if (remaining > 0) boxes.push({ box_number: fullBoxes + 1, quantity_in_box: remaining });
  return boxes;
}

function packSizeRowsHtml(shipment) {
  return shippedByLine(shipment).map(e => `
    <tr>
      <td class="line-no">#${e.line_no ?? ""}</td>
      <td class="grow">${itemLabel(e.item_id)}</td>
      <td class="num">${fmtQty(e.qty)}</td>
      <td><input type="number" step="1" min="1" class="pack-size-input qty-input" data-line="${e.order_line_id}" data-qty="${e.qty}" value="${packSizeFor(shipment, e)}" oninput="splitByPackSize(${e.order_line_id})">
        ${packSourceHtml(shipment, e)}</td>
      <td class="box-summary" data-line="${e.order_line_id}"></td>
      <td><input type="text" class="line-pallet" data-line="${e.order_line_id}" style="width:100px;" placeholder="Optional"
            value="${escapeHtml(linePallet(shipment, e.order_line_id))}" oninput="setLinePallet(${e.order_line_id}, this.value)"></td>
    </tr>
  `).join("");
}

// Under a pack size: where it came from (saved / customer's last / last packed / item default) + the item's recent packings.
function packSourceHtml(sh, e) {
  const saved = sh.boxes.some(b => b.order_line_id === e.order_line_id), sug = (packSuggest[sh.id] || {})[e.order_line_id];
  const label = saved ? "Saved packing" : sug ? sug.label : "", src = saved ? "saved" : sug ? sug.source : "none";
  return `<div class="pack-hints"><span class="pack-src src-${src}" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
    <a class="link small" onclick="usePackFromHistory(this, ${e.item_id}, ${(order(sh.order_id) || {}).customer_id || "null"}, ${e.order_line_id})" title="What this item was packed at lately">${icon("clock")} Recent</a></div>`;
}
async function usePackFromHistory(el, itemId, customerId, lineId) {
  const v = await showPackUses(itemId, customerId);
  if (!v) return;
  const input = document.querySelector(`.pack-size-input[data-line="${lineId}"]`);
  input.value = v;
  splitByPackSize(lineId);
  if (proc) proc.dirty = true;
}

// A line's pallet # = the pallet(s) its saved boxes are on.
function linePallet(shipment, lineId) {
  return [...new Set(shipment.boxes.filter(b => b.order_line_id === lineId).map(b => b.pallet_number).filter(Boolean))].join(", ");
}

// Typing a pallet # on a line puts all of that line's boxes on it.
function setLinePallet(lineId, value) {
  document.querySelectorAll("#box-rows tr").forEach(tr => {
    if (parseInt(tr.querySelector(".box-line").value) === lineId) tr.querySelector(".box-pallet").value = value;
  });
  renderPalletTable();
}

function splitByPackSize(lineId) {
  const input = document.querySelector(`.pack-size-input[data-line="${lineId}"]`);
  const src = input.closest("td").querySelector(".pack-src");
  if (src) { src.className = "pack-src src-changed"; src.textContent = "Changed — Accept Packaging to keep"; src.title = src.textContent; }
  const qty = parseFloat(input.dataset.qty);
  const packSize = parseInt(input.value) || qty;
  const lineInput = document.querySelector(`.line-pallet[data-line="${lineId}"]`);
  const pallet = lineInput ? lineInput.value : "";
  const newBoxes = calculateBoxes(qty, packSize).map(b => ({ ...b, order_line_id: lineId, lot_code: lotCodeForLine(lineId), pallet_number: pallet }));

  // Replace any existing rows for this line with the freshly-split set.
  const tbody = document.getElementById("box-rows");
  Array.from(tbody.querySelectorAll("tr")).forEach(tr => {
    if (parseInt(tr.querySelector(".box-line").value) === lineId) tr.remove();
  });
  tbody.insertAdjacentHTML("beforeend", boxRowHtmlList(newBoxes));
  tbody.querySelectorAll(".box-number").forEach((el, i) => { el.value = i + 1; });
  refreshBoxSummary();
}

// All lot codes this order line was booked from in this shipment, joined -- same pattern as the existing portal.
function lotCodeForLine(orderLineId) {
  const shipment = shipmentsById[currentShipmentId];
  const lotIds = [...new Set(shipment.lines.filter(l => l.order_line_id === orderLineId).map(l => l.lot_id).filter(Boolean))];
  const codes = lotIds.map(id => { const lot = lots.find(l => l.id === id); return lot ? lot.lot_code : null; }).filter(Boolean);
  return codes.join(", ");
}

async function renameShipment(id) {
  const sh = shipmentsById[id];
  if (await renameRecord("shipment", sh.code, `/api/shipments/${id}/code`)) { await reloadList(); await showDetail(id); }
}

async function showDetail(id) {
  const [shipment, allInvoices] = await Promise.all([apiFetch(`/api/shipments/${id}`), (AuthGuard.can("invoices") ? apiFetch("/api/invoices/").catch(() => []) : []),
    packSuggest[id] ? null : loadPackSuggestions([id])]);
  const invoicesForShipment = allInvoices.filter(inv => (inv.shipment_ids || [inv.shipment_id]).includes(id) && inv.status !== "void");
  // Other shipments of this order that shipped and aren't billed yet -- can go on the same invoice.
  const combinable = shipments.filter(s => s.order_id === shipment.order_id && s.id !== shipment.id
    && ["shipped", "delivered"].includes(s.status));
  shipmentsById[shipment.id] = shipment;
  currentShipmentId = shipment.id;
  detailShownId = shipment.id;
  const card = detailContainer();
  card.style.display = "block";
  const ord = order(shipment.order_id);
  const open = ["new", "ready"].includes(shipment.status), shipped = SHIPPED.includes(shipment.status);
  card.classList.toggle("ship-inproc", open);
  card.classList.toggle("ship-done", shipped);

  card.innerHTML = `
    <h3>${shipment.code}${AuthGuard.can("shipments.work") && shipment.status !== "cancelled" ? ` <button type="button" class="icon-btn" title="Change the shipment #"
      onclick="renameShipment(${shipment.id})">${icon("pencil")}</button>` : ""} <span class="tag ${shipment.status}">${shipment.status}</span></h3>
    <p class="muted">Order <a class="link" href="customer-orders.html?id=${shipment.order_id}">${orderCode(shipment.order_id)}</a>${ord && ord.po_number
      ? ` · PO <a class="link" href="customer-orders.html?id=${shipment.order_id}">${escapeHtml(ord.po_number)}</a>` : ""}
      — created ${fmtDate(shipment.created_at)}${shipment.ship_date ? ` — shipped ${fmtDate(shipment.ship_date)}` : ""}</p>
    <div id="shipment-sticky" class="sticky-strip"></div>

    <div class="ship-actions-bar">
      ${shipment.status !== "cancelled" ? `<button class="next-step" onclick="openProcess(${shipment.id})" ${open ? "" : "disabled"}
          title="${open ? "Pick, pack and ship -- every change to the shipment happens here" : "Already shipped -- use Modify Shipment"}">${icon("package")} Process Shipment</button>` : ""}
      ${shipped ? `<button class="secondary" onclick="openProcess(${shipment.id})" title="Carrier / tracking, or Undo Ship">${icon("pencil")} Modify Shipment</button>` : ""}
      ${shipment.status === "shipped" && AuthGuard.can("shipments.deliver") ? `<button class="secondary" onclick="deliverNow(${shipment.id})" title="Managers can mark delivered without a POD (today's date; change it under Delivery)">Mark Delivered (no POD)</button>` : ""}
      <span class="spacer"></span>
      ${open ? `<button class="danger" onclick="cancelShipment(${shipment.id})">Cancel Shipment</button>` : ""}
      ${["new", "ready", "cancelled"].includes(shipment.status) ? `<button class="danger" onclick="deleteShipment(${shipment.id})">Delete Shipment</button>` : ""}
    </div>
    ${shipTimelineHtml(shipment, invoicesForShipment[0] || null)}
    <div id="lifecycle-error" class="error"></div>

    <section class="dsec">${linesSectionHtml(shipment)}

    ${shipment.status !== "cancelled" ? `
    </section><section class="dsec" id="sec-packing">${packingReadOnlyHtml(shipment)}
    </section><section class="dsec" id="sec-carrier">${carrierReadOnlyHtml(shipment)}` : ""}

    </section><section class="dsec"><h4 class="dsec-title">Proof of delivery &amp; documents</h4>
    <p class="muted small" style="margin-top:0;">Delivery photos, signed packing lists, bills of lading.
      Drivers can use the phone page: <a class="link" href="pod.html?id=${shipment.id}" target="_blank">pod.html?id=${shipment.id}</a></p>
    ${shipment.pods.length ? `<p><button class="secondary" onclick="openPodEmail(${shipment.id})">✉ Email POD to customer</button></p>` : ""}
    <div id="shipment-attachments"></div>

    </section><section class="dsec"><h4 class="dsec-title">Delivery</h4>
    ${deliverySectionHtml(shipment)}

    ${AuthGuard.can("invoices") ? `</section><section class="dsec"><h4 class="dsec-title">Invoicing</h4>
    ${shipment.status === "invoiced" ? `
      <p>Invoiced on ${invoicesForShipment.length ? invoicesForShipment.map(inv =>
        `${invoiceChip(inv, { here: shipment.code })} <span class="muted small">${inv.status} · ${fmtMoney(inv.total)}${inv.is_combined
          ? ` · combined with ${escapeHtml(inv.shipment_codes.filter(c => c !== shipment.code).join(", "))}` : ""}</span>`).join(", ") : `<a class="link" href="invoices.html">Invoices</a>`}</p>
    ` : !["shipped", "delivered"].includes(shipment.status) ? `
      <p class="muted">Available once the shipment has shipped.</p>
    ` : `
      <div class="row" style="max-width:500px;">
        <div><label>Due date</label><input type="date" id="inv-due-date"></div>
        <div><label>Shipping charge</label><input type="number" step="0.01" id="inv-shipping" value="0"></div>
      </div>
      <label>Free text</label><textarea id="inv-free-text" rows="2" placeholder="Optional note printed on the invoice"></textarea>
      ${combinable.length ? `
        <label>Combine on one invoice <span class="muted small">(other shipped, un-invoiced shipments of this order)</span></label>
        <div>${combinable.map(s => `<label class="check-label" style="margin-right:14px;"><input type="checkbox" class="inv-combine" value="${s.id}">
          ${escapeHtml(s.code)} <span class="muted small">${s.status} ${fmtDate(s.delivered_at || s.ship_date)}</span></label>`).join("")}</div>` : ""}
      <button onclick="createInvoice(${shipment.id})" style="margin-top:10px;">Create Invoice</button>
    `}
    <div id="invoice-error" class="error"></div>
    ` : ""}

    </section>
    <button class="secondary" onclick="closeShipmentDetail()" style="margin-top:16px;">Close</button>
  `;
  decorateIcons(card);
  stickyNotes("shipment-sticky", "shipment", shipment.id);
  renderAttachments("shipment-attachments", "shipment", shipment.id, ["pod", "bol", "other"],
    { notePlaceholder: "Note — E.g. Received By / Signed By", onChange: async () => {
      // A POD upload may have just marked it delivered.
      const fresh = await apiFetch(`/api/shipments/${shipment.id}`);
      if (fresh.status !== shipment.status || fresh.delivered_at !== shipment.delivered_at) { await reloadList(); showDetail(shipment.id); }
    } });
}

function addBoxRow() {
  const tbody = document.getElementById("box-rows");
  const idx = tbody.children.length;
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><select class="box-line">${lineOptions(null)}</select></td>
    <td><input type="number" step="1" class="box-number" value="${idx + 1}" style="width:60px;"></td>
    <td><input type="number" step="1" min="1" class="box-qty qty-input" value="0"></td>
    <td><input type="text" class="box-lot" style="width:110px;"></td>
    <td><input type="text" class="box-pallet" style="width:90px;"></td>
    <td class="line-actions">${trashBtn("this.closest('tr').remove(); refreshBoxSummary();", "Remove this box row")}</td>
  `;
  tbody.appendChild(tr);
  refreshBoxSummary();
}

function collectBoxes() {
  const packOf = lineId => parseInt((document.querySelector(`.pack-size-input[data-line="${lineId}"]`) || {}).value) || null;
  return Array.from(document.querySelectorAll("#box-rows tr")).map(tr => ({
    pack_size: packOf(parseInt(tr.querySelector(".box-line").value)),
    order_line_id: parseInt(tr.querySelector(".box-line").value),
    item_id: parseInt(tr.querySelector(".box-line").selectedOptions[0].dataset.item),
    box_number: parseInt(tr.querySelector(".box-number").value) || 1,
    quantity_in_box: parseFloat(tr.querySelector(".box-qty").value) || 0,
    lot_code: tr.querySelector(".box-lot").value || null,
    pallet_number: tr.querySelector(".box-pallet").value || null,
  }));
}

// ---- pack-size memory: recent packings of an item (pick one to use it) ----
const usageCache = {};
async function packUses(itemId) {
  if (!usageCache[itemId]) usageCache[itemId] = (await apiFetch(`/api/stock-items/pack-sizes/usage?item_ids=${itemId}&limit=12`))[itemId] || [];
  return usageCache[itemId];
}
// Resolves to the chosen size, or null.
async function showPackUses(itemId, customerId) {
  let uses = [];
  try { uses = await packUses(itemId); } catch {}
  const item = itemObj(itemId) || {};
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-backdrop over-glass";
    const done = v => { document.removeEventListener("keydown", onKey, true); back.remove(); resolve(v); };
    const onKey = e => { if (e.key === "Escape") { e.stopPropagation(); done(null); } };
    back.innerHTML = `<div class="modal pack-uses" role="dialog" aria-modal="true">
      <h3 style="margin:0 0 4px;">${escapeHtml(item.code || "")} — recent packings</h3>
      <p class="muted small" style="margin:0 0 8px;">${escapeHtml(item.title || "")}. Click a row to use that size.</p>
      ${uses.length ? `<table class="fit-table no-table-tools no-col-bands"><thead><tr><th class="num">Size</th><th>Packed</th><th>Shipment</th><th>Order</th><th class="grow">Customer</th></tr></thead><tbody>
        ${uses.map(u => `<tr class="pu-row ${customerId && u.customer_id === customerId ? "pu-mine" : ""}" data-v="${u.pack_size}">
          <td class="num"><b>${u.pack_size}</b>${u.sure ? "" : ` <span class="muted small" title="Packed as a single box, so the real pack size may have been bigger">1 box</span>`}</td>
          <td>${fmtDate(u.date)}</td><td>${escapeHtml(u.shipment)}</td><td>${escapeHtml(u.order)}${u.po ? ` · PO ${escapeHtml(u.po)}` : ""}</td>
          <td class="grow">${escapeHtml(u.customer || "")}${customerId && u.customer_id === customerId ? ` <span class="tag confirmed">This customer</span>` : ""}</td></tr>`).join("")}
      </tbody></table>` : `<p class="muted">Not packed in AT-HUB yet.</p>`}
      <div class="btn-row" style="margin-top:12px;">
        ${item.default_pack_size ? `<button type="button" class="secondary" data-v="${item.default_pack_size}">Use Item Default (${item.default_pack_size})</button>` : ""}
        <button type="button" class="secondary" data-close="1">Close</button></div></div>`;
    back.addEventListener("click", e => {
      if (e.target === back || e.target.closest("[data-close]")) return done(null);
      const hit = e.target.closest("[data-v]");
      if (hit) done(parseInt(hit.dataset.v));
    });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(back);
  });
}

// Same layout and fields as the main portal's 4x6 labels (shared printBoxLabels in auth-guard.js).
async function printLabels(shipmentId) {
  const shipment = await apiFetch(`/api/shipments/${shipmentId}`);
  if (!shipment.boxes.length) {
    alert("Save a packing list first — there are no boxes to print labels for.");
    return;
  }
  const ord = order(shipment.order_id);
  printBoxLabels(shipment.boxes.map(b => ({
    customer: customerName(ord ? ord.customer_id : null), customer_id: ord ? ord.customer_id : null, shipment: shipment.code,
    order: ord ? ord.code : "", po: ord ? ord.po_number : "", job: ord ? ord.job_number : "",
    item_code: itemCode(b.item_id), item_title: (itemObj(b.item_id) || {}).title || "", qty: b.quantity_in_box,
    lot: b.lot_code || "", pallet: b.pallet_number || "", ship_to: ord ? ord.ship_to_address || "" : "",
  })), docFileName(shipment.code, ord ? ord.po_number : "", "Labels"));
}

// Shipment pallet labels (4x6): one per pallet (its row highlighted) or one summary label.
// What goes on the label: ticked in the pop-up each time, remembered on this computer for next time.
const PALLET_LABEL_OPTS = [["boxes_col", "Boxes column (boxes on each pallet)"], ["total_pallets", "Total pallets"],
                           ["total_boxes", "Total boxes"], ["total_weight", "Total weight"], ["company_line", "Our company name and ship date"]];
async function printPalletLabels(shipmentId, ids = null) {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("at_hub_pallet_label_opts") || "{}"); } catch (e) {}
  const on = k => saved[k] !== false;
  const { value, el } = await askDialog({ title: "Shipment pallet labels",
    body: `<p class="muted small" style="margin-top:0;">Every label shows the PO #, job # and each pallet with its customer item #s. Tick what else to print.</p>
      <div class="pl-opts">
        <label class="inline-check"><input type="radio" name="pl-mode" value="each" ${saved.mode !== "one" ? "checked" : ""}> <b>One label per pallet</b> <span class="muted small">(its own pallet highlighted)</span></label>
        <label class="inline-check"><input type="radio" name="pl-mode" value="one" ${saved.mode === "one" ? "checked" : ""}> <b>One summary label</b></label>
        <hr style="margin:8px 0;">
        ${PALLET_LABEL_OPTS.map(([k, l]) => `<label class="inline-check"><input type="checkbox" data-opt="${k}" ${on(k) ? "checked" : ""}> ${l}</label>`).join("")}
      </div>`,
    buttons: [{ label: "Print", value: "print", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!value) return;
  const opts = { mode: el.querySelector("input[name=pl-mode]:checked").value };
  el.querySelectorAll("[data-opt]").forEach(c => { opts[c.dataset.opt] = c.checked; });
  try { localStorage.setItem("at_hub_pallet_label_opts", JSON.stringify(opts)); } catch (e) {}
  const q = `per_pallet=${opts.mode === "each"}&` + PALLET_LABEL_OPTS.map(([k]) => `${k}=${opts[k]}`).join("&");
  openPdf(ids ? `/api/shipments/pallet-labels.pdf?ids=${ids}&${q}` : `/api/shipments/${shipmentId}/pallet-labels.pdf?${q}`);
}

function packingListQuery() {
  const boxes = document.getElementById("pl-boxes")?.checked ?? true;
  const pallets = document.getElementById("pl-pallets")?.checked ?? false;
  const lots = document.getElementById("pl-lots")?.checked ?? false;
  const notes = document.getElementById("pl-notes")?.checked ?? true;
  const palletBoxes = document.getElementById("pl-pallet-boxes")?.checked ?? false;
  return `boxes=${boxes}&pallets=${pallets}&lots=${lots}&notes=${notes}&pallet_boxes=${palletBoxes}`;
}

function printPackingList(shipmentId) {
  openPdf(`/api/shipments/${shipmentId}/packing-list.pdf?${packingListQuery()}`);
}

// Excel / CSV: the same content as the PDF, with the same ticks (box details, pallet info, lot #, line notes).
async function exportPackingList(shipmentId, fmt) {
  const sh = shipments.find(s => s.id === shipmentId) || {};
  const ord = (typeof orders !== "undefined" ? orders : []).find(o => o.id === sh.order_id) || {};
  const name = docFileName(sh.code || "Shipment", ord.po_number || "", "Packing List").replace(/\.pdf$/i, "") + "." + fmt;
  try { await downloadFile(`/api/shipments/${shipmentId}/packing-list.${fmt}?${packingListQuery()}`, name); }
  catch (e) { toast(e.message); }
}

async function createInvoice(shipmentId) {
  const errorEl = document.getElementById("invoice-error");
  errorEl.textContent = "";
  try {
    const extra = Array.from(document.querySelectorAll(".inv-combine:checked")).map(c => parseInt(c.value));
    if (!await deliveredCheckBeforeInvoice([shipmentId, ...extra].map(id => shipmentsById[id]))) return;
    const invoice = await apiFetch(`/api/invoices/from-shipments`, {
      method: "POST",
      body: JSON.stringify({
        shipment_ids: [shipmentId, ...extra],
        due_date: document.getElementById("inv-due-date").value || null,
        free_text: document.getElementById("inv-free-text").value,
        shipping_charge: parseFloat(document.getElementById("inv-shipping").value) || 0,
      }),
    });
    window.location.href = `invoices.html?id=${invoice.id}`;
  } catch (err) {
    errorEl.textContent = err.message;
  }
}


// ---- Proof of delivery: list cell + email to customer ----
// POD column on shipment lists: a link per POD file, or "POD missing" once it has shipped.
function podCellHtml(s) {
  if (s.pods && s.pods.length) {
    return s.pods.map((f, i) => `<a class="link" onclick="event.stopPropagation(); openAttachment(${f.id})" title="${escapeHtml(f.filename)}">POD${s.pods.length > 1 ? ` ${i + 1}` : ""}</a>`).join(" ")
      + ` <a class="link small" onclick="event.stopPropagation(); openPodEmail(${s.id})" title="Email the POD to the customer">✉</a>`;
  }
  if (["shipped", "delivered", "invoiced"].includes(s.status)) return `<span class="tag overdue">POD missing</span>`;
  return `<span class="muted small">—</span>`;
}

async function openPodEmail(shipmentId) {
  let modal = document.getElementById("pod-email-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "pod-email-modal";
    modal.className = "modal-backdrop";
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.body.appendChild(modal);
  }
  modal.innerHTML = `<div class="modal"><p class="muted">Loading…</p></div>`;
  const [s, history] = await Promise.all([apiFetch(`/api/shipments/${shipmentId}`), apiFetch(`/api/shipments/${shipmentId}/pod-emails`)]);
  const ord = order(s.order_id) || {};
  const cust = customers.find(c => c.id === ord.customer_id) || {};
  const when = s.delivered_at ? fmtDate(s.delivered_at) : s.ship_date ? fmtDate(s.ship_date) : "";
  const ref = [ord.po_number ? `PO ${ord.po_number}` : "", s.code].filter(Boolean).join(" / ");
  modal.innerHTML = `<div class="modal" style="max-width:640px;">
    <h3 style="margin-top:0;">Email proof of delivery — ${escapeHtml(s.code)}</h3>
    ${s.pods.length ? "" : `<p class="error">No POD on this shipment yet. Upload one first.</p>`}
    <label>To</label><input type="text" id="pod-to" value="${escapeHtml(cust.email || "")}" placeholder="customer@example.com">
    <label>CC</label><input type="text" id="pod-cc">
    <label>Subject</label><input type="text" id="pod-subject" value="${escapeHtml(`Proof of delivery — ${ref}`)}">
    <label>Message</label><textarea id="pod-body" rows="6">${escapeHtml(`Hello${cust.contact_name ? " " + cust.contact_name : ""},

Attached is the proof of delivery for ${ref}${when ? `, delivered ${when}` : ""}.

Please let us know if you have any questions.`)}</textarea>
    <label>Attach</label>
    <div>${s.pods.map(f => `<label class="check-label" style="margin-right:12px;"><input type="checkbox" class="pod-file" value="${f.id}" checked> ${escapeHtml(f.filename)}</label>`).join("")}</div>
    <div class="btn-row">
      <button id="pod-send" onclick="sendPodEmail(${s.id})" ${s.pods.length ? "" : "disabled"}>Send</button>
      <button class="secondary" onclick="document.getElementById('pod-email-modal').remove()">Cancel</button>
    </div>
    <div id="pod-email-error" class="error"></div>
    ${history.length ? `<h4>Sent before</h4><table class="compact-table no-table-tools"><thead><tr><th>When</th><th>To</th><th>Files</th><th>By</th></tr></thead><tbody>
      ${history.map(h => `<tr><td class="nowrap">${fmtWhen(h.sent_at)}</td><td>${escapeHtml(h.to_address)}</td>
        <td class="small">${escapeHtml(h.files || "")}</td><td>${escapeHtml(h.sent_by || "")}</td></tr>`).join("")}</tbody></table>` : ""}
  </div>`;
}

async function sendPodEmail(shipmentId) {
  const err = document.getElementById("pod-email-error");
  err.textContent = "";
  const ids = Array.from(document.querySelectorAll(".pod-file:checked")).map(c => parseInt(c.value));
  if (!ids.length) { err.textContent = "Tick at least one file."; return; }
  const btn = document.getElementById("pod-send");
  btn.disabled = true; btn.textContent = "Sending…";
  try {
    await apiFetch(`/api/shipments/${shipmentId}/email-pod`, { method: "POST", body: JSON.stringify({
      to: document.getElementById("pod-to").value, cc: document.getElementById("pod-cc").value || null,
      subject: document.getElementById("pod-subject").value, body: document.getElementById("pod-body").value, attachment_ids: ids,
    }) });
    document.getElementById("pod-email-modal").remove();
    alert("Proof of delivery sent.");
  } catch (e) {
    err.textContent = e.message;
    btn.disabled = false; btn.textContent = "Send";
  }
}


// ---- Pack size manager: every item's current default pack size and its history ----
// Edit or clear the default, or delete old sizes from the history. Shared by
// Shipments and Batch Shipments.
async function openPackSizeManager(onChange) {
  let modal = document.getElementById("pack-manager-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "pack-manager-modal";
    modal.className = "modal-backdrop";
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.body.appendChild(modal);
  }
  modal.innerHTML = `<div class="modal" style="max-width:900px;"><p class="muted">Loading…</p></div>`;
  const [list, history] = await Promise.all([apiFetch("/api/stock-items/"), apiFetch("/api/stock-items/pack-sizes/history")]);
  const render = () => {
    const q = (document.getElementById("pm-search")?.value || "").toLowerCase();
    const onlySet = document.getElementById("pm-only-set")?.checked ?? false;
    const rows = list.filter(i => (!q || `${i.code} ${i.title}`.toLowerCase().includes(q)) && (!onlySet || i.default_pack_size));
    document.getElementById("pm-body").innerHTML = rows.map(i => {
      const old = history.filter(h => h.item_id === i.id);
      return `<tr data-item="${i.id}">
        <td><strong>${escapeHtml(i.code)}</strong><div class="muted small">${escapeHtml(i.title)}</div></td>
        <td><input type="number" step="1" min="1" class="qty-input pm-size" value="${i.default_pack_size ?? ""}" placeholder="None"></td>
        <td class="nowrap"><a class="link" onclick="pmSave(${i.id})">Save</a>
          ${i.default_pack_size ? ` · <a class="link" onclick="pmClear(${i.id})">Clear</a>` : ""}</td>
        <td>${old.length ? old.map(h => `<span class="pack-chip" title="${escapeHtml(`${fmtWhen(h.changed_at)} · ${h.source || ""} · ${h.changed_by || ""}${h.reference ? " · " + h.reference : ""}`)}">
            ${h.previous_pack_size ?? "None"} → ${h.pack_size ?? "None"}
            <a onclick="pmDeleteHistory(${h.id})" title="Delete this history entry" style="cursor:pointer;color:#b91c1c;margin-left:3px;">×</a></span>`).join(" ")
          : `<span class="muted small">—</span>`}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="4" class="muted">No items match.</td></tr>`;
  };
  window.pmReload = async () => { openPackSizeManager(onChange); if (onChange) onChange(); };
  window.pmSave = async id => {
    const v = document.querySelector(`#pm-body tr[data-item="${id}"] .pm-size`).value;
    const size = v ? parseInt(v) : null;
    if (v && (!size || size <= 0)) { document.getElementById("pm-error").textContent = "Pack size must be a whole number above 0."; return; }
    try { await apiFetch(`/api/stock-items/${id}`, { method: "PUT", body: JSON.stringify({ default_pack_size: size }) }); pmReload(); }
    catch (e) { document.getElementById("pm-error").textContent = e.message; }
  };
  window.pmClear = async id => {
    if (!confirm("Clear this item's default pack size? (The old size stays in its history.)")) return;
    try { await apiFetch(`/api/stock-items/${id}`, { method: "PUT", body: JSON.stringify({ default_pack_size: null }) }); pmReload(); }
    catch (e) { document.getElementById("pm-error").textContent = e.message; }
  };
  window.pmDeleteHistory = async hid => {
    if (!confirm("Delete this pack size history entry?")) return;
    try { await apiFetch(`/api/stock-items/pack-sizes/history/${hid}`, { method: "DELETE" }); pmReload(); }
    catch (e) { document.getElementById("pm-error").textContent = e.message; }
  };
  modal.innerHTML = `<div class="modal" style="max-width:900px;">
    <h3 style="margin-top:0;">Pack Sizes</h3>
    <div class="row" style="align-items:center;">
      <input type="text" id="pm-search" placeholder="Search item # or title…" oninput="window.pmRender()">
      <label class="check-label" style="flex:0 0 auto;"><input type="checkbox" id="pm-only-set" onchange="window.pmRender()"> Only Items With A Pack Size</label>
    </div>
    <div id="pm-error" class="error"></div>
    <div style="max-height:60vh; overflow:auto; margin-top:8px;">
      <table class="compact-table no-table-tools">
        <thead><tr><th>Item</th><th>Default Pack Size</th><th></th><th>History (Old → New)</th></tr></thead>
        <tbody id="pm-body"></tbody>
      </table>
    </div>
    <button class="secondary" style="margin-top:12px;" onclick="document.getElementById('pack-manager-modal').remove()">Close</button>
  </div>`;
  window.pmRender = render;
  render();
}

// Status tag for a shipment; an invoiced one also shows its invoice's status (managers).
function shipmentStatusHtml(s) {
  const tag = `<span class="tag ${s.status}">${s.status}</span>`;
  if (s.status !== "invoiced" || !s.invoice_code || !AuthGuard.can("invoices")) return tag;
  return invoiceChipFromShipment(s);
}
