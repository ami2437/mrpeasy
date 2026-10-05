// Shipment detail panel shared by the Shipments page and the Batch Shipments page:
// lines, picking, carrier, packing/boxes/pallets, labels, packing list, POD, delivery, invoicing.
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

function fmtDate(d) { return d ? new Date(d).toLocaleDateString() : ""; }
function orderCode(id) { const o = orders.find(o => o.id === id); return o ? o.code : id; }
function order(id) { return orders.find(o => o.id === id); }
function itemLabel(id) { const i = items.find(i => i.id === id); return i ? `${i.code} — ${i.title}` : id; }
function itemCode(id) { const i = items.find(i => i.id === id); return i ? i.code : id; }
function itemObj(id) { return items.find(i => i.id === id); }
function customerName(id) { const c = customers.find(c => c.id === id); return c ? c.name : id; }


function lotCode(id) { const lot = lots.find(l => l.id === id); return lot ? lot.lot_code : ""; }

const STATUS_HELP = {
  new: "Items are booked from stock (reserved, still on hand). Confirm the bookings to start picking, unbook individual lines, or cancel to release everything.",
  ready: "Bookings confirmed — pick the items, then review and accept the packing; Ship Now sends it.",
  shipped: "Fully picked and shipped — stock has left on-hand. Waiting for proof of delivery.",
  delivered: "Delivered to the customer — ready to invoice.",
  invoiced: "Shipped and invoiced.",
  cancelled: "Cancelled — bookings were released back to stock.",
};

