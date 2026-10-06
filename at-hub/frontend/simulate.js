// Simulate (simulate.html): what an order -- or several, or one repeated -- would make, before committing.
// Left page: demand (customer orders, quotes, pasted lines; each block repeatable "x 8").
// Right page: sources (POs, vendor quotes, pasted lines) with their extra costs, plus costs shared by every source.
// Below: the item-by-item comparison, profit per order, and what to order. The doc autosaves (app/routes/simulations.py).
AuthGuard.requirePerm("simulate");
document.getElementById("sidebar").innerHTML = renderSidebar("simulate.html");

let SIMS = [], sim = null, doc = null, ITEMS = [], VENDORS = [], CUSTOMERS = [], INSIGHT = {}, saveTimer = null, saveState = "";
let GEN = { generics: {}, serves: {} }, genKey = "";  // generic bulk nuts in this simulation (POST /api/simulations/generic)
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
  return { demand: [], sources: [], shared_costs: [], estimates: {}, generic_off: {}, stock_off: {}, est_stock: {}, tracker: {}, use_stock: false, sheet_cols: [], summary: {} };
}

// ================= the numbers =================
// How the "to order" quantities are worked out (shown on the Order Tracker, shared with the comparison):
//   stock: none | system (AT-HUB on hand) | estimate (my on hand) | mixed (my on hand where entered, else system)
//   incoming: count open POs not already in this simulation; buffer: % extra; pack: round up to the pack size
// stock basis: [key, name, what it does]
const STOCK_MODES = [["none", "Don't count stock", "Order the full need, as if the shelf were empty."],
                     ["system", "AT-HUB on hand", "Use the on-hand quantity AT-HUB shows. Untick any item whose count you don't trust."],
                     ["estimate", "Only my counts", "Use only the numbers you type in My on hand. Items left blank count as zero."],
                     ["mixed", "My counts, else AT-HUB", "Use your number where you typed one in My on hand, and AT-HUB's on hand for everything else."]];
