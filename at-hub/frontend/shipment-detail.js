// Shipment detail panel shared by the Shipments page and the Batch Shipments page:
// lines, picking, carrier, packing/boxes/pallets, labels, packing list, POD, delivery, invoicing.
// The page provides the data globals (shipments, orders, items, customers, lots,
// shipmentsById, currentShipmentId) and reloadList(); it can set SHIPMENT_DETAIL_CONTAINER
// and onShipmentDetailClose. Only one detail is open at a time (element ids are fixed).
let SHIPMENT_DETAIL_CONTAINER = "detail-card";
let onShipmentDetailClose = null;

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
  ready: "Bookings confirmed — pick the items. The shipment ships automatically once every line is fully picked.",
  shipped: "Fully picked and shipped — stock has left on-hand. Waiting for proof of delivery.",
  delivered: "Delivered to the customer — ready to invoice.",
  invoiced: "Shipped and invoiced.",
  cancelled: "Cancelled — bookings were released back to stock.",
};

function linesSectionHtml(shipment) {
  const picking = shipment.status === "ready";
  const open = ["new", "ready"].includes(shipment.status);
  return `
    <h4>Items</h4>
    <table class="fit-table">
      <thead><tr><th title="Order line">Line</th><th class="grow">Item</th><th>Lot</th><th class="num">Booked</th><th class="num">Picked</th>${picking ? "<th>Pick now</th>" : ""}${open ? "<th>Unbook</th>" : ""}</tr></thead>
      <tbody>
        ${shipment.lines.slice().sort((a, b) => (a.line_no || 0) - (b.line_no || 0) || a.id - b.id).map(l => {
          const left = Math.max(0, l.quantity - l.picked_quantity);
          return `
          <tr>
            <td class="line-no">#${l.line_no ?? ""}</td>
            <td class="grow">${itemLabel(l.item_id)}</td>
            <td>${lotCode(l.lot_id)}</td>
            <td class="num">${fmtQty(l.quantity)}</td>
            <td class="num">${fmtQty(l.picked_quantity)}${l.picked_quantity >= l.quantity ? " ✓" : ""}</td>
            ${picking ? `<td>${left > 0 ? `<input type="number" step="1" min="0" class="pick-qty qty-input" data-line="${l.id}" value="${left}">` : ""}</td>` : ""}
            ${open ? `<td class="nowrap">${left > 0 ? `
              <input type="number" step="1" min="1" max="${left}" value="${left}" id="unbook-${l.id}" class="qty-input">
              <button class="small-btn secondary" onclick="unbookLine(${shipment.id}, ${l.id})">Unbook</button>` : `<span class="muted small">Picked</span>`}</td>` : ""}
          </tr>
        `;
        }).join("")}
      </tbody>
    </table>
    <div style="margin-top:10px;">
      ${shipment.status === "new" ? `<button onclick="shipmentAction(${shipment.id}, 'confirm-booking')">Confirm Bookings</button>` : ""}
      ${picking ? `
        <button onclick="pickEntered(${shipment.id})">Pick Entered Quantities</button>
        <button onclick="pickAll(${shipment.id})">Pick All &amp; Ship</button>
      ` : ""}
      ${["new", "ready"].includes(shipment.status) ? `<button class="danger" onclick="cancelShipment(${shipment.id})">Cancel Shipment</button>` : ""}
      ${shipment.status === "shipped" && AuthGuard.hasRole("manager") ? `<button onclick="shipmentAction(${shipment.id}, 'delivered', {delivered_at: null})" title="Managers can mark delivered without a POD (today's date; change it under Proof of delivery)">Mark Delivered (no POD)</button>` : ""}
      ${["shipped", "delivered", "invoiced"].includes(shipment.status) ? `<button class="secondary" onclick="unshipShipment(${shipment.id})">Undo Ship</button>` : ""}
      ${["new", "ready", "cancelled"].includes(shipment.status) ? `<button class="danger" onclick="deleteShipment(${shipment.id})">Delete Shipment</button>` : ""}
    </div>
    <div id="lifecycle-error" class="error"></div>
  `;
}

