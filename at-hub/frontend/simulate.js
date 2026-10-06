// Simulate (simulate.html): what an order -- or several, or one repeated -- would make, before committing.
// Left page: demand (customer orders, quotes, pasted lines; each block repeatable "x 8").
// Right page: sources (POs, vendor quotes, pasted lines) with their extra costs, plus costs shared by every source.
// Below: the item-by-item comparison, profit per order, and what to order. The doc autosaves (app/routes/simulations.py).
AuthGuard.requirePerm("simulate");
document.getElementById("sidebar").innerHTML = renderSidebar("simulate.html");

let SIMS = [], sim = null, doc = null, ITEMS = [], VENDORS = [], CUSTOMERS = [], INSIGHT = {}, saveTimer = null, saveState = "";
const COST_TYPES = [["tariff", "Tariff / duty", "percent"], ["inland", "Inland freight", "total"], ["trucking", "Trucking", "total"],
                    ["shipping", "Ocean / air shipping", "total"], ["customs", "Customs & brokerage", "total"], ["fees", "Fees", "total"],
                    ["insurance", "Insurance", "percent"], ["other", "Other", "total"]];
const MODES = [["per_unit", "$ / unit"], ["total", "$ total"], ["percent", "% of value"]];
const uid = () => Math.random().toString(36).slice(2, 10);
const num = v => { const n = parseFloat(String(v ?? "").replace(/[$,]/g, "")); return isNaN(n) ? 0 : n; };
const itemById = id => ITEMS.find(i => i.id === id);
const keyOf = l => l.item_id ? "i" + l.item_id : "c:" + String(l.code || l.desc || "").trim().toLowerCase();
const money = v => v == null || isNaN(v) ? "—" : fmtMoney(v);
const price = v => v == null || isNaN(v) ? "—" : fmtPrice(v);

function blankDoc() {
  return { demand: [], sources: [], shared_costs: [], estimates: {}, use_stock: false, sheet_cols: [], summary: {} };
}

// ================= the numbers =================
function calc() {
  const d = doc, items = {};
  const it = key => (items[key] ??= { key, item_id: null, code: "", desc: "", need: 0, revenue: 0, supply: 0, vendorValue: 0, effValue: 0, demandBlocks: new Set(), sourceBlocks: new Set() });
  // sources: each line's effective unit cost = its price + its share of the source's and the shared extra costs
  const allSrcLines = d.sources.flatMap(b => b.lines.map(l => ({ b, l })));
  const effPer = {};  // line id -> extras per unit
  const spread = (costs, lines) => {
    const value = lines.reduce((s, { l }) => s + num(l.qty) * num(l.price), 0), qty = lines.reduce((s, { l }) => s + num(l.qty), 0);
    for (const c of costs || []) {
      const amt = num(c.amount);
      for (const { l } of lines) {
        const q = num(l.qty);
        if (!q) continue;
        let add = 0;
        if (c.mode === "per_unit") add = amt;
        else if (c.mode === "percent") add = num(l.price) * amt / 100;
        else add = (c.alloc === "qty" ? (qty ? amt * q / qty : 0) : (value ? amt * (q * num(l.price)) / value : 0)) / q;
        effPer[l.id] = (effPer[l.id] || 0) + add;
      }
    }
  };
  d.sources.forEach(b => spread(b.costs, b.lines.map(l => ({ b, l }))));
  spread(d.shared_costs, allSrcLines);
  for (const { b, l } of allSrcLines) {
    const q = num(l.qty); if (!q) continue;
    const x = it(keyOf(l));
    Object.assign(x, { item_id: x.item_id || l.item_id, code: x.code || l.code, desc: x.desc || l.desc });
    x.supply += q; x.vendorValue += q * num(l.price); x.effValue += q * (num(l.price) + (effPer[l.id] || 0)); x.sourceBlocks.add(b.id);
  }
  for (const b of d.demand) {
    const m = Math.max(1, num(b.multiplier) || 1);
    for (const l of b.lines) {
      const q = num(l.qty) * m; if (!q) continue;
      const x = it(keyOf(l));
      Object.assign(x, { item_id: x.item_id || l.item_id, code: x.code || l.code, desc: x.desc || l.desc });
      x.need += q; x.revenue += q * num(l.price); x.demandBlocks.add(b.id);
    }
  }
  const rows = Object.values(items).map(x => {
    const ins = x.item_id ? INSIGHT[x.item_id] : null;
    const est = d.estimates[x.key];
    const vendor = x.supply ? x.vendorValue / x.supply : null, eff = x.supply ? x.effValue / x.supply : null;
    const cost = eff != null ? eff : (est !== undefined && est !== "" ? num(est) : null);
    const custPrice = x.need ? x.revenue / x.need : null;
    const onHand = ins ? Math.max(0, ins.available || 0) : 0;
    return { ...x, vendor, eff, extras: eff != null && vendor != null ? eff - vendor : null, cost, estimated: eff == null && cost != null,
             custPrice, profitUnit: custPrice != null && cost != null ? custPrice - cost : null,
             profit: cost != null && x.need ? x.revenue - x.need * cost : null, onHand,
             toOrder: Math.max(0, x.need - x.supply - (d.use_stock ? onHand : 0)), notInDb: !x.item_id };
  }).sort((a, b) => (b.need > 0) - (a.need > 0) || String(a.code).localeCompare(String(b.code)));
  const costOf = Object.fromEntries(rows.map(r => [r.key, r.cost]));
  const orders = d.demand.map(b => {
    // profit counts only lines with a cost: a line nobody has priced yet would otherwise look like pure profit
    let rev = 0, costedRev = 0, cost = 0, missing = 0;
    for (const l of b.lines) {
      const q = num(l.qty); rev += q * num(l.price);
      const c = costOf[keyOf(l)];
      if (c == null) { if (q) missing++; } else { cost += q * c; costedRev += q * num(l.price); }
    }
    const m = Math.max(1, num(b.multiplier) || 1);
    return { b, m, rev, cost, profit: costedRev - cost, missing, margin: costedRev ? (costedRev - cost) / costedRev * 100 : null };
  });
  const withCost = rows.filter(r => r.need && r.cost != null);
  const revenue = rows.reduce((s, r) => s + r.revenue, 0), cost = withCost.reduce((s, r) => s + r.need * r.cost, 0);
  const profit = withCost.reduce((s, r) => s + r.revenue - r.need * r.cost, 0);
  const costedRev = withCost.reduce((s, r) => s + r.revenue, 0);
  const summary = { revenue, cost, profit, margin: costedRev ? profit / costedRev * 100 : null, items: rows.filter(r => r.need).length,
                    missing: rows.filter(r => r.need && r.cost == null).length, notInDb: rows.filter(r => r.notInDb).length,
                    toOrder: rows.filter(r => r.toOrder > 0).length };
  return { rows, orders, summary, effPer };
}

