// Reports page tabs (app/routes/analytics.py): Overview (the page as it was), Sales & Margin, Statements, Inventory,
// Vendors, Lot Trace, Reorder. Each tab loads when first opened.
const RP_TABS = [
  ["overview", "Overview"], ["sales", "Sales & Margin"], ["statements", "Statements"], ["inventory", "Inventory"],
  ["vendors", "Vendors"], ["lots", "Lot Trace"], ["reorder", "Reorder"],
];
let rpTab = new URLSearchParams(location.search).get("tab") || "overview";
const rpMoney = n => fmtMoney(n || 0);
const rpPct = n => n == null ? "—" : `${n}%`;

function rpRenderTabs() {
  document.getElementById("rp-tabs").innerHTML = RP_TABS.map(([k, l]) =>
    `<div class="tab ${k === rpTab ? "active" : ""}" onclick="rpShow('${k}')">${l}</div>`).join("");
}
function rpShow(tab) {
  rpTab = tab;
  rpRenderTabs();
  history.replaceState(null, "", tab === "overview" ? "reports.html" : `reports.html?tab=${tab}`);
  document.getElementById("rp-overview").style.display = tab === "overview" ? "" : "none";
  const view = document.getElementById("rp-view");
  view.style.display = tab === "overview" ? "none" : "";
  if (tab !== "overview") { view.innerHTML = `<div class="card"><p class="muted">Loading…</p></div>`; RP_LOADERS[tab]().catch(e => { view.innerHTML = `<div class="card error">${escapeHtml(e.message)}</div>`; }); }
}