async function shipmentAction(id, action, body) {
  const errorEl = document.getElementById("lifecycle-error");
  errorEl.textContent = "";
  try {
    await apiFetch(`/api/shipments/${id}/${action}`, { method: "POST", body: body ? JSON.stringify(body) : undefined });
    await reloadList();
    showDetail(id);
  } catch (err) {
    errorEl.textContent = err.message;
  }
}

function pickEntered(id) {
  const lines = Array.from(document.querySelectorAll(".pick-qty"))
    .map(el => ({ shipment_line_id: parseInt(el.dataset.line), quantity: parseFloat(el.value) || 0 }))
    .filter(l => l.quantity > 0);
  if (!lines.length) {
    document.getElementById("lifecycle-error").textContent = "Enter a picked quantity on at least one line.";
    return;
  }
  shipmentAction(id, "pick", { lines });
}

async function unbookLine(shipmentId, lineId) {
  const qty = parseFloat(document.getElementById(`unbook-${lineId}`).value);
  if (!Number.isInteger(qty) || qty <= 0) {
    document.getElementById("lifecycle-error").textContent = "Unbook quantity must be a whole number greater than 0.";
    return;
  }
  await shipmentAction(shipmentId, "unbook", { shipment_line_id: lineId, quantity: qty });
}

function pickAll(id) {
  if (!confirm("Mark every line fully picked? The shipment will ship and stock will leave on-hand.")) return;
  shipmentAction(id, "pick", { pick_all: true });
}

function cancelShipment(id) {
  if (!confirm("Cancel this shipment? Its bookings are released back to stock and its packing list is cleared.")) return;
  shipmentAction(id, "cancel");
}

// Delivered date: set by a POD upload, or by hand (managers and up). It's printed on the invoice.
function deliverySectionHtml(shipment) {
  if (!["shipped", "delivered", "invoiced"].includes(shipment.status)) return `<p class="muted">Available once the shipment has shipped.</p>`;
  const canMark = AuthGuard.hasRole("manager");
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
    await apiFetch(`/api/shipments/${id}/delivered`, { method: "POST", body: JSON.stringify({ delivered_at: value ? `${value}T12:00:00` : null }) });
    await reloadList();
    showDetail(id);
  } catch (err) {
    errorEl.textContent = err.message;
  }
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