// ================= drawing =================
function partyName(list, id) { return (list.find(x => x.id === id) || {}).name || ""; }
function lineRow(side, b, l, effPer) {
  const glow = !l.item_id ? "sim-unknown" : "";
  const ext = num(l.qty) * num(l.price);
  const cands = !l.item_id && l.candidates && l.candidates.length
    ? `<select class="sim-cand" onchange="pickCandidate('${side}','${b.id}','${l.id}', this.value)"><option value="">Not in our database — match it?</option>
        ${l.candidates.map(c => `<option value="${c.item_id}">${escapeHtml(c.code)} — ${escapeHtml(c.title)}</option>`).join("")}</select>` : "";
  return `<tr class="${glow}" data-line="${l.id}">
    <td class="sim-item"><input class="sim-code" value="${escapeHtml(l.code || "")}" placeholder="Item #" onchange="setCode('${side}','${b.id}','${l.id}', this.value)"
        title="${l.item_id ? "In our database" : "Not in our database: type our item # to link it, or price it by hand"}">
      <input class="sim-desc" value="${escapeHtml(l.desc || "")}" placeholder="Description" oninput="setField('${side}','${b.id}','${l.id}','desc', this.value, true)">
      ${!l.item_id ? `<span class="sim-tag" title="Not in our database: no price history, enter the price yourself">not in DB</span>` : ""}${cands}</td>
    <td class="num"><input type="number" class="sim-num" value="${l.qty ?? ""}" min="0" step="1" oninput="setField('${side}','${b.id}','${l.id}','qty', this.value)"></td>
    <td class="num"><input type="number" class="sim-num" value="${l.price ?? ""}" min="0" step="any" placeholder="${side === "demand" ? "price" : "cost"}" oninput="setField('${side}','${b.id}','${l.id}','price', this.value)"></td>
    ${side === "sources" ? `<td class="num muted small" data-eff="${l.id}">${effPer[l.id] ? "+" + fmtPrice(effPer[l.id]) : ""}</td>` : ""}
    <td class="num" data-ext="${l.id}">${money(ext)}</td>
    <td><a class="sim-x" title="Remove line" onclick="removeLine('${side}','${b.id}','${l.id}')">×</a></td></tr>`;
}
function blockCard(side, b, effPer, orderCalc) {
  const isD = side === "demand";
  const party = isD ? partyName(CUSTOMERS, b.party_id) : partyName(VENDORS, b.party_id);
  const total = b.lines.reduce((s, l) => s + num(l.qty) * num(l.price), 0);
  const oc = orderCalc;
  const kindTag = { order: "Order", quote: "Quote", po: "PO", paste: "Pasted", pdf: "AI read", manual: "Manual", sheet: "Sheet" }[b.kind] || "";
  return `<div class="sim-block ${isD ? "is-demand" : "is-source"}" data-block="${b.id}">
    <div class="sim-bhead">
      <input class="sim-label" value="${escapeHtml(b.label || "")}" placeholder="${isD ? "Customer order / job" : "Source"}" oninput="setBlock('${side}','${b.id}','label', this.value)">
      <span class="tag draft">${kindTag}</span>
      ${b.ref_link ? `<a class="link small" href="${b.ref_link}">${escapeHtml(b.ref_code || "")}</a>` : ""}
      <span class="spacer"></span>
      ${isD ? `<label class="sim-mult" title="This order repeated (e.g. the same order 8 times)">× <input type="number" min="1" step="1" value="${b.multiplier || 1}" oninput="setBlock('demand','${b.id}','multiplier', this.value)"></label>` : ""}
      <a class="sim-x" title="Remove this ${isD ? "order" : "source"}" onclick="removeBlock('${side}','${b.id}')">×</a></div>
    <div class="sim-party"><select onchange="setBlock('${side}','${b.id}','party_id', parseInt(this.value) || null, true)">
        <option value="">${isD ? "Customer…" : "Vendor…"}</option>${(isD ? CUSTOMERS : VENDORS).map(p => `<option value="${p.id}" ${p.id === b.party_id ? "selected" : ""}>${escapeHtml(p.name)}</option>`).join("")}</select>
      ${party ? "" : `<span class="muted small">${isD ? "pick the customer for price history and to make an order" : "pick the vendor to make a PO"}</span>`}</div>
    <table class="compact-table no-table-tools sim-lines"><thead><tr><th>Item</th><th class="num">Qty</th><th class="num">${isD ? "Price" : "Cost"}</th>${isD ? "" : `<th class="num" title="Extra costs per unit">+ Extras</th>`}<th class="num">Total</th><th></th></tr></thead>
      <tbody>${b.lines.map(l => lineRow(side, b, l, effPer)).join("")}</tbody></table>
    <div class="sim-bfoot"><a class="link small" onclick="addLine('${side}','${b.id}')">+ Line</a>
      <span class="spacer"></span><span class="small" data-btotal="${b.id}">${isD ? `${money(total)} per order${b.multiplier > 1 ? ` · ${money(total * b.multiplier)} × ${b.multiplier}` : ""}` : `${money(total)} goods`}</span></div>
    ${isD && oc ? `<div class="sim-bprofit" data-bprofit="${b.id}">${orderProfitChip(oc)}</div>` : ""}
    ${!isD ? costsTable(`sources','${b.id}`, b.costs || [], b.id) : ""}
    <div class="sim-bactions">${isD ? `<button class="secondary small-btn" onclick="makeOrder('${b.id}')" title="A draft customer order from these lines">Create Customer Order</button>`
      : `<button class="secondary small-btn" onclick="makePo('${b.id}')" title="A draft PO to this vendor from these lines">Create PO</button>`}</div>
  </div>`;
}
function orderProfitChip(oc) {
  if (!oc.rev) return `<span class="muted small">Add prices to see this order's profit.</span>`;
  const cls = oc.profit >= 0 ? "pos" : "neg";
  return `<span class="small">Profit per order${oc.missing ? " (costed lines)" : ""} <b class="${cls}">${money(oc.profit)}</b>${oc.margin != null ? ` (${oc.margin.toFixed(1)}%)` : ""}
    ${oc.m > 1 ? ` · × ${oc.m} = <b class="${cls}">${money(oc.profit * oc.m)}</b>` : ""}${oc.missing ? ` · <span class="sim-warn">${oc.missing} line${oc.missing === 1 ? "" : "s"} without a cost</span>` : ""}</span>`;
}
function costsTable(path, costs, blockId) {
  const shared = blockId === "shared";
  return `<div class="sim-costs"><div class="sim-costs-head"><b>${shared ? "Shared extra costs" : "Extra costs"}</b>
      <span class="muted small">${shared ? "spread over every source line" : "spread over this source's lines"}</span></div>
    ${costs.length ? `<table class="compact-table no-table-tools"><tbody>${costs.map(c => `<tr>
      <td><input value="${escapeHtml(c.label || "")}" oninput="setCost('${blockId}','${c.id}','label', this.value)"></td>
      <td><select onchange="setCost('${blockId}','${c.id}','mode', this.value, true)">${MODES.map(([k, l]) => `<option value="${k}" ${k === c.mode ? "selected" : ""}>${l}</option>`).join("")}</select></td>
      <td class="num"><input type="number" class="sim-num" step="any" value="${c.amount ?? ""}" oninput="setCost('${blockId}','${c.id}','amount', this.value)"></td>
      <td>${c.mode === "total" ? `<select title="Spread by" onchange="setCost('${blockId}','${c.id}','alloc', this.value, true)"><option value="value" ${c.alloc !== "qty" ? "selected" : ""}>by value</option><option value="qty" ${c.alloc === "qty" ? "selected" : ""}>by qty</option></select>` : ""}</td>
      <td><a class="sim-x" onclick="removeCost('${blockId}','${c.id}')">×</a></td></tr>`).join("")}</tbody></table>` : ""}
    <div class="sim-chips">${COST_TYPES.map(([k, l, m]) => `<a class="pack-chip" onclick="addCost('${blockId}','${k}')">+ ${l}</a>`).join("")}</div></div>`;
}
function compareTable(c) {
  if (!c.rows.length) return `<p class="muted">Add customer demand on the left and sources on the right — the comparison appears here.</p>`;
  return `<table class="compact-table sim-compare"><thead><tr><th>Item</th><th class="num sum">Need</th><th class="num sum">Sourced</th>
      <th class="num" title="Available stock (on hand minus booked)">On hand</th><th class="num sum">To order</th><th class="num">Customer price</th>
      <th class="num">Vendor price</th><th class="num">+ Extras</th><th class="num" title="Vendor price + extras (or your estimate)">Effective cost</th>
      <th class="num">Profit / unit</th><th class="num sum">Profit</th><th></th></tr></thead><tbody>
    ${c.rows.map(r => `<tr class="${r.notInDb ? "sim-unknown" : ""}">
      <td><b>${escapeHtml(r.code || "")}</b> <span class="muted small">${escapeHtml(r.desc || (r.item_id && itemById(r.item_id) ? itemById(r.item_id).title : ""))}</span>
        ${r.notInDb ? `<span class="sim-tag">not in DB</span>` : ""}</td>
      <td class="num">${fmtQty(r.need)}</td>
      <td class="num">${r.supply ? fmtQty(r.supply) : "—"}${r.supply && r.supply < r.need ? `<div class="sim-warn small">short ${fmtQty(r.need - r.supply)}</div>` : ""}</td>
      <td class="num">${r.item_id ? fmtQty(r.onHand) : "—"}</td>
      <td class="num">${r.toOrder ? `<b>${fmtQty(r.toOrder)}</b>` : "—"}</td>
      <td class="num">${price(r.custPrice)}</td><td class="num">${price(r.vendor)}</td>
      <td class="num">${r.extras ? "+" + fmtPrice(r.extras) : "—"}</td>
      <td class="num">${r.eff != null ? `<b>${fmtPrice(r.eff)}</b>` : `<input type="number" class="sim-num sim-est" step="any" placeholder="estimate" value="${doc.estimates[r.key] ?? ""}"
          title="Nobody sources this yet: estimate what it will cost us" oninput="setEstimate('${escapeHtml(r.key)}', this.value)">`}</td>
      <td class="num ${r.profitUnit != null ? (r.profitUnit >= 0 ? "pos" : "neg") : ""}">${r.profitUnit != null ? fmtPrice(r.profitUnit) : "—"}
        ${r.profitUnit != null && r.custPrice ? `<div class="small muted">${(r.profitUnit / r.custPrice * 100).toFixed(1)}%</div>` : ""}</td>
      <td class="num ${r.profit != null ? (r.profit >= 0 ? "pos" : "neg") : ""}">${r.profit != null ? money(r.profit) : `<span class="sim-warn small">${r.need ? "needs a cost" : ""}</span>`}</td>
      <td>${r.item_id ? `<a class="link small" onclick="showInsight(${r.item_id})" title="Who bought and sold it, at what">History</a>` : ""}</td></tr>`).join("")}
    </tbody></table>`;
}
function ordersTable(c) {
  if (!c.orders.length) return "";
  return `<table class="compact-table no-table-tools"><thead><tr><th>Order</th><th class="num">Repeated</th><th class="num">Revenue / order</th>
      <th class="num">Cost / order</th><th class="num">Profit / order</th><th class="num">Margin</th><th class="num">Total profit</th></tr></thead><tbody>
    ${c.orders.map(o => `<tr><td>${escapeHtml(o.b.label || o.b.ref_code || "Order")}${o.missing ? ` <span class="sim-warn small">${o.missing} without cost</span>` : ""}</td>
      <td class="num">× ${o.m}</td><td class="num">${money(o.rev)}</td><td class="num">${money(o.cost)}</td>
      <td class="num ${o.profit >= 0 ? "pos" : "neg"}">${money(o.profit)}</td><td class="num">${o.margin != null ? o.margin.toFixed(1) + "%" : "—"}</td>
      <td class="num ${o.profit >= 0 ? "pos" : "neg"}"><b>${money(o.profit * o.m)}</b></td></tr>`).join("")}</tbody></table>`;
}
function tiles(s) {
  const t = (label, v, sub, cls = "") => `<div class="mtile ${cls}"><span>${label}</span><strong>${v}</strong><small>${sub}</small></div>`;
  return `<div class="money-tiles">${t("Revenue", money(s.revenue), `${s.items} item${s.items === 1 ? "" : "s"}`, "t-total")}
    ${t("Cost", money(s.cost), "vendor price + extras", "t-wait")}
    ${t("Profit", money(s.profit), s.margin != null ? `${s.margin.toFixed(1)}% margin${s.missing ? " on costed items" : ""}` : "—", s.profit >= 0 ? "t-paid" : "t-owed")}
    ${t("Without a cost", s.missing, s.missing ? "estimate or add a source" : "every item costed", s.missing ? "t-owed" : "t-zero")}
    ${t("To order", s.toOrder, s.notInDb ? `${s.notInDb} not in our database` : "items short", s.toOrder ? "t-billed" : "t-zero")}</div>`;
}
function render() {
  const el = document.getElementById("sim-main");
  if (!sim) {
    el.innerHTML = `<div class="card"><p class="muted" style="margin:0;">Pick a simulation above or start a new one.</p></div>`;
    return;
  }
  const c = calc();
  const ocById = Object.fromEntries(c.orders.map(o => [o.b.id, o]));
  el.innerHTML = `
    <div id="sim-tiles">${tiles(c.summary)}</div>
    <div class="sim-book">
      <section class="sim-page sim-left"><div class="sim-page-head"><h3>Sell <span class="muted small">what customers want</span></h3>
          <div class="sim-add">${[["order", "Customer Order"], ["quote", "Quote"], ["paste", "Paste Lines"], ["pdf", "AI: Read PO PDF"], ["manual", "Empty"]].map(([k, l]) =>
            `<button class="secondary small-btn" onclick="addDemand('${k}')">+ ${l}</button>`).join("")}</div></div>
        ${doc.demand.map(b => blockCard("demand", b, c.effPer, ocById[b.id])).join("") || `<p class="muted small">No demand yet.</p>`}</section>
      <div class="sim-binding" aria-hidden="true"></div>
      <section class="sim-page sim-right"><div class="sim-page-head"><h3>Buy <span class="muted small">where it comes from</span></h3>
          <div class="sim-add">${[["po", "Purchase Order"], ["paste", "Paste Vendor Quote"], ["pdf", "AI: Read Vendor Quote"], ["manual", "Empty"]].map(([k, l]) =>
            `<button class="secondary small-btn" onclick="addSource('${k}')">+ ${l}</button>`).join("")}</div></div>
        ${doc.sources.map(b => blockCard("sources", b, c.effPer)).join("") || `<p class="muted small">No sources yet — items without a source need an estimated cost below.</p>`}
        <div class="sim-block is-shared">${costsTable("shared", doc.shared_costs, "shared")}</div></section>
    </div>
    <div class="card"><div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;"><h3 style="margin:0;">Item By Item</h3>
        <label class="check-label"><input type="checkbox" ${doc.use_stock ? "checked" : ""} onchange="doc.use_stock = this.checked; changed(true)"> Use stock on hand before ordering</label>
        <span class="spacer"></span><button class="secondary small-btn" onclick="makePoFromShort()" title="A draft PO for everything still to order">Create PO For What's Short</button></div>
      <div id="sim-compare">${compareTable(c)}</div></div>
    <div class="card"><h3 style="margin-top:0;">Profit Per Order</h3><div id="sim-orders">${ordersTable(c) || `<p class="muted small">Add demand to see each order's profit.</p>`}</div></div>`;
  doc.summary = c.summary;
}
// numbers changed: redraw only the results (keeps the cursor where you're typing)
function renderResults() {
  const c = calc();
  doc.summary = c.summary;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set("sim-tiles", tiles(c.summary));
  set("sim-orders", ordersTable(c));
  const cmp = document.getElementById("sim-compare");
  if (cmp && !cmp.contains(document.activeElement)) cmp.innerHTML = compareTable(c);
  for (const b of [...doc.demand, ...doc.sources]) {
    const isD = doc.demand.includes(b);
    const total = b.lines.reduce((s, l) => s + num(l.qty) * num(l.price), 0);
    const t = document.querySelector(`[data-btotal="${b.id}"]`);
    if (t) t.innerHTML = isD ? `${money(total)} per order${b.multiplier > 1 ? ` · ${money(total * b.multiplier)} × ${b.multiplier}` : ""}` : `${money(total)} goods`;
    const p = document.querySelector(`[data-bprofit="${b.id}"]`);
    const oc = c.orders.find(o => o.b.id === b.id);
    if (p && oc) p.innerHTML = orderProfitChip(oc);
    for (const l of b.lines) {
      const e = document.querySelector(`[data-ext="${l.id}"]`); if (e) e.textContent = money(num(l.qty) * num(l.price));
      const f = document.querySelector(`[data-eff="${l.id}"]`); if (f) f.textContent = c.effPer[l.id] ? "+" + fmtPrice(c.effPer[l.id]) : "";
    }
  }
}