// Bookings confirmed (in the Create Shipment pop-up, or with Confirm Bookings) are locked: no unbook boxes
// until "Change Bookings". Per order line: ordered, shipped before, this shipment, and what's left after it.
const unbookOpen = {};  // shipment id -> unbook boxes shown on a ready (locked) shipment
function linesSectionHtml(shipment) {
  const ready = shipment.status === "ready";
  const isShipped = ["shipped", "delivered", "invoiced"].includes(shipment.status);
  const locked = ready && !unbookOpen[shipment.id];
  const picking = ready && locked;  // changing bookings hides picking
  const open = shipment.status === "new" || (ready && !locked);
  const ord = order(shipment.order_id);
  const sorted = shipment.lines.slice().sort((a, b) => (a.line_no || 0) - (b.line_no || 0) || a.id - b.id);
  const group = {};  // order line id -> { first row id, rows, this shipment qty }
  sorted.forEach(l => { const g = group[l.order_line_id] ??= { first: l.id, n: 0, qty: 0 }; g.n++; g.qty += l.quantity; });
  const olOf = id => (ord && ord.lines.find(x => x.id === id)) || null;
  return `
    <h4 style="display:flex; align-items:center; gap:10px;">Items
      ${ready ? (locked ? `<span class="lock-tag" title="Booked quantities are confirmed. Change Bookings to unbook.">${icon("lock")}Bookings locked</span>
        <button class="secondary small-btn" onclick="unbookOpen[${shipment.id}] = true; showDetail(${shipment.id})">Change Bookings</button>
        ${shipment.lines.every(l => !(l.picked_quantity > 0)) ? `<button class="danger small-btn" onclick="unconfirmShipment(${shipment.id})" title="Back to New -- stock stays booked">Unconfirm Bookings</button>` : ""}`
        : `<button class="secondary small-btn" onclick="unbookOpen[${shipment.id}] = false; showDetail(${shipment.id})">Done Changing</button>`) : ""}</h4>
    <table class="fit-table ship-lines">
      <thead><tr><th title="Order line">Line</th><th class="grow">Item</th><th>Lot</th>
        <th class="num" title="Quantity on the order line">Ordered</th>
        <th class="num" title="Shipped on earlier shipments of this order">Shipped before</th>
        <th class="num" title="Booked into this shipment, one row per lot">Booked (by lot)</th>
        <th class="num" title="The whole order line on this shipment -- every lot added up">Line total</th><th class="num">Picked</th>
        <th class="num" title="Still to ship on the order line once this shipment has gone">Left after this</th>
        ${picking ? "<th>Pick now</th>" : ""}${open ? "<th>Unbook</th>" : ""}</tr></thead>
      <tbody oninput="refreshLeftAfter()">
        ${sorted.map(l => {
          const left = Math.max(0, l.quantity - l.picked_quantity), g = group[l.order_line_id], ol = olOf(l.order_line_id), first = g.first === l.id;
          const before = ol ? Math.max(0, ol.shipped_quantity - (isShipped ? g.qty : 0)) : null;
          const after = ol ? Math.max(0, ol.quantity - before - g.qty) : null;
          const span = g.n > 1 ? ` rowspan="${g.n}"` : "";
          return `
          <tr data-ol="${l.order_line_id}">
            ${first ? `<td class="line-no"${span}>#${l.line_no ?? ""}</td><td class="grow"${span}>${itemLabel(l.item_id)}</td>` : ""}
            <td>${lotCode(l.lot_id)}</td>
            ${first ? `<td class="num"${span}>${ol ? fmtQty(ol.quantity) : ""}</td><td class="num muted"${span}>${before != null ? fmtQty(before) : ""}</td>` : ""}
            <td class="num">${g.n > 1 ? fmtQty(l.quantity) : `<strong>${fmtQty(l.quantity)}</strong>`}</td>
            ${first ? `<td class="num line-total"${span}><strong>${fmtQty(g.qty)}</strong>${g.n > 1 ? `<div class="muted small">${g.n} lots</div>` : ""}</td>` : ""}
            <td class="num">${fmtQty(l.picked_quantity)}${l.picked_quantity >= l.quantity ? " ✓" : ""}</td>
            ${first ? `<td class="num left-after"${span} data-after="${after ?? ""}">${after == null ? "" : after > 0 ? `<strong>${fmtQty(after)}</strong>` : `<span class="pos">0 ✓</span>`}</td>` : ""}
            ${picking ? `<td>${left > 0 ? `<input type="number" step="1" min="0" class="pick-qty qty-input" data-line="${l.id}" value="${left}">` : ""}</td>` : ""}
            ${open ? `<td class="nowrap">${left > 0 ? `
              <input type="number" step="1" min="1" max="${left}" placeholder="${left}" id="unbook-${l.id}" class="qty-input unbook-qty" data-ol="${l.order_line_id}" title="Blank = all ${left}">
              <button class="small-btn secondary" onclick="unbookLine(${shipment.id}, ${l.id})">Unbook</button>` : `<span class="muted small">Picked</span>`}</td>` : ""}
          </tr>
        `;
        }).join("")}
      </tbody>
    </table>
    <div style="margin-top:10px;">
      ${shipment.status === "new" ? `<button class="next-step" onclick="shipmentAction(${shipment.id}, 'confirm-booking')">Confirm Bookings</button>` : ""}
      ${picking && !allPicked(shipment) ? `<button class="next-step" onclick="pickEntered(${shipment.id})" title="Records the Pick now quantities (they start at everything left)">Pick</button>` : ""}
      ${picking && allPicked(shipment) ? `<span class="pick-done">${icon("checkCircle")}All picked</span>
        ${shipment.packed_at ? `<span class="muted small">Packing accepted by ${escapeHtml(shipment.packed_by || "")}</span>
          <button class="ship-now next-step" onclick="shipNow(${shipment.id})">Ship Now</button>
          <button class="secondary" onclick="openPackReview(${shipment.id})">Review Packing</button>`
        : `<button class="ship-now next-step" onclick="openPackReview(${shipment.id})">Review Packing &amp; Ship</button>`}` : ""}
      ${shipment.status === "ready" && shipment.lines.some(l => (l.picked_quantity || 0) > 0)
        ? `<button class="danger" onclick="unpickShipment(${shipment.id})" title="Picked quantities back to 0 (bookings and packing stay)">Unpick</button>` : ""}
      ${["new", "ready"].includes(shipment.status) ? `<button class="danger" onclick="cancelShipment(${shipment.id})">Cancel Shipment</button>` : ""}
      ${shipment.status === "shipped" && AuthGuard.can("shipments.deliver") ? `<button class="next-step" onclick="deliverNow(${shipment.id})" title="Managers can mark delivered without a POD (today's date; change it under Proof of delivery)">Mark Delivered (no POD)</button>` : ""}
      ${["shipped", "delivered", "invoiced"].includes(shipment.status) && AuthGuard.can("shipments.undo") ? `<button class="secondary" onclick="unshipShipment(${shipment.id})">Undo Ship</button>` : ""}
      ${["new", "ready", "cancelled"].includes(shipment.status) ? `<button class="danger" onclick="deleteShipment(${shipment.id})">Delete Shipment</button>` : ""}
    </div>
    <div id="lifecycle-error" class="error"></div>
  `;
}

