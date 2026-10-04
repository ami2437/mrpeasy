// Create Shipment pop-up on the Customer Orders page (uses its globals: currentOrder, items, orders,
// customerName, loadOrders). Every line with something left to ship is listed and ticked; the quantity to
// book starts at what's free (or the whole line when generic stock covers the rest) and "free after" follows
// as you type. Create books it, confirms the bookings (locked on the shipment until Change Bookings) and
// opens the shipment to pack -- after the lines drop into the box.
let shipModal = null;  // { order, lines: [{ line, item, open }], sources: { item_id: [...] } }

async function openShipModal(orderId) {
  const order = currentOrder && currentOrder.id === orderId ? currentOrder : await apiFetch(`/api/customer-orders/${orderId}`);
  const lines = order.lines.map(l => ({ line: l, item: items.find(i => i.id === l.item_id) || { code: l.item_id, title: "", available: 0 },
    open: Math.max(0, l.quantity - l.shipped_quantity - l.booked_quantity) })).filter(x => x.open > 1e-9);
  if (!lines.length) { toast("Nothing left to book on this order"); return; }
  shipModal = { order, lines, sources: {} };
  const draft = order.status === "draft", canConfirm = AuthGuard.can("orders.edit");
  const back = document.createElement("div");
  back.className = "glass-back";
  back.id = "ship-modal";
  back.addEventListener("click", e => { if (e.target === back) closeShipModal(); });
  back.innerHTML = `<div class="glass-panel" role="dialog" aria-modal="true" aria-labelledby="sm-title">
    <div class="sm-head">
      <div><h3 id="sm-title">New shipment</h3>
        <div class="muted small">${escapeHtml(order.code)} · ${escapeHtml(customerName(order.customer_id))}${order.po_number ? ` · PO ${escapeHtml(order.po_number)}` : ""}</div></div>
      <button type="button" class="icon-btn sm-close" aria-label="Close" onclick="closeShipModal()">${icon("x")}</button>
    </div>
    ${draft ? `<div class="sm-draft">${icon("pencil")}<span>${escapeHtml(order.code)} is still a draft — ${canConfirm ? "creating the shipment confirms it." : "ask a manager to confirm it first."}</span></div>` : ""}
    <div class="sm-body">
      <table class="sm-table no-table-tools no-col-bands">
        <thead><tr>
          <th class="sm-check"><input type="checkbox" id="sm-all" checked onchange="shipModalAll(this.checked)" aria-label="Select all lines"></th>
          <th>#</th><th class="grow">Item</th><th class="num">Ordered</th><th class="num" title="Shipped already, plus booked into other open shipments">Shipped / booked</th>
          <th class="num" title="Ordered minus shipped and booked">To ship</th><th class="num">Book now</th><th>Stock</th></tr></thead>
        <tbody>${lines.map((x, i) => `<tr class="sm-row" data-i="${i}">
          <td class="sm-check"><input type="checkbox" class="sm-pick" checked onchange="shipModalRefresh()" aria-label="Ship line ${x.line.line_no ?? ""}"></td>
          <td class="line-no">#${x.line.line_no ?? ""}</td>
          <td class="grow"><strong class="sm-code">${escapeHtml(x.item.code)}</strong><div class="muted small sm-title">${escapeHtml(x.item.title || "")}</div></td>
          <td class="num">${fmtQty(x.line.quantity)}</td>
          <td class="num">${fmtQty(x.line.shipped_quantity)}${x.line.booked_quantity ? `<div class="muted small">+${fmtQty(x.line.booked_quantity)} booked</div>` : ""}</td>
          <td class="num"><strong>${fmtQty(x.open)}</strong></td>
          <td class="num"><input type="number" step="1" min="0" max="${x.open}" class="sm-qty qty-input" value="${Math.floor(Math.min(x.open, Math.max(0, x.item.available)))}" oninput="shipModalRefresh()"></td>
          <td class="sm-stock"></td></tr>`).join("")}</tbody>
      </table>
    </div>
    <div class="sm-foot">
      <div class="sm-box" id="sm-box" aria-hidden="true">${icon("package")}<span class="sm-count" id="sm-count">0</span></div>
      <div class="sm-summary" id="sm-summary"></div>
      <div class="error" id="sm-error"></div>
      <button type="button" class="secondary" onclick="closeShipModal()">Cancel</button>
      <button type="button" class="sm-go" id="sm-go" onclick="createShipmentFromModal()" ${draft && !canConfirm ? "disabled" : ""}></button>
    </div></div>`;
  document.body.appendChild(back);
  document.body.classList.add("glass-open");
  document.addEventListener("keydown", shipModalKey);
  shipModalRefresh();
  // short lines: what generic stock can cover them (58-NUT for 15420-NUT)
  const short = lines.filter(x => x.open > Math.max(0, x.item.available) + 1e-9).map(x => x.item.id);
  if (short.length && AuthGuard.can("orders.edit")) {
    try { shipModal.sources = await apiFetch("/api/stock-items/generic-sources", { method: "POST", body: JSON.stringify({ item_ids: [...new Set(short)] }) }); } catch (e) {}
    document.querySelectorAll("#ship-modal .sm-row").forEach(tr => {
      const x = shipModal.lines[tr.dataset.i], src = (shipModal.sources[x.item.id] || [])[0];
      if (src && src.match !== "check") tr.querySelector(".sm-qty").value = Math.floor(Math.min(x.open, Math.max(0, x.item.available) + src.free));
    });
    shipModalRefresh();
  }
  const first = back.querySelector(".sm-qty");
  if (first) first.focus();
}