// ================= editing =================
function blocksOf(side) { return side === "demand" ? doc.demand : doc.sources; }
function findBlock(side, id) { return blocksOf(side).find(b => b.id === id); }
function findLine(side, bid, lid) { return (findBlock(side, bid) || { lines: [] }).lines.find(l => l.id === lid); }
function changed(full = false) { if (full) render(); else renderResults(); scheduleSave(); }
function setField(side, bid, lid, field, value, text = false) {
  const l = findLine(side, bid, lid); if (!l) return;
  l[field] = text ? value : (value === "" ? "" : num(value));
  changed(false);
}
function setCode(side, bid, lid, value) {
  const l = findLine(side, bid, lid); if (!l) return;
  const it = ITEMS.find(i => i.code.toLowerCase() === value.trim().toLowerCase());
  l.code = it ? it.code : value.trim();
  l.item_id = it ? it.id : null;
  if (it && !l.desc) l.desc = it.title;
  if (it) { ensureInsights([it.id]).then(() => { fillPrice(side, findBlock(side, bid), l); changed(true); }); }
  changed(true);
}
function pickCandidate(side, bid, lid, itemId) {
  const l = findLine(side, bid, lid), it = itemById(parseInt(itemId)); if (!l || !it) return;
  Object.assign(l, { item_id: it.id, code: it.code, desc: l.desc || it.title, candidates: null });
  ensureInsights([it.id]).then(() => { fillPrice(side, findBlock(side, bid), l); changed(true); });
}
function setBlock(side, bid, field, value, full = false) {
  const b = findBlock(side, bid); if (!b) return;
  b[field] = field === "multiplier" ? Math.max(1, parseInt(value) || 1) : value;
  if (field === "party_id" && side === "demand") ensureInsights(b.lines.map(l => l.item_id).filter(Boolean), b.party_id);
  changed(full);
}
function addLine(side, bid) { findBlock(side, bid).lines.push({ id: uid(), item_id: null, code: "", desc: "", qty: 1, price: "" }); changed(true); }
function removeLine(side, bid, lid) { const b = findBlock(side, bid); b.lines = b.lines.filter(l => l.id !== lid); changed(true); }
function removeBlock(side, bid) {
  if (!confirm("Remove this from the simulation?")) return;
  if (side === "demand") doc.demand = doc.demand.filter(b => b.id !== bid); else doc.sources = doc.sources.filter(b => b.id !== bid);
  changed(true);
}
function costList(bid) { return bid === "shared" ? doc.shared_costs : (findBlock("sources", bid).costs ||= []); }
function addCost(bid, type) {
  const [k, label, mode] = COST_TYPES.find(t => t[0] === type);
  costList(bid).push({ id: uid(), type: k, label, mode, amount: "", alloc: "value" }); changed(true);
}
function setCost(bid, cid, field, value, full = false) {
  const c = costList(bid).find(x => x.id === cid); if (!c) return;
  c[field] = field === "amount" ? (value === "" ? "" : num(value)) : value; changed(full);
}
function removeCost(bid, cid) {
  const list = costList(bid), i = list.findIndex(x => x.id === cid); if (i >= 0) list.splice(i, 1); changed(true);
}
function setEstimate(key, value) { if (value === "") delete doc.estimates[key]; else doc.estimates[key] = num(value); changed(false); }