// ---- Sales & Margin ----
// Stacked bars: cost (bottom) + margin (top) = revenue. Colors validated (dataviz validator) for light and dark surfaces.
function rpChartColors() {
  const dark = currentTheme() === "dark";
  return { margin: dark ? "#5b8ff9" : "#2f6fed", cost: dark ? "#d95926" : "#eb6834" };
}
function rpSalesChart(months) {
  const W = 760, H = 240, padL = 64, padB = 26, padT = 10, gap = 2, c = rpChartColors();
  const max = Math.max(1, ...months.map(m => m.revenue));
  const step = 10 ** Math.floor(Math.log10(max)), nice = Math.ceil(max / step) * step;
  const y = v => padT + (H - padT - padB) * (1 - v / nice);
  const bw = (W - padL - 10) / months.length, barW = Math.min(44, bw * 0.62);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => f * nice);
  const label = k => { const [yy, mm] = k.split("-"); return new Date(+yy, +mm - 1, 1).toLocaleDateString(undefined, { month: "short" }); };
  const bars = months.map((m, i) => {
    const x = padL + i * bw + (bw - barW) / 2, cost = Math.max(0, Math.min(m.cost, m.revenue)), mar = Math.max(0, m.revenue - cost);
    const yc = y(cost), ym = y(cost + mar);
    const costH = Math.max(0, y(0) - yc), marH = Math.max(0, yc - ym - (cost && mar ? gap : 0));
    return `<g class="rp-bar" data-i="${i}">
      <rect x="${x - 4}" y="${padT}" width="${barW + 8}" height="${H - padT - padB}" fill="transparent"/>
      ${costH ? `<rect x="${x}" y="${yc}" width="${barW}" height="${costH}" fill="${c.cost}" rx="${mar ? 0 : 4}"/>` : ""}
      ${marH ? `<path d="M${x},${yc - (cost ? gap : 0)} v-${marH - 4} q0,-4 4,-4 h${barW - 8} q4,0 4,4 v${marH - 4} z" fill="${c.margin}"/>` : ""}
      <text x="${x + barW / 2}" y="${H - 8}" text-anchor="middle" class="rp-axis">${escapeHtml(label(m.key))}</text></g>`;
  }).join("");
  return `<div class="rp-chart-wrap"><svg viewBox="0 0 ${W} ${H}" class="rp-chart" role="img" aria-label="Revenue by month, split into cost and margin">
      ${ticks.map(t => `<line x1="${padL}" x2="${W - 6}" y1="${y(t)}" y2="${y(t)}" class="rp-grid"/>
        <text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end" class="rp-axis">${t >= 1000 ? "$" + Math.round(t / 1000) + "k" : "$" + t}</text>`).join("")}
      ${bars}</svg><div class="rp-tip" hidden></div></div>
    <div class="rp-legend"><span><b style="background:${c.margin}"></b>Margin</span><span><b style="background:${c.cost}"></b>Cost (landed)</span>
      <span class="muted">Bar height = revenue shipped that month</span></div>`;
}
function rpWireTips(root, months) {
  const tip = root.querySelector(".rp-tip"), wrap = root.querySelector(".rp-chart-wrap");
  root.querySelectorAll(".rp-bar").forEach(g => {
    g.addEventListener("mousemove", e => {
      const m = months[+g.dataset.i], r = wrap.getBoundingClientRect();
      tip.hidden = false;
      tip.innerHTML = `<strong>${escapeHtml(new Date(+m.key.slice(0, 4), +m.key.slice(5) - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" }))}</strong>
        <div>Revenue ${rpMoney(m.revenue)}</div><div>Cost ${rpMoney(m.cost)}</div><div>Margin ${rpMoney(m.margin)} · ${rpPct(m.margin_pct)}</div>`;
      tip.style.left = `${Math.min(e.clientX - r.left + 12, r.width - 190)}px`;
      tip.style.top = `${e.clientY - r.top - 10}px`;
      g.classList.add("hot");
    });
    g.addEventListener("mouseleave", () => { tip.hidden = true; g.classList.remove("hot"); });
  });
}
async function rpSales(months = 12) {
  const d = await apiFetch(`/api/analytics/sales?months=${months}`);
  const tot = d.months.reduce((a, m) => ({ revenue: a.revenue + m.revenue, cost: a.cost + m.cost }), { revenue: 0, cost: 0 });
  const mar = tot.revenue - tot.cost;
  const table = (rows, first, extra = false) => `<table class="compact-table"><thead><tr><th>${first}</th>${extra ? `<th class="num sum">Qty</th>` : ""}
      <th class="num sum">Revenue</th><th class="num sum">Cost</th><th class="num sum">Margin</th><th class="num">Margin %</th></tr></thead><tbody>
      ${rows.map(r => `<tr><td>${extra ? `<a class="link" href="item.html?id=${r.item_id}">${escapeHtml(r.key)}</a><div class="muted small">${escapeHtml(r.title || "")}</div>` : escapeHtml(r.key)}</td>
        ${extra ? `<td class="num">${fmtQty(r.quantity)}</td>` : ""}<td class="num">${rpMoney(r.revenue)}</td><td class="num">${rpMoney(r.cost)}</td>
        <td class="num">${rpMoney(r.margin)}</td><td class="num">${rpPct(r.margin_pct)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">Nothing shipped in this period.</td></tr>`}
      </tbody></table>`;
  const view = document.getElementById("rp-view");
  view.innerHTML = `<div class="card">
      <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;"><h3 style="margin:0;">Sales & Margin</h3>
        <select id="rp-months" style="width:auto;" onchange="rpSales(+this.value)">${[3, 6, 12, 24].map(n => `<option value="${n}" ${n === months ? "selected" : ""}>Last ${n} months</option>`).join("")}</select>
        <span class="muted small">By ship date. Margin uses each lot's landed cost.</span></div>
      <div class="money-tiles" style="margin-top:12px; max-width:640px;">
        <div class="mtile t-total"><span>Revenue shipped</span><strong>${rpMoney(tot.revenue)}</strong><small>last ${months} months</small></div>
        <div class="mtile t-wait"><span>Cost</span><strong>${rpMoney(tot.cost)}</strong><small>landed lot cost</small></div>
        <div class="mtile t-paid"><span>Margin</span><strong>${rpMoney(mar)}</strong><small>${tot.revenue ? (mar / tot.revenue * 100).toFixed(1) + "% of revenue" : "—"}</small></div></div>
      ${d.lines_without_cost ? `<div class="notice" style="margin-top:10px;">${d.lines_without_cost} shipped line${d.lines_without_cost === 1 ? "" : "s"} came from lots with no cost (mostly MRPeasy history),
        so margin is overstated for those months. Set lot costs on the Lots page to correct it.</div>` : ""}
      ${rpSalesChart(d.months)}
    </div>
    <div class="card"><h3 style="margin-top:0;">By Month</h3>${table([...d.months].reverse().map(m => ({ ...m, key: new Date(+m.key.slice(0, 4), +m.key.slice(5) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" }) })), "Month")}</div>
    <div class="card"><h3 style="margin-top:0;">By Customer</h3>${table(d.customers, "Customer")}</div>
    <div class="card"><h3 style="margin-top:0;">By Item <span class="muted small">(top 100 by revenue)</span></h3>${table(d.items, "Item", true)}</div>`;
  rpWireTips(view, d.months);
}

// ---- Statements: receivables aging by customer + statement PDF ----
async function rpStatements() {
  const rows = await apiFetch("/api/analytics/ar-aging");
  document.getElementById("rp-view").innerHTML = `<div class="card"><h3 style="margin-top:0;">Customer Statements</h3>
    <p class="muted small" style="margin-top:0;">Sent invoices with money still owed, by how late they are (company time). Open a statement to print or email it.</p>
    <table class="compact-table"><thead><tr><th>Customer</th><th class="num sum">Current</th><th class="num sum">1–30</th><th class="num sum">31–60</th>
      <th class="num sum">61–90</th><th class="num sum">90+</th><th class="num sum">Total</th><th></th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><strong>${escapeHtml(r.customer)}</strong><div class="muted small">${r.invoices.length} invoice${r.invoices.length === 1 ? "" : "s"}</div></td>
      <td class="num">${rpMoney(r.current)}</td><td class="num ${r.d30 ? "neg" : ""}">${rpMoney(r.d30)}</td><td class="num ${r.d60 ? "neg" : ""}">${rpMoney(r.d60)}</td>
      <td class="num ${r.d90 ? "neg" : ""}">${rpMoney(r.d90)}</td><td class="num ${r.d90p ? "neg" : ""}">${rpMoney(r.d90p)}</td><td class="num"><strong>${rpMoney(r.total)}</strong>${r.credit ? `<div class="muted small" title="${escapeHtml((r.credits || []).map(c => c.code).join(", "))}">after ${rpMoney(-r.credit)} credit</div>` : ""}</td>
      <td class="nowrap"><a class="link" onclick="openPdf('/api/analytics/statement/${r.customer_id}.pdf')">Statement PDF</a>
        ${(r.d30 || r.d60 || r.d90 || r.d90p) && AuthGuard.can("invoices") ? ` · <a class="link" onclick="sendReminder(${r.customer_id})" title="Email this customer a reminder with their statement attached">Send Reminder</a>` : ""}</td></tr>`).join("")
      || `<tr><td colspan="8" class="muted">Nobody owes anything on a sent invoice.</td></tr>`}</tbody></table></div>`;
}

// Overdue reminder: the email (editable) with the customer's statement attached; logged so Tasks stops nagging.
async function sendReminder(customerId) {
  let d;
  try { d = await apiFetch(`/api/reminders/draft/${customerId}`); } catch (e) { return toast(e.message); }
  let error = "";
  while (true) {
    const { value, el } = await askDialog({ title: `Payment Reminder — ${d.customer}`,
      body: `<p class="muted small" style="margin-top:0;">${d.invoices.length} overdue · ${rpMoney(d.amount)} · oldest ${d.oldest_days} days late${d.last_reminder ? ` · last reminder ${fmtDate(d.last_reminder)}` : ""}. Their statement is attached.</p>
        <label>To</label><input type="text" class="rm-to" value="${escapeHtml(d.to)}">
        <label>CC</label><input type="text" class="rm-cc" value="${escapeHtml(d.cc || "")}">
        <label>Subject</label><input type="text" class="rm-subject" value="${escapeHtml(d.subject)}">
        <label>Message</label><textarea class="rm-body" rows="10">${escapeHtml(d.body)}</textarea>
        ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}`,
      buttons: [{ label: "Send Reminder", value: "send", cls: "confirm-btn" }, { label: "Statement PDF", value: "pdf", cls: "secondary" }, { label: "Cancel", value: null, cls: "secondary" }] });
    if (!value) return;
    d = { ...d, to: el.querySelector(".rm-to").value, cc: el.querySelector(".rm-cc").value, subject: el.querySelector(".rm-subject").value, body: el.querySelector(".rm-body").value };
    if (value === "pdf") { openPdf(`/api/analytics/statement/${customerId}.pdf`); continue; }
    try {
      const r = await apiFetch(`/api/reminders/send/${customerId}`, { method: "POST", body: JSON.stringify({ to: d.to, cc: d.cc, subject: d.subject, body: d.body }) });
      toast(`Reminder sent to ${r.to.join(", ")}`);
      return;
    } catch (e) { error = e.message; }
  }
}

// ---- Inventory value and slow movers ----
async function rpInventory() {
  const d = await apiFetch("/api/analytics/inventory");
  document.getElementById("rp-view").innerHTML = `<div class="card"><h3 style="margin-top:0;">Inventory Value <span class="muted small">${rpMoney(d.total)} at landed cost</span></h3>
    <table class="compact-table"><thead><tr><th>Product group</th><th class="num sum">Items</th><th class="num sum">Units</th><th class="num sum">Value</th><th class="num">Share</th></tr></thead><tbody>
    ${d.groups.map(g => `<tr><td>${escapeHtml(g.group)}</td><td class="num">${g.items}</td><td class="num">${fmtQty(g.units)}</td><td class="num">${rpMoney(g.value)}</td>
      <td class="num">${d.total ? (g.value / d.total * 100).toFixed(1) + "%" : ""}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h3 style="margin-top:0;">Slow Movers <span class="muted small">in stock, nothing shipped in ${d.slow_days} days</span></h3>
    <table class="compact-table"><thead><tr><th>Item</th><th class="num sum">On hand</th><th class="num sum">Value</th><th>Last shipped</th></tr></thead><tbody>
    ${d.slow.map(r => `<tr><td><a class="link" href="item.html?id=${r.item_id}">${escapeHtml(r.code)}</a><div class="muted small">${escapeHtml(r.title || "")}</div></td>
      <td class="num">${fmtQty(r.on_hand)}</td><td class="num">${rpMoney(r.value)}</td><td>${r.last_out ? `${fmtDate(r.last_out)} <span class="muted small">(${r.days} days)</span>` : `<span class="muted">Never</span>`}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">Everything in stock has moved recently.</td></tr>`}</tbody></table></div>`;
}

// ---- Vendor performance ----
async function rpVendors() {
  const rows = await apiFetch("/api/analytics/vendors");
  document.getElementById("rp-view").innerHTML = `<div class="card"><h3 style="margin-top:0;">Vendor Performance <span class="muted small">last 12 months</span></h3>
    <p class="muted small" style="margin-top:0;">On time = the first receipt came on or before the PO's expected date. Lead time = order date to first receipt.</p>
    <table class="compact-table"><thead><tr><th>Vendor</th><th class="num sum">POs</th><th class="num sum">Spend</th><th class="num">Received</th>
      <th class="num">On time</th><th class="num">Late</th><th class="num">On-time %</th><th class="num">Avg lead (days)</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td>${escapeHtml(r.vendor)}</td><td class="num">${r.pos}</td><td class="num">${rpMoney(r.spend)}</td><td class="num">${r.received}</td>
      <td class="num">${r.on_time}</td><td class="num ${r.late ? "neg" : ""}">${r.late}</td>
      <td class="num">${r.on_time_pct == null ? "—" : `<span class="tag ${r.on_time_pct >= 90 ? "shipped" : r.on_time_pct >= 70 ? "draft" : "cancelled"}">${r.on_time_pct}%</span>`}</td>
      <td class="num">${r.avg_lead_days ?? "—"}</td></tr>`).join("") || `<tr><td colspan="8" class="muted">No purchase orders in this period.</td></tr>`}</tbody></table></div>`;
}

// ---- Lot trace ----
async function rpLots(q = "") {
  const view = document.getElementById("rp-view");
  if (!document.getElementById("rp-lot-q")) {
    view.innerHTML = `<div class="card"><h3 style="margin-top:0;">Lot Trace</h3>
      <p class="muted small" style="margin-top:0;">Type a lot #: where it came from (PO, vendor, MTRs) and every customer it shipped to. For a quality question or a recall.</p>
      <input type="search" id="rp-lot-q" placeholder="Lot # (e.g. L00512)" style="max-width:320px;" onkeydown="if (event.key === 'Enter') rpLots(this.value)">
      <button class="secondary" onclick="rpLots(document.getElementById('rp-lot-q').value)" style="margin-left:6px;">Trace</button>
      <div id="rp-lot-out" style="margin-top:12px;"></div></div>`;
    if (!q) return;
    document.getElementById("rp-lot-q").value = q;
  }
  const out = document.getElementById("rp-lot-out");
  try {
    const lots = await apiFetch(`/api/analytics/lot-trace?q=${encodeURIComponent(q)}`);
    out.innerHTML = lots.map(l => `<div class="rp-lot">
        <div><strong>${escapeHtml(l.lot_code)}</strong> · <a class="link" href="item.html?id=${l.item_id}">${escapeHtml(l.item_code)}</a> <span class="muted small">${escapeHtml(l.item_title)}</span></div>
        <div class="small muted">Received ${fmtDate(l.received_date)} · ${fmtQty(l.initial_quantity)} in, ${fmtQty(l.quantity)} left ·
          ${l.po_id ? `<a class="link" href="purchase-orders.html?id=${l.po_id}">${escapeHtml(l.po)}</a>` : escapeHtml(l.po || l.source || "")}${l.vendor ? " · " + escapeHtml(l.vendor) : ""}
          ${l.mtrs.length ? " · MTR: " + l.mtrs.map(m => `<a class="link" onclick="openAttachment(${m.id})">${escapeHtml(m.filename)}</a>`).join(", ") : ` · <span class="neg">no MTR linked</span>`}</div>
        ${l.shipments.length ? `<table class="compact-table no-table-tools" style="margin-top:6px;"><thead><tr><th>Shipment</th><th>Customer</th><th>Order / PO</th><th>Shipped</th><th class="num">Qty</th></tr></thead><tbody>
          ${l.shipments.map(s => `<tr><td><a class="link" href="shipments.html?id=${s.shipment_id}">${escapeHtml(s.shipment)}</a></td><td>${escapeHtml(s.customer || "")}</td>
            <td><a class="link" href="customer-orders.html?id=${s.order_id}">${escapeHtml(s.order || "")}</a>${s.po_number ? ` · PO ${escapeHtml(s.po_number)}` : ""}</td>
            <td>${s.ship_date ? fmtDate(s.ship_date) : `<span class="muted">${escapeHtml(s.status)}</span>`}</td><td class="num">${fmtQty(s.quantity)}</td></tr>`).join("")}</tbody></table>`
          : `<div class="small muted" style="margin-top:4px;">Not shipped to anyone yet.</div>`}</div>`).join("") || `<p class="muted">No lot matches "${escapeHtml(q)}".</p>`;
  } catch (e) { out.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`; }
}

// ---- Reorder suggestions -> draft POs ----
let rpReorderRows = [];
async function rpReorder() {
  rpReorderRows = await apiFetch("/api/analytics/reorder");
  const vendors = AuthGuard.can("purchasing") ? await apiFetch("/api/vendors/") : [];
  const canBuy = AuthGuard.can("purchasing");
  document.getElementById("rp-view").innerHTML = `<div class="card"><div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
      <h3 style="margin:0;">Reorder Suggestions</h3>
      <span class="muted small">Open orders need more than on hand + on order, or stock is at its reorder point. Quantities round up to the pack size.</span>
      ${canBuy ? `<button style="margin-left:auto;" onclick="rpCreatePos()">Create Draft POs</button>` : ""}</div>
    <table class="compact-table" style="margin-top:10px;"><thead><tr>${canBuy ? `<th style="width:28px;"><input type="checkbox" onchange="document.querySelectorAll('.rp-ro').forEach(c => c.checked = this.checked)"></th>` : ""}
      <th>Item</th><th class="num">Available</th><th class="num">On order</th><th class="num">Open orders need</th><th class="num">Short</th><th class="num">Buy</th><th>Vendor</th><th class="num">Unit cost</th></tr></thead><tbody>
    ${rpReorderRows.map((r, i) => `<tr><td>${canBuy ? `<input type="checkbox" class="rp-ro" data-i="${i}" ${r.vendor_id ? "checked" : ""}>` : ""}</td>
      <td><a class="link" href="item.html?id=${r.item_id}">${escapeHtml(r.code)}</a><div class="muted small">${escapeHtml(r.why)}</div></td>
      <td class="num">${fmtQty(r.available)}</td><td class="num">${fmtQty(r.on_order)}</td><td class="num">${fmtQty(r.open_demand)}</td>
      <td class="num ${r.short ? "neg" : ""}">${fmtQty(r.short)}</td>
      <td class="num">${canBuy ? `<input type="number" class="rp-qty" data-i="${i}" value="${r.suggest}" min="1" step="1" style="width:84px;">` : fmtQty(r.suggest)}</td>
      <td>${canBuy ? `<select class="rp-vendor" data-i="${i}" style="width:170px;"><option value="">Pick a vendor</option>${vendors.map(v => `<option value="${v.id}" ${v.id === r.vendor_id ? "selected" : ""}>${escapeHtml(v.name)}</option>`).join("")}</select>`
        : escapeHtml(r.vendor || "—")}${r.last_po ? `<div class="muted small">last ${escapeHtml(r.last_po)}</div>` : ""}</td>
      <td class="num">${canBuy ? `<input type="number" class="rp-cost" data-i="${i}" value="${r.unit_cost ?? 0}" step="any" style="width:90px;">` : rpMoney(r.unit_cost)}</td></tr>`).join("")
      || `<tr><td colspan="9" class="muted">Nothing to reorder: every open order is covered and nothing is under its reorder point.</td></tr>`}</tbody></table></div>`;
}
async function rpCreatePos() {
  const lines = [...document.querySelectorAll(".rp-ro:checked")].map(c => {
    const i = c.dataset.i, v = document.querySelector(`.rp-vendor[data-i="${i}"]`).value;
    return { item_id: rpReorderRows[i].item_id, quantity: parseFloat(document.querySelector(`.rp-qty[data-i="${i}"]`).value) || 0,
             vendor_id: v ? parseInt(v) : null, unit_cost: parseFloat(document.querySelector(`.rp-cost[data-i="${i}"]`).value) || 0 };
  });
  const noVendor = lines.filter(l => !l.vendor_id);
  if (!lines.length) { toast("Tick the items to buy"); return; }
  if (noVendor.length) { toast(`Pick a vendor for ${noVendor.length} ticked item${noVendor.length === 1 ? "" : "s"}`); return; }
  try {
    const r = await apiFetch("/api/analytics/reorder/create-pos", { method: "POST", body: JSON.stringify({ lines }) });
    document.getElementById("rp-view").insertAdjacentHTML("afterbegin", `<div class="notice info">Drafted ${r.created.map(p => `<a class="link" href="purchase-orders.html?id=${p.id}">${escapeHtml(p.code)}</a>`).join(", ")}
      — check prices and dates, then mark them ordered and send them.</div>`);
  } catch (e) { toast(e.message); }
}

const RP_LOADERS = { sales: () => rpSales(+(document.getElementById("rp-months") || {}).value || 12), statements: rpStatements, inventory: rpInventory,
                     vendors: rpVendors, lots: () => rpLots(new URLSearchParams(location.search).get("lot") || ""), reorder: rpReorder };
document.addEventListener("DOMContentLoaded", () => { rpRenderTabs(); if (rpTab !== "overview") rpShow(rpTab); });