function shipModalKey(e) { if (e.key === "Escape") closeShipModal(); }
function closeShipModal() {
  const el = document.getElementById("ship-modal");
  document.removeEventListener("keydown", shipModalKey);
  document.body.classList.remove("glass-open");
  if (!el) return;
  el.classList.add("closing");
  setTimeout(() => el.remove(), 180);
}
function shipModalAll(on) {
  document.querySelectorAll("#ship-modal .sm-pick").forEach(cb => { cb.checked = on; });
  shipModalRefresh();
}

// The rows' state: picked, quantity, and the generic draw (ticked or not) -- free stock is shared by
// every line of the same item, so "free after" counts them all.
function shipModalRows() {
  return [...document.querySelectorAll("#ship-modal .sm-row")].map(tr => {
    const x = shipModal.lines[tr.dataset.i], draw = tr.querySelector(".sm-draw");
    return { tr, x, on: tr.querySelector(".sm-pick").checked, qty: parseFloat(tr.querySelector(".sm-qty").value) || 0,
             drawOn: draw ? draw.checked : null, srcId: tr.querySelector(".sm-src") ? parseInt(tr.querySelector(".sm-src").value) : null };
  });
}

function shipModalRefresh() {
  const rows = shipModalRows();
  const used = {};  // item id -> qty booked from its own free stock by the rows above
  let nLines = 0, units = 0, bad = false, why = "";
  rows.forEach(r => {
    const { tr, x } = r;
    tr.classList.toggle("off", !r.on);
    const free = Math.max(0, x.item.available) - (used[x.item.id] || 0);
    const want = r.on ? r.qty : 0;
    const fromOwn = Math.min(free, want), short = Math.max(0, want - free);
    used[x.item.id] = (used[x.item.id] || 0) + fromOwn;
    const srcs = shipModal.sources[x.item.id] || [];
    const cell = tr.querySelector(".sm-stock");
    if (!cell.dataset.built && srcs.length) {  // generic offer, built once so the tick survives re-renders
      const best = srcs[0];
      cell.dataset.built = "1";
      cell.innerHTML = `<div class="sm-free"></div><label class="sm-gen" title="${escapeHtml(best.why)}"><input type="checkbox" class="sm-draw" ${best.match !== "check" ? "checked" : ""} onchange="shipModalDrawToggle(this)">
          draw from ${srcs.length > 1 ? `<select class="sm-src" onchange="shipModalRefresh()">${srcs.map(g => `<option value="${g.id}">${escapeHtml(g.code)}</option>`).join("")}</select>`
            : `<strong>${escapeHtml(best.code)}</strong><input type="hidden" class="sm-src" value="${best.id}">`}</label><div class="sm-gen-note"></div>`;
    } else if (!cell.dataset.built) cell.innerHTML = `<div class="sm-free"></div>`;
    const src = srcs.find(g => g.id === (tr.querySelector(".sm-src") ? parseInt(tr.querySelector(".sm-src").value) : -1));
    const drawOn = tr.querySelector(".sm-draw") ? tr.querySelector(".sm-draw").checked : false;
    const covered = short > 0 && drawOn && src && src.free >= short;
    tr.querySelector(".sm-free").innerHTML = `<span class="muted">${fmtQty(free)} free</span> <span class="sm-arrow">→</span> <strong class="${short > 0 && !covered ? "neg" : ""}">${fmtQty(Math.max(0, free - want))}</strong> <span class="muted">after</span>`
      + (short > 0 ? `<div class="${covered ? "sm-gen-ok" : "neg"} small">${covered ? `+${fmtQty(short)} from ${escapeHtml(src.code)}` : `short ${fmtQty(short)}`}</div>` : "");
    const note = tr.querySelector(".sm-gen-note");
    if (note) note.innerHTML = src && src.match === "check" ? `<span class="muted small">check the finish</span>` : src ? `<span class="muted small">${fmtQty(src.free)} free there</span>` : "";
    if (r.on && want > 0) { nLines++; units += want; }
    if (r.on && (want > r.x.open + 1e-9 || !Number.isInteger(want) || (short > 0 && !covered))) {
      bad = true;
      why = why || `#${x.line.line_no ?? ""} ${x.item.code}: ${want > x.open + 1e-9 ? `only ${fmtQty(x.open)} left to ship` : !Number.isInteger(want) ? "whole numbers only" : `short ${fmtQty(short)} -- lower it${srcs.length ? " or tick the generic draw" : ""}`}`;
    }
  });
  const all = rows.every(r => r.on), go = document.getElementById("sm-go");
  document.getElementById("sm-all").checked = all;
  document.getElementById("sm-summary").innerHTML = `<strong>${nLines}</strong> line${nLines === 1 ? "" : "s"} · ${fmtQty(units)} units`
    + (why ? `<div class="neg small">${escapeHtml(why)}</div>` : "");
  const draft = shipModal.order.status === "draft";
  go.textContent = `${draft ? "Confirm & " : ""}${all && nLines === rows.length ? "Ship All Lines" : `Create Shipment (${nLines})`}`;
  go.disabled = !nLines || bad || (draft && !AuthGuard.can("orders.edit"));
  go.title = why;
}