// prices that fill themselves: our item on the sell side -> the suggested price; on the buy side -> the last cost
function fillPrice(side, b, l) {
  if (!l.item_id || (l.price !== "" && l.price != null && num(l.price) > 0)) return;
  const ins = INSIGHT[l.item_id]; if (!ins) return;
  const p = side === "demand" ? ins.suggested_price : ins.last_cost;
  if (p) l.price = Math.round(p * 100000) / 100000;
}
async function ensureInsights(ids, customerId = null) {
  const want = [...new Set(ids)].filter(id => id && (!INSIGHT[id] || customerId));
  if (!want.length) return;
  try { Object.assign(INSIGHT, await apiFetch("/api/simulations/insights", { method: "POST", body: JSON.stringify({ item_ids: want, customer_id: customerId }) })); }
  catch (e) { toast(e.message); }
}

// ================= adding demand / sources =================
const fromDocLine = (item_id, code, desc, qty, price) => ({ id: uid(), item_id: item_id || null, code: code || "", desc: desc || "", qty: num(qty) || 0, price: price ?? "" });
async function pickFrom(title, options, label) {
  // picking from the list adds it right away (the Add button does the same)
  const ask = askDialog({ title, body: `<select id="sim-pick" data-searchable><option value="">${label}</option>${options}</select>
      <p class="muted small" style="margin:6px 0 0;">Type to search, then pick one.</p>`,
    buttons: [{ label: "Add", value: "ok", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  const sel = document.querySelector(".ask-dialog #sim-pick");
  if (sel) sel.addEventListener("change", () => { if (sel.value) sel.closest(".ask-dialog").querySelector("button[data-i='0']").click(); });
  const { value, el } = await ask;
  return value ? el.querySelector("#sim-pick").value : null;
}
async function pasteDialog(title, side) {
  const list = side === "demand" ? CUSTOMERS : VENDORS;
  const { value, el } = await askDialog({ title, body: `<p class="muted small" style="margin-top:0;">Paste lines from an email, a quote or a spreadsheet (code, description, qty, price — any order).
      Items we have are matched; anything else is kept and marked <b>not in DB</b> for you to price.</p>
      <select id="sim-party"><option value="">${side === "demand" ? "Customer (optional)" : "Vendor (optional)"}</option>${list.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("")}</select>
      <textarea id="sim-text" rows="10" style="margin-top:8px;" placeholder="15420  5/8-11 x 2 A325 HDG   500   0.95"></textarea>`,
    buttons: [{ label: "Read Lines", value: "ok", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!value) return null;
  return { text: el.querySelector("#sim-text").value, party_id: parseInt(el.querySelector("#sim-party").value) || null };
}
async function pdfPick(accept = "application/pdf") {
  return new Promise(resolve => {
    const inp = Object.assign(document.createElement("input"), { type: "file", accept });
    inp.onchange = () => resolve(inp.files[0] || null);
    inp.click();
  });
}
async function addDemand(kind) {
  try {
    let b = { id: uid(), kind, label: "", party_id: null, multiplier: 1, lines: [] };
    if (kind === "order") {
      const orders = await apiFetch("/api/customer-orders/");
      const id = await pickFrom("Add a customer order", orders.filter(o => o.status !== "cancelled").sort((a, b) => b.id - a.id)
        .map(o => `<option value="${o.id}">${escapeHtml(o.code)} — ${escapeHtml(partyName(CUSTOMERS, o.customer_id))}${o.po_number ? ` — PO ${escapeHtml(o.po_number)}` : ""}</option>`).join(""), "Pick an order…");
      if (!id) return;
      const o = orders.find(x => x.id === parseInt(id));
      Object.assign(b, { label: o.po_number ? `PO ${o.po_number}` : o.code, ref_id: o.id, ref_code: o.code, ref_link: `customer-orders.html?id=${o.id}`, party_id: o.customer_id,
                         lines: o.lines.map(l => fromDocLine(l.item_id, (itemById(l.item_id) || {}).code, (itemById(l.item_id) || {}).title, l.quantity, l.unit_price)) });
    } else if (kind === "quote") {
      const quotes = await apiFetch("/api/quotes/");
      const id = await pickFrom("Add a quote", quotes.sort((a, b) => b.id - a.id).map(q => `<option value="${q.id}">${escapeHtml(q.code)} — ${escapeHtml(partyName(CUSTOMERS, q.customer_id))}</option>`).join(""), "Pick a quote…");
      if (!id) return;
      const q = await apiFetch(`/api/quotes/${id}`);
      Object.assign(b, { label: q.code, ref_id: q.id, ref_code: q.code, party_id: q.customer_id,
                         lines: (q.lines || []).map(l => fromDocLine(l.item_id, (itemById(l.item_id) || {}).code || l.item_code || "", l.description || (itemById(l.item_id) || {}).title, l.quantity, l.unit_price)) });
    } else if (kind === "paste") {
      const p = await pasteDialog("Paste customer lines", "demand"); if (!p || !p.text.trim()) return;
      const rows = await apiFetch("/api/simulations/parse", { method: "POST", body: JSON.stringify({ text: p.text, side: "demand", party_id: p.party_id }) });
      Object.assign(b, { label: "Pasted lines", party_id: p.party_id, lines: rows.map(r => ({ ...fromDocLine(r.item_id, r.code, r.description, r.qty, r.price), candidates: r.item_id ? null : r.candidates })) });
    } else if (kind === "pdf") {
      const f = await pdfPick(); if (!f) return;
      toast("Reading the PO with the local AI… (up to a minute)");
      const fd = new FormData(); fd.append("file", f, f.name);
      const r = await apiUpload("/api/ai-orders/extract", fd);
      Object.assign(b, { kind: "pdf", label: r.po_number ? `PO ${r.po_number}` : f.name, party_id: (r.customer || {}).customer_id || null,
                         lines: (r.lines || []).map(l => ({ ...fromDocLine(l.item_id, l.item_id ? (itemById(l.item_id) || {}).code : (l.item_code || l.customer_item_code), l.description, l.quantity, l.unit_price),
                                                           candidates: l.item_id ? null : (l.candidates || []).slice(0, 3).map(c => ({ item_id: c.item_id, code: (itemById(c.item_id) || {}).code || "", title: (itemById(c.item_id) || {}).title || "" })) })) });
    } else {
      b.label = "New order"; b.lines = [fromDocLine(null, "", "", 1, "")];
    }
    await ensureInsights(b.lines.map(l => l.item_id).filter(Boolean), b.party_id);
    b.lines.forEach(l => fillPrice("demand", b, l));
    doc.demand.push(b);
    changed(true);
  } catch (e) { toast(e.message); }
}
async function addSource(kind) {
  try {
    let b = { id: uid(), kind, label: "", party_id: null, lines: [], costs: [] };
    if (kind === "po") {
      const pos = await apiFetch("/api/purchase-orders/");
      const id = await pickFrom("Add a purchase order", pos.filter(p => p.status !== "cancelled").sort((a, b) => b.id - a.id)
        .map(p => `<option value="${p.id}">${escapeHtml(p.code)} — ${escapeHtml(partyName(VENDORS, p.vendor_id))} (${p.status.replace("_", " ")})</option>`).join(""), "Pick a PO…");
      if (!id) return;
      const p = pos.find(x => x.id === parseInt(id));
      Object.assign(b, { label: p.code, ref_id: p.id, ref_code: p.code, ref_link: `purchase-orders.html?id=${p.id}`, party_id: p.vendor_id,
                         lines: p.lines.map(l => fromDocLine(l.item_id, (itemById(l.item_id) || {}).code, l.vendor_description || (itemById(l.item_id) || {}).title, l.quantity, l.unit_cost)),
                         costs: (p.charges || []).map(ch => ({ id: uid(), type: "shipping", label: ch.description || ch.charge_type, mode: "total", amount: ch.amount, alloc: "value" })) });
    } else if (kind === "paste") {
      const p = await pasteDialog("Paste a vendor quote", "source"); if (!p || !p.text.trim()) return;
      const rows = await apiFetch("/api/simulations/parse", { method: "POST", body: JSON.stringify({ text: p.text, side: "source", party_id: p.party_id }) });
      Object.assign(b, { label: "Vendor quote", party_id: p.party_id, lines: rows.map(r => ({ ...fromDocLine(r.item_id, r.code, r.description, r.qty, r.price), candidates: r.item_id ? null : r.candidates })) });
    } else if (kind === "pdf") {
      const f = await pdfPick("application/pdf,image/*"); if (!f) return;
      toast("Reading the vendor quote with the local AI… (up to a minute)");
      const fd = new FormData(); fd.append("file", f, f.name); fd.append("kind", "vendor_order");
      const r = await apiUpload("/api/ai-docs/extract", fd);
      Object.assign(b, { label: r.document_number ? `Quote ${r.document_number}` : f.name, party_id: (r.vendor || {}).vendor_id || null,
                         lines: (r.lines || []).map(l => ({ ...fromDocLine(l.item_id, l.item_id ? (itemById(l.item_id) || {}).code : l.vendor_item_code, l.description, l.quantity, l.unit_price),
                                                           candidates: l.item_id ? null : (l.candidates || []).slice(0, 3).map(c => ({ item_id: c.item_id, code: (itemById(c.item_id) || {}).code || "", title: (itemById(c.item_id) || {}).title || "" })) })),
                         costs: r.shipping_handling ? [{ id: uid(), type: "shipping", label: "Shipping on the quote", mode: "total", amount: r.shipping_handling, alloc: "value" }] : [] });
    } else {
      b.label = "New source"; b.lines = [fromDocLine(null, "", "", 1, "")];
    }
    await ensureInsights(b.lines.map(l => l.item_id).filter(Boolean));
    b.lines.forEach(l => fillPrice("sources", b, l));
    doc.sources.push(b);
    changed(true);
  } catch (e) { toast(e.message); }
}

// ================= making real documents =================
async function makePo(bid) {
  const b = findBlock("sources", bid);
  if (!b.party_id) { toast("Pick the vendor on this source first"); return; }
  const lines = b.lines.filter(l => l.item_id && num(l.qty) > 0).map(l => ({ item_id: l.item_id, quantity: num(l.qty), price: num(l.price) }));
  const skipped = b.lines.filter(l => !l.item_id).length;
  if (!lines.length) { toast("No lines with our items to order"); return; }
  if (!confirm(`Create a draft PO to ${partyName(VENDORS, b.party_id)} with ${lines.length} line${lines.length === 1 ? "" : "s"}?${skipped ? `\n${skipped} line(s) not in our database are left out.` : ""}`)) return;
  try { const r = await apiFetch("/api/simulations/create-po", { method: "POST", body: JSON.stringify({ vendor_id: b.party_id, lines, notes: `Drafted from simulation "${sim.name}"` }) });
        toast(`Draft ${r.code} created`); b.ref_link = `purchase-orders.html?id=${r.id}`; b.ref_code = r.code; changed(true); }
  catch (e) { toast(e.message); }
}
async function makePoFromShort() {
  const c = calc(), short = c.rows.filter(r => r.toOrder > 0 && r.item_id);
  if (!short.length) { toast("Nothing short to order"); return; }
  const { value, el } = await askDialog({ title: "Draft a PO for what's short",
    body: `<p class="small">${short.map(r => `${escapeHtml(r.code)}: <b>${fmtQty(r.toOrder)}</b>`).join(" · ")}</p>
      <select id="sim-vendor"><option value="">Vendor…</option>${VENDORS.map(v => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join("")}</select>`,
    buttons: [{ label: "Create Draft PO", value: "ok", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!value) return;
  const vendor_id = parseInt(el.querySelector("#sim-vendor").value);
  if (!vendor_id) { toast("Pick the vendor"); return; }
  const lines = short.map(r => ({ item_id: r.item_id, quantity: Math.ceil(r.toOrder), price: r.vendor || (INSIGHT[r.item_id] || {}).last_cost || 0 }));
  try { const r = await apiFetch("/api/simulations/create-po", { method: "POST", body: JSON.stringify({ vendor_id, lines, notes: `Shortfall from simulation "${sim.name}"` }) });
        toast(`Draft ${r.code} created`); }
  catch (e) { toast(e.message); }
}
async function makeOrder(bid) {
  const b = findBlock("demand", bid);
  if (!b.party_id) { toast("Pick the customer on this order first"); return; }
  const missing = b.lines.filter(l => !l.item_id && num(l.qty) > 0);
  if (missing.length) { toast(`${missing.length} line(s) aren't in our database — link them to our items (type our item #) or add the items first`); return; }
  const { value, el } = await askDialog({ title: `Draft customer order for ${partyName(CUSTOMERS, b.party_id)}`,
    body: `<div class="lbl-grid"><div><label>Customer PO #</label><input id="sim-po"></div><div><label>Job #</label><input id="sim-job"></div></div>
      <p class="muted small">${b.lines.length} line${b.lines.length === 1 ? "" : "s"}${b.multiplier > 1 ? ` · the order is made once (× ${b.multiplier} is only for the simulation)` : ""}.</p>`,
    buttons: [{ label: "Create Draft Order", value: "ok", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!value) return;
  const lines = b.lines.filter(l => num(l.qty) > 0).map(l => ({ item_id: l.item_id, quantity: num(l.qty), price: num(l.price) }));
  try { const r = await apiFetch("/api/simulations/create-order", { method: "POST", body: JSON.stringify({ customer_id: b.party_id, po_number: el.querySelector("#sim-po").value, job_number: el.querySelector("#sim-job").value, lines }) });
        toast(`Draft ${r.code} created`); b.ref_link = `customer-orders.html?id=${r.id}`; b.ref_code = r.code; changed(true); }
  catch (e) { toast(e.message); }
}

// ================= price history =================
async function showInsight(itemId) {
  await ensureInsights([itemId]);
  const x = INSIGHT[itemId]; if (!x) return;
  const tbl = (rows, party, page) => rows.length ? `<table class="compact-table no-table-tools"><thead><tr><th>Date</th><th>${party}</th><th>Doc</th><th class="num">Qty</th><th class="num">Price</th></tr></thead><tbody>
    ${rows.map(h => `<tr><td>${fmtDate(h.date)}</td><td>${escapeHtml(h.party)}</td><td><a class="link" href="${page}?id=${h.doc_id}">${escapeHtml(h.doc)}</a></td><td class="num">${fmtQty(h.qty)}</td><td class="num">${fmtPrice(h.price)}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted small">None yet.</p>`;
  await askDialog({ title: `${x.code} — price history`, body: `<p class="muted small" style="margin-top:0;">${escapeHtml(x.title)} · on hand ${fmtQty(x.on_hand)} (available ${fmtQty(x.available)})${x.pack_size ? ` · pack ${x.pack_size}` : ""}</p>
      <h4 style="margin:8px 0 4px;">Sold to customers</h4>${tbl(x.sales, "Customer", "customer-orders.html")}
      <h4 style="margin:12px 0 4px;">Bought from vendors</h4>${tbl(x.purchases, "Vendor", "purchase-orders.html")}`,
    buttons: [{ label: "Close", value: null, cls: "secondary" }] });
}

// ================= saving / picking simulations =================
function scheduleSave() {
  setSaveState("Unsaved…");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 1200);
}
async function saveNow() {
  if (!sim) return;
  clearTimeout(saveTimer);
  try {
    const clean = JSON.parse(JSON.stringify(doc, (k, v) => k === "candidates" ? undefined : v));
    await apiFetch(`/api/simulations/${sim.id}`, { method: "PUT", body: JSON.stringify({ doc: clean }) });
    setSaveState(`Saved ${fmtTime(new Date())}`);
  } catch (e) { setSaveState("Not saved: " + e.message); }
}
function setSaveState(t) { saveState = t; const el = document.getElementById("sim-save"); if (el) el.textContent = t; }
function drawBar() {
  document.getElementById("sim-bar").innerHTML = `
    <h1 class="page-title">Simulate</h1>
    <select id="sim-pick-doc" style="width:auto; min-width:220px;" onchange="openSim(parseInt(this.value))">
      <option value="">${SIMS.length ? "Open a simulation…" : "No simulations yet"}</option>
      ${SIMS.map(s => `<option value="${s.id}" ${sim && s.id === sim.id ? "selected" : ""}>${escapeHtml(s.name)}${s.summary && s.summary.profit != null ? ` · ${fmtMoney(s.summary.profit)}` : ""}</option>`).join("")}</select>
    <button onclick="newSim()">+ New</button>
    ${sim ? `<input id="sim-name" value="${escapeHtml(sim.name)}" style="width:220px;" onchange="renameSim(this.value)" title="Name">
      <button class="secondary" onclick="openSheet()" data-icon="layers" title="Everything as an editable spreadsheet, with an AI assistant">Spreadsheet</button>
      <button class="secondary" onclick="copySim()" title="A copy to try something different">Copy</button>
      <button class="secondary" onclick="deleteSim()">Delete</button>
      <span class="muted small" id="sim-save">${escapeHtml(saveState)}</span>` : ""}`;
}
async function loadList() { SIMS = await apiFetch("/api/simulations/"); drawBar(); }
async function openSim(id) {
  if (!id) return;
  if (saveTimer) await saveNow();
  sim = await apiFetch(`/api/simulations/${id}`);
  doc = Object.assign(blankDoc(), sim.doc || {});
  history.replaceState(null, "", `simulate.html?id=${id}`);
  await ensureInsights([...doc.demand, ...doc.sources].flatMap(b => b.lines.map(l => l.item_id)).filter(Boolean));
  setSaveState(`Saved ${fmtWhen(sim.updated_at)}`);
  drawBar(); render();
}
async function newSim() {
  const name = prompt("Name the simulation", `Simulation ${new Date().toLocaleDateString()}`);
  if (name === null) return;
  const s = await apiFetch("/api/simulations/", { method: "POST", body: JSON.stringify({ name, doc: blankDoc() }) });
  await loadList(); openSim(s.id);
}
async function renameSim(name) { await apiFetch(`/api/simulations/${sim.id}`, { method: "PUT", body: JSON.stringify({ name }) }); sim.name = name; loadList(); }
async function copySim() { await saveNow(); const c = await apiFetch(`/api/simulations/${sim.id}/copy`, { method: "POST" }); await loadList(); openSim(c.id); toast("Copied — you're now on the copy"); }
async function deleteSim() {
  if (!confirm(`Delete "${sim.name}"?`)) return;
  await apiFetch(`/api/simulations/${sim.id}`, { method: "DELETE" });
  sim = doc = null; history.replaceState(null, "", "simulate.html"); await loadList(); render();
}

(async function init() {
  [ITEMS, VENDORS, CUSTOMERS] = await Promise.all([apiFetch("/api/stock-items/"), apiFetch("/api/vendors/"), apiFetch("/api/customers/")]);
  await loadList();
  const id = parseInt(new URLSearchParams(location.search).get("id")) || (SIMS[0] || {}).id;
  if (id) await openSim(id); else render();
  window.addEventListener("beforeunload", () => { if (saveTimer) saveNow(); });
})().catch(e => { document.getElementById("sim-main").innerHTML = `<div class="card error">${escapeHtml(e.message)}</div>`; });

// ================= spreadsheet (everything as one editable grid, with an AI helper) =================
// One row per line. Side + Order / Source say where it lives; editing them moves the line. Extra columns are yours
// (notes, a target price, a competitor's price...) and are kept on each line. "Apply" writes the sheet back to the simulation.
const BASE_COLS = ["Side", "Order / Source", "Item #", "Description", "Qty", "Price"];
let S = null, S_UNDO = [];
function sheetFromDoc() {
  const cols = [...BASE_COLS, ...(doc.sheet_cols || [])];
  const rows = [];
  for (const [side, blocks] of [["Sell", doc.demand], ["Buy", doc.sources]])
    for (const b of blocks) for (const l of b.lines)
      rows.push({ _id: l.id, "Side": side, "Order / Source": b.label || "", "Item #": l.code || "", "Description": l.desc || "",
                  "Qty": l.qty ?? "", "Price": l.price ?? "", ...Object.fromEntries((doc.sheet_cols || []).map(c => [c, (l.extra || {})[c] ?? ""])) });
  return { cols, rows };
}
function openSheet() {
  S = sheetFromDoc(); S_UNDO = [];
  const back = document.createElement("div");
  back.className = "modal-backdrop"; back.id = "sim-sheet";
  back.innerHTML = `<div class="modal sim-sheet-modal" role="dialog" aria-modal="true">
    <div class="sim-sheet-bar"><h3 style="margin:0;">Spreadsheet</h3>
      <button class="secondary small-btn" onclick="sheetAddRow()">+ Row</button>
      <button class="secondary small-btn" onclick="sheetAddCol()">+ Column</button>
      <button class="secondary small-btn" onclick="sheetUndo()" id="sim-undo" disabled>Undo</button>
      <button class="secondary small-btn" onclick="sheetCsv()">Download CSV</button>
      <span class="spacer"></span>
      <button onclick="sheetApply()">Apply To Simulation</button><button class="secondary" onclick="sheetClose()">Cancel</button></div>
    <div class="sim-ai"><span class="sim-ai-badge">AI</span>
      <input id="sim-ai-q" placeholder="Ask the sheet: add 12% to every Buy price, add a column Margin %, remove rows with qty 0..."
        onkeydown="if (event.key === 'Enter') sheetAi('local')">
      <button class="small-btn" onclick="sheetAi('local')" title="The AI running on our own server: nothing leaves the building">Ask Local AI</button>
      ${AuthGuard.can("ai") ? `<button class="secondary small-btn" onclick="sheetAi('claude')" title="Smarter, uses Claude in the cloud (customer and vendor names are hidden first)">Ask Claude</button>` : ""}</div>
    <div id="sim-ai-note" class="muted small"></div>
    <div class="sim-sheet-wrap"><table class="sim-grid no-table-tools" id="sim-grid"></table></div>
    <p class="muted small" style="margin:6px 0 0;">Click a cell to type. Paste a block straight from Excel. Side is Sell or Buy; a new Order / Source name makes a new block.</p></div>`;
  document.body.appendChild(back);
  drawSheet();
}
function drawSheet() {
  const base = new Set(BASE_COLS);
  const known = new Set(ITEMS.map(i => i.code.toLowerCase()));
  document.getElementById("sim-grid").innerHTML = `<thead><tr><th class="sim-rn">#</th>${S.cols.map((c, ci) => `<th>${escapeHtml(c)}${base.has(c) ? "" :
      ` <a class="sim-x" title="Remove column" onclick="sheetDelCol(${ci})">×</a>`}</th>`).join("")}<th></th></tr></thead>
    <tbody>${S.rows.map((r, ri) => `<tr class="${r["Item #"] && !known.has(String(r["Item #"]).toLowerCase()) ? "sim-unknown" : ""}"><td class="sim-rn">${ri + 1}</td>
      ${S.cols.map((c, ci) => `<td contenteditable="plaintext-only" data-r="${ri}" data-c="${ci}" class="${["Qty", "Price"].includes(c) ? "num" : ""}">${escapeHtml(r[c] ?? "")}</td>`).join("")}
      <td><a class="sim-x" title="Remove row" onclick="sheetDelRow(${ri})">×</a></td></tr>`).join("")}</tbody>`;
  const grid = document.getElementById("sim-grid");
  grid.oninput = e => { const td = e.target.closest("td[data-r]"); if (td) S.rows[+td.dataset.r][S.cols[+td.dataset.c]] = td.textContent; };
  grid.onfocusin = e => { const td = e.target.closest("td[data-r]"); if (td && !td._snap) { td._snap = 1; snap(); } };
  grid.onpaste = e => {
    const td = e.target.closest("td[data-r]"), text = (e.clipboardData || window.clipboardData).getData("text");
    if (!td || !/[\t\n]/.test(text.trim())) return;
    e.preventDefault(); snap();
    const r0 = +td.dataset.r, c0 = +td.dataset.c;
    text.replace(/\r/g, "").replace(/\n$/, "").split("\n").forEach((line, i) => {
      while (S.rows.length <= r0 + i) S.rows.push(newSheetRow());
      line.split("\t").forEach((v, j) => { if (c0 + j < S.cols.length) S.rows[r0 + i][S.cols[c0 + j]] = v.trim(); });
    });
    drawSheet();
  };
  grid.onkeydown = e => {
    const td = e.target.closest("td[data-r]"); if (!td) return;
    const move = { Enter: 1, ArrowDown: 1, ArrowUp: -1 }[e.key];
    if (move && !e.shiftKey) {
      e.preventDefault();
      const next = grid.querySelector(`td[data-r="${+td.dataset.r + move}"][data-c="${td.dataset.c}"]`);
      if (next) next.focus();
    }
  };
  document.getElementById("sim-undo").disabled = !S_UNDO.length;
}
function snap() { S_UNDO.push(JSON.stringify(S)); if (S_UNDO.length > 30) S_UNDO.shift(); const u = document.getElementById("sim-undo"); if (u) u.disabled = false; }
function sheetUndo() { if (!S_UNDO.length) return; S = JSON.parse(S_UNDO.pop()); drawSheet(); }
function newSheetRow() {
  const last = S.rows[S.rows.length - 1] || {};
  return { _id: null, ...Object.fromEntries(S.cols.map(c => [c, ""])), "Side": last["Side"] || "Sell", "Order / Source": last["Order / Source"] || "From the sheet" };
}
function sheetAddRow() { snap(); S.rows.push(newSheetRow()); drawSheet(); const cells = document.querySelectorAll(`#sim-grid td[data-r="${S.rows.length - 1}"]`); if (cells[2]) cells[2].focus(); }
function sheetDelRow(i) { snap(); S.rows.splice(i, 1); drawSheet(); }
function sheetAddCol() {
  const name = (prompt("Column name (e.g. Target price, Competitor, Notes)") || "").trim();
  if (!name) return;
  if (S.cols.includes(name)) { toast("There's already a column with that name"); return; }
  snap(); S.cols.push(name); drawSheet();
}
function sheetDelCol(ci) { snap(); const c = S.cols.splice(ci, 1)[0]; S.rows.forEach(r => delete r[c]); drawSheet(); }
function sheetClose() { const m = document.getElementById("sim-sheet"); if (m) m.remove(); S = null; }
function sheetCsv() {
  const q = v => /[",\n]/.test(String(v ?? "")) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? "");
  const csv = [S.cols.map(q).join(","), ...S.rows.map(r => S.cols.map(c => q(r[c])).join(","))].join("\r\n");
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: `${(sim.name || "simulation").replace(/[^\w -]+/g, "")}.csv` });
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
async function sheetAi(engine) {
  const inp = document.getElementById("sim-ai-q"), q = inp.value.trim(), note = document.getElementById("sim-ai-note");
  if (!q) { inp.focus(); return; }
  note.textContent = engine === "claude" ? "Asking Claude…" : "The local AI is working on it… (can take a minute)";
  try {
    const r = await apiFetch("/api/simulations/ai-sheet", { method: "POST", body: JSON.stringify({ instruction: q, columns: S.cols, rows: S.rows, engine }) });
    snap();
    const cols = [...BASE_COLS.filter(c => !r.columns.includes(c)), ...r.columns];  // the base columns can't be dropped
    S = { cols, rows: r.rows.map(x => ({ ...Object.fromEntries(cols.map(c => [c, ""])), ...x, _id: x._id || null })) };
    drawSheet();
    note.innerHTML = `${escapeHtml(r.note || "Done.")} <span class="muted">(${r.engine === "claude" ? "Claude" : "local AI"} · Undo puts it back)</span>`;
    inp.value = "";
  } catch (e) { note.textContent = e.message; }
}
function sheetApply() {
  const lineById = {};
  for (const b of [...doc.demand, ...doc.sources]) for (const l of b.lines) lineById[l.id] = l;
  for (const b of [...doc.demand, ...doc.sources]) b.lines = [];
  const custom = S.cols.filter(c => !BASE_COLS.includes(c));
  for (const r of S.rows) {
    const side = /^\s*(buy|source|vendor|po)/i.test(r["Side"] || "") ? "sources" : "demand";
    const label = String(r["Order / Source"] || "").trim() || (side === "demand" ? "From the sheet" : "Source from the sheet");
    let b = blocksOf(side).find(x => (x.label || "").trim() === label);
    if (!b) { b = { id: uid(), kind: "sheet", label, party_id: null, multiplier: 1, lines: [], costs: [] }; blocksOf(side).push(b); }
    const code = String(r["Item #"] || "").trim();
    const it = code ? ITEMS.find(i => i.code.toLowerCase() === code.toLowerCase()) : null;
    const l = lineById[r._id] || { id: uid() };
    Object.assign(l, { code: it ? it.code : code, item_id: it ? it.id : null, desc: String(r["Description"] ?? ""),
                       qty: r["Qty"] === "" ? "" : num(r["Qty"]), price: r["Price"] === "" ? "" : num(r["Price"]),
                       extra: Object.fromEntries(custom.map(c => [c, r[c] ?? ""])) });
    b.lines.push(l);
  }
  // blocks the sheet emptied go away, unless they carry something the sheet can't show (a link, extra costs)
  const keep = b => b.lines.length || b.ref_link || (b.costs || []).length;
  doc.demand = doc.demand.filter(keep); doc.sources = doc.sources.filter(keep);
  doc.sheet_cols = custom;
  sheetClose();
  ensureInsights([...doc.demand, ...doc.sources].flatMap(b => b.lines.map(l => l.item_id)).filter(Boolean)).then(() => changed(true));
  toast("Sheet applied");
}