// While unbooking: "Left after this" grows by what the unbook boxes would release.
function refreshLeftAfter() {
  document.querySelectorAll(".ship-lines .left-after").forEach(td => {
    if (td.dataset.after === "") return;
    const back = [...document.querySelectorAll(`.unbook-qty[data-ol="${td.closest("tr").dataset.ol}"]`)].reduce((s, i) => s + (parseFloat(i.value) || 0), 0);
    const open = document.querySelector(".unbook-qty") !== null;
    const after = parseFloat(td.dataset.after) + (open ? back : 0);
    td.innerHTML = after > 0 ? `<strong>${fmtQty(after)}</strong>${open && back ? `<div class="muted small">if unbooked</div>` : ""}` : `<span class="pos">0 ✓</span>`;
  });
}

async function shipmentAction(id, action, body) {
  const errorEl = document.getElementById("lifecycle-error");
  errorEl.textContent = "";
  try {
    await apiFetch(`/api/shipments/${id}/${action}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
    await reloadList();
    await showDetail(id);
  } catch (err) {
    errorEl.textContent = err.message;
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
    body: `<p>Picked quantities go back to 0. Bookings and packing stay.</p>`,
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

async function pickEntered(id) {
  const lines = Array.from(document.querySelectorAll(".pick-qty"))
    .map(el => ({ shipment_line_id: parseInt(el.dataset.line), quantity: parseFloat(el.value) || 0 }))
    .filter(l => l.quantity > 0);
  if (!lines.length) {
    document.getElementById("lifecycle-error").textContent = "Enter a picked quantity on at least one line.";
    return;
  }
  await shipmentAction(id, "pick", { lines });
  const sh = shipmentsById[id];
  if (sh && sh.status === "ready" && allPicked(sh)) openPackReview(id);  // everything picked: on to packing
}

// ---- Pack & ship: once everything is picked, the packing, pallets, labels and carrier sections open in a
// glass pop-up. Accept Packaging saves them and lights up Ship Now; shipping returns to the previous screen.
let packReview = null;  // { id, moved: [[section, placeholder]] }
function openPackReview(id) {
  const sh = shipmentsById[id];
  if (!sh || document.getElementById("pack-review")) return;
  const back = document.createElement("div");
  back.className = "glass-back";
  back.id = "pack-review";
  back.innerHTML = `<div class="glass-panel pack-panel" role="dialog" aria-modal="true" aria-labelledby="pr-title">
    <div class="sm-head">
      <div><h3 id="pr-title">Pack &amp; ship ${escapeHtml(sh.code)}</h3>
        <div class="pr-steps"><span class="done">${icon("check")}Picked</span><span class="pr-step-pack ${sh.packed_at ? "done" : "on"}">${sh.packed_at ? icon("check") : "2"} Packing</span><span class="pr-step-ship">3 Ship</span></div></div>
      <button type="button" class="icon-btn sm-close" aria-label="Close" onclick="closePackReview()">${icon("x")}</button>
    </div>
    <p class="muted small" style="margin:0 0 8px;">Check how it's packed — boxes by pack size, pallets, labels and the packing list — and the carrier, then accept the packaging.</p>
    <div class="pr-body" id="pr-body"></div>
    <div class="sm-foot">
      <div class="sm-summary" id="pr-summary"></div>
      <div class="error" id="pr-error"></div>
      <button type="button" class="secondary" onclick="closePackReview()">Close</button>
      <button type="button" id="pr-accept" onclick="acceptPackaging(${id})">${sh.packed_at ? "Accept Changes" : "Accept Packaging"}</button>
      <button type="button" class="ship-now" id="pr-ship" onclick="shipNow(${id}, true)" ${sh.packed_at ? "" : "disabled"}>Ship Now</button>
    </div></div>`;
  document.body.appendChild(back);
  document.body.classList.add("glass-open");
  // the real sections move in (their inputs and buttons keep working) and move back on close
  const body = back.querySelector("#pr-body");
  packReview = { id, moved: [] };
  ["sec-packing", "sec-pallets", "sec-carrier"].forEach(secId => {
    const sec = document.getElementById(secId);
    if (!sec) return;
    const ph = document.createComment(secId);
    sec.parentNode.insertBefore(ph, sec);
    body.appendChild(sec);
    packReview.moved.push([sec, ph]);
  });
  const boxes = document.getElementById("box-count");
  document.getElementById("pr-summary").innerHTML = sh.packed_at ? `<span class="pos">Packing accepted</span> · ready to ship` : `${boxes ? boxes.textContent : "0"} boxes proposed`;
  if (sh.packed_at) document.getElementById("pr-ship").classList.add("lit");
}
function closePackReview() {
  const back = document.getElementById("pack-review");
  if (packReview) packReview.moved.forEach(([sec, ph]) => { ph.parentNode.insertBefore(sec, ph); ph.remove(); });
  packReview = null;
  document.body.classList.remove("glass-open");
  if (back) { back.classList.add("closing"); setTimeout(() => back.remove(), 180); }
}
async function acceptPackaging(id) {
  const err = document.getElementById("pr-error"), btn = document.getElementById("pr-accept");
  err.textContent = "";
  btn.disabled = true;
  try {
    await apiFetch(`/api/shipments/${id}/boxes`, { method: "PUT", body: JSON.stringify({ boxes: collectBoxes() }) });
    await apiFetch(`/api/shipments/${id}/pallet-weights`, { method: "PUT", body: JSON.stringify({ pallets: collectPallets() }) });
    if (document.getElementById("s-carrier")) {
      const cost = document.getElementById("s-cost").value;
      await apiFetch(`/api/shipments/${id}`, { method: "PUT", body: JSON.stringify({
        carrier: document.getElementById("s-carrier").value || null, tracking_number: document.getElementById("s-tracking").value || null,
        shipping_cost: cost ? parseFloat(cost) : null, notes: document.getElementById("s-notes").value || null }) });
    }
    shipmentsById[id] = await apiFetch(`/api/shipments/${id}/accept-packing`, { method: "POST" });
    btn.textContent = "✓ Packaging Accepted";
    btn.classList.add("secondary");
    document.querySelector("#pack-review .pr-step-pack").className = "pr-step-pack done";
    document.querySelector("#pack-review .pr-step-pack").innerHTML = `${icon("check")} Packing`;
    document.querySelector("#pack-review .pr-step-ship").classList.add("on");
    document.getElementById("pr-summary").innerHTML = `<span class="pos">Packing accepted</span> · ${shipmentsById[id].boxes.length} boxes`;
    const ship = document.getElementById("pr-ship");
    ship.disabled = false;
    ship.classList.add("lit");
    ship.focus();
  } catch (e) { err.textContent = e.message; }
  btn.disabled = false;
}
async function shipNow(id, fromReview = false) {
  const err = document.getElementById(fromReview ? "pr-error" : "lifecycle-error");
  if (err) err.textContent = "";
  try {
    const sh = await apiFetch(`/api/shipments/${id}/ship`, { method: "POST" });
    if (fromReview) {
      const panel = document.querySelector("#pack-review .glass-panel");
      const done = document.createElement("div");
      done.className = "sm-done";
      done.innerHTML = `<div class="sm-done-check ship-truck">${icon("truck")}</div><h3>${escapeHtml(sh.code)} shipped</h3><p class="muted">Stock has left on-hand · taking you back…</p>`;
      panel.appendChild(done);
      requestAnimationFrame(() => done.classList.add("show"));
      await new Promise(r => setTimeout(r, 1200));
      closePackReview();
    } else toast(`${sh.code} shipped`);
    // back to where they came from: the page that linked here, else the shipments list
    const ref = document.referrer ? new URL(document.referrer) : null;
    if (ref && ref.origin === location.origin && ref.pathname !== location.pathname && !ref.pathname.endsWith("login.html")) location.href = ref.href;
    else if (typeof closeRecordPage === "function" && SHIPMENT_DETAIL_CONTAINER === "detail-card" && !document.getElementById("detail-card").classList.contains("embedded")) { await reloadList(); closeRecordPage(); }
    else { await reloadList(); showDetail(id); }
  } catch (e) { if (err) err.textContent = e.message; }
}

async function unbookLine(shipmentId, lineId) {
  const box = document.getElementById(`unbook-${lineId}`);
  const qty = box.value.trim() === "" ? parseFloat(box.placeholder) : parseFloat(box.value);
  if (!Number.isInteger(qty) || qty <= 0) {
    document.getElementById("lifecycle-error").textContent = "Unbook quantity must be a whole number greater than 0.";
    return;
  }
  await shipmentAction(shipmentId, "unbook", { shipment_line_id: lineId, quantity: qty });
}


function cancelShipment(id) {
  if (!confirm("Cancel this shipment? Its bookings are released back to stock and its packing list is cleared.")) return;
  shipmentAction(id, "cancel");
}

// Delivered date: set by a POD upload, or by hand (managers and up). It's printed on the invoice.
function deliverySectionHtml(shipment) {
  if (!["shipped", "delivered", "invoiced"].includes(shipment.status)) return `<p class="muted">Available once the shipment has shipped.</p>`;
  const canMark = AuthGuard.can("shipments.deliver");
  const today = new Date().toISOString().substring(0, 10);
  return `
    ${shipment.delivered_at
      ? `<p><span class="tag delivered">Delivered</span> <strong>${fmtDate(shipment.delivered_at)}</strong>
          <span class="muted small">Recorded By ${escapeHtml(shipment.delivered_by || "")}</span></p>`
      : `<p class="muted">Not delivered yet — uploading a proof of delivery marks it delivered.</p>`}
    ${canMark ? `
      <div class="row" style="max-width:520px; align-items:flex-end;">
        <div><label>Delivered on</label><input type="date" id="delivered-date" value="${shipment.delivered_at ? shipment.delivered_at.substring(0, 10) : today}" max="${today}"></div>
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
  catch (e) { document.getElementById("lifecycle-error").textContent = e.message; return; }
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
    await reloadList();
    showDetail(id);
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

async function saveShipmentInfo(id) {
  const errorEl = document.getElementById("info-error");
  errorEl.textContent = "";
  const cost = document.getElementById("s-cost").value;
  try {
    await apiFetch(`/api/shipments/${id}`, {
      method: "PUT",
      body: JSON.stringify({
        carrier: document.getElementById("s-carrier").value || null,
        tracking_number: document.getElementById("s-tracking").value || null,
        shipping_cost: cost ? parseFloat(cost) : null,
        notes: document.getElementById("s-notes").value || null,
      }),
    });
    await reloadList();
    showDetail(id);
  } catch (err) {
    errorEl.textContent = err.message;
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
  const names = Object.keys(byPallet);
  if (!names.length) {
    container.innerHTML = `<p class="muted">No pallets yet. Type a pallet # on a line above (or paste pallet data) to enter its weight and dimensions.</p>`;
    return;
  }
  container.innerHTML = `
    <table class="fit-table no-table-tools">
      <thead><tr><th>Pallet #</th><th class="grow">Items</th><th class="num">Boxes</th><th>Weight (lbs)</th><th>Dimensions (L x W x H in)</th></tr></thead>
      <tbody>${names.map(pn => {
        const saved = shipment.pallets.find(p => p.pallet_number === pn) || {};
        const v = Object.assign({ weight: saved.weight ?? "", dimensions: saved.dimensions ?? "" }, typed[pn] || {}, pending[pn] || {});
        return `<tr data-pallet="${escapeHtml(pn)}">
          <td><strong>${escapeHtml(pn)}</strong></td>
          <td class="grow">${escapeHtml([...byPallet[pn].items].join(", "))}</td>
          <td class="num">${byPallet[pn].boxes}</td>
          <td><input type="number" step="0.1" min="0" class="pallet-weight" style="width:100px;" value="${escapeHtml(v.weight)}"></td>
          <td><input type="text" class="pallet-dimensions" style="width:150px;" placeholder="48 x 40 x 50" value="${escapeHtml(v.dimensions)}"
                onblur="this.value = normalizeDimensions(this.value)"></td>
        </tr>`;
      }).join("")}</tbody>
    </table>`;
}

// Paste rows of: Item # <tab> Pallet # [<tab> Weight] [<tab> Dimensions] -- same format as the main portal.
// Tab or comma separated; a header row is skipped; weight/dimensions only need to appear once per pallet.
function applyPastedPallets() {
  const status = document.getElementById("pallet-paste-status");
  const shipment = shipmentsById[currentShipmentId];
  const rows = document.getElementById("pallet-paste").value.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  const pending = {};
  let applied = 0;
  const unknown = [];
  rows.forEach(row => {
    const cols = row.split(row.includes("\t") ? "\t" : ",").map(c => c.trim());
    if (cols.length < 2 || /^item/i.test(cols[0])) return;
    const [code, pallet, weight, dims] = cols;
    if (!pallet) return;
    const entries = shippedByLine(shipment).filter(e => String(itemCode(e.item_id)).toLowerCase() === code.toLowerCase());
    if (!entries.length) { unknown.push(code); return; }
    entries.forEach(e => {
      const input = document.querySelector(`.line-pallet[data-line="${e.order_line_id}"]`);
      if (input) input.value = pallet;
      setLinePallet(e.order_line_id, pallet);
      applied++;
    });
    const p = pending[pallet] = pending[pallet] || {};
    if (weight) p.weight = parseFloat(weight.replace(/[^\d.]/g, "")) || "";
    if (dims) p.dimensions = normalizeDimensions(dims);
  });
  renderPalletTable(pending);
  status.textContent = `Applied to ${applied} line${applied === 1 ? "" : "s"}.`
    + (unknown.length ? ` Not on this shipment: ${unknown.join(", ")}.` : "") + " Click Save Packing to keep it.";
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
      <td><input type="number" step="1" min="1" class="pack-size-input qty-input" data-line="${e.order_line_id}" data-qty="${e.qty}" value="${packSizeFor(shipment, e)}" oninput="splitByPackSize(${e.order_line_id})"></td>
      <td class="box-summary" data-line="${e.order_line_id}"></td>
      <td><input type="text" class="line-pallet" data-line="${e.order_line_id}" style="width:100px;" placeholder="Optional"
            value="${escapeHtml(linePallet(shipment, e.order_line_id))}" oninput="setLinePallet(${e.order_line_id}, this.value)"></td>
    </tr>
  `).join("");
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

async function showDetail(id) {
  const [shipment, allInvoices] = await Promise.all([apiFetch(`/api/shipments/${id}`), apiFetch("/api/invoices/").catch(() => []),
    packSuggest[id] ? null : loadPackSuggestions([id])]);
  const invoicesForShipment = allInvoices.filter(inv => (inv.shipment_ids || [inv.shipment_id]).includes(id) && inv.status !== "void");
  // Other shipments of this order that shipped and aren't billed yet -- can go on the same invoice.
  const combinable = shipments.filter(s => s.order_id === shipment.order_id && s.id !== shipment.id
    && ["shipped", "delivered"].includes(s.status));
  shipmentsById[shipment.id] = shipment;
  currentShipmentId = shipment.id;
  const card = detailContainer();
  card.style.display = "block";
  const ord = order(shipment.order_id);

  card.innerHTML = `
    <h3>${shipment.code} <span class="tag ${shipment.status}">${shipment.status}</span></h3>
    <p class="muted">Order <a class="link" href="customer-orders.html?id=${shipment.order_id}">${orderCode(shipment.order_id)}</a>${ord && ord.po_number
      ? ` · PO <a class="link" href="customer-orders.html?id=${shipment.order_id}">${escapeHtml(ord.po_number)}</a>` : ""}
      — created ${fmtDate(shipment.created_at)}${shipment.ship_date ? ` — shipped ${fmtDate(shipment.ship_date)}` : ""}</p>
    <p>${STATUS_HELP[shipment.status] || ""}</p>

    <section class="dsec">${linesSectionHtml(shipment)}

    ${shipment.status !== "cancelled" ? `
    </section><section class="dsec" id="sec-packing"><h4 class="dsec-title">Packing
      ${shipment.boxes.length
        ? `<span class="tag shipped">packed · ${shipment.boxes.length} box${shipment.boxes.length === 1 ? "" : "es"}</span>`
        : `<span class="tag draft">not packed — review and Save Packing</span>`}</h4>
    <p class="muted">Each order line is split into boxes by its pack size (pre-filled from the item's default) — lines are packed separately even when they're the same item. Change a pack size and the boxes update. You can pack before or after picking.</p>
    <div class="row" style="max-width:300px;">
      <div><label>Pallet # for every line (optional)</label>
        <input type="text" id="default-pallet" placeholder="E.g. PLT-1" oninput="applyPalletToAll(this.value)"></div>
    </div>
    <table class="fit-table">
      <thead><tr><th>Line</th><th class="grow">Item</th><th class="num">Qty</th><th>Pack size</th><th>Boxes</th><th>Pallet #</th></tr></thead>
      <tbody>${packSizeRowsHtml(shipment)}</tbody>
    </table>

    <details id="box-details" style="margin-top:12px;">
      <summary class="link" style="cursor:pointer;">Edit individual boxes (<span id="box-count">0</span>) — uneven splits, lot code or pallet per box</summary>
      <table class="lines-table" style="margin-top:8px;">
        <thead><tr><th>Order line</th><th>Box #</th><th>Qty in box</th><th>Lot code</th><th>Pallet #</th><th></th></tr></thead>
        <tbody id="box-rows" oninput="refreshBoxSummary(); renderPalletTable()" onchange="refreshBoxSummary(); renderPalletTable()">${boxRowsHtml(shipment)}</tbody>
      </table>
      <button class="secondary" onclick="addBoxRow()" style="margin-top:8px;">+ Add box</button>
    </details>

    </section><section class="dsec" id="sec-pallets"><h4 class="dsec-title">Pallets <span class="muted small">(optional)</span></h4>
    <p class="muted">Give lines a pallet # above, then enter each pallet's weight and dimensions here. Or paste from a spreadsheet:
      <strong>Item # · Pallet # · Weight · Dimensions</strong>, one row per item. Weight and dimensions only need to be on one row per pallet.</p>
    <details style="margin-bottom:10px;">
      <summary class="link" style="cursor:pointer;">Paste pallet data</summary>
      <textarea id="pallet-paste" rows="4" style="font-family:monospace;margin-top:6px;max-width:520px;" placeholder="Item&#9;Pallet&#9;Weight&#9;Dimensions&#10;16713&#9;PLT-1&#9;250&#9;48x40x50&#10;15420&#9;PLT-1&#10;15422&#9;PLT-2&#9;300&#9;48x40x45"></textarea>
      <div><button class="secondary" onclick="applyPastedPallets()" style="margin-top:6px;">Apply Pasted</button>
        <span id="pallet-paste-status" class="muted small"></span></div>
    </details>
    <div id="pallet-table"></div>

    <div style="margin-top:14px;">
      <button onclick="savePacking(${shipment.id})">Save Packing</button>
      ${["new", "ready"].includes(shipment.status) && (shipment.boxes.length || shipment.packed_at)
        ? `<button class="danger" onclick="unpackShipment(${shipment.id})" title="Clear the boxes, pallets and accepted packing (picking stays)">Unpack</button>` : ""}
      <button class="secondary" onclick="printLabels(${shipment.id})">Print Labels</button>
      <button class="secondary" onclick="location.href='labels.html?shipment_id=${shipment.id}'" title="Edit a label before printing, print-only">Custom Label</button>
      <button class="secondary" onclick="printPackingList(${shipment.id})">Packing List PDF</button>
      <span class="muted small" style="margin-left:6px;">Print on packing list:</span>
      <label class="inline-check"><input type="checkbox" id="pl-boxes" checked> Box details</label>
      <label class="inline-check"><input type="checkbox" id="pl-pallets" ${shipment.boxes.some(b => b.pallet_number) ? "checked" : ""}> Pallet info</label>
      <label class="inline-check" title="How many boxes ride on each pallet, in the pallet table (off unless needed)"><input type="checkbox" id="pl-pallet-boxes"> Boxes per pallet</label>
      <label class="inline-check"><input type="checkbox" id="pl-lots"> Lot #</label>
      <label class="inline-check" title="Line notes from the order (a note marked 'don't print' never prints)"><input type="checkbox" id="pl-notes" checked> Line notes</label>
    </div>
    <div id="packing-error" class="error"></div>
    ` : ""}

    ${shipment.status !== "cancelled" ? `
    </section><section class="dsec" id="sec-carrier"><h4 class="dsec-title">Carrier</h4>
    <div class="carrier-grid">
      <div><label>Carrier</label><input type="text" id="s-carrier" value="${escapeHtml(shipment.carrier || "")}" placeholder="E.g. UPS, FedEx Freight"></div>
      <div><label>Tracking Number</label><input type="text" id="s-tracking" value="${escapeHtml(shipment.tracking_number || "")}"></div>
      ${hidesMoney() ? `<input type="hidden" id="s-cost" value="">` : `<div class="money-field" title="What we pay the carrier"><label>Shipping Cost</label><input type="number" step="0.01" min="0" id="s-cost" value="${shipment.shipping_cost ?? ""}"></div>`}
    </div>
    <div class="carrier-notes"><label>Notes</label><textarea id="s-notes" rows="2">${escapeHtml(shipment.notes || "")}</textarea></div>
    <button class="secondary" onclick="saveShipmentInfo(${shipment.id})" style="margin-top:8px;">Save Carrier Info</button>
    <div id="info-error" class="error"></div>
    ` : ""}

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
  if (document.getElementById("box-rows")) { refreshBoxSummary(); renderPalletTable(); }
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

async function savePacking(shipmentId) {
  const errorEl = document.getElementById("packing-error");
  errorEl.textContent = "";
  try {
    const pallets = collectPallets();
    await apiFetch(`/api/shipments/${shipmentId}/boxes`, {
      method: "PUT",
      body: JSON.stringify({ boxes: collectBoxes() }),
    });
    await apiFetch(`/api/shipments/${shipmentId}/pallet-weights`, {
      method: "PUT",
      body: JSON.stringify({ pallets }),
    });
    showDetail(shipmentId);
  } catch (err) {
    errorEl.textContent = err.message;
  }
}

// Same layout and fields as the main portal's 4x6 labels (shared printBoxLabels in auth-guard.js).
async function printLabels(shipmentId) {
  const shipment = await apiFetch(`/api/shipments/${shipmentId}`);
  if (!shipment.boxes.length) {
    alert("Save a packing list first — there are no boxes to print labels for.");
    return;
  }
  const ord = order(shipment.order_id);
  const job = ord && ord.job_number ? `-${ord.job_number}` : "";
  printBoxLabels(shipment.boxes.map(b => ({
    customer: customerName(ord ? ord.customer_id : null), customer_id: ord ? ord.customer_id : null, shipment: shipment.code,
    order: ord ? ord.code : "", po: ord ? ord.po_number : "", job: ord ? ord.job_number : "",
    item_code: itemCode(b.item_id), item_title: (itemObj(b.item_id) || {}).title || "", qty: b.quantity_in_box,
    lot: b.lot_code || "", pallet: b.pallet_number || "", ship_to: ord ? ord.ship_to_address || "" : "",
  })), `${shipment.code}${job}-Labels`);
}

function printPackingList(shipmentId) {
  const boxes = document.getElementById("pl-boxes")?.checked ?? true;
  const pallets = document.getElementById("pl-pallets")?.checked ?? false;
  const lots = document.getElementById("pl-lots")?.checked ?? false;
  const notes = document.getElementById("pl-notes")?.checked ?? true;
  const palletBoxes = document.getElementById("pl-pallet-boxes")?.checked ?? false;
  openPdf(`/api/shipments/${shipmentId}/packing-list.pdf?boxes=${boxes}&pallets=${pallets}&lots=${lots}&notes=${notes}&pallet_boxes=${palletBoxes}`);
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
      ${history.map(h => `<tr><td class="nowrap">${new Date(h.sent_at + (h.sent_at.endsWith("Z") ? "" : "Z")).toLocaleString()}</td><td>${escapeHtml(h.to_address)}</td>
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
        <td>${old.length ? old.map(h => `<span class="pack-chip" title="${escapeHtml(`${new Date(h.changed_at).toLocaleString()} · ${h.source || ""} · ${h.changed_by || ""}${h.reference ? " · " + h.reference : ""}`)}">
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