// Ticking the generic draw books the whole line; unticking books only what's free.
function shipModalDrawToggle(box) {
  const tr = box.closest("tr"), x = shipModal.lines[tr.dataset.i];
  const src = (shipModal.sources[x.item.id] || []).find(g => g.id === parseInt(tr.querySelector(".sm-src").value)) || { free: 0 };
  tr.querySelector(".sm-qty").value = Math.floor(box.checked ? Math.min(x.open, Math.max(0, x.item.available) + src.free) : Math.min(x.open, Math.max(0, x.item.available)));
  shipModalRefresh();
}

async function createShipmentFromModal() {
  const err = document.getElementById("sm-error"), go = document.getElementById("sm-go");
  err.textContent = "";
  const rows = shipModalRows().filter(r => r.on && r.qty > 0);
  const free = {};
  const lines = rows.map(r => {
    const left = (free[r.x.item.id] ??= Math.max(0, r.x.item.available));
    const short = Math.max(0, r.qty - left);
    free[r.x.item.id] = Math.max(0, left - r.qty);
    return { line_id: r.x.line.id, quantity: r.qty, draw_from_item_id: short > 0 && r.drawOn ? r.srcId : null };
  });
  go.disabled = true;
  go.textContent = "Creating…";
  const order = shipModal.order;
  try {
    if (order.status === "draft") await apiFetch(`/api/customer-orders/${order.id}/confirm`, { method: "POST" });
    const sh = await apiFetch(`/api/customer-orders/${order.id}/shipments`, { method: "POST", body: JSON.stringify({ lines }) });
    // Reviewed here, so the bookings are confirmed: locked on the shipment until "Change Bookings".
    await apiFetch(`/api/shipments/${sh.id}/confirm-booking`, { method: "POST" }).catch(() => {});
    await shipModalAnimate(rows, sh);
    location.href = `shipments.html?id=${sh.id}`;
  } catch (e) {
    err.textContent = e.message;
    shipModalRefresh();
  }
}

// The picked lines drop into the box one by one, it bounces and counts, then a tick and the shipment code.
function shipModalAnimate(rows, sh) {
  const box = document.getElementById("sm-box"), count = document.getElementById("sm-count");
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const target = box.getBoundingClientRect();
  const tx = target.left + target.width / 2, ty = target.top + target.height / 2;
  box.classList.add("filling");
  const flights = rows.map((r, i) => new Promise(resolve => {
    const from = r.tr.querySelector(".sm-code").getBoundingClientRect();
    const chip = document.createElement("div");
    chip.className = "sm-chip";
    chip.textContent = `${r.x.item.code} × ${fmtQty(r.qty)}`;
    chip.style.left = `${from.left}px`;
    chip.style.top = `${from.top - 2}px`;
    document.body.appendChild(chip);
    const land = () => { chip.remove(); count.textContent = String(i + 1); box.animate([{ transform: "scale(1)" }, { transform: "scale(1.18)" }, { transform: "scale(1)" }], { duration: 260, easing: "ease-out" }); resolve(); };
    if (reduce) { setTimeout(land, 40 * i); return; }
    r.tr.classList.add("sent");
    const dx = tx - (from.left + chip.offsetWidth / 2), dy = ty - (from.top + chip.offsetHeight / 2);
    chip.animate([
      { transform: "translate(0, 0) scale(1)", opacity: 1 },
      { transform: `translate(${dx * 0.55}px, ${dy * 0.35 - 40}px) scale(.85)`, opacity: 1, offset: 0.55 },
      { transform: `translate(${dx}px, ${dy}px) scale(.25)`, opacity: 0.2 },
    ], { duration: 620, delay: i * 90, easing: "cubic-bezier(.45,.05,.35,1)", fill: "forwards" }).onfinish = land;
  }));
  return Promise.all(flights).then(() => new Promise(resolve => {
    const panel = document.querySelector("#ship-modal .glass-panel");
    const done = document.createElement("div");
    done.className = "sm-done";
    done.innerHTML = `<div class="sm-done-check">${icon("check")}</div><h3>${escapeHtml(sh.code)} created</h3>
      <p class="muted">${rows.length} line${rows.length === 1 ? "" : "s"} booked · opening the shipment to pack…</p>`;
    panel.appendChild(done);
    requestAnimationFrame(() => done.classList.add("show"));
    setTimeout(resolve, reduce ? 300 : 1100);
  }));
}