function unshipShipment(id) {
  if (!confirm("Undo this shipment? Stock goes back to its lots and stays booked; the shipment returns to New so you can unbook, change, cancel or delete it. (An invoiced shipment needs its invoice voided first.)")) return;
  shipmentAction(id, "unship");
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
  if (saved.length) return Math.max(...saved.map(b => b.quantity_in_box));
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
  const [shipment, allInvoices] = await Promise.all([apiFetch(`/api/shipments/${id}`), apiFetch("/api/invoices/").catch(() => [])]);
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
    <p class="muted">Order <a class="link" href="customer-orders.html?id=${shipment.order_id}">${orderCode(shipment.order_id)}</a>
      — created ${fmtDate(shipment.created_at)}${shipment.ship_date ? ` — shipped ${fmtDate(shipment.ship_date)}` : ""}</p>
    <p>${STATUS_HELP[shipment.status] || ""}</p>

    <section class="dsec">${linesSectionHtml(shipment)}

    ${shipment.status !== "cancelled" ? `
    </section><section class="dsec"><h4 class="dsec-title">Carrier</h4>
    <div class="carrier-grid">
      <div><label>Carrier</label><input type="text" id="s-carrier" value="${escapeHtml(shipment.carrier || "")}" placeholder="E.g. UPS, FedEx Freight"></div>
      <div><label>Tracking Number</label><input type="text" id="s-tracking" value="${escapeHtml(shipment.tracking_number || "")}"></div>
      ${hidesMoney() ? `<input type="hidden" id="s-cost" value="">` : `<div class="money-field" title="What we pay the carrier"><label>Shipping Cost</label><input type="number" step="0.01" min="0" id="s-cost" value="${shipment.shipping_cost ?? ""}"></div>`}
    </div>
    <div class="carrier-notes"><label>Notes</label><textarea id="s-notes" rows="2">${escapeHtml(shipment.notes || "")}</textarea></div>
    <button class="secondary" onclick="saveShipmentInfo(${shipment.id})" style="margin-top:8px;">Save Carrier Info</button>
    <div id="info-error" class="error"></div>
    ` : ""}

    ${shipment.status !== "cancelled" ? `
    </section><section class="dsec"><h4 class="dsec-title">Packing
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

    </section><section class="dsec"><h4 class="dsec-title">Pallets <span class="muted small">(optional)</span></h4>
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
      <button class="secondary" onclick="printLabels(${shipment.id})">Print Labels</button>
      <button class="secondary" onclick="location.href='labels.html?shipment_id=${shipment.id}'" title="Edit a label before printing, print-only">Custom Label</button>
      <button class="secondary" onclick="printPackingList(${shipment.id})">Packing List PDF</button>
      <span class="muted small" style="margin-left:6px;">Print on packing list:</span>
      <label class="inline-check"><input type="checkbox" id="pl-boxes" checked> Box details</label>
      <label class="inline-check"><input type="checkbox" id="pl-pallets" ${shipment.boxes.some(b => b.pallet_number) ? "checked" : ""}> Pallet info</label>
      <label class="inline-check"><input type="checkbox" id="pl-lots"> Lot #</label>
      <label class="inline-check" title="Line notes from the order (a note marked 'don't print' never prints)"><input type="checkbox" id="pl-notes" checked> Line notes</label>
    </div>
    <div id="packing-error" class="error"></div>
    ` : ""}

    </section><section class="dsec"><h4 class="dsec-title">Proof of delivery &amp; documents</h4>
    <p class="muted small" style="margin-top:0;">Delivery photos, signed packing lists, bills of lading.
      Drivers can use the phone page: <a class="link" href="pod.html?id=${shipment.id}" target="_blank">pod.html?id=${shipment.id}</a></p>
    ${shipment.pods.length ? `<p><button class="secondary" onclick="openPodEmail(${shipment.id})">✉ Email POD to customer</button></p>` : ""}
    <div id="shipment-attachments"></div>

    </section><section class="dsec"><h4 class="dsec-title">Delivery</h4>
    ${deliverySectionHtml(shipment)}

    ${AuthGuard.hasRole("manager") ? `</section><section class="dsec"><h4 class="dsec-title">Invoicing</h4>
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
  return Array.from(document.querySelectorAll("#box-rows tr")).map(tr => ({
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
    customer: customerName(ord ? ord.customer_id : null), shipment: shipment.code,
    order: ord ? ord.code : "", po: ord ? ord.po_number : "", job: ord ? ord.job_number : "",
    item_code: itemCode(b.item_id), item_title: (itemObj(b.item_id) || {}).title || "", qty: b.quantity_in_box,
  })), `${shipment.code}${job}-Labels`);
}

function printPackingList(shipmentId) {
  const boxes = document.getElementById("pl-boxes")?.checked ?? true;
  const pallets = document.getElementById("pl-pallets")?.checked ?? false;
  const lots = document.getElementById("pl-lots")?.checked ?? false;
  const notes = document.getElementById("pl-notes")?.checked ?? true;
  openPdf(`/api/shipments/${shipmentId}/packing-list.pdf?boxes=${boxes}&pallets=${pallets}&lots=${lots}&notes=${notes}`);
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
  if (s.status !== "invoiced" || !s.invoice_code || !AuthGuard.hasRole("manager")) return tag;
  return invoiceChipFromShipment(s);
}