function plan() {
  doc.plan ||= { stock: doc.use_stock ? "system" : "none", incoming: false, buffer: 0, pack: false };
  doc.est_stock ||= {}; doc.tracker ||= {}; doc.stock_off ||= {}; doc.generic_off ||= {};
  return doc.plan;
}
const isPoBlock = b => /purchase-orders\.html/.test(b.ref_link || "");
function calc() {
  const d = doc, items = {}, P = plan();
  const it = key => (items[key] ??= { key, item_id: null, code: "", desc: "", need: 0, revenue: 0, supply: 0, costQty: 0, received: 0, vendorValue: 0, effValue: 0,
                                      parts: [], demandBlocks: new Set(), sourceBlocks: new Set() });
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
    const key = keyOf(l), counted = P.stock !== "none" && !(P.stock !== "estimate" && d.stock_off[key]);
    const rec = counted && isPoBlock(b) ? Math.min(q, num(l.received)) : 0, qs = q - rec;
    x.supply += qs; x.received += rec; x.costQty += q;
    x.vendorValue += q * num(l.price); x.effValue += q * (num(l.price) + (effPer[l.id] || 0)); x.sourceBlocks.add(b.id);
    if (qs > 0) x.parts.push({ b, l, q: qs, price: num(l.price), onPo: isPoBlock(b) });
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
  // stock we count for an item: on-hand counts can be wrong while stock is being cleaned up, so it's a choice --
  // none, the system's (minus items ticked off), my own estimate, or my estimate where I gave one
  const myStock = r => { const v = d.est_stock[r.key]; return v !== undefined && v !== "" ? num(v) : null; };
  const stockOf = r => {
    const sys = d.stock_off[r.key] ? 0 : r.onHand, mine = myStock(r);
    return P.stock === "system" ? sys : P.stock === "estimate" ? (mine ?? 0) : P.stock === "mixed" ? (mine ?? sys) : 0;
  };
  const simPos = new Set(d.sources.filter(isPoBlock).map(b => b.ref_id));
  const incomingOf = r => P.incoming && r.item_id ? ((INSIGHT[r.item_id] || {}).incoming || []).filter(x => !simPos.has(x.po_id)).reduce((s, x) => s + x.qty, 0) : 0;
  const estOf = key => { const e = d.estimates[key]; return e !== undefined && e !== "" ? num(e) : null; };
  const base = Object.values(items).map(x => {
    const ins = x.item_id ? INSIGHT[x.item_id] : null;
    const vendor = x.costQty ? x.vendorValue / x.costQty : null, eff = x.costQty ? x.effValue / x.costQty : null;
    return { ...x, vendor, eff, ins, lastBuy: ins ? ins.last_buy || null : null, onHand: ins ? Math.max(0, ins.available || 0) : 0,
             notInDb: !x.item_id, generic: x.item_id ? (GEN.generics[x.item_id] || null) : null, via: null, viaQty: 0, servedBy: [], rolled: 0 };
  });
  base.forEach(r => { r.myStock = myStock(r); r.stock = stockOf(r); r.incoming = incomingOf(r); });
  const byItem = Object.fromEntries(base.filter(r => r.item_id).map(r => [r.item_id, r]));
  // generic bulk nuts (58-NUT): a specific nut (15420-NUT) with no source of its own costs what the generic costs,
  // and what it still needs is bought as the generic -- it rolls up into the generic's "to order"
  for (const r of base) {
    if (!r.need || !r.item_id || r.generic || d.generic_off[r.key]) continue;
    const opts = (GEN.serves[r.item_id] || []).filter(o => byItem[o.generic_id] || GEN.generics[o.generic_id]);
    if (!opts.length) continue;
    const o = opts.find(o => byItem[o.generic_id] && byItem[o.generic_id].supply) || opts[0];
    const g = byItem[o.generic_id] || null;
    const short = Math.max(0, r.need - r.supply - r.stock - r.incoming);
    r.via = { id: o.generic_id, code: GEN.generics[o.generic_id].code, match: o.match, g };
    r.viaQty = short;
    if (g) { g.servedBy.push(r); g.rolled += short; }
  }
  // a cost with no vendor price: my estimate, else the last purchase price (marked as such), else nothing
  const costFor = r => {
    if (r.eff != null) return { cost: r.eff, src: "vendor" };
    const e = estOf(r.key);
    if (e != null) return { cost: e, src: "manual" };
    if (r.lastBuy && r.lastBuy.price) return { cost: Math.round(r.lastBuy.price * 100000) / 100000, src: "history" };
    return { cost: null, src: null };
  };
  const rows = base.map(x => {
    let { cost, src } = costFor(x), viaCost = null;
    if (x.via) {
      const g = x.via.g;
      const gc = g ? costFor(g) : { cost: (INSIGHT[x.via.id] || {}).last_cost || null, src: "history" };
      viaCost = gc.cost;
      // own supply first at its cost, the rest at the generic's
      const own = Math.min(x.supply, x.need);
      if (viaCost != null) { cost = x.need ? ((x.eff != null ? x.eff * own : 0) + viaCost * (x.need - own)) / x.need : viaCost; src = "generic"; }
    }
    const custPrice = x.need ? x.revenue / x.need : null;
    // what we still have to get: the need (+ what generic nuts stand in for), less stock and incoming, plus the buffer
    const net = x.via ? 0 : Math.max(0, Math.ceil(Math.max(0, x.need + x.rolled - x.stock - x.incoming) * (1 + num(P.buffer) / 100) - 1e-9));
    return { ...x, cost, costSrc: src, viaCost, extras: x.eff != null && x.vendor != null ? x.eff - x.vendor : null,
             estimated: src === "manual" || src === "history", custPrice, profitUnit: custPrice != null && cost != null ? custPrice - cost : null,
             profit: cost != null && x.need ? x.revenue - x.need * cost : null, net, toOrder: Math.max(0, net - x.supply) };
  }).sort((a, b) => (b.need > 0 || b.servedBy.length > 0) - (a.need > 0 || a.servedBy.length > 0) || String(a.code).localeCompare(String(b.code)));
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
  const track = trackerLines(rows, P);
  const withCost = rows.filter(r => r.need && r.cost != null);
  const revenue = rows.reduce((s, r) => s + r.revenue, 0), cost = withCost.reduce((s, r) => s + r.need * r.cost, 0);
  const profit = withCost.reduce((s, r) => s + r.revenue - r.need * r.cost, 0);
  const costedRev = withCost.reduce((s, r) => s + r.revenue, 0);
  const summary = { revenue, cost, profit, margin: costedRev ? profit / costedRev * 100 : null, items: rows.filter(r => r.need).length,
                    missing: rows.filter(r => r.need && r.cost == null).length, notInDb: rows.filter(r => r.notInDb).length,
                    estimated: rows.filter(r => r.need && r.estimated).length,
                    toOrder: track.filter(t => t.status === "todo").length };
  return { rows, orders, summary, effPer, track };
}
// The order tracker's lines: per item, what we still have to get (net), covered first by POs already in the
// simulation ("On PO"), then by vendor quotes in it, then the rest from a vendor you pick (default: whoever we
// last bought it from). Your choices (vendor, qty, status, reference) are kept per line in doc.tracker.
function trackerLines(rows, P) {
  const out = [];
  for (const r of rows) {
    if (r.via || r.net <= 0) continue;
    let left = r.net;
    const add = (key, part, qty, price, vendorDefault, fixed) => {
      const T = doc.tracker[key] || {};
      const pack = P.pack && r.ins && r.ins.pack_size > 1 ? r.ins.pack_size : 0;
      const suggested = fixed ? qty : pack ? Math.ceil(qty / pack - 1e-9) * pack : qty;
      const q = T.qty !== undefined && T.qty !== "" && !fixed ? num(T.qty) : suggested;
      const p = T.price !== undefined && T.price !== "" && !fixed ? num(T.price) : price == null ? null : Math.round(price * 100000) / 100000;
      out.push({ key, r, part, need: qty, suggested, qty: q, price: p, value: q * (p || 0), pack, fixed: !!fixed,
                 vendor_id: fixed ? part.b.party_id : (T.vendor_id !== undefined ? T.vendor_id : vendorDefault),
                 vendorHint: !fixed && T.vendor_id === undefined && vendorDefault ? (part ? "from the quote" : "last bought from") : "",
                 status: fixed ? "onpo" : (T.status || "todo"), ref: fixed ? part.b.ref_code || part.b.label : (T.ref || ""),
                 po_id: fixed ? part.b.ref_id : T.po_id, po_code: fixed ? part.b.ref_code : T.po_code, flag: fixed ? "" : T.flag || "" });
    };
    for (const part of [...r.parts].sort((a, b) => b.onPo - a.onPo)) {
      if (left <= 0) break;
      const take = Math.min(part.q, left);
      add(`${r.key}|${part.b.id}`, part, take, part.price, part.b.party_id || null, part.onPo);
      left -= take;
    }
    if (left > 0) add(`${r.key}|open`, null, left, r.vendor ?? r.cost ?? null, (r.lastBuy && r.lastBuy.vendor_id) || null, false);
  }
  return out;
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
// The comparison table is built once and kept: each change redraws only its rows, so the header's sort and
// ▾ filters (shared table tools) and the quick chips stay as you set them while you edit the numbers.
let CMP = null, CMP_CHIP = "";
const CMP_CHIPS = [["", "All"], ["nocost", "Needs a cost"], ["order", "To order"], ["loss", "Losing money"],
                   ["generic", "From generic nuts"], ["nodb", "Not in DB"]];
const chipTest = {
  need: r => r.need > 0, nocost: r => r.need > 0 && r.cost == null, order: r => r.toOrder > 0 || (r.via && r.viaQty > 0),
  loss: r => r.profit != null && r.profit < 0, generic: r => r.generic || r.via, nodb: r => r.notInDb };
function drawCompare(c) {
  const host = document.getElementById("sim-compare");
  if (!host) return;
  // only what we deliver: items on the customer side. Source-only items (other PO lines, the generic nuts
  // themselves) stay out of here -- what we buy for them shows in the Order Tracker
  const rows = c.rows.filter(r => r.need > 0);
  if (!rows.length) { host.innerHTML = `<p class="muted">Add customer demand on the left and sources on the right — the comparison appears here.</p>`; CMP = null; return; }
  if (!CMP) {
    const wrap = document.createElement("div");
    wrap.innerHTML = `<div class="sim-chips-row" id="sim-cmp-chips"></div><div class="sim-cmp-scroll">${compareHead()}<tbody></tbody></table></div>`;
    CMP = wrap;
  }
  if (CMP.parentNode !== host) host.replaceChildren(CMP);
  CMP.classList.toggle("stock-ticks", ["system", "mixed"].includes(plan().stock));
  const shown = CMP_CHIP ? rows.filter(chipTest[CMP_CHIP]) : rows;
  CMP.querySelector("#sim-cmp-chips").innerHTML = CMP_CHIPS.map(([k, l]) => {
    const n = k ? rows.filter(chipTest[k]).length : rows.length;
    return `<a class="pack-chip ${CMP_CHIP === k ? "on" : ""}" onclick="CMP_CHIP = '${k}'; drawCompare(calc())">${l} <b>${n}</b></a>`;
  }).join("");
  const tb = CMP.querySelector("tbody");
  if (tb.contains(document.activeElement)) return;  // typing an estimate: don't pull the box away
  tb.innerHTML = shown.length ? compareRows(shown) : `<tr><td colspan="12" class="muted">Nothing here.</td></tr>`;
}
function compareHead() {
  return `<table class="compact-table sim-compare"><thead><tr><th>Item</th><th class="num sum">Need</th><th class="num sum">Sourced</th>
      <th class="num" data-label="On hand" title="Available stock in AT-HUB (on hand minus booked)">On hand<span class="sim-allnone"><a onclick="setAllStock(true)" title="Count every item's AT-HUB stock">all</a> · <a onclick="setAllStock(false)" title="Count no item's AT-HUB stock">none</a></span></th>
      <th class="num" title="What you think is really on the shelf -- used when the stock basis is My on hand">My on hand</th><th class="num sum">To order</th><th class="num">Customer price</th>
      <th class="num" title="The vendor's price, and below it the extra costs per unit (tariff, freight...)">Vendor price</th><th class="num" title="Vendor price + extras; with no vendor price: your estimate (red) or the last purchase price (blue)">Effective cost</th>
      <th class="num">Profit / unit</th><th class="num sum">Profit</th><th data-nosort></th></tr></thead>`;
}
function compareRows(rows) {
  return `${rows.map(r => `<tr class="${r.notInDb ? "sim-unknown" : ""}">
      <td><b>${escapeHtml(r.code || "")}</b> <span class="muted small">${escapeHtml(r.desc || (r.item_id && itemById(r.item_id) ? itemById(r.item_id).title : ""))}</span>
        ${r.notInDb ? `<span class="sim-tag">not in DB</span>` : ""}${genNote(r)}</td>
      <td class="num">${fmtQty(r.need)}${r.rolled ? `<div class="small sim-gen-txt" title="Specific nuts on the left that this generic nut stands in for">+ ${fmtQty(r.rolled)} for nuts</div>` : ""}</td>
      <td class="num">${r.supply ? fmtQty(r.supply) : "—"}${r.received ? `<div class="small muted" title="Already received: it's in stock, so it's counted there, not twice">+ ${fmtQty(r.received)} received</div>` : ""}${r.supply && r.supply < r.need + (r.rolled || 0) && !r.via ? `<div class="sim-warn small">short ${fmtQty(r.need + (r.rolled || 0) - r.supply)}</div>` : ""}</td>
      <td class="num">${r.item_id ? onHandCell(r) : "—"}</td>
      <td class="num">${r.via ? "" : `<input type="number" class="sim-num sim-mystock" min="0" step="1" placeholder="—" value="${r.myStock ?? ""}"
          title="What you think you really have" oninput="setMyStock('${escapeHtml(r.key)}', this.value)" onchange="changed(false)">`}</td>
      <td class="num">${r.toOrder ? `<b>${fmtQty(r.toOrder)}</b>` : r.via && r.viaQty ? `<span class="small sim-gen-txt">as ${escapeHtml(r.via.code)}</span>` : "—"}</td>
      <td class="num">${price(r.custPrice)}</td><td class="num">${price(r.vendor)}${r.extras ? `<div class="small muted" title="Extra costs per unit">+${fmtPrice(r.extras)}</div>` : ""}</td>
      <td class="num">${r.via && r.cost != null ? `<b>${fmtPrice(r.cost)}</b><div class="small sim-gen-txt">${escapeHtml(r.via.code)} cost</div>` : r.eff != null ? `<b>${fmtPrice(r.eff)}</b>` : estimateCell(r)}</td>
      <td class="num ${r.profitUnit != null ? (r.profitUnit >= 0 ? "pos" : "neg") : ""}">${r.profitUnit != null ? fmtPrice(r.profitUnit) : "—"}
        ${r.profitUnit != null && r.custPrice ? `<div class="small muted">${(r.profitUnit / r.custPrice * 100).toFixed(1)}%</div>` : ""}</td>
      <td class="num ${r.profit != null ? (r.profit >= 0 ? "pos" : "neg") : ""}">${r.profit != null ? money(r.profit) : `<span class="sim-warn small">${r.need ? "needs a cost" : ""}</span>`}</td>
      <td>${r.item_id ? `<a class="link small" onclick="showInsight(${r.item_id})" title="Who bought and sold it, at what">History</a>` : ""}</td></tr>`).join("")}
    `;
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
    <div id="sim-alert"></div>
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
        ${stockSelect()}
        ${["system", "mixed"].includes(plan().stock) && Object.keys(doc.stock_off).length ? `<span class="muted small">${Object.keys(doc.stock_off).length} item${Object.keys(doc.stock_off).length === 1 ? "" : "s"} ignored ·
          <a class="link" onclick="doc.stock_off = {}; changed(true)">count all</a></span>` : ""}
        <span class="spacer"></span><button class="secondary small-btn" onclick="document.getElementById('sim-track-card').scrollIntoView({ behavior: 'smooth' })">Order Tracker ↓</button></div>
      <div id="sim-compare"></div></div>
    <div class="card"><h3 style="margin-top:0;">Profit Per Order</h3><div id="sim-orders">${ordersTable(c) || `<p class="muted small">Add demand to see each order's profit.</p>`}</div></div>
    <div class="card" id="sim-track-card"><div class="sim-track-head"><h3 style="margin:0;">Order Tracker</h3>
        <span class="muted small">what we still have to buy, who from, and whether it's ordered</span></div>
      <div class="sim-plan" id="sim-plan"></div>
      <div id="sim-track"></div></div>`;
  drawCompare(c);
  drawTracker(c);
  drawAlert();
  doc.summary = c.summary;
}
// numbers changed: redraw only the results (keeps the cursor where you're typing)
function renderResults() {
  const c = calc();
  doc.summary = c.summary;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
  set("sim-tiles", tiles(c.summary));
  set("sim-orders", ordersTable(c));
  drawCompare(c);
  drawTracker(c);
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
function changed(full = false) {
  if (full) { render(); refreshGeneric().then(fetched => { if (fetched) render(); }); } else renderResults();
  scheduleSave();
}
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
async function refreshGeneric(extra = []) {
  const ids = [...new Set([...doc.demand, ...doc.sources].flatMap(b => b.lines.map(l => l.item_id)).concat(extra).filter(Boolean))].sort((a, b) => a - b);
  const key = ids.join(",");
  if (key === genKey) return false;
  genKey = key;
  try { GEN = ids.length ? await apiFetch("/api/simulations/generic", { method: "POST", body: JSON.stringify({ item_ids: ids }) }) : { generics: {}, serves: {} }; }
  catch (e) { GEN = { generics: {}, serves: {} }; }
  return true;
}
// generic nuts: the badge on 58-NUT ("covers 15420-NUT ...") and the link on a specific nut ("from 58-NUT")
function genNote(r) {
  if (r.generic) {
    const kids = r.servedBy.map(k => k.code);
    return `<div class="small"><span class="sim-gen" title="${escapeHtml(r.generic.why)}">generic</span>
      ${kids.length ? `<span class="sim-gen-txt">stands in for ${escapeHtml(kids.slice(0, 6).join(", "))}${kids.length > 6 ? ` +${kids.length - 6}` : ""}</span>` : `<span class="muted">no matching nuts on the left</span>`}</div>`;
  }
  if (r.via) return `<div class="small"><span class="sim-gen-txt">from ${escapeHtml(r.via.code)}${r.via.match === "check" ? ` <span class="sim-warn" title="Size, thread and grade match; the finish or heavy isn't stated on one of them">(check finish)</span>` : ""}</span>
      · <a class="link" onclick="setGenericOff('${escapeHtml(r.key)}', true)" title="Cost and order this nut on its own">don't use</a></div>`;
  if (doc.generic_off[r.key] && GEN.serves[r.item_id]) return `<div class="small muted">not using generic stock · <a class="link" onclick="setGenericOff('${escapeHtml(r.key)}', false)">use ${escapeHtml((GEN.generics[GEN.serves[r.item_id][0].generic_id] || {}).code || "generic")}</a></div>`;
  return "";
}
// On hand column: with stock counted, each item has its own tick -- untick to ignore a count you don't trust
function onHandCell(r) {
  if (!["system", "mixed"].includes(plan().stock)) return `<span class="muted" title="Not counted: Stock to count is set to ${escapeHtml(STOCK_MODES.find(m => m[0] === plan().stock)[1])}">${fmtQty(r.onHand)}</span>`;
  const on = !(doc.stock_off || {})[r.key];
  return `<label class="sim-stock ${on ? "" : "off"}" title="${on ? "Counted -- untick if this count is wrong" : "Ignored -- tick to count it"}">
    <input type="checkbox" ${on ? "checked" : ""} onchange="setStockOff('${escapeHtml(r.key)}', !this.checked)"> ${fmtQty(r.onHand)}</label>`;
}
// Cost with no vendor price. Blue = filled from the last purchase (where it came from underneath),
// red = your number with no price history to go on, amber = your number over a known last purchase.
// Clearing the box goes back to the last purchase price.
function estimateCell(r) {
  const mine = doc.estimates[r.key], lb = r.lastBuy, hasMine = mine !== undefined && mine !== "";
  const cls = hasMine ? (lb ? "est-own" : "est-red") : lb ? "est-hist" : "";
  const val = hasMine ? mine : lb ? Math.round(lb.price * 100000) / 100000 : "";
  const from = lb ? [lb.doc ? `<a class="link" href="purchase-orders.html?id=${lb.doc_id}">${escapeHtml(lb.doc)}</a>` : "", escapeHtml(lb.party || ""),
                     lb.date ? fmtDate(lb.date, { month: "short", day: "numeric", year: "2-digit" }) : ""].filter(Boolean).join(" · ") : "";
  const label = hasMine ? (lb ? `<span class="sim-src own">your estimate · ${lb.doc ? "last buy" : "item cost"} ${fmtPrice(lb.price)}</span>` : `<span class="sim-src red">your estimate · no price history</span>`)
    : lb ? `<span class="sim-src hist">${lb.doc ? `last buy · ${from}` : "item card cost"}</span>` : `<span class="sim-src red">no price history: estimate it</span>`;
  return `<input type="number" class="sim-num sim-est ${cls}" step="any" placeholder="estimate" value="${val}"
      title="No vendor price in this simulation. Type your own estimate; clear it to use the last purchase price."
      oninput="setEstimate('${escapeHtml(r.key)}', this.value)" onchange="setTimeout(() => changed(false))">${label}`;
}
function setMyStock(key, value) { plan(); if (value === "") delete doc.est_stock[key]; else doc.est_stock[key] = Math.max(0, num(value)); changed(false); }
function stockSelect(withHelp = false) {
  const m = STOCK_MODES.find(x => x[0] === plan().stock);
  return `<label class="check-label" title="Which stock covers part of the need. ${escapeHtml(m[2])}">Stock to count:
    <select style="width:auto;" onchange="setStockMode(this.value)">${STOCK_MODES.map(([k, l, d]) =>
      `<option value="${k}" title="${escapeHtml(d)}" ${plan().stock === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    ${withHelp ? `<span class="muted small sim-stock-help">${escapeHtml(m[2])}</span>` : ""}`;
}
// switching to a basis that uses AT-HUB's counts starts with every item counted (ticked)
function setStockMode(mode) { plan().stock = mode; if (mode === "system" || mode === "mixed") doc.stock_off = {}; changed(true); }
function setAllStock(on) {
  plan();
  doc.stock_off = {};
  if (!on) calc().rows.filter(r => r.need > 0 && r.item_id && !r.via).forEach(r => { doc.stock_off[r.key] = true; });
  changed(true);
}

// ================= order tracker =================
const TRACK_STATUS = { todo: "To order", ordered: "Ordered", draft: "Draft PO", onpo: "On PO" };
function planBar(c) {
  const P = plan();
  const withIncoming = c.rows.filter(r => r.item_id && ((INSIGHT[r.item_id] || {}).incoming || []).length).length;
  return `${stockSelect(true)}
    <label title="Quantities still coming in on open POs (not the ones already in this simulation)"><input type="checkbox" ${P.incoming ? "checked" : ""}
      onchange="plan().incoming = this.checked; changed(true)"> Count open POs (incoming)${withIncoming ? ` <span class="muted small">${withIncoming} item${withIncoming === 1 ? "" : "s"} have some</span>` : ""}</label>
    <label title="Order this much extra on top of the need">Buffer <input type="number" min="0" step="1" style="width:64px;" value="${P.buffer || 0}"
      onchange="plan().buffer = Math.max(0, num(this.value)); changed(true)"> %</label>
    <label title="Round each order quantity up to the item's pack size"><input type="checkbox" ${P.pack ? "checked" : ""} onchange="plan().pack = this.checked; changed(true)"> Round up to pack size</label>`;
}
function trackGroups(track) {
  const vname = id => id ? partyName(VENDORS, id) || "?" : "";
  const rank = { todo: 0, draft: 1, ordered: 2, onpo: 3 };
  // not ordered yet first, grouped by the vendor you picked (no vendor yet at the very top), then drafts, ordered, on PO
  const sorted = [...track].sort((a, b) => rank[a.status] - rank[b.status] || (a.status === "todo" && !!a.vendor_id - !!b.vendor_id)
    || vname(a.vendor_id).localeCompare(vname(b.vendor_id)) || String(a.r.code).localeCompare(String(b.r.code)));
  const groups = [];
  for (const t of sorted) {
    const label = t.status === "todo" ? (t.vendor_id ? vname(t.vendor_id) : "No vendor picked yet") : `${TRACK_STATUS[t.status]}${t.vendor_id ? " · " + vname(t.vendor_id) : ""}`;
    const g = groups[groups.length - 1];
    if (g && g.label === label && g.status === t.status) g.lines.push(t); else groups.push({ label, status: t.status, lines: [t] });
  }
  return groups;
}
// Lines ticked for the next "Create Draft POs" (only lines still to order with a vendor and one of our items).
// Not saved: a fresh pick each visit. A vendor's group row ticks all of its lines.
let TRK_SEL = new Set();
const trkReady = t => t.status === "todo" && t.vendor_id && t.r.item_id && t.qty > 0;
function trackerProgress(c) {
  const all = c.track, done = all.filter(t => t.status !== "todo");
  const v = ls => ls.reduce((s, t) => s + t.value, 0);
  const pct = all.length ? Math.round(done.length / all.length * 100) : 0;
  const by = s => all.filter(t => t.status === s).length;
  return `<div class="trk-progress"><div class="trk-bar"><span style="width:${pct}%"></span></div>
    <span><b>${done.length} of ${all.length}</b> lines handled (${pct}%) · ${money(v(done))} of ${money(v(all))}</span>
    <span class="muted small">${by("onpo")} on PO · ${by("draft")} draft PO · ${by("ordered")} ordered elsewhere · <b>${by("todo")} still to order (${money(v(all.filter(t => t.status === "todo")))})</b></span></div>`;
}
function trackerTable(c) {
  if (!c.track.length) return `<p class="muted">Nothing to buy: the need is covered${plan().stock !== "none" ? " by stock and" : " by"} the sources above.</p>`;
  const live = new Set(c.track.filter(trkReady).map(t => t.key));
  TRK_SEL = new Set([...TRK_SEL].filter(k => live.has(k)));  // drop ticks on lines that were ordered / changed
  const vendorOpts = id => `<option value="">Vendor…</option>${VENDORS.map(v => `<option value="${v.id}" ${v.id === id ? "selected" : ""}>${escapeHtml(v.name)}</option>`).join("")}`;
  const k = t => escapeHtml(t.key);
  const why = r => [`need ${fmtQty(r.need)}`, r.rolled ? `+ ${fmtQty(r.rolled)} for nuts` : "", r.stock ? `− stock ${fmtQty(r.stock)}` : "",
                    r.incoming ? `− incoming ${fmtQty(r.incoming)}` : "", num(plan().buffer) ? `+ ${plan().buffer}%` : ""].filter(Boolean).join(" ");
  const tick = t => trkReady(t) ? `<input type="checkbox" class="trk-tick" ${TRK_SEL.has(t.key) ? "checked" : ""} data-keys="${escapeHtml(JSON.stringify([t.key]))}" onchange="trkTickEl(this)" title="Include in the next Create Draft POs">`
    : t.status === "todo" ? `<span class="muted small" title="${t.r.item_id ? "Pick a vendor first" : "Not in our database"}">—</span>` : "";
  const row = t => `<tr class="${t.r.notInDb ? "sim-unknown" : ""} ${TRK_SEL.has(t.key) ? "trk-picked" : ""}">
      <td class="trk-c">${tick(t)}</td>
      <td><b>${escapeHtml(t.r.code || "")}</b> <span class="muted small">${escapeHtml((t.r.desc || "").slice(0, 44))}</span>
        ${t.r.notInDb ? `<span class="sim-tag" title="Add the item to AT-HUB before it can go on a PO">not in DB</span>` : ""}
        ${t.part && !t.fixed ? `<div class="small muted">quoted on ${escapeHtml(t.part.b.label || "a source")}</div>` : ""}
        ${t.flag && t.status === "todo" ? `<div class="small neg">${escapeHtml(t.flag)}</div>` : ""}</td>
      <td class="num">${fmtQty(t.need)}<div class="small muted">${why(t.r)}</div></td>
      <td class="num">${t.fixed || t.status !== "todo" ? fmtQty(t.qty) : `<input type="number" class="sim-num" min="0" step="1" value="${t.qty}" onchange="setTrack('${k(t)}', 'qty', this.value)">
        ${t.qty !== t.suggested ? `<div class="small"><a class="link" onclick="setTrack('${k(t)}', 'qty', '')">suggested ${fmtQty(t.suggested)}</a></div>` : t.pack ? `<div class="small muted">pack ${fmtQty(t.pack)}</div>` : ""}`}</td>
      <td class="num">${t.fixed || t.status !== "todo" ? price(t.price) : `<input type="number" class="sim-num" min="0" step="any" value="${t.price ?? ""}" placeholder="price" onchange="setTrack('${k(t)}', 'price', this.value)">`}</td>
      <td class="num">${money(t.value)}</td>
      <td>${t.fixed || t.status === "draft" ? escapeHtml(partyName(VENDORS, t.vendor_id) || "—") : `<select onchange="setTrack('${k(t)}', 'vendor_id', parseInt(this.value) || null)">${vendorOpts(t.vendor_id)}</select>${t.vendorHint ? `<div class="small muted">${t.vendorHint}</div>` : ""}`}</td>
      <td>${t.fixed ? `<span class="trk-st onpo">On PO</span>` : `<select onchange="setTrack('${k(t)}', 'status', this.value)" style="width:auto;">
          <option value="todo" ${t.status === "todo" ? "selected" : ""}>To order</option><option value="ordered" ${t.status === "ordered" ? "selected" : ""}>Ordered</option>
          ${t.po_code ? `<option value="draft" ${t.status === "draft" ? "selected" : ""}>Draft PO</option>` : ""}</select>`}</td>
      <td>${t.po_id ? `<a class="link" href="purchase-orders.html?id=${t.po_id}">${escapeHtml(t.po_code || "PO")}</a>${!t.fixed && TRK_PO[t.po_id] ? ` <span class="tag ${escapeHtml(TRK_PO[t.po_id].status)}">${escapeHtml(TRK_PO[t.po_id].status.replace("_", " "))}</span>` : ""}` : t.fixed ? escapeHtml(t.ref) :
          `<input value="${escapeHtml(t.ref)}" placeholder="PO # / note" style="width:96px;" onchange="setTrack('${k(t)}', 'ref', this.value)">`}</td></tr>`;
  const groups = trackGroups(c.track);
  const todo = c.track.filter(t => t.status === "todo");
  const ready = todo.filter(trkReady);
  const picked = ready.filter(t => TRK_SEL.has(t.key));
  const pickedVendors = new Set(picked.map(t => t.vendor_id));
  const groupTick = g => {
    const rl = g.lines.filter(trkReady);
    if (!rl.length) return "";
    const n = rl.filter(t => TRK_SEL.has(t.key)).length;
    return `<input type="checkbox" class="trk-tick" ${n === rl.length ? "checked" : ""} data-some="${n > 0 && n < rl.length ? 1 : ""}"
      data-keys="${escapeHtml(JSON.stringify(rl.map(t => t.key)))}" onchange="trkTickEl(this)" title="Tick every line for this vendor">`;
  };
  return `${trackerProgress(c)}
    <table class="compact-table sim-track no-table-tools"><thead><tr><th class="trk-c">${ready.length ? `<input type="checkbox" class="trk-tick" ${picked.length && picked.length === ready.length ? "checked" : ""}
        data-some="${picked.length && picked.length < ready.length ? 1 : ""}" data-keys="${escapeHtml(JSON.stringify(ready.map(t => t.key)))}" onchange="trkTickEl(this)" title="Tick every line ready to order">` : ""}</th>
      <th>Item</th><th class="num">Still need</th><th class="num">Order qty</th><th class="num">Price</th>
      <th class="num">Value</th><th>Vendor</th><th>Status</th><th>PO / reference</th></tr></thead><tbody>
    ${groups.map(g => `<tr class="trk-group"><td class="trk-c">${g.status === "todo" ? groupTick(g) : ""}</td><td colspan="4"><span class="trk-st ${g.status}">${TRACK_STATUS[g.status]}</span> ${escapeHtml(g.label.replace(TRACK_STATUS[g.status] + " · ", ""))}</td>
        <td class="num">${money(g.lines.reduce((s, t) => s + t.value, 0))}</td><td colspan="3" class="muted small">${g.lines.length} line${g.lines.length === 1 ? "" : "s"}</td></tr>
      ${g.lines.map(row).join("")}`).join("")}</tbody>
    <tfoot><tr><td colspan="5">${picked.length ? `<b>${picked.length} ticked</b> · ${pickedVendors.size} vendor${pickedVendors.size === 1 ? "" : "s"} · ${money(picked.reduce((s, t) => s + t.value, 0))}`
        : `Tick the lines (or a vendor) to order now${todo.length > ready.length ? ` · <span class="muted">${todo.length - ready.length} line${todo.length - ready.length === 1 ? "" : "s"} need a vendor${todo.some(t => t.r.notInDb) ? " or aren't in our database" : ""}</span>` : ""}`}</td>
      <td class="num">${money(todo.reduce((s, t) => s + t.value, 0))}</td><td colspan="3">
      <button class="small-btn" ${picked.length ? "" : "disabled"} onclick="createTrackerPos()" title="One draft PO per vendor, for the ticked lines only">
        Create ${pickedVendors.size || ""} Draft PO${pickedVendors.size === 1 ? "" : "s"}${picked.length ? ` (${picked.length} line${picked.length === 1 ? "" : "s"})` : ""}</button></td></tr></tfoot></table>`;
}
function trkTickEl(el) { trkTick(JSON.parse(el.dataset.keys), el.checked); }
function trkTick(keys, on) {
  keys.forEach(key => on ? TRK_SEL.add(key) : TRK_SEL.delete(key));
  const c = calc();
  const host = document.getElementById("sim-track");
  if (host) host.innerHTML = trackerTable(c);
  host.querySelectorAll("input.trk-tick[data-some='1']").forEach(x => { x.indeterminate = true; });
}
function drawTracker(c) {
  const host = document.getElementById("sim-track"), bar = document.getElementById("sim-plan");
  if (!host) return;
  if (bar && !bar.contains(document.activeElement)) bar.innerHTML = planBar(c);
  if (host.contains(document.activeElement) && document.activeElement.matches("input:not([type=checkbox])")) return;  // typing: leave the box alone
  host.innerHTML = trackerTable(c);
  host.querySelectorAll("input.trk-tick[data-some='1']").forEach(x => { x.indeterminate = true; });
}
function setTrack(key, field, value) {
  plan();
  const T = (doc.tracker[key] ||= {});
  if (value === "" || value === undefined) delete T[field]; else T[field] = field === "qty" || field === "price" ? num(value) : value;
  if (field === "status" && value === "todo") { delete T.po_id; delete T.po_code; }
  setTimeout(() => changed(false));
}
// one draft PO per vendor from the TICKED lines; they then show "Draft PO" with its link, the rest stay "To order"
async function createTrackerPos() {
  const c = calc();
  const ready = c.track.filter(t => trkReady(t) && TRK_SEL.has(t.key));
  if (!ready.length) { toast("Tick the lines to order first"); return; }
  const byVendor = {};
  ready.forEach(t => (byVendor[t.vendor_id] ||= []).push(t));
  const left = c.track.filter(t => t.status === "todo").length - ready.length;
  const list = Object.entries(byVendor).map(([v, ls]) => `<li><b>${escapeHtml(partyName(VENDORS, +v))}</b>: ${ls.length} line${ls.length === 1 ? "" : "s"}, ${money(ls.reduce((s, t) => s + t.value, 0))}</li>`).join("");
  const { value } = await askDialog({ title: `Create ${Object.keys(byVendor).length} draft PO${Object.keys(byVendor).length === 1 ? "" : "s"}?`,
    body: `<ul style="margin:0 0 8px 18px;">${list}</ul>${left > 0 ? `<p class="small" style="margin:0 0 6px;">${left} other line${left === 1 ? "" : "s"} stay${left === 1 ? "s" : ""} on the tracker as <b>To order</b>.</p>` : ""}
      <p class="muted small" style="margin:0;">Drafts only: nothing is sent to vendors until you open each PO and order it.</p>`,
    buttons: [{ label: "Create Drafts", value: "ok", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!value) return;
  plan();
  const made = [];
  for (const [v, ls] of Object.entries(byVendor)) {
    try {
      const r = await apiFetch("/api/simulations/create-po", { method: "POST", body: JSON.stringify({ vendor_id: +v,
        lines: ls.map(t => ({ item_id: t.r.item_id, quantity: Math.ceil(t.qty), price: t.price || 0 })), notes: `From simulation "${sim.name}" (order tracker)` }) });
      ls.forEach(t => { doc.tracker[t.key] = { ...(doc.tracker[t.key] || {}), vendor_id: +v, status: "draft", po_id: r.id, po_code: r.code, qty: t.qty, price: t.price, flag: undefined }; TRK_SEL.delete(t.key); });
      made.push(r.code);
    } catch (e) { toast(`${partyName(VENDORS, +v)}: ${e.message}`); }
  }
  if (made.length) toast(`Draft ${made.join(", ")} created`);
  changed(true);
}
function setStockOff(key, off) { doc.stock_off ||= {}; if (off) doc.stock_off[key] = true; else delete doc.stock_off[key]; changed(true); }
function setGenericOff(key, off) { if (off) doc.generic_off[key] = true; else delete doc.generic_off[key]; changed(true); }

// ================= blocks from real records (also used when the review pop-up adds them) =================
// snap: what the record looked like when it was added -- status + item/qty/price per line -- to spot later changes
function snapOf(status, lines) {
  return `${status || ""}|${(lines || []).map(l => `${l.item_id}:${+num(l.qty)}:${(+num(l.price)).toFixed(5)}`).sort().join(",")}`;
}
function blockFromOrder(o) {
  return { id: uid(), kind: "order", label: o.po_number ? `PO ${o.po_number}` : o.code, party_id: o.customer_id, multiplier: 1,
           ref_id: o.id, ref_code: o.code, ref_link: `customer-orders.html?id=${o.id}`,
           lines: o.lines.map(l => fromDocLine(l.item_id, (itemById(l.item_id) || {}).code, (itemById(l.item_id) || {}).title, l.quantity, l.unit_price)),
           snap: snapOf(o.status, o.lines.map(l => ({ item_id: l.item_id, qty: l.quantity, price: l.unit_price }))) };
}
function blockFromQuote(q) {
  return { id: uid(), kind: "quote", label: q.code, party_id: q.customer_id, multiplier: 1, ref_id: q.id, ref_code: q.code,
           lines: (q.lines || []).map(l => fromDocLine(l.item_id, (itemById(l.item_id) || {}).code || l.item_code || "", l.description || (itemById(l.item_id) || {}).title, l.quantity, l.unit_price)),
           snap: snapOf(q.status, (q.lines || []).map(l => ({ item_id: l.item_id, qty: l.quantity, price: l.unit_price }))) };
}
function blockFromPo(p) {
  return { id: uid(), kind: "po", label: p.code, party_id: p.vendor_id, ref_id: p.id, ref_code: p.code, ref_link: `purchase-orders.html?id=${p.id}`,
           lines: p.lines.map(l => ({ ...fromDocLine(l.item_id, (itemById(l.item_id) || {}).code, l.vendor_description || (itemById(l.item_id) || {}).title, l.quantity, l.unit_cost),
                                      received: l.received_quantity || 0 })),
           costs: (p.charges || []).map(ch => ({ id: uid(), type: "shipping", label: ch.description || ch.charge_type, mode: "total", amount: ch.amount, alloc: "value" })),
           snap: snapOf(p.status, p.lines.map(l => ({ item_id: l.item_id, qty: l.quantity, price: l.unit_cost }))) };
}

// ================= "what changed since you last worked on this" =================
// On opening a simulation: new open POs / confirmed customer orders / quotes on its items (not already in it, not
// already seen), records it was built from that changed, and draft POs from its tracker that were cancelled.
// A pop-up asks what to add; a banner keeps it one click away. doc.seen = records already offered.
let CHG = null, TRK_PO = {};
const CHG_TYPE = { po: "PO", order: "Customer order", quote: "Quote" };
function diffText(oldSnap, cur) {
  const parse = s => { const [st, ls] = String(s || "").split("|"); const m = {};
    (ls || "").split(",").filter(Boolean).forEach(x => { const [i, q, p] = x.split(":"); m[i] = { q: +q, p: +p }; }); return { st, m }; };
  const a = parse(oldSnap), b = parse(snapOf(cur.status, cur.lines));
  const code = id => (itemById(+id) || {}).code || `item ${id}`;
  const out = [];
  if (a.st !== b.st) out.push(`status ${a.st || "?"} → <b>${b.st}</b>`);
  for (const id of new Set([...Object.keys(a.m), ...Object.keys(b.m)])) {
    const x = a.m[id], y = b.m[id];
    if (!y) out.push(`${escapeHtml(code(id))} removed`);
    else if (!x) out.push(`${escapeHtml(code(id))} added (${fmtQty(y.q)})`);
    else {
      if (x.q !== y.q) out.push(`${escapeHtml(code(id))} qty ${fmtQty(x.q)} → <b>${fmtQty(y.q)}</b>`);
      if (Math.abs(x.p - y.p) > 1e-6) out.push(`${escapeHtml(code(id))} price ${fmtPrice(x.p)} → <b>${fmtPrice(y.p)}</b>`);
    }
  }
  return out;
}
async function checkChanges(popup = true) {
  if (!sim) return;
  let r;
  try { r = await apiFetch(`/api/simulations/${sim.id}/changes`, { method: "POST" }); } catch (e) { return; }
  plan();
  let dirty = false;
  const changedBlocks = [], flags = [];
  // records the simulation was built from
  for (const [side, blocks] of [["demand", doc.demand], ["sources", doc.sources]]) for (const b of blocks) {
    if (!Number.isInteger(b.ref_id) || (side === "sources" && !isPoBlock(b))) continue;
    const kind = side === "sources" ? "po" : b.kind === "quote" ? "quote" : "order";
    const cur = r.linked[`${kind}:${b.ref_id}`];
    if (!cur) continue;
    if (cur.missing) { changedBlocks.push({ b, side, kind, cur, lines: [`${escapeHtml(b.ref_code || "")} no longer exists`], gone: true }); continue; }
    if (kind === "po") {  // received so far, per line (goods received are on hand: counted once)
      const left = {};
      cur.lines.forEach(l => { left[l.item_id] = (left[l.item_id] || 0) + (l.received || 0); });
      for (const l of b.lines) {
        const rec = Math.min(num(l.qty), left[l.item_id] || 0);
        if (l.item_id) left[l.item_id] = (left[l.item_id] || 0) - rec;
        if ((l.received || 0) !== rec) { l.received = rec; dirty = true; }
      }
    }
    const snap = snapOf(cur.status, cur.lines);
    if (!b.snap) { b.snap = snap; dirty = true; continue; }  // added before changes were tracked: start from now
    if (b.snap !== snap) changedBlocks.push({ b, side, kind, cur, lines: diffText(b.snap, cur), gone: cur.status === "cancelled" });
  }
  // draft POs made by the order tracker: follow them; cancelled / deleted -> back to To order
  TRK_PO = {};
  for (const T of Object.values(doc.tracker)) {
    if (!T.po_id) continue;
    const cur = r.linked[`po:${T.po_id}`];
    if (cur && !cur.missing && cur.status !== "cancelled") { TRK_PO[T.po_id] = cur; continue; }
    flags.push(`${escapeHtml(T.po_code || "A PO")} (made from this tracker) was ${cur && cur.status === "cancelled" ? "cancelled" : "deleted"} — its lines are back to <b>To order</b>.`);
    T.flag = `${T.po_code || "PO"} was ${cur && cur.status === "cancelled" ? "cancelled" : "deleted"}`;
    T.status = "todo"; delete T.po_id; delete T.po_code; dirty = true;
  }
  // new open records on these items
  const inSim = new Set([...doc.demand.filter(b => Number.isInteger(b.ref_id)).map(b => (b.kind === "quote" ? "quote:" : "order:") + b.ref_id),
                         ...doc.sources.filter(b => Number.isInteger(b.ref_id) && isPoBlock(b)).map(b => "po:" + b.ref_id),
                         ...Object.values(doc.tracker).filter(T => T.po_id).map(T => "po:" + T.po_id)]);
  const open = [...r.open.pos.map(x => ({ ...x, type: "po" })), ...r.open.orders.map(x => ({ ...x, type: "order" })), ...r.open.quotes.map(x => ({ ...x, type: "quote" }))]
    .map(x => ({ ...x, key: `${x.type}:${x.id}` }));
  if (!doc.seen) { doc.seen = Object.fromEntries(open.map(x => [x.key, 1])); dirty = true; }  // first check: what exists now is the starting point
  const openKeys = new Set(open.map(x => x.key));
  for (const k of Object.keys(doc.seen)) if (!openKeys.has(k)) { delete doc.seen[k]; dirty = true; }
  const fresh = open.filter(x => !doc.seen[x.key] && !inSim.has(x.key));
  CHG = { fresh, changedBlocks, flags };
  if (dirty) { changed(true); }
  drawAlert();
  if (popup && (fresh.length || changedBlocks.length || flags.length)) openReview();
}
function chgCount() { return CHG ? CHG.fresh.length + CHG.changedBlocks.length + CHG.flags.length : 0; }
function drawAlert() {
  const el = document.getElementById("sim-alert");
  if (!el) return;
  const n = chgCount();
  if (!n) { el.innerHTML = ""; return; }
  const parts = [];
  const pos = CHG.fresh.filter(x => x.type === "po").length, ords = CHG.fresh.filter(x => x.type === "order").length, qs = CHG.fresh.filter(x => x.type === "quote").length;
  if (pos) parts.push(`${pos} new PO${pos === 1 ? "" : "s"}`);
  if (ords) parts.push(`${ords} new confirmed order${ords === 1 ? "" : "s"}`);
  if (qs) parts.push(`${qs} quote${qs === 1 ? "" : "s"}`);
  if (CHG.changedBlocks.length) parts.push(`${CHG.changedBlocks.length} linked record${CHG.changedBlocks.length === 1 ? "" : "s"} changed`);
  if (CHG.flags.length) parts.push(`${CHG.flags.length} PO${CHG.flags.length === 1 ? "" : "s"} cancelled`);
  el.innerHTML = `<div class="sim-alert">${icon("bell")}<span><b>Since you last worked on this:</b> ${parts.join(" · ")}. Your counts may be affected.</span>
    <button class="small-btn" onclick="openReview()">Review</button></div>`;
}
async function openReview() {
  if (!chgCount()) return;
  const when = x => [x.created_by ? `by ${escapeHtml(x.created_by)}` : "", x.created_at ? fmtDate(x.created_at, { month: "short", day: "numeric" }) : ""].filter(Boolean).join(" · ");
  const linesTxt = x => x.lines.slice(0, 6).map(l => `${escapeHtml(l.code || "")} ${fmtQty(l.qty)}${l.price ? ` @ ${fmtPrice(l.price)}` : ""}`).join(" · ") + (x.lines.length > 6 ? ` · +${x.lines.length - 6} more` : "");
  const rec = (x, checked, what) => `<label class="chg-row"><input type="checkbox" data-key="${escapeHtml(x.key)}" ${checked ? "checked" : ""}>
      <span><b>${escapeHtml(x.code)}</b> ${escapeHtml(x.party || "")} <span class="tag ${escapeHtml(x.status)}">${escapeHtml(String(x.status).replace("_", " "))}</span>
        <span class="muted small">${when(x)}</span><br><span class="small">${what}: ${linesTxt(x)}</span></span></label>`;
  const sec = (title, note, rows) => rows.length ? `<h4 class="chg-h">${title}</h4>${note ? `<p class="muted small" style="margin:0 0 4px;">${note}</p>` : ""}${rows.join("")}` : "";
  const f = CHG.fresh;
  const body = `<div class="chg-body">
    ${sec("New purchase orders", "They include items in this simulation. Ticked ones are added on the Buy side as sources.", f.filter(x => x.type === "po").map(x => rec(x, true, "items here")))}
    ${sec("New confirmed customer orders", "They need the same items. Ticked ones are added on the Sell side.", f.filter(x => x.type === "order").map(x => rec(x, true, "items here")))}
    ${sec("Quotes were also found — include them?", "Quotes aren't confirmed yet, so they're left out unless you tick them.", f.filter(x => x.type === "quote").map(x => rec(x, false, "items here")))}
    ${sec("Changed since you added them", "Ticked: update this simulation to match. Unticked: keep your version.", CHG.changedBlocks.map((c, i) => `<label class="chg-row">
        <input type="checkbox" data-chg="${i}" checked><span><b>${escapeHtml(c.b.label || c.b.ref_code || "")}</b> <span class="muted small">${CHG_TYPE[c.kind]}${c.gone ? " · will be removed" : ""}</span><br>
        <span class="small">${c.lines.join(" · ") || "details changed"}</span></span></label>`))}
    ${CHG.flags.length ? `<h4 class="chg-h">Order tracker</h4>${CHG.flags.map(t => `<p class="small" style="margin:2px 0;">${t}</p>`).join("")}` : ""}
    </div>`;
  const { value, el } = await askDialog({ title: "New activity on this simulation's items", body,
    buttons: [{ label: "Apply", value: "ok", cls: "confirm-btn" }, { label: "Not now", value: null, cls: "secondary" }] });
  if (!value) return;
  const pickKeys = new Set([...el.querySelectorAll("input[data-key]:checked")].map(x => x.dataset.key));
  const pickChg = new Set([...el.querySelectorAll("input[data-chg]:checked")].map(x => +x.dataset.chg));
  const added = [];
  for (const x of f) {
    doc.seen[x.key] = 1;  // offered once: ticked or not, it won't be asked again
    if (!pickKeys.has(x.key)) continue;
    try {
      if (x.type === "po") doc.sources.push(blockFromPo(await apiFetch(`/api/purchase-orders/${x.id}`)));
      else if (x.type === "order") doc.demand.push(blockFromOrder(await apiFetch(`/api/customer-orders/${x.id}`)));
      else doc.demand.push(blockFromQuote(await apiFetch(`/api/quotes/${x.id}`)));
      added.push(x.code);
    } catch (e) { toast(`${x.code}: ${e.message}`); }
  }
  CHG.changedBlocks.forEach((c, i) => {
    const list = c.side === "demand" ? doc.demand : doc.sources;
    if (!pickChg.has(i)) { if (!c.cur.missing) c.b.snap = snapOf(c.cur.status, c.cur.lines); return; }  // keep mine
    if (c.gone) { list.splice(list.indexOf(c.b), 1); return; }
    // update to match: same block (label, repeats, extra costs), the record's lines now
    const keepQty = {};
    c.b.lines.forEach(l => { keepQty[l.item_id] = l; });
    c.b.lines = c.cur.lines.map(l => { const old = keepQty[l.item_id];
      return { ...fromDocLine(l.item_id, (itemById(l.item_id) || {}).code || l.code, old ? old.desc : (itemById(l.item_id) || {}).title, l.qty, l.price), ...(c.kind === "po" ? { received: l.received || 0 } : {}) }; });
    c.b.snap = snapOf(c.cur.status, c.cur.lines);
  });
  CHG = { fresh: [], changedBlocks: [], flags: [] };
  drawAlert();
  await ensureInsights([...doc.demand, ...doc.sources].flatMap(b => b.lines.map(l => l.item_id)).filter(Boolean));
  changed(true);
  await saveNow();
  if (added.length) toast(`Added ${added.join(", ")}`);
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
      b = blockFromOrder(orders.find(x => x.id === parseInt(id)));
    } else if (kind === "quote") {
      const quotes = await apiFetch("/api/quotes/");
      const id = await pickFrom("Add a quote", quotes.sort((a, b) => b.id - a.id).map(q => `<option value="${q.id}">${escapeHtml(q.code)} — ${escapeHtml(partyName(CUSTOMERS, q.customer_id))}</option>`).join(""), "Pick a quote…");
      if (!id) return;
      b = blockFromQuote(await apiFetch(`/api/quotes/${id}`));
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
      b = blockFromPo(pos.find(x => x.id === parseInt(id)));
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
        toast(`Draft ${r.code} created`); b.ref_link = `purchase-orders.html?id=${r.id}`; b.ref_code = r.code; b.ref_id = r.id; delete b.snap; changed(true); }
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
        toast(`Draft ${r.code} created`); b.ref_link = `customer-orders.html?id=${r.id}`; b.ref_code = r.code; b.ref_id = r.id; b.kind = "order"; delete b.snap; changed(true); }
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
      <button class="secondary" onclick="exportSim()" data-icon="download" title="Everything on this page as Excel, PDF or CSV">Export</button>
      <button class="secondary" onclick="copySim()" title="A copy to try something different">Copy</button>
      <button class="secondary" onclick="deleteSim()">Delete</button>
      <span class="muted small" id="sim-save">${escapeHtml(saveState)}</span>` : ""}`;
}
async function loadList() { SIMS = await apiFetch("/api/simulations/"); drawBar(); }
async function openSim(id) {
  if (!id) return;
  if (saveTimer) await saveNow();
  sim = await apiFetch(`/api/simulations/${id}`);
  CMP = null; CMP_CHIP = ""; TRK_SEL = new Set(); CHG = null; TRK_PO = {};  // a fresh table (and filters, ticks) per simulation
  doc = Object.assign(blankDoc(), sim.doc || {});
  history.replaceState(null, "", `simulate.html?id=${id}`);
  doc.generic_off ||= {}; doc.stock_off ||= {};
  await ensureInsights([...doc.demand, ...doc.sources].flatMap(b => b.lines.map(l => l.item_id)).filter(Boolean));
  genKey = ""; await refreshGeneric();
  setSaveState(`Saved ${fmtWhen(sim.updated_at)}`);
  drawBar(); render();
  checkChanges(true);  // anything new on these items since last time? asks right away
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
  // back on this tab after a while: look again (banner only -- the pop-up is for opening the simulation)
  let lastCheck = Date.now();
  document.addEventListener("visibilitychange", () => { if (!document.hidden && sim && Date.now() - lastCheck > 60000) { lastCheck = Date.now(); checkChanges(false); } });
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

// ================= export: the whole page as Excel / PDF / CSV =================
async function exportSim() {
  const { value: fmt } = await askDialog({ title: "Export this simulation",
    body: `<p class="muted small" style="margin-top:0;">Everything on the page: the summary, Sell and Buy lines, extra costs, Item By Item,
      Profit Per Order and the Order Tracker. Excel puts each on its own sheet.</p>`,
    buttons: [{ label: "Excel (.xlsx)", value: "xlsx", cls: "confirm-btn" }, { label: "PDF", value: "pdf", cls: "secondary" },
              { label: "CSV", value: "csv", cls: "secondary" }, { label: "Cancel", value: null, cls: "secondary" }] });
  if (!fmt) return;
  const c = calc(), col = (name, f = "text") => ({ name, fmt: f });
  const n = v => v === "" || v == null || isNaN(num(v)) ? null : num(v);
  const costSrc = r => ({ vendor: "vendor price + extras", manual: "my estimate", generic: `generic nut (${r.via ? r.via.code : ""})`,
    history: r.lastBuy ? (r.lastBuy.doc ? `last buy ${r.lastBuy.doc} ${r.lastBuy.party || ""}` : "item card cost") : "" }[r.costSrc] || "");
  const stockName = STOCK_MODES.find(m => m[0] === plan().stock)[1];
  const facts = [["Revenue", fmtMoney(c.summary.revenue)], ["Cost", fmtMoney(c.summary.cost)], ["Profit", fmtMoney(c.summary.profit)],
                 ["Margin", c.summary.margin != null ? c.summary.margin.toFixed(1) + "%" : "—"], ["Items without a cost", c.summary.missing],
                 ["Items with an estimated cost", c.summary.estimated], ["Stock counted", stockName], ["Open POs counted", plan().incoming ? "yes" : "no"],
                 ["Buffer", `${plan().buffer || 0}%`], ["Round up to pack size", plan().pack ? "yes" : "no"], ["Exported", fmtWhen(new Date().toISOString())]];
  const sheets = [
    { title: "Sell", columns: [col("Order / job"), col("Customer"), col("Repeated", "qty"), col("Item #"), col("Description"), col("Qty", "qty"),
        col("Price", "price"), col("Total per order", "money"), col("In our database")],
      rows: doc.demand.flatMap(b => b.lines.map(l => [b.label || "", partyName(CUSTOMERS, b.party_id), num(b.multiplier) || 1, l.code || "", l.desc || "",
        n(l.qty), n(l.price), num(l.qty) * num(l.price), l.item_id ? "yes" : "no"])) },
    { title: "Buy", columns: [col("Source"), col("Vendor"), col("Item #"), col("Description"), col("Qty", "qty"), col("Cost", "price"),
        col("Extras / unit", "price"), col("Effective / unit", "price"), col("Total", "money")],
      rows: doc.sources.flatMap(b => b.lines.map(l => [b.label || "", partyName(VENDORS, b.party_id), l.code || "", l.desc || "", n(l.qty), n(l.price),
        c.effPer[l.id] || 0, num(l.price) + (c.effPer[l.id] || 0), num(l.qty) * num(l.price)])) },
    { title: "Extra costs", columns: [col("On"), col("Cost"), col("How"), col("Amount", "price"), col("Spread by")],
      rows: [...doc.sources.flatMap(b => (b.costs || []).map(x => [b.label || "Source", x.label, (MODES.find(m => m[0] === x.mode) || [])[1] || x.mode, n(x.amount), x.mode === "total" ? (x.alloc === "qty" ? "qty" : "value") : ""])),
             ...doc.shared_costs.map(x => ["All sources (shared)", x.label, (MODES.find(m => m[0] === x.mode) || [])[1] || x.mode, n(x.amount), x.mode === "total" ? (x.alloc === "qty" ? "qty" : "value") : ""])] },
    { title: "Item by item", columns: [col("Item #"), col("Description"), col("Need", "qty"), col("Sourced", "qty"), col("AT-HUB on hand", "qty"),
        col("My on hand", "qty"), col("Stock counted", "qty"), col("Incoming counted", "qty"), col("To order", "qty"), col("Customer price", "price"),
        col("Vendor price", "price"), col("Extras / unit", "price"), col("Effective cost", "price"), col("Cost from"), col("Profit / unit", "price"),
        col("Margin", "pct"), col("Profit", "money")],
      rows: c.rows.filter(r => r.need > 0).map(r => [r.code || "", r.desc || "", r.need, r.supply, r.item_id ? r.onHand : null, r.myStock, r.stock, r.incoming,
        r.toOrder, r.custPrice, r.vendor, r.extras, r.cost, costSrc(r), r.profitUnit, r.profitUnit != null && r.custPrice ? r.profitUnit / r.custPrice * 100 : null, r.profit]) },
    { title: "Profit per order", columns: [col("Order"), col("Repeated", "qty"), col("Revenue / order", "money"), col("Cost / order", "money"),
        col("Profit / order", "money"), col("Margin", "pct"), col("Total profit", "money"), col("Lines without cost", "qty")],
      rows: c.orders.map(o => [o.b.label || o.b.ref_code || "Order", o.m, o.rev, o.cost, o.profit, o.margin, o.profit * o.m, o.missing]) },
    { title: "Order tracker", columns: [col("Status"), col("Vendor"), col("Item #"), col("Description"), col("Still need", "qty"), col("Order qty", "qty"),
        col("Price", "price"), col("Value", "money"), col("PO / reference")],
      rows: trackGroups(c.track).flatMap(g => g.lines).map(t => [TRACK_STATUS[t.status], partyName(VENDORS, t.vendor_id) || "", t.r.code || "", t.r.desc || "",
        t.need, t.qty, t.price, t.value, t.po_code || t.ref || ""]) },
  ];
  try {
    const res = await fetch(`${API_BASE}/api/simulations/export`, { method: "POST",
      headers: { Authorization: `Bearer ${AuthGuard.getToken()}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name: sim.name, fmt, facts, sheets }) });
    if (!res.ok) { let d = `Export failed (${res.status})`; try { d = (await res.json()).detail || d; } catch {} throw new Error(d); }
    saveBlob(await res.blob(), `${(sim.name || "Simulation").replace(/[^\w .,()&-]+/g, "").trim() || "Simulation"}.${fmt}`);
  } catch (e) { toast(e.message); }
}
