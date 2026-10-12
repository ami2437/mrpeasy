// Accounting -- the newer tabs and pieces (accounting.html loads this first; it uses that page's helpers: view(), META,
// PERIOD, qs(), money(), money0(), day(), acc(), accountOptions(), show(), TXF, drill(), store, pickLines() ...).
// Partner Accounts · Payees · 1099 · Recurring · Ignored · Check My Books · Reconcile (QuickBooks) · Projections ·
// Notes · the comment box on every tab · Loans quick calculations.

const pkey = () => `${PERIOD.start}..${PERIOD.end}`;
const pct = (now, before) => !before ? "" : (() => { const c = (now - before) / Math.abs(before) * 100; return `<span class="${c >= 0 ? "pos" : "neg"} small">${c >= 0 ? "+" : ""}${c.toFixed(0)}%</span>`; })();
const vsPrev = (now, before, label = "before") => before == null ? "" : `<div class="s">${escapeHtml(label)}: ${fmtMoney(before)} ${pct(now, before)}</div>`;

// ================================================================ comments: a box at the foot of every tab (per period)
async function commentsCard(section) {
  const box = document.createElement("div");
  box.className = "card ac-comments";
  box.id = "cm-" + section;
  view().appendChild(box);
  await drawComments(section);
}
async function drawComments(section) {
  const box = document.getElementById("cm-" + section);
  if (!box) return;
  let list = [];
  try { list = await apiFetch(`/api/accounting/comments?${new URLSearchParams({ section, period_key: pkey() })}`); } catch (e) { box.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`; return; }
  const me = AuthGuard.getUser ? (AuthGuard.getUser() || {}) : {};
  box.innerHTML = `<div class="ac-filters"><h4 style="margin:0;">Comments</h4><span class="muted small">for ${escapeHtml(PERIOD.key && PERIOD.key !== "custom" ? PERIOD.key : PERIOD.start + " – " + PERIOD.end)} — they go into the Output Excel</span></div>
    ${list.map(c => `<div class="ac-comment"><div>${escapeHtml(c.text).replace(/\n/g, "<br>")}</div>
      <div class="muted small">${escapeHtml(c.created_by || "")} · ${fmtWhen(c.created_at)}${c.updated_at ? ` · changed ${fmtWhen(c.updated_at)}` : ""}${c.period_key ? "" : " · every period"}
        <a class="link" href="#" onclick="editComment(${c.id}, '${section}');return false;">Change</a> · <a class="link" href="#" onclick="deleteComment(${c.id}, '${section}');return false;">Delete</a></div></div>`).join("")}
    <div class="ac-comment-new"><textarea id="cm-new-${section}" rows="2" placeholder="Add a comment for this period…"></textarea>
      <div class="btn-row"><button class="small-btn" onclick="addComment('${section}', false)">${icon("plus")} Add</button>
      <button class="small-btn" onclick="addComment('${section}', true)" title="Shows in every period">Add For Every Period</button></div></div>`;
}
async function addComment(section, every) {
  const t = document.getElementById("cm-new-" + section);
  if (!t.value.trim()) return;
  try { await apiFetch("/api/accounting/comments", { method: "POST", body: JSON.stringify({ section, text: t.value, period_key: every ? null : pkey() }) }); drawComments(section); }
  catch (e) { alert(e.message); }
}
async function editComment(id, section) {
  const box = document.getElementById("cm-" + section);
  const cur = [...box.querySelectorAll(".ac-comment")].find(d => d.querySelector(`a[onclick*="editComment(${id},"]`));
  const old = cur ? cur.firstElementChild.innerText : "";
  const { value, el } = await askDialog({ title: "Change Comment", body: `<textarea id="cm-edit" rows="4" style="width:100%;">${escapeHtml(old)}</textarea>`, buttons: [{ label: "Save", value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  if (!value) return;
  await apiFetch(`/api/accounting/comments/${id}`, { method: "PUT", body: JSON.stringify({ section, text: el.querySelector("#cm-edit").value }) });
  drawComments(section);
}
async function deleteComment(id, section) {
  if (!(await askDialog({ title: "Delete this comment?", body: "", buttons: [{ label: "Delete", value: true, cls: "danger" }, { label: "Cancel", value: null }] })).value) return;
  await apiFetch(`/api/accounting/comments/${id}`, { method: "DELETE" });
  drawComments(section);
}

// ================================================================ Partner Accounts
async function tabPartnerAccounts() {
  const d = await apiFetch(`/api/accounting/partner-accounts?${qs()}`);
  // every figure opens the lines behind it (who, which dates, what)
  const cell = (who, a, b, what, v, strong) => !v ? `<td class="ac-amt"></td>` : `<td class="ac-amt"><a class="link" href="#" title="See the lines behind this" onclick='partnerCell(${JSON.stringify(who)}, ${JSON.stringify(a)}, ${JSON.stringify(b)}, "${what}");return false;'>${strong ? "<strong>" : ""}${money(v)}${strong ? "</strong>" : ""}</a></td>`;
  const t = (x, who, a, b) => !who ? `<td class="ac-amt">${money(x.withdrawn)}</td><td class="ac-amt">${money(x.invested)}</td><td class="ac-amt"><strong>${money(x.net)}</strong></td><td class="ac-amt">${x.lent ? money(x.lent) : ""}</td><td class="ac-amt">${x.repaid ? money(x.repaid) : ""}</td>`
    : cell(who, a, b, "withdrawn", x.withdrawn) + cell(who, a, b, "invested", x.invested) + cell(who, a, b, "net", x.net, true) + cell(who, a, b, "lent", x.lent) + cell(who, a, b, "repaid", x.repaid);
  const monthEnd = m => { const [y, mo] = m.split("-").map(Number); return `${m}-${String(new Date(y, mo, 0).getDate()).padStart(2, "0")}`; };
  const fyRange = fy => { const y = META.years.find(x => x.label === fy); return y ? [y.start, y.end] : [null, null]; };
  const head = `<th class="num">Withdrawn</th><th class="num">Invested</th><th class="num">Net</th><th class="num">Lent To Co.</th><th class="num">Repaid</th>`;
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Partner Accounts — ${escapeHtml(d.year)}</h3><span style="flex:1"></span>
      <button onclick="downloadFile('/api/accounting/partner-accounts/export.xlsx?${qs()}', 'Partner Accounts.xlsx').catch(e => alert(e.message))">${icon("download")} Excel</button></div>
    <p class="ac-help">Who took how much out (withdrawn: money out of their partner accounts), who put money in (invested), and money a partner lent the company
      (loan accounts tagged with the partner). <strong>Click any figure</strong> to see the lines behind it. To get a partner's lines here, file them in an account of type Partner Draws with that partner (or a loan with the partner set).</p>
    <table class="compact-table no-table-tools"><thead><tr><th>Partner</th>${head}<th class="num">Share</th><th class="num">Remaining</th></tr></thead><tbody>
    ${d.partners.map(p => `<tr><td><strong>${escapeHtml(p.name)}</strong>${p.share_pct != null ? `<div class="muted small">${(p.share_pct * 100).toFixed(1)}%</div>` : ""}</td>${t(p.period, p.name, PERIOD.start, PERIOD.end)}
      <td class="ac-amt">${p.share != null ? fmtMoney(p.share) : ""}</td><td class="ac-amt">${p.remaining != null ? money(p.remaining) : ""}</td></tr>`).join("")}
    <tr class="tot"><td><strong>Total</strong></td>${t(d.partners.reduce((a, p) => { for (const k of ["withdrawn", "invested", "net", "lent", "repaid"]) a[k] += p.period[k]; return a; }, { withdrawn: 0, invested: 0, net: 0, lent: 0, repaid: 0 }))}
      <td class="ac-amt">${fmtMoney(d.to_share)}</td><td></td></tr></tbody></table></div>
    <div class="ac-grid2">${d.partners.map(p => `<div class="card"><div class="ac-filters"><h3 style="margin:0;">${escapeHtml(p.name)}</h3><span style="flex:1"></span>
        ${p.account_ids.length ? `<button class="small-btn" onclick='partnerLines(${JSON.stringify(p.account_ids)})'>Lines</button>` : ""}</div>
      <div class="ac-tiles"><div class="ac-tile click" onclick='partnerCell(${JSON.stringify(p.name)}, PERIOD.start, PERIOD.end, "withdrawn")'><div class="l">Withdrawn</div><div class="v neg">${fmtMoney(p.period.withdrawn)}</div><div class="s">all time ${fmtMoney(p.all_time.withdrawn)}</div></div>
        <div class="ac-tile"><div class="l">Invested</div><div class="v pos">${fmtMoney(p.period.invested)}</div><div class="s">all time ${fmtMoney(p.all_time.invested)}</div></div>
        <div class="ac-tile click" title="Withdrawn + invested (their draws, net) — click for the lines" onclick='partnerCell(${JSON.stringify(p.name)}, PERIOD.start, PERIOD.end, "net")'><div class="l">Net</div>
          <div class="v ${p.period.net < 0 ? "neg" : "pos"}">${fmtMoney(p.period.net)}</div><div class="s">all time ${fmtMoney(p.all_time.net)}</div></div>
        <div class="ac-tile"><div class="l">Lent To The Company</div><div class="v">${fmtMoney(p.all_time.loan_owed)}</div><div class="s">still owed to them (all time)</div></div></div>
      <div class="muted small">${p.accounts.length ? "Accounts: " + p.accounts.map(escapeHtml).join(", ") : "No accounts for this partner yet."}</div>
      ${p.by_fy.length ? `<h4 style="margin:10px 0 4px;">By Fiscal Year</h4><table class="compact-table no-table-tools"><thead><tr><th>Year</th>${head}</tr></thead><tbody>${p.by_fy.map(x => `<tr><td>${escapeHtml(x.fy)}</td>${t(x, p.name, ...fyRange(x.fy))}</tr>`).join("")}</tbody></table>` : ""}
      ${p.by_month.length ? `<h4 style="margin:10px 0 4px;">By Month (This Period)</h4><table class="compact-table no-table-tools"><thead><tr><th>Month</th>${head}</tr></thead><tbody>${p.by_month.map(x => `<tr><td>${monthLabel(x.month)}</td>${t(x, p.name, x.month + "-01", monthEnd(x.month))}</tr>`).join("")}</tbody></table>` : ""}
    </div>`).join("")}</div>`;
}
function partnerLines(ids) { TXF = { account_id: ids[0] }; show("txns"); }
async function partnerCell(who, start, end, what) {
  let d;
  try { d = await apiFetch(`/api/accounting/partner-lines?${new URLSearchParams({ name: who, start: start || "", end: end || "", what })}`); } catch (e) { alert(e.message); return; }
  const label = { withdrawn: "Withdrawn", invested: "Invested", net: "Net (draws)", lent: "Lent To The Company", repaid: "Repaid To Them" }[what] || what;
  const { value } = await askDialog({ title: `${who} — ${label}`, wide: true, body: `<p class="ac-help">${day(start)} – ${day(end)} · <strong>${d.count}</strong> ${d.count === 1 ? "line" : "lines"} ·
      total <strong>${fmtMoney(d.total)}</strong>. A split line counts only ${escapeHtml(who)}'s part (the line's full amount is shown beside it).</p>
    <div style="max-height:58vh;overflow:auto;"><table class="compact-table no-table-tools"><thead><tr><th>Date</th><th>Line</th><th>Account</th><th class="num">${escapeHtml(who)}'s Part</th><th class="num">Line</th></tr></thead><tbody>
    ${d.rows.map(r => `<tr><td class="nowrap">${day(r.date)}</td><td><span class="ac-who">${escapeHtml(r.payee || "")}</span><div class="ac-desc">${escapeHtml(r.description)}</div>${r.note ? `<div class="small">${icon("notePen")} ${escapeHtml(r.note)}</div>` : ""}</td>
      <td class="small">${escapeHtml(r.part_account)}${r.splits.length ? ` <span class="ac-how split">${icon("layers")}Split</span>` : ""}</td><td class="ac-amt"><strong>${money(r.part)}</strong></td>
      <td class="ac-amt muted">${r.splits.length ? fmtMoney(r.amount) : ""}</td></tr>`).join("") || `<tr><td colspan="5" class="ac-empty">No lines.</td></tr>`}</tbody>
    <tfoot><tr class="tot"><td></td><td>Total</td><td></td><td class="ac-amt"><strong>${fmtMoney(d.total)}</strong></td><td></td></tr></tfoot></table></div>`,
    buttons: [{ label: "Open In Transactions", value: "txns" }, { label: "Close", value: null }] });
  if (value === "txns") { TXF = { date_from: start, date_to: end, account_id: (META.accounts.find(a => (a.partner || "").toLowerCase() === who.toLowerCase() && a.kind === "owner" && !a.parent_id) || {}).id || "" }; saveTxf(); show("txns"); }
}

// ================================================================ Payees
async function tabPayees() {
  const rows = await apiFetch(`/api/accounting/payees?${qs()}`);
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Payees</h3><input type="search" id="py-q" placeholder="Search payees…" oninput="drawPayees()"></div>
    <p class="ac-help">Who the money went to and came from in this period, biggest first. Click one to see its lines.</p><div id="py-list"></div></div>`;
  window.PAYEES = rows; drawPayees();
}
function drawPayees() {
  const q = (document.getElementById("py-q")?.value || "").toLowerCase();
  const rows = PAYEES.filter(r => !q || r.payee.toLowerCase().includes(q));
  document.getElementById("py-list").innerHTML = `<table class="compact-table"><thead><tr><th>Payee</th><th class="num">Lines</th><th class="num">In</th><th class="num">Out</th><th class="num">Net</th><th>Months</th><th>Filed In</th><th>Last</th></tr></thead><tbody>
    ${rows.slice(0, 400).map(r => `<tr><td><a class="link" href="#" onclick='TXF={q:${escapeHtml(JSON.stringify(r.payee.replace(/ \(.*\)$/, "")))}};show("txns");return false;'>${escapeHtml(r.payee)}</a></td>
      <td class="num">${r.count}</td><td class="ac-amt">${r.in ? money(r.in) : ""}</td><td class="ac-amt">${r.out ? money(r.out) : ""}</td><td class="ac-amt"><strong>${money(r.net)}</strong></td>
      <td class="num">${r.months}</td><td class="small">${r.accounts.map(a => `${escapeHtml(a.account)} <span class="muted">×${a.count}</span>`).join("<br>")}</td><td class="nowrap">${day(r.last)}</td></tr>`).join("")
    || `<tr><td colspan="8" class="muted">No payees.</td></tr>`}</tbody>
    <tfoot><tr class="tot"><td>Total (${rows.length})</td><td class="num">${rows.reduce((a, r) => a + r.count, 0)}</td><td class="ac-amt">${money(rows.reduce((a, r) => a + r.in, 0))}</td>
      <td class="ac-amt">${money(rows.reduce((a, r) => a + r.out, 0))}</td><td class="ac-amt">${money(rows.reduce((a, r) => a + r.net, 0))}</td><td></td><td></td><td></td></tr></tfoot></table>`;
}

// ================================================================ 1099
async function tab1099() {
  const yNow = new Date(META.today).getFullYear();
  const y = +store.get("tax.year", yNow - 1);
  const d = await apiFetch(`/api/accounting/tax1099?year=${y}`);
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">1099 List</h3>
      <select onchange="store.set('tax.year', this.value);show('tax')" style="width:auto;">${[0, 1, 2, 3].map(i => yNow - i).map(v => `<option ${v === y ? "selected" : ""}>${v}</option>`).join("")}</select>
      <span style="flex:1"></span><button onclick="downloadFile('/api/accounting/tax1099/export.xlsx?year=${y}', '1099 list ${y}.xlsx').catch(e => alert(e.message))">${icon("download")} Excel For The CPA</button></div>
    <p class="ac-help">People and businesses paid $600 or more in ${y} (calendar year), from lines filed in cost of goods, expenses or other. Mark who needs a 1099 —
      card companies and corporations usually don't. The CPA makes the forms; this is the list.</p>
    <table class="compact-table"><thead><tr><th>Payee</th><th class="num">Paid In ${y}</th><th class="num">Payments</th><th>Filed In</th><th>First – Last</th><th>Needs A 1099?</th></tr></thead><tbody>
    ${d.rows.map(r => `<tr><td><strong>${escapeHtml(r.payee)}</strong></td><td class="ac-amt">${fmtMoney(r.total)}</td><td class="num">${r.count}</td><td class="small">${r.accounts.map(escapeHtml).join(", ")}</td>
      <td class="nowrap small">${day(r.first)} – ${day(r.last)}</td>
      <td class="nowrap">${["yes", "no"].map(m => `<label class="inline-check" style="margin-right:8px;"><input type="radio" name="t-${escapeHtml(r.payee)}" ${r.mark === m ? "checked" : ""} onchange='mark1099(${y}, ${escapeHtml(JSON.stringify(r.payee))}, "${m}")'> ${m === "yes" ? "Yes" : "No"}</label>`).join("")}</td></tr>`).join("")
      || `<tr><td colspan="6" class="muted">Nobody was paid $600 or more in ${y}.</td></tr>`}</tbody></table>
    <p class="muted small">Marked yes: ${fmtMoney(d.needs)} in total.</p></div>`;
}
async function mark1099(year, payee, mark) { await apiFetch("/api/accounting/tax1099/mark", { method: "POST", body: JSON.stringify({ year, payee, mark }) }); toast("Saved"); }

// ================================================================ Recurring
async function tabRecurring() {
  const rows = await apiFetch("/api/accounting/recurring");
  view().innerHTML = `<div class="card"><h3 style="margin-top:0;">Recurring</h3>
    <p class="ac-help">Payments that keep coming back — rent, loan payments, payroll, card payments — spotted from the last 15 months: how often, how much, when the next one is due.
      <span class="neg">Red</span> = looks missed (later than usual).</p>
    <table class="compact-table"><thead><tr><th>Payee</th><th>Money</th><th>How Often</th><th class="num">Amount</th><th class="num">Per Month</th><th>Times</th><th>Last</th><th>Next</th><th>Filed In</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><a class="link" href="#" onclick='TXF={q:${escapeHtml(JSON.stringify(r.payee.replace(/ \(.*\)$/, "")))}};show("txns");return false;'>${escapeHtml(r.payee)}</a></td>
      <td>${r.money === "out" ? "Out" : "In"}</td><td>${escapeHtml(r.how_often)}</td><td class="ac-amt">${fmtMoney(r.amount)}${r.same_amount ? "" : `<div class="muted small">varies</div>`}</td>
      <td class="ac-amt">${money(r.per_month)}</td><td class="num">${r.times}</td><td class="nowrap">${day(r.last)}</td>
      <td class="nowrap ${r.missed ? "neg" : ""}">${day(r.next)}${r.missed ? " · missed?" : ""}</td><td class="small">${escapeHtml(r.account || "—")}</td></tr>`).join("")
      || `<tr><td colspan="9" class="muted">Nothing regular found yet.</td></tr>`}
    </tbody>${rows.length ? `<tfoot><tr class="tot"><td>Per month (all of them)</td><td></td><td></td><td></td><td class="ac-amt">${money(rows.reduce((a, r) => a + r.per_month, 0))}</td><td></td><td></td><td></td><td></td></tr></tfoot>` : ""}</table></div>`;
}

// ================================================================ Ignored (duplicates / estimates)
async function tabIgnored() {
  const d = await apiFetch("/api/accounting/ignored");
  const possible = d.rows.filter(r => r.status === "possible"), ign = d.rows.filter(r => r.status === "ignored"), rest = d.rows.filter(r => r.status === "restored");
  const why = { exact: "already in (same date, amount, wording)", near: "looked like a line already in", estimate: "a workbook AR / AP estimate" };
  const head = `<thead><tr><th></th><th>Date</th><th>Description</th><th class="num">Amount</th><th>Why</th></tr></thead>`;
  const line = (r, tick) => `<tr>${tick ? `<td><input type="checkbox" class="ig-tick" data-id="${r.id}"></td>` : "<td></td>"}<td class="nowrap">${day(r.date)}<div class="muted small">${escapeHtml(r.source)}</div></td>
    <td>${escapeHtml(r.description || "")}${r.extra && r.extra.account ? `<div class="muted small">${escapeHtml(r.extra.account)} · ${escapeHtml(r.extra.category || "")}</div>` : ""}</td><td class="ac-amt">${money(r.amount || 0)}</td>
    <td class="small">${escapeHtml(why[r.reason] || r.reason)}${r.dup_of ? `<div class="ac-desc">vs ${day(r.dup_of.date)} · ${fmtMoney(r.dup_of.amount)} · ${escapeHtml(r.dup_of.description.slice(0, 60))}${r.dup_of.account ? ` · ${escapeHtml(r.dup_of.account)}` : ""}</div>` : ""}</td></tr>`;
  view().innerHTML = `<div class="card"><h3 style="margin-top:0;">Possible Duplicates <span class="muted small">${possible.length}</span></h3>
    <p class="ac-help">Lines from a file that look like lines already in — same amount, a few days apart, nearly the same wording. They're not in the books until you say
      <strong>Keep Both</strong>; <strong>Ignore</strong> keeps them out.</p>
    ${possible.length ? `<div class="btn-row" style="margin-bottom:6px;"><button class="small-btn" onclick="igDo('ignore', 'possible')">Ignore Ticked</button><button class="small-btn" onclick="igDo('keep', 'possible')">Keep Both (Ticked)</button>
      <button class="small-btn" onclick="document.querySelectorAll('#ig-possible .ig-tick').forEach(c => c.checked = true)">Tick All</button></div>` : ""}
    <table class="compact-table no-table-tools" id="ig-possible">${head}<tbody>${possible.map(r => line(r, true)).join("") || `<tr><td class="muted">None waiting.</td></tr>`}</tbody></table></div>
    <div class="card"><h3 style="margin-top:0;">Ignored <span class="muted small">${ign.length} · ${fmtMoney(d.total_ignored)}</span></h3>
    <p class="ac-help">Not brought in: exact repeats from overlapping files, duplicates someone ignored, workbook estimates. Wasn't a duplicate? Tick it and <strong>Restore</strong> — it goes to Review.</p>
    ${ign.length ? `<div class="btn-row" style="margin-bottom:6px;"><button class="small-btn" onclick="igDo('restore', 'ignored')">Restore Ticked</button></div>` : ""}
    <table class="compact-table" id="ig-ignored">${head}<tbody>${ign.slice(0, 500).map(r => line(r, true)).join("") || `<tr><td class="muted">Nothing ignored.</td></tr>`}</tbody></table>
    ${rest.length ? `<p class="muted small">${rest.length} restored earlier.</p>` : ""}</div>`;
}
async function igDo(action, where) {
  const ids = [...document.querySelectorAll(`#ig-${where} .ig-tick:checked`)].map(c => +c.dataset.id);
  if (!ids.length) { toast("Tick some lines"); return; }
  try { const r = await apiFetch("/api/accounting/ignored/decide", { method: "POST", body: JSON.stringify({ ids, action }) }); doneToast(`${r.done} done`, r.change_id); show("ignored"); refreshCount(); }
  catch (e) { alert(e.message); }
}

// ================================================================ Check My Books
async function tabCheck() {
  const all = store.get("check.all", false);
  view().innerHTML = `<div class="card"><p class="muted">Checking the books…</p></div>`;
  const d = await apiFetch(`/api/accounting/check?${all ? "all_dates=true" : qs()}`);
  window.CHECK = d;
  const by = {};
  d.findings.forEach(f => (by[f.check] = by[f.check] || []).push(f));
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Check My Books</h3>
      <label class="inline-check"><input type="checkbox" ${all ? "checked" : ""} onchange="store.set('check.all', this.checked);show('check')"> All dates</label><span style="flex:1"></span>
      <button onclick="show('check')">${icon("check")} Check Again</button>
      <button onclick="downloadFile('/api/accounting/check/export.xlsx?${all ? "all_dates=true" : qs()}', 'Check My Books.xlsx').catch(e => alert(e.message))">${icon("download")} Excel</button></div>
    <p class="ac-help">Lines that may be in the wrong account or look wrong, ${all ? "every date" : "in this period"}. Fix one with a click, or say <strong>It's Right</strong> and it won't be shown again.</p>
    <div class="ac-tiles">${d.counts.map(c => `<div class="ac-tile click" onclick="document.getElementById('ck-${c.check}').scrollIntoView({behavior:'smooth'})"><div class="l">${escapeHtml(c.label)}</div>
      <div class="v ${c.severity === "high" ? "neg" : ""}">${c.count}</div></div>`).join("") || `<div class="ac-tile"><div class="l">All Clear</div><div class="v pos">0</div><div class="s">nothing looks wrong</div></div>`}</div></div>
    <div class="ac-bulk" id="ck-bulk" style="display:none;position:sticky;top:0;z-index:4;"></div>
    ${Object.entries(by).map(([k, fs]) => `<div class="card" id="ck-${k}"><h3 style="margin-top:0;">${escapeHtml(fs[0].label)} <span class="muted small">${fs.length}</span></h3>
      <table class="compact-table no-table-tools"><thead><tr><th><input type="checkbox" title="Tick all in this check" onchange="this.closest('table').querySelectorAll('.ck-t').forEach(c => c.checked = this.checked);ckBulk()"></th>
        <th>Date</th><th>Line / Why</th><th>Account</th><th class="num">Amount</th><th></th></tr></thead><tbody>${fs.map(f => `<tr data-key="${escapeHtml(f.key)}"><td>${f.txn ? `<input type="checkbox" class="ck-t" data-id="${f.txn.id}" data-check="${f.check}" data-sug="${f.suggest ? f.suggest.account_id : ""}" data-amt="${f.txn.amount}" onchange="ckBulk()">` : ""}</td><td class="nowrap">${f.txn ? day(f.txn.date) : ""}</td>
        <td>${f.txn ? `<span class="ac-who">${escapeHtml(f.txn.payee || "")}</span><div class="ac-desc">${escapeHtml(f.txn.description)}</div>` : ""}<div class="small">${escapeHtml(f.why)}</div>
          ${f.also.map(x => `<div class="ac-desc">vs ${day(x.date)} · ${fmtMoney(x.amount)} · ${escapeHtml(x.description.slice(0, 60))}</div>`).join("")}</td>
        <td class="small">${f.txn ? escapeHtml(f.txn.account || "Not filed") : ""}</td><td class="ac-amt">${f.txn ? money(f.txn.amount) : ""}</td>
        <td class="nowrap">${f.txn && f.suggest ? `<button class="small-btn" onclick="ckMove(${f.txn.id}, ${f.suggest.account_id}, this)">${icon("check")} Move To ${escapeHtml(f.suggest.account)}</button>` : ""}
          ${f.txn ? `<button class="small-btn" onclick="ckBack(${f.txn.id}, this)">Send Back</button><button class="small-btn" onclick="ckOk(${f.txn.id}, '${f.check}', this)">It's Right</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`).join("")}`;
}
const ckDone = (btn, msg, changeId) => { const tr = btn.closest("tr"); tr.style.opacity = .4; tr.querySelectorAll("button").forEach(b => b.disabled = true); doneToast(msg, changeId); };
async function ckMove(id, aid, btn) { const r = await apiFetch(`/api/accounting/txns/${id}`, { method: "PUT", body: JSON.stringify({ account_id: aid, learn: false }) }); ckDone(btn, `Moved to ${acc(aid).path}`, r.change_id); }
async function ckBack(id, btn) { const r = await apiFetch("/api/accounting/txns/send-back", { method: "POST", body: JSON.stringify({ ids: [id], reason: "Check My Books" }) }); ckDone(btn, "Sent back to Review", r.change_id); refreshCount(); }
async function ckOk(id, check, btn) { const r = await apiFetch("/api/accounting/check/ok", { method: "POST", body: JSON.stringify({ txn_id: id, check }) }); ckDone(btn, "OK — won't be shown again", r.change_id); }
function ckTicked() { return [...document.querySelectorAll(".ck-t:checked")]; }
function ckBulk() {
  const el = document.getElementById("ck-bulk"); if (!el) return;
  const t = ckTicked(); el.style.display = t.length ? "" : "none";
  const sum = t.reduce((a, c) => a + (+c.dataset.amt || 0), 0);
  el.innerHTML = `<strong>${t.length} ticked</strong> <span class="small">${fmtMoney(sum)}</span>
    <select id="ck-acc" style="width:auto;max-width:240px;">${accountOptions(null, { blank: "— Move to account —" }).replace(`<option value="new">+ New account…</option>`, "")}</select><button class="small-btn" onclick="ckBulkDo('move')">Move</button>
    <button class="small-btn" onclick="ckBulkDo('suggested')" title="Each to the account the check suggests">Each To Its Suggestion</button>
    <button class="small-btn" onclick="ckBulkDo('back')">Send Back</button><button class="small-btn" onclick="ckBulkDo('ok')">It's Right</button>`;
}
async function ckBulkDo(what) {
  const t = ckTicked(); if (!t.length) return;
  const ids = [...new Set(t.map(c => +c.dataset.id))];
  try {
    if (what === "move") {
      const a = +document.getElementById("ck-acc").value; if (!a) { toast("Pick the account"); return; }
      const r = await apiFetch("/api/accounting/txns/bulk", { method: "POST", body: JSON.stringify({ ids, action: "file", account_id: a }) }); doneToast(`${r.done} moved to ${acc(a).path}`, r.change_id);
    } else if (what === "suggested") {
      const by = {}; t.forEach(c => { if (c.dataset.sug) (by[c.dataset.sug] = by[c.dataset.sug] || []).push(+c.dataset.id); });
      let n = 0; for (const [a, ids_] of Object.entries(by)) { const r = await apiFetch("/api/accounting/txns/bulk", { method: "POST", body: JSON.stringify({ ids: ids_, action: "file", account_id: +a }) }); n += r.done; }
      toast(`${n} moved to their suggested account (Recent Changes to undo)`);
    } else if (what === "back") {
      const r = await apiFetch("/api/accounting/txns/send-back", { method: "POST", body: JSON.stringify({ ids, reason: "Check My Books" }) }); doneToast(`${r.done} sent back to Review`, r.change_id);
    } else {
      for (const c of t) await apiFetch("/api/accounting/check/ok", { method: "POST", body: JSON.stringify({ txn_id: +c.dataset.id, check: c.dataset.check }) });
      toast(`${t.length} marked right`);
    }
    refreshCount(); show("check");
  } catch (e) { alert(e.message); }
}

// ================================================================ Reconcile against QuickBooks
let REC = { rows: [], res: null };
async function tabReconcile() {
  const hist = await apiFetch("/api/accounting/reconcile/history");
  const picked = new Set(store.get("rec.accounts", META.accounts.filter(a => a.kind === "owner" && !a.parent_id).map(a => a.id)));
  const kl = KIND_LABEL();
  const order = ["owner", "loan", "transfer", "income", "cogs", "expense", "other"];
  view().innerHTML = `<div class="card"><h3 style="margin-top:0;">Reconcile With QuickBooks</h3>
    <p class="ac-help">Make sure lines sit in the right accounts — partner draws especially. Pick AT-HUB's accounts on the left, paste the same accounts' lines from QuickBooks on the right
      (QuickBooks: Reports → <em>Transaction Detail by Account</em> → Export to Excel, then copy the rows — or pick the file). Each QuickBooks line is matched to AT-HUB's by amount and date.</p>
    <div class="ac-grid2"><div><h4 style="margin:0 0 6px;">AT-HUB Accounts</h4>
        <div class="rec-accs">${order.map(k => { const l = META.accounts.filter(a => a.kind === k); return l.length ? `<div class="muted small" style="margin-top:6px;">${escapeHtml(kl[k])}</div>` +
          l.map(a => `<label class="inline-check" style="display:block;padding-left:${(a.depth || 0) * 16}px;"><input type="checkbox" class="rec-acc" value="${a.id}" ${picked.has(a.id) ? "checked" : ""}> ${escapeHtml(a.name)}</label>`).join("") : ""; }).join("")}</div>
        <div class="ac-form" style="margin-top:8px;"><label>Dates</label><span>${escapeHtml(PERIOD.key && PERIOD.key !== "custom" ? PERIOD.key : PERIOD.start + " – " + PERIOD.end)} <span class="muted small">(change at the top)</span></span>
          <label>Days Apart</label><input type="number" id="rec-tol" value="${store.get("rec.tol", 5)}" min="0" max="30" style="width:80px;" title="How many days apart the same line can be in the two books"></div></div>
      <div><h4 style="margin:0 0 6px;">QuickBooks Lines</h4>
        <textarea id="rec-text" rows="10" style="width:100%;font-family:monospace;font-size:12px;" placeholder="Paste here (with the header row: Date, Transaction Type, Num, Name, Memo/Description, Amount …)"></textarea>
        <div class="btn-row" style="margin-top:6px;"><button onclick="recParse()">${icon("check")} Read Lines</button><button onclick="recFile()">${icon("upload")} Pick A File</button><span id="rec-read" class="muted small"></span></div>
        <details style="margin-top:8px;" ${REC.register ? "open" : ""}><summary class="small"><strong>QuickBooks Register</strong> (optional) — the full register / all accounts, so a line only in AT-HUB shows where QuickBooks has it</summary>
          <textarea id="rec-reg" rows="4" style="width:100%;font-family:monospace;font-size:12px;margin-top:6px;" placeholder="Paste the whole QuickBooks register here (Date, Description, Amount, Deposit, Account …)"></textarea>
          <div class="btn-row" style="margin-top:6px;"><button class="small-btn" onclick="recReg()">Read Register</button><button class="small-btn" onclick="recRegFile()">${icon("upload")} Register File</button>
            <span id="rec-reg-read" class="muted small">${REC.register ? `${REC.register.length} register line(s) loaded` : ""}</span></div></details></div></div>
    <div id="rec-map"></div><div class="btn-row" style="margin-top:10px;"><button class="primary" id="rec-run" onclick="recRun()" disabled>${icon("check")} Match Them</button></div></div>
    <div id="rec-out"></div>
    ${hist.length ? `<div class="card"><div class="ac-filters"><h4 style="margin:0;">Earlier Checks</h4><span class="muted small">${hist.length}</span><span style="flex:1"></span>
        <button class="small-btn" onclick="recDelTicked()">Delete Ticked</button>${hist.length > 1 ? `<button class="small-btn" onclick="recDelOld()">Delete All But The Newest</button>` : ""}</div>
      <table class="compact-table no-table-tools"><thead><tr><th></th><th>When</th><th>Name / Accounts</th><th>Dates</th><th class="num">Lines</th><th>Result</th><th></th></tr></thead><tbody>
      ${hist.map(h => `<tr><td><input type="checkbox" class="rh-t" value="${h.id}"></td><td class="nowrap">${fmtWhen(h.created_at)}<div class="muted small">${escapeHtml(h.created_by || "")}</div></td>
        <td class="small">${h.name ? `<strong>${escapeHtml(h.name)}</strong><br>` : ""}${h.accounts.map(escapeHtml).join(", ")}${h.register ? ` <span class="ac-chip">+ register</span>` : ""}</td>
        <td class="nowrap small">${day(h.start)} – ${day(h.end)}</td><td class="num">${h.lines}</td>
        <td class="small">${h.counts.same || 0} match · <span class="${h.counts.different ? "neg" : ""}">${h.counts.different || 0} different</span> · ${h.counts.qb_only || 0} QB only · ${h.counts.athub_only || 0} AT-HUB only</td>
        <td class="nowrap"><button class="small-btn" onclick="recLoad(${h.id})">Open</button><button class="small-btn icon-only" title="Name it" onclick="recRename(${h.id}, ${escapeHtml(JSON.stringify(h.name || ""))})">${icon("pencil")}</button>
          <button class="small-btn icon-only" title="Delete" onclick="recDel([${h.id}])">${icon("trash")}</button></td></tr>`).join("")}</tbody></table></div>` : ""}`;
}
async function recDel(ids, keepNewest = false) {
  if (!keepNewest && !ids.length) { toast("Tick some checks"); return; }
  try {
    const r = await apiFetch("/api/accounting/reconcile/delete", { method: "POST", body: JSON.stringify(keepNewest ? { keep_newest: true } : { ids }) });
    toast(`${r.deleted.length} earlier check(s) deleted`, { label: "Undo", run: async () => { await apiFetch("/api/accounting/reconcile/restore", { method: "POST", body: JSON.stringify({ ids: r.deleted }) }); show("reconcile"); } }, 10000);
    show("reconcile");
  } catch (e) { alert(e.message); }
}
function recDelTicked() { recDel([...document.querySelectorAll(".rh-t:checked")].map(c => +c.value)); }
async function recDelOld() {
  if (!(await askDialog({ title: "Delete All But The Newest?", body: "<p>Every earlier check except the newest goes from the list (Undo brings them back). What you decided about lines stays.</p>", buttons: [{ label: "Delete", value: true, cls: "danger" }, { label: "Cancel", value: null }] })).value) return;
  recDel([], true);
}
async function recRename(id, cur) {
  const { value, el } = await askDialog({ title: "Name This Check", body: `<input id="rn-n" style="width:100%;" value="${escapeHtml(cur)}" placeholder="e.g. Partner draws vs QB, Oct 2026">`, buttons: [{ label: "Save", value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  if (!value) return;
  await apiFetch(`/api/accounting/reconcile/${id}`, { method: "PUT", body: JSON.stringify({ name: el.querySelector("#rn-n").value }) }); show("reconcile");
}
async function recParse(file) {
  const fd = new FormData();
  if (file) fd.append("file", file); else fd.append("text", document.getElementById("rec-text").value);
  try {
    const r = await apiUpload("/api/accounting/reconcile/parse", fd);
    REC.rows = r.rows;
    document.getElementById("rec-read").textContent = `${r.rows.length} QuickBooks line(s) read${r.skipped ? `, ${r.skipped} skipped` : ""}.`;
    document.getElementById("rec-run").disabled = !r.rows.length;
    const names = [...new Set(r.rows.map(x => x.account))];
    document.getElementById("rec-map").innerHTML = names.length ? `<h4 style="margin:12px 0 4px;">QuickBooks Account → AT-HUB Account</h4>
      <p class="muted small" style="margin-top:0;">Guessed where it can — change any. Remembered for next time.</p>
      <table class="compact-table no-table-tools"><tbody>${names.map(n => `<tr><td>${escapeHtml(n || "(no account)")}</td><td><select class="rec-m" data-name="${escapeHtml(n)}" style="max-width:320px;">${accountOptions(null, { blank: "— not mapped (guess) —" }).replace(`<option value="new">+ New account…</option>`, "")}</select></td>
        <td class="muted small">${r.rows.filter(x => x.account === n).length} line(s)</td></tr>`).join("")}</tbody></table>` : "";
    const saved = await apiFetch("/api/accounting/reconcile/map");
    document.querySelectorAll(".rec-m").forEach(s => { const v = saved[(s.dataset.name || "").trim().toLowerCase().replace(/\s+/g, " ")]; if (v) s.value = v; });
  } catch (e) { alert(e.message); }
}
async function recReg(file) {
  const fd = new FormData();
  if (file) fd.append("file", file); else fd.append("text", document.getElementById("rec-reg").value);
  try { const r = await apiUpload("/api/accounting/reconcile/parse", fd); REC.register = r.rows; document.getElementById("rec-reg-read").textContent = `${r.rows.length} register line(s) read — used on the next Match.`; }
  catch (e) { alert(e.message); }
}
function recRegFile() { const p = Object.assign(document.createElement("input"), { type: "file", accept: ".csv,.xlsx,.xlsm,.txt" }); p.onchange = () => p.files[0] && recReg(p.files[0]); p.click(); }
function recBody(extra = {}) {
  const account_ids = [...document.querySelectorAll(".rec-acc:checked")].map(c => +c.value);
  const mapping = {}; document.querySelectorAll(".rec-m").forEach(s => { if (s.value) mapping[s.dataset.name] = +s.value; });
  return { account_ids, start: PERIOD.start, end: PERIOD.end, rows: REC.rows, mapping, tol_days: +(document.getElementById("rec-tol") || {}).value || store.get("rec.tol", 5), register_rows: REC.register || null, ...extra };
}
function recFile() { const p = Object.assign(document.createElement("input"), { type: "file", accept: ".csv,.xlsx,.xlsm,.txt" }); p.onchange = () => p.files[0] && recParse(p.files[0]); p.click(); }
async function recRun() {
  const account_ids = [...document.querySelectorAll(".rec-acc:checked")].map(c => +c.value);
  if (!account_ids.length) { alert("Pick AT-HUB accounts on the left"); return; }
  store.set("rec.accounts", account_ids);
  const tol = +document.getElementById("rec-tol").value || 0; store.set("rec.tol", tol);
  const mapping = {}; document.querySelectorAll(".rec-m").forEach(s => { if (s.value) mapping[s.dataset.name] = +s.value; });
  Object.assign(mapping, REC.extraMap || {}); REC.extraMap = null;
  try { REC.res = await apiFetch("/api/accounting/reconcile/run", { method: "POST", body: JSON.stringify({ account_ids, start: PERIOD.start, end: PERIOD.end, rows: REC.rows, mapping, tol_days: tol, register_rows: REC.register || null }) }); drawRec(); }
  catch (e) { alert(e.message); }
}
async function recLoad(id) {
  const r = await apiFetch(`/api/accounting/reconcile/${id}`);
  REC.rows = r.rows;
  REC.register = r.register_rows || null;
  if (document.getElementById("rec-reg-read")) document.getElementById("rec-reg-read").textContent = REC.register ? `${REC.register.length} register line(s) from the earlier check` : "";
  document.querySelectorAll(".rec-acc").forEach(c => c.checked = r.account_ids.includes(+c.value));
  document.getElementById("rec-read").textContent = `${r.rows.length} QuickBooks line(s) from the earlier check (dates ${r.start} – ${r.end}).`;
  document.getElementById("rec-run").disabled = false;
  recRun();
}
const REC_BUCKETS = [
  ["different", "Different Account", "QuickBooks has the line in one account, AT-HUB in another.", true],
  ["weak", "Check These Matches", "Matched on the amount but not much else (different bank references, days apart, few shared words) — make sure they're the same line.", true],
  ["split", "Split In QuickBooks", "Several QuickBooks lines add up to one AT-HUB line — QuickBooks has it split over accounts.", true],
  ["amount_differs", "Amount Differs", "The same line (same reference / same start of the text, near the date) with a different amount.", true],
  ["date_differs", "Date Differs", "The same line and amount, but further apart than the days allowed — one book has the wrong date.", true],
  ["unmapped", "QuickBooks Account Not Mapped", "Matched a line, but the QuickBooks account isn't linked to an AT-HUB account — set it in the mapping below.", false],
  ["qb_only", "Only In QuickBooks", "No AT-HUB line with that amount within the days allowed. The closest AT-HUB lines are listed — pair one if it's the same.", true],
  ["athub_only", "Only In AT-HUB", "In the picked AT-HUB accounts, but not in what QuickBooks gave.", true],
  ["same", "Same Account", "These agree.", false],
  ["decided", "Decided Earlier", "You said these are right (or not for AT-HUB) — they're left alone. Undecide to see them again.", false]];
let REC_SEL = new Set();
function recRowsOf(k) { return (REC.res[k] || []).map((r, i) => ({ k, i, r })); }
function recFlat() { return REC_BUCKETS.filter(b => b[0] !== "same" && b[0] !== "decided").flatMap(b => recRowsOf(b[0])); }
function recTxnId(k, r) { return k === "athub_only" ? r.id : r.athub ? r.athub.id : null; }
function drawRec() {
  const d = REC.res;
  REC_SEL = new Set();
  const conf = r => r.confidence ? `<span class="ac-chip ${r.confidence === "high" ? "" : r.confidence === "low" ? "flag" : "check"}" title="${escapeHtml(r.reason || "")}">${r.confidence}</span>` : "";
  const qbCell = r => `<td class="nowrap">${day(r.date)}</td><td>${escapeHtml(r.name || "")}<div class="ac-desc">${escapeHtml(r.memo || "")}</div></td>
    <td class="small">${escapeHtml(r.account || "")}${r.mapped_account ? `<div class="muted">→ ${escapeHtml(r.mapped_account)}${r.mapped_by === "guess" ? " (guess)" : ""}</div>` : ""}</td><td class="ac-amt">${fmtMoney(r.amount)}</td>`;
  const atCell = (a, part) => a ? `<td><span class="ac-who">${escapeHtml(a.payee || "")}</span> <span class="muted small">${day(a.date)}</span><div class="ac-desc">${escapeHtml(a.description.slice(0, 80))}</div></td>
    <td class="small"><strong>${escapeHtml(part ? part.account + " (part)" : a.account || (a.splits.length ? "split" : "Not filed"))}</strong></td>` : `<td class="muted">—</td><td></td>`;
  const actions = (k, r, i) => {
    const id = recTxnId(k, r), b = [];
    if (k === "split") return r.already_same ? `<span class="pos small">already split this way</span>` : r.can_split ? `<button class="small-btn primary" onclick="recSplit(${i})">Split It The Same Way</button>` : `<span class="muted small">map the accounts first</span>`;
    if (id && r.mapped_account_id && !r.agree && k !== "qb_only") b.push(`<button class="small-btn" title="Move AT-HUB's line to the account QuickBooks has" onclick="recMove([${id}], ${r.mapped_account_id})">→ ${escapeHtml(r.mapped_account.split(" › ").pop())}</button>`);
    if (id) b.push(`<button class="small-btn" onclick="recMoveTo([${id}])">Move To…</button><button class="small-btn" title="Keep the account, wait in Review with a red flag" onclick="recFlag([${id}], ${escapeHtml(JSON.stringify(r.account ? "Reconcile: QuickBooks says " + r.account : "Reconcile: not in QuickBooks"))})">Flag</button>`);
    if (k === "qb_only") b.push(`<button class="small-btn" title="Type it into AT-HUB (a book entry in QuickBooks' account)" onclick="recAdd(${i})">Add To AT-HUB</button><button class="small-btn" onclick='recDecide([{key: ${JSON.stringify(r.key)}, decision: "ignore"}])'>Ignore</button>`);
    else if (k === "athub_only") b.push(`<button class="small-btn" onclick='recDecide([{txn_id: ${r.id}, decision: "ok"}])'>OK</button>`);
    else if (k !== "decided") b.push(`<button class="small-btn" title="AT-HUB's account is right — don't show it again" onclick='recDecide([{key: ${JSON.stringify(r.key)}, decision: "athub_right", txn_id: ${id || "null"}}])'>AT-HUB Is Right</button>`);
    if (k === "decided") b.push(`<button class="small-btn" onclick='recDecide([${r.athub_only ? `{txn_id: ${r.athub.id}, key: "athub:${r.athub.id}", decision: null}` : `{key: ${JSON.stringify(r.key)}, decision: null}`}])'>Undecide</button>`);
    return b.join("");
  };
  const head = k => k === "athub_only" ? `<tr><th></th><th>Date</th><th>AT-HUB Description</th><th>AT-HUB Account</th><th class="num">Amount</th><th>Closest In QuickBooks</th><th></th></tr>`
    : `<tr><th></th><th>QB Date</th><th>QuickBooks: Name / Memo</th><th>QuickBooks Account</th><th class="num">Amount</th><th>AT-HUB: Description</th><th>AT-HUB Account</th><th class="num">Days</th><th>Match</th><th></th></tr>`;
  const rowHtml = (k, r, i) => {
    const id = recTxnId(k, r);
    const tick = id || k === "qb_only" ? `<td onclick="event.stopPropagation()"><input type="checkbox" class="rec-t" data-k="${k}" data-i="${i}" data-amt="${r.amount}" onchange="recTick()"></td>` : "<td></td>";
    if (k === "athub_only") return `<tr class="drill" onclick="recCompare('${k}', ${i})">${tick}<td class="nowrap">${day(r.date)}</td><td><span class="ac-who">${escapeHtml(r.payee || "")}</span><div class="ac-desc">${escapeHtml(r.description)}</div></td>
      <td class="small">${escapeHtml(r.account || (r.splits.length ? "split" : ""))}</td><td class="ac-amt">${money(r.amount)}</td>
      <td class="small">${r.qb_register ? (r.qb_register.missing ? `<div class="neg"><strong>Not in the QuickBooks register at all</strong></div>`
        : `<div><strong>QuickBooks has it in ${escapeHtml(r.qb_register.account)}</strong>${r.qb_register.memo ? ` — ${escapeHtml(r.qb_register.memo.slice(0, 50))}` : ""}</div>
           ${r.qb_register.mapped_account_id ? `<button class="small-btn" onclick='event.stopPropagation();recMove([${r.id}], ${r.qb_register.mapped_account_id})'>Move To ${escapeHtml((r.qb_register.mapped_account || "").split(" › ").pop())}</button>` : `<span class="muted">(that QuickBooks account isn't mapped)</span>`}`) : ""}
        ${(r.closest || []).map(c => `<div>${day(c.date)} ${fmtMoney(c.amount)} ${escapeHtml((c.name || c.memo || "").slice(0, 30))} <a class="link" href="#" onclick='event.stopPropagation();recDecide([{key: ${JSON.stringify(c.key)}, decision: "pair", txn_id: ${r.id}}]);return false;'>Pair</a></div>`).join("") || `<span class="muted">—</span>`}</td>
      <td class="nowrap" onclick="event.stopPropagation()">${actions(k, r, i)}</td></tr>`;
    if (k === "split") return `<tr class="drill" onclick="recCompare('${k}', ${i})"><td></td><td class="nowrap">${day(r.date)}</td><td>${r.parts.map(p => `<div>${escapeHtml(p.name || p.memo || "")} <span class="muted">${fmtMoney(p.amount)}</span></div>`).join("")}</td>
      <td class="small">${r.parts.map(p => `<div>${escapeHtml(p.account)}${p.mapped_account ? ` → ${escapeHtml(p.mapped_account)}` : ` <span class="neg">(not mapped)</span>`}</div>`).join("")}</td><td class="ac-amt">${fmtMoney(r.amount)}</td>
      ${atCell(r.athub)}<td></td><td><span class="ac-chip flag">split</span></td><td class="nowrap" onclick="event.stopPropagation()">${actions(k, r, i)}</td></tr>`;
    const extra = k === "amount_differs" ? `<div class="neg small">differs by ${fmtMoney(r.difference)}</div>` : k === "date_differs" ? `<div class="neg small">${r.days} days apart</div>` : "";
    const closest = k === "qb_only" && (r.closest || []).length ? `<div class="small" style="margin-top:3px;">Closest: ${r.closest.map(c => `${day(c.date)} ${fmtMoney(c.amount)} ${escapeHtml((c.description || "").slice(0, 28))}${c.taken ? " (matched elsewhere)" : ""}
      <a class="link" href="#" onclick='event.stopPropagation();recDecide([{key: ${JSON.stringify(r.key)}, decision: "pair", txn_id: ${c.id}}]);return false;'>Pair</a>`).join(" · ")}</div>` : "";
    return `<tr class="drill" onclick="recCompare('${k}', ${i})">${tick}${qbCell(r)}${r.athub ? atCell(r.athub, r.athub_part) : `<td class="muted" colspan="2">not found in AT-HUB${closest}</td>`}
      <td class="num">${r.days ?? ""}</td><td>${conf(r)}${extra}${r.agree === false ? `<div><span class="ac-chip flag">accounts differ</span></div>` : ""}</td><td class="nowrap" onclick="event.stopPropagation()">${actions(k, r, i)}</td></tr>`;
  };
  const sec = ([k, title, help, open]) => `<details class="card" id="rec-${k}" ${open && (d.counts[k] || 0) ? "open" : ""}><summary><strong>${title}</strong> <span class="muted small">${d.counts[k] || 0}</span></summary>
    <p class="ac-help" style="margin-top:6px;">${help}</p>${(d[k] || []).length ? `<div class="btn-row" style="margin-bottom:4px;"><a class="link small" href="#" onclick="document.querySelectorAll('#rec-${k} .rec-t').forEach(c => c.checked = true);recTick();return false;">Tick all in this section</a></div>` : ""}
    <table class="compact-table"><thead>${head(k)}</thead><tbody>${(d[k] || []).map((r, i) => rowHtml(k, r, i)).join("") || `<tr><td colspan="10" class="muted">None.</td></tr>`}</tbody></table></details>`;
  document.getElementById("rec-out").innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Result</h3><span style="flex:1"></span>
      <button class="small-btn primary" onclick="recFix()" title="Only what needs changing in QuickBooks, one action per line, to tick off">${icon("listTodo")} Fix In QuickBooks…</button>
      <button class="small-btn" onclick="recExport()">${icon("download")} Excel</button></div>
    <div class="ac-tiles">${REC_BUCKETS.filter(b => b[0] !== "decided" || d.counts.decided).map(([k, t]) => `<div class="ac-tile click" onclick="const x=document.getElementById('rec-${k}');x.open=true;x.scrollIntoView({behavior:'smooth'})"><div class="l">${t}</div>
      <div class="v ${["different", "weak", "split", "amount_differs", "date_differs"].includes(k) && d.counts[k] ? "neg" : k === "same" ? "pos" : ""}">${d.counts[k] || 0}</div></div>`).join("")}</div>
    <table class="compact-table no-table-tools"><thead><tr><th>Account</th><th class="num">AT-HUB</th><th class="num">QuickBooks</th><th class="num">Difference</th></tr></thead><tbody>
      ${d.totals.map(t => `<tr><td>${escapeHtml(t.account)}</td><td class="ac-amt">${fmtMoney(t.athub_abs)}</td><td class="ac-amt">${fmtMoney(t.qb_abs)}</td>
        <td class="ac-amt ${Math.abs(t.difference) > 0.009 ? "neg" : "pos"}"><strong>${fmtMoney(t.difference)}</strong></td></tr>`).join("")}</tbody></table>
    <details style="margin-top:8px;" ${d.first_bad_month ? "open" : ""}><summary><strong>Month By Month</strong> <span class="muted small">${d.first_bad_month ? `first month that disagrees: ${monthLabel(d.first_bad_month)}` : "every month agrees"}</span></summary>
      <table class="compact-table no-table-tools" style="margin-top:6px;"><thead><tr><th>Month</th><th class="num">QuickBooks</th><th class="num">AT-HUB</th><th class="num">Difference</th><th class="num">Running</th><th></th></tr></thead><tbody>
      ${d.months.map(m => `<tr ${m.month === d.first_bad_month ? 'style="background:var(--surface-3)"' : ""}><td>${monthLabel(m.month)}</td><td class="ac-amt">${fmtMoney(m.qb)} <span class="muted small">${m.qb_lines}</span></td>
        <td class="ac-amt">${fmtMoney(m.athub)} <span class="muted small">${m.athub_lines}</span></td><td class="ac-amt ${Math.abs(m.difference) > 0.009 ? "neg" : ""}">${fmtMoney(m.difference)}</td>
        <td class="ac-amt">${fmtMoney(m.running)}</td><td><a class="link small" href="#" onclick="recMonth('${m.month}');return false;">Lines</a></td></tr>`).join("")}</tbody></table></details>
    <details style="margin-top:8px;"><summary><strong>Account Mapping</strong> <span class="muted small">QuickBooks account → AT-HUB account, with both totals</span></summary>
      <table class="compact-table no-table-tools" style="margin-top:6px;"><thead><tr><th>QuickBooks Account</th><th class="num">Lines</th><th class="num">QuickBooks</th><th>AT-HUB Account</th><th class="num">AT-HUB</th><th class="num">Difference</th></tr></thead><tbody>
      ${d.mapping.map(m => `<tr><td>${escapeHtml(m.name || "(no account)")}</td><td class="num">${m.count}</td><td class="ac-amt">${fmtMoney(m.qb_total)}</td>
        <td><select class="rec-m2" data-name="${escapeHtml(m.name)}" style="max-width:260px;">${accountOptions(m.account_id, { blank: "— not mapped —" }).replace(`<option value="new">+ New account…</option>`, "")}</select>${m.by === "guess" ? ` <span class="muted small">guess</span>` : ""}</td>
        <td class="ac-amt">${m.athub_total != null ? fmtMoney(m.athub_total) : ""}</td><td class="ac-amt ${m.difference && Math.abs(m.difference) > 0.009 ? "neg" : ""}">${m.difference != null ? fmtMoney(m.difference) : ""}</td></tr>`).join("")}</tbody></table>
      <button class="small-btn" style="margin-top:6px;" onclick="recRemap()">Save Mapping &amp; Match Again</button></details></div>
    <div class="ac-bulk" id="rec-bulk" style="display:none;position:sticky;top:0;z-index:4;"></div>
    ${REC_BUCKETS.map(sec).join("")}`;
}
function recTick() {
  const t = [...document.querySelectorAll(".rec-t:checked")];
  const el = document.getElementById("rec-bulk"); if (!el) return;
  el.style.display = t.length ? "" : "none";
  const sum = t.reduce((a, c) => a + (+c.dataset.amt || 0), 0);
  el.innerHTML = `<strong>${t.length} ticked</strong> <span class="small">${fmtMoney(sum)}</span>
    <select id="rec-acc" style="width:auto;max-width:240px;">${accountOptions(null, { blank: "— Move to account —" })}</select><button class="small-btn" onclick="recBulk('move')">Move</button>
    <button class="small-btn" onclick="recBulk('qb')" title="Each AT-HUB line to the account QuickBooks has it in">Each To QuickBooks' Account</button>
    <button class="small-btn" onclick="recBulk('flag')">Flag For Review</button><button class="small-btn" onclick="recBulk('right')">AT-HUB Is Right</button>
    <button class="small-btn" onclick="recBulk('add')" title="Only-in-QuickBooks lines: type them into AT-HUB, all in one go">Add Ticked To AT-HUB</button>
    <button class="small-btn" onclick="recBulk('register')" title="Only-in-AT-HUB lines: each to the account QuickBooks' register has it in">Each To Where QuickBooks Has It</button>
    <button class="small-btn" onclick="recBulk('ok')" title="Only-in-AT-HUB lines that are fine">OK</button><button class="small-btn" onclick="document.querySelectorAll('.rec-t').forEach(c => c.checked = false);recTick()">Clear</button>`;
  const sel = document.getElementById("rec-acc");
  sel.onchange = async () => { if (sel.value === "new") await pickNew(sel, -1); };
}
function recTicked() { return [...document.querySelectorAll(".rec-t:checked")].map(c => ({ k: c.dataset.k, r: REC.res[c.dataset.k][+c.dataset.i] })); }
async function recBulk(what) {
  const t = recTicked(); if (!t.length) return;
  const ids = [...new Set(t.map(x => recTxnId(x.k, x.r)).filter(Boolean))];
  if (what === "move") { const a = +document.getElementById("rec-acc").value; if (!a) { toast("Pick the account"); return; } return recMove(ids, a); }
  if (what === "qb") {
    const by = {}; t.forEach(x => { const id = recTxnId(x.k, x.r); if (id && x.r.mapped_account_id) (by[x.r.mapped_account_id] = by[x.r.mapped_account_id] || []).push(id); });
    const changes = [];
    for (const [a, ids_] of Object.entries(by)) { const r = await apiFetch("/api/accounting/txns/bulk", { method: "POST", body: JSON.stringify({ ids: ids_, action: "file", account_id: +a }) }); if (r.change_id) changes.push(r.change_id); }
    toast(`${Object.values(by).flat().length} line(s) moved to QuickBooks' accounts`, changes.length ? { label: "Undo", run: async () => { for (const c of changes.reverse()) await apiFetch(`/api/accounting/changes/${c}/undo`, { method: "POST" }); recRun(); } } : null, 10000);
    return recRun();
  }
  if (what === "flag") return recFlag(ids, "Reconcile: check against QuickBooks");
  if (what === "add") return recAddMany(t.filter(x => x.k === "qb_only").map(x => x.r));
  if (what === "register") {
    const by = {}; t.filter(x => x.k === "athub_only" && x.r.qb_register && x.r.qb_register.mapped_account_id).forEach(x => (by[x.r.qb_register.mapped_account_id] = by[x.r.qb_register.mapped_account_id] || []).push(x.r.id));
    if (!Object.keys(by).length) { toast("None of the ticked lines has a mapped QuickBooks account in the register"); return; }
    for (const [a, ids_] of Object.entries(by)) await apiFetch("/api/accounting/txns/bulk", { method: "POST", body: JSON.stringify({ ids: ids_, action: "file", account_id: +a }) });
    toast(`${Object.values(by).flat().length} line(s) moved to where QuickBooks has them (Recent Changes to undo)`); return recRun();
  }
  if (what === "right") return recDecide(t.filter(x => x.k !== "athub_only").map(x => ({ key: x.r.key, decision: "athub_right", txn_id: recTxnId(x.k, x.r) })));
  if (what === "ok") return recDecide(t.filter(x => x.k === "athub_only").map(x => ({ txn_id: x.r.id, decision: "ok" })));
}
async function recMove(ids, aid) {
  try { const r = await apiFetch("/api/accounting/txns/bulk", { method: "POST", body: JSON.stringify({ ids, action: "file", account_id: aid }) }); doneToast(`${r.done} moved to ${acc(aid).path}`, r.change_id, recRun); recRun(); refreshCount(); }
  catch (e) { alert(e.message); }
}
async function recMoveTo(ids) {
  const { value, el } = await askDialog({ title: "Move To…", body: `<select id="rm-acc" style="width:100%;">${accountOptions(null)}</select>`, buttons: [{ label: "Move", value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  if (!value) return;
  let a = el.querySelector("#rm-acc").value;
  if (a === "new") { const x = await accountDialog(null, "expense"); if (!x) return; await loadMeta(); a = x.id; }
  if (a) recMove(ids, +a);
}
async function recFlag(ids, reason) {
  try { const r = await apiFetch("/api/accounting/txns/flag", { method: "POST", body: JSON.stringify({ ids, reason }) }); doneToast(`${r.done} flagged — waiting in Review`, r.change_id, recRun); refreshCount(); }
  catch (e) { alert(e.message); }
}
async function recDecide(items) {
  if (!items.length) return;
  try { await apiFetch("/api/accounting/reconcile/decide", { method: "POST", body: JSON.stringify({ items }) }); toast(`${items.length} remembered`); recRun(); }
  catch (e) { alert(e.message); }
}
async function recAddMany(rows) {
  if (!rows.length) { toast("Tick lines in Only In QuickBooks"); return; }
  const src = META.sources.filter(s => s.is_active);
  const unm = rows.filter(r => !r.mapped_account_id).length;
  const { value, el } = await askDialog({ title: `Add ${rows.length} ${rows.length === 1 ? "Line" : "Lines"} To AT-HUB?`, wide: true, body: `<div class="ac-form"><label>Into</label>
      <select id="ra-src">${src.map(s => `<option value="${s.id}" ${s.kind === "book" ? "selected" : ""}>${escapeHtml(s.name)}${s.kind === "book" ? " (not on a statement)" : " (expected on the statement)"}</option>`).join("")}</select></div>
    ${unm ? `<p class="neg small">${unm} line(s) have a QuickBooks account that isn't mapped — they come in not filed (Review).</p>` : ""}
    <div style="max-height:46vh;overflow:auto;"><table class="compact-table no-table-tools"><thead><tr><th>Date</th><th>QuickBooks</th><th class="num">Amount</th><th>Goes Into</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td>${day(r.date)}</td><td class="small">${escapeHtml(r.name || "")} ${escapeHtml(r.memo || "")}<div class="muted">${escapeHtml(r.account)}</div></td><td class="ac-amt">${fmtMoney(r.amount)}</td>
      <td class="small">${r.mapped_account ? escapeHtml(r.mapped_account) : `<span class="neg">not mapped</span>`}</td></tr>`).join("")}</tbody></table></div>`,
    buttons: [{ label: `Add ${rows.length}`, value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  if (!value) return;
  try { const r = await apiFetch("/api/accounting/reconcile/add", { method: "POST", body: JSON.stringify({ rows, source_id: +el.querySelector("#ra-src").value }) });
    doneToast(`${r.added} line(s) added to AT-HUB`, r.change_id, recRun); recRun(); refreshCount(); } catch (e) { alert(e.message); }
}
async function recAdd(i) {
  const ticked = recTicked().filter(x => x.k === "qb_only");
  if (ticked.length > 1 && ticked.some(x => x.r === REC.res.qb_only[i])) {
    const { value } = await askDialog({ title: "Just This One?", body: `<p>${ticked.length} lines in Only In QuickBooks are ticked. Add all of them, or just this one?</p>`,
      buttons: [{ label: `All ${ticked.length} Ticked`, value: "all", cls: "primary" }, { label: "Just This One", value: "one" }, { label: "Cancel", value: null }] });
    if (!value) return;
    if (value === "all") return recAddMany(ticked.map(x => x.r));
  }
  const r = REC.res.qb_only[i];
  const t = await addLinePrefilled({ amount: r.amount, description: [r.name, r.memo].filter(Boolean).join(" — ") || r.account, note: `from QuickBooks (${r.account})`, account_id: r.mapped_account_id, date: r.date });
  if (t) recRun();
}
async function recSplit(i) {
  const s = REC.res.split[i];
  const splits = s.parts.map(p => ({ account_id: p.mapped_account_id, amount: p.amount, note: p.name || p.memo || "" }));
  try { const r = await apiFetch(`/api/accounting/txns/${s.athub.id}`, { method: "PUT", body: JSON.stringify({ splits, learn: false }) }); doneToast("Split like QuickBooks", r.change_id, recRun); recRun(); refreshCount(); }
  catch (e) { alert(e.message); }
}
async function recRemap() {
  const mapping = {}; document.querySelectorAll(".rec-m2").forEach(s => { mapping[s.dataset.name] = s.value ? +s.value : 0; });
  document.querySelectorAll(".rec-m").forEach(s => { if (s.dataset.name in mapping) s.value = mapping[s.dataset.name] || ""; });
  REC.extraMap = mapping; recRun();
}
function recMonth(m) {
  const [y, mo] = m.split("-").map(Number), last = new Date(y, mo, 0).getDate();
  TXF = { date_from: `${m}-01`, date_to: `${m}-${String(last).padStart(2, "0")}`, account_id: (store.get("rec.accounts", []) || [])[0] || "" }; saveTxf(); show("txns");
}
async function recFix() {
  let d;
  try { d = await apiFetch("/api/accounting/reconcile/fix", { method: "POST", body: JSON.stringify(recBody()) }); } catch (e) { alert(e.message); return; }
  const by = {}; d.actions.forEach((a, i) => (by[a.kind] = by[a.kind] || []).push({ ...a, i }));
  const { value, el } = await askDialog({ title: `Fix In QuickBooks — ${d.actions.length} ${d.actions.length === 1 ? "Action" : "Actions"}`, wide: true, body: `<p class="ac-help">Everything that needs changing in QuickBooks, in plain words.
      Tick the ones you've fixed and <strong>Mark Fixed</strong> — they won't show again. The Excel has a Done column to print and work from.</p>
    <div style="max-height:56vh;overflow:auto;">${Object.entries(by).map(([k, xs]) => `<h4 style="margin:10px 0 4px;">${escapeHtml(d.labels[k] || k)} <span class="muted small">${xs.length}</span></h4>
      <table class="compact-table no-table-tools"><thead><tr><th></th><th>QuickBooks Line</th><th class="num">Amount</th><th>What To Do</th><th>AT-HUB</th></tr></thead><tbody>
      ${xs.map(a => `<tr><td>${a.key && k !== "split_athub" ? `<input type="checkbox" class="fx-t" data-key="${escapeHtml(a.key)}">` : ""}</td>
        <td class="small">${a.qb_date ? day(a.qb_date) : ""} ${escapeHtml(a.qb_name || "")} ${escapeHtml((a.qb_memo || "").slice(0, 60))}<div class="muted">${escapeHtml(a.qb_account || "")}</div></td>
        <td class="ac-amt">${a.qb_amount != null ? fmtMoney(a.qb_amount) : a.athub_amount != null ? fmtMoney(a.athub_amount) : ""}</td>
        <td class="small"><strong>${escapeHtml(a.what)}</strong>${a.why ? `<div class="muted">${escapeHtml(a.why)}</div>` : ""}</td>
        <td class="small">${a.athub_date ? day(a.athub_date) : ""} ${escapeHtml((a.athub_description || "").slice(0, 50))}<div class="muted">${escapeHtml(a.athub_account || "")}</div></td></tr>`).join("")}</tbody></table>`).join("")
      || `<p class="ac-empty">${icon("checkCircle")}Nothing to fix in QuickBooks for these accounts.</p>`}</div>`,
    buttons: [{ label: "Mark Ticked Fixed", value: "fixed", cls: "primary" }, { label: "Excel", value: "xlsx" }, { label: "Close", value: null }] });
  if (value === "xlsx") {
    try {
      const res = await fetch(`${API_BASE}/api/accounting/reconcile/fix.xlsx`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${AuthGuard.getToken()}` }, body: JSON.stringify(recBody()) });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(await res.blob()), download: `Fix In QuickBooks ${PERIOD.start} to ${PERIOD.end}.xlsx` });
      document.body.appendChild(a); a.click(); a.remove();
    } catch (e) { alert(e.message); }
  }
  if (value === "fixed") {
    const keys = [...el.querySelectorAll(".fx-t:checked")].map(c => c.dataset.key);
    if (keys.length) { await apiFetch("/api/accounting/reconcile/decide", { method: "POST", body: JSON.stringify({ items: keys.map(key => ({ key, decision: "fixed_in_qb" })) }) }); toast(`${keys.length} marked fixed in QuickBooks`); recRun(); }
  }
}
async function recExport() {
  try {
    const body = JSON.stringify(recBody({ account_ids: store.get("rec.accounts", []) }));
    const res = await fetch(`${API_BASE}/api/accounting/reconcile/export.xlsx`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${AuthGuard.getToken()}` }, body });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    const blob = await res.blob(), a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `Reconcile ${PERIOD.start} to ${PERIOD.end}.xlsx` });
    document.body.appendChild(a); a.click(); a.remove();
  } catch (e) { alert(e.message); }
}
// ---- Compare: one QuickBooks line against AT-HUB's, field by field, words told apart; Prev / Next with the arrow keys
function recWords(s) { return (s || "").toUpperCase().split(/\s+/).filter(Boolean); }
function recSameWord(a, b) {
  const clean = w => w.replace(/[^A-Z0-9*#]/g, "");
  a = clean(a); b = clean(b); if (!a || !b) return false; if (a === b) return true;
  const m = x => /^([A-WYZ#]*)X{3,}(\d+)$/.exec(x);
  for (const [x, y] of [[a, b], [b, a]]) { const mm = m(x); if (mm && y.startsWith(mm[1]) && y.endsWith(mm[2])) return true; }
  return false;
}
function recDiff(mine, other) {  // each word of mine: kept plain if the other side has it, marked if not
  const o = recWords(other);
  return recWords(mine).map(w => o.some(x => recSameWord(w, x)) ? escapeHtml(w) : `<mark title="Not on the other side">${escapeHtml(w)}</mark>`).join(" ");
}
async function recCompare(k, i) {
  const list = recFlat().concat(k === "same" || k === "decided" ? recRowsOf(k) : []);
  let pos = list.findIndex(x => x.k === k && x.i === i); if (pos < 0) return;
  const draw = async () => {
    const { k, r } = list[pos];
    const a = k === "athub_only" ? r : r.athub, qbText = k === "athub_only" ? "" : [r.name, r.memo].filter(Boolean).join(" ");
    let hist = [];
    if (a) { try { hist = (await apiFetch(`/api/accounting/txns/${a.id}/rules`)).history; } catch {} }
    const qbAcc = k === "split" ? r.parts.map(p => `${escapeHtml(p.account)} ${fmtMoney(p.amount)}${p.mapped_account ? ` → ${escapeHtml(p.mapped_account)}` : ""}`).join("<br>") : escapeHtml(r.account || "") + (r.mapped_account ? ` → ${escapeHtml(r.mapped_account)}` : "");
    const atAcc = a ? (a.splits.length ? a.splits.map(x => `${escapeHtml(x.account)} ${fmtMoney(x.amount)}`).join("<br>") : escapeHtml(a.account || "Not filed")) : "—";
    const refs = s => (s.toUpperCase().match(/[A-Z0-9]{8,}/g) || []).filter(w => /\d/.test(w) && /[A-Z]/.test(w) && !w.includes("XXX"));
    const rq = refs(qbText), ra = a ? refs(a.description) : [];
    const refTxt = rq.length && ra.length ? (rq.some(x => ra.includes(x)) ? `<span class="pos">same: ${escapeHtml(rq.filter(x => ra.includes(x)).join(", "))}</span>` : `<span class="neg">different: ${escapeHtml(rq.join(", "))} vs ${escapeHtml(ra.join(", "))}</span>`) : `<span class="muted">not on both</span>`;
    const days = a && r.date ? Math.round((new Date(a.date) - new Date(k === "athub_only" ? a.date : r.date)) / 864e5) : null;
    const row = (label, q, at, hl) => `<tr${hl ? ' style="background:var(--surface-3)"' : ""}><th style="width:120px;text-align:left;">${label}</th><td>${q}</td><td>${at}</td></tr>`;
    const body = `<div class="muted small" style="margin-bottom:6px;">${escapeHtml((REC_BUCKETS.find(b => b[0] === k) || [])[1] || "")} · ${pos + 1} of ${list.length} · ← → to walk</div>
      <table class="compact-table no-table-tools"><thead><tr><th></th><th>QuickBooks</th><th>AT-HUB</th></tr></thead><tbody>
      ${row("Date", k === "athub_only" ? "—" : day(r.date), a ? `${day(a.date)}${days ? ` <span class="${Math.abs(days) > 3 ? "neg" : "muted"}">(${days > 0 ? "+" : ""}${days} days)</span>` : ""}` : "—", days && Math.abs(days) > 0)}
      ${row("Amount", k === "athub_only" ? "—" : fmtMoney(r.amount), a ? fmtMoney(a.amount) + (k !== "athub_only" && Math.abs(Math.abs(a.amount) - Math.abs(r.amount)) > 0.005 ? ` <span class="neg">(${fmtMoney(Math.abs(r.amount) - Math.abs(a.amount))} apart)</span>` : "") : "—", r.difference)}
      ${row("Description", qbText ? recDiff(qbText, a ? a.description : "") : "—", a ? recDiff(a.description, qbText) + (a.qb_memo ? `<div class="ac-memo">QuickBooks added: ${escapeHtml(a.qb_memo)}</div>` : "") : "—")}
      ${row("Payee / Name", escapeHtml(r.name || "—"), a ? escapeHtml(a.payee || "—") : "—")}
      ${row("Account", qbAcc, atAcc, r.agree === false)}
      ${row("Memo / Note", escapeHtml(r.memo || "—"), a ? escapeHtml(a.note || "—") : "—")}
      ${row("Bank References", refTxt, "")}
      ${row("Bank Account", "", a ? escapeHtml(a.source) : "—")}
      ${row("Why Matched", r.reason ? `${escapeHtml(r.reason)} ${r.confidence ? `<span class="ac-chip">${r.confidence}</span>` : ""}` : `<span class="muted">${k === "qb_only" ? "no AT-HUB line matched" : k === "athub_only" ? "no QuickBooks line matched" : k === "split" ? "the parts add up to the AT-HUB line" : "—"}</span>`, "")}
      </tbody></table>
      ${hist.length ? `<h4 style="margin:10px 0 4px;">AT-HUB Line History</h4>${hist.map(h => `<div class="small">${fmtWhen(h.at)} · ${escapeHtml(h.by || "")} · ${escapeHtml(h.what)}${h.undone ? " (undone)" : ""}</div>`).join("")}`
        : a ? `<p class="muted small" style="margin-top:8px;">${a.created_by ? `Brought in by ${escapeHtml(a.created_by)}` : ""}${a.updated_by ? ` · last changed by ${escapeHtml(a.updated_by)}` : ""}${a.how ? ` · filed by ${escapeHtml(a.how)}` : ""}</p>` : ""}
`;
    const btns = [{ label: "← Prev", value: "prev" }, { label: "Next →", value: "next" }];
    const id = a && a.id;
    if (k === "split" && r.can_split && !r.already_same) btns.unshift({ label: "Split It The Same Way", value: "split", cls: "primary" });
    if (id && r.mapped_account_id && r.agree === false) btns.unshift({ label: `Move To ${r.mapped_account.split(" › ").pop()}`, value: "qb", cls: "primary" });
    if (id) btns.push({ label: "Move To…", value: "move" }, { label: "Flag For Review", value: "flag" });
    if (k !== "athub_only" && k !== "qb_only" && k !== "decided") btns.push({ label: "AT-HUB Is Right", value: "right" });
    if (k === "qb_only") btns.push({ label: "Add To AT-HUB", value: "add" });
    btns.push({ label: "Close", value: null });
    const dlg = askDialog({ title: "Compare", body, buttons: btns, wide: true });
    const onKey = e => { const b = [...document.querySelectorAll(".ask-dialog button")]; if (e.key === "ArrowRight") b.find(x => x.textContent === "Next →")?.click(); if (e.key === "ArrowLeft") b.find(x => x.textContent === "← Prev")?.click(); };
    document.addEventListener("keydown", onKey);
    const { value } = await dlg;
    document.removeEventListener("keydown", onKey);
    if (value === "prev") { pos = (pos - 1 + list.length) % list.length; return draw(); }
    if (value === "next") { pos = (pos + 1) % list.length; return draw(); }
    if (value === "split") return recSplit(list[pos].i);
    if (value === "qb") return recMove([id], r.mapped_account_id);
    if (value === "move") return recMoveTo([id]);
    if (value === "flag") return recFlag([id], r.account ? "Reconcile: QuickBooks says " + r.account : "Reconcile: check against QuickBooks");
    if (value === "right") return recDecide([{ key: r.key, decision: "athub_right", txn_id: id || null }]);
    if (value === "add") return recAdd(list[pos].i);
  };
  draw();
}

// ================================================================ a small formula engine (Projections, Loans quick calc) -- no eval
// numbers, + - * / ^ ( ), 10%, R3 (row 3's value), SUM / MIN / MAX / AVG (R2:R8, R3, 5 ...), GROUP("Cash In Bank")
function makeCalc(rows) {
  const cache = {}, busy = new Set();
  const val = i => {
    if (i in cache) return cache[i];
    if (busy.has(i)) throw new Error(`R${i} refers to itself`);
    busy.add(i);
    const r = rows[i - 1];
    let v = 0;
    if (r) { const a = String(r.a ?? "").trim(); v = a.startsWith("=") ? parse(a.slice(1)) : (parseFloat(a.replace(/[$,]/g, "")) || 0); }
    busy.delete(i);
    return (cache[i] = v);
  };
  const group = g => rows.reduce((s, r, k) => s + ((r.group || "").toLowerCase() === g.toLowerCase() ? val(k + 1) : 0), 0);
  function parse(src) {
    let i = 0;
    const peek = () => src[i], skip = () => { while (src[i] === " ") i++; };
    const expr = () => { let v = term(); for (;;) { skip(); if (peek() === "+") { i++; v += term(); } else if (peek() === "-") { i++; v -= term(); } else return v; } };
    const term = () => { let v = pow(); for (;;) { skip(); if (peek() === "*") { i++; v *= pow(); } else if (peek() === "/") { i++; const d = pow(); v = d ? v / d : 0; } else return v; } };
    const pow = () => { let v = unary(); skip(); if (peek() === "^") { i++; v = Math.pow(v, unary()); } return v; };
    const unary = () => { skip(); if (peek() === "-") { i++; return -unary(); } if (peek() === "+") { i++; return unary(); } return atom(); };
    const args = () => {
      const out = [];
      skip(); if (peek() === ")") { i++; return out; }
      for (;;) {
        skip();
        const m = /^R(\d+):R(\d+)/i.exec(src.slice(i));
        if (m) { i += m[0].length; for (let k = +m[1]; k <= +m[2]; k++) out.push(val(k)); }
        else out.push(expr());
        skip(); if (peek() === ",") { i++; continue; } if (peek() === ")") { i++; return out; } throw new Error("missing )");
      }
    };
    const atom = () => {
      skip();
      if (peek() === "(") { i++; const v = expr(); skip(); if (peek() !== ")") throw new Error("missing )"); i++; return v; }
      let m = /^GROUP\("([^"]*)"\)/i.exec(src.slice(i));
      if (m) { i += m[0].length; return group(m[1]); }
      m = /^(SUM|MIN|MAX|AVG)\(/i.exec(src.slice(i));
      if (m) { i += m[0].length; const a = args(); const f = m[1].toUpperCase(); return !a.length ? 0 : f === "SUM" ? a.reduce((x, y) => x + y, 0) : f === "MIN" ? Math.min(...a) : f === "MAX" ? Math.max(...a) : a.reduce((x, y) => x + y, 0) / a.length; }
      m = /^R(\d+)/i.exec(src.slice(i));
      if (m) { i += m[0].length; return val(+m[1]); }
      m = /^\$?[\d,]*\.?\d+(%?)/.exec(src.slice(i));
      if (m) { i += m[0].length; const n = parseFloat(m[0].replace(/[$,%]/g, "")); return m[1] ? n / 100 : n; }
      throw new Error(`can't read "${src.slice(i, i + 8)}"`);
    };
    const v = expr(); skip();
    if (i < src.length) throw new Error(`can't read "${src.slice(i, i + 8)}"`);
    return v;
  }
  return { val, group };
}

// ================================================================ Projections (scratch -- never the books)
const PJ_GROUPS = ["Cash In Bank", "Customers Owe Us (AR)", "Vendors We Owe (AP)", "Loans We Owe", "Loans Owed To Us", "Expected Money In", "Expected Money Out", "Partner Draws", "Other"];
const PJ_OLD = { "AR Owed To Us": "Customers Owe Us (AR)", "AP We Owe": "Vendors We Owe (AP)", "Loans Owed": "Loans We Owe" };
const PJ_OWE = new Set(["Vendors We Owe (AP)", "Loans We Owe", "Expected Money Out", "Partner Draws"]);  // money going out: always shown red
const PJ_IN = new Set(["Cash In Bank", "Customers Owe Us (AR)", "Loans Owed To Us", "Expected Money In"]);
function pjMigrate(doc) {  // the old group names (and GROUP("...") in formulas) read as the new ones
  (doc.rows || []).forEach(r => {
    if (PJ_OLD[r.group]) r.group = PJ_OLD[r.group];
    if (typeof r.a === "string") r.a = r.a.replace(/GROUP\("([^"]*)"\)/gi, (m, g) => PJ_OLD[g] ? `GROUP("${PJ_OLD[g]}")` : m);
  });
}
function pjGroups(doc) { return [...PJ_GROUPS, ...new Set((doc.rows || []).map(r => r.group).filter(g => g && !PJ_GROUPS.includes(g)))]; }
let PJ = { list: [], cur: null, dirty: false };
async function tabProjections() {
  PJ.list = await apiFetch("/api/accounting/sheets?kind=projection");
  if (!PJ.list.length) PJ.list = [await apiFetch("/api/accounting/sheets", { method: "POST", body: JSON.stringify({ kind: "projection", name: "Where We Stand", doc: { rows: [], settings: { months: 6, ar_pct: 90, ar_months: 2, ap_months: 2, extra_loan: 0 } } }) })];
  const want = +store.get("pj.cur", 0);
  PJ.cur = PJ.list.find(x => x.id === want) || PJ.list[0];
  drawProjections();
}
function pjSettings() { return Object.assign({ months: 6, ar_pct: 90, ar_months: 2, ap_months: 2, extra_loan: 0 }, PJ.cur.doc.settings || {}); }
function drawProjections() {
  const doc = PJ.cur.doc; doc.rows = doc.rows || [];
  pjMigrate(doc);
  const S = pjSettings();
  const calc = makeCalc(doc.rows);
  const vals = doc.rows.map((r, k) => { try { return { v: calc.val(k + 1) }; } catch (e) { return { v: 0, err: e.message }; } });
  const g = name => doc.rows.reduce((s, r, k) => s + ((r.group || "") === name ? vals[k].v : 0), 0);
  const cash = g("Cash In Bank"), ar = Math.abs(g("Customers Owe Us (AR)")) * S.ar_pct / 100, ap = Math.abs(g("Vendors We Owe (AP)")), loans = Math.abs(g("Loans We Owe"));
  const lent = Math.abs(g("Loans Owed To Us"));
  const expIn = Math.abs(g("Expected Money In")), expOut = Math.abs(g("Expected Money Out")), draws = Math.abs(g("Partner Draws"));
  const custom = pjGroups(doc).filter(x => !PJ_GROUPS.includes(x));
  const other = g("Other") + custom.reduce((a, x) => a + g(x), 0);
  const net = cash + ar + lent - ap - loans + expIn - expOut - draws + other;
  const partners = (META.settings.partners || []);
  // month-by-month cash: rows with a month land in that month, without one every month (Expected In / Out) or spread (AR / AP)
  const months = [], start = new Date(META.today + "T00:00:00");
  for (let k = 0; k < S.months; k++) { const d = new Date(start.getFullYear(), start.getMonth() + k, 1); months.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`); }
  let run = cash, low = null;
  const fc = months.map((m, k) => {
    let inn = 0, out = 0;
    doc.rows.forEach((r, j) => {
      const v = vals[j].v, has = r.month && /^\d{4}-\d\d$/.test(r.month);
      if (has && r.month !== m) return;
      if (r.group === "Expected Money In") inn += Math.abs(v);
      else if (r.group === "Expected Money Out" || r.group === "Partner Draws") out += Math.abs(v) * (has ? 1 : (r.group === "Partner Draws" ? 0 : 1));
      else if (r.group === "Customers Owe Us (AR)" && (has || k < S.ar_months)) inn += Math.abs(v) * S.ar_pct / 100 / (has ? 1 : S.ar_months);
      else if (r.group === "Vendors We Owe (AP)" && (has || k < S.ap_months)) out += Math.abs(v) / (has ? 1 : S.ap_months);
      else if ((r.group === "Loans Owed To Us" || r.group === "Loans We Owe") && has) (r.group === "Loans Owed To Us" ? inn += Math.abs(v) : out += Math.abs(v));
    });
    out += +S.extra_loan || 0;
    run += inn - out;
    const row = { m, inn, out, bal: run };
    if (!low || run < low.bal) low = row;
    return row;
  });
  const maxB = Math.max(1, ...fc.map(x => Math.abs(x.bal)));
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Projections</h3>
      <select onchange="store.set('pj.cur', this.value);show('projections')" style="width:auto;">${PJ.list.map(x => `<option value="${x.id}" ${x.id === PJ.cur.id ? "selected" : ""}>${escapeHtml(x.name)}</option>`).join("")}</select>
      <button class="small-btn" onclick="pjNew()">${icon("plus")} New</button><button class="small-btn" onclick="pjCopy()">Copy</button><button class="small-btn" onclick="pjRename()">Rename</button>
      <button class="small-btn" onclick="pjCompare()">Compare</button><button class="small-btn" onclick="pjDelete()">${icon("trash")}</button><span style="flex:1"></span>
      <button class="small-btn" onclick="downloadFile('/api/accounting/sheets/${PJ.cur.id}/export.xlsx', '${escapeHtml(PJ.cur.name)}.xlsx').catch(e => alert(e.message))">${icon("download")} Excel</button>
      <button id="pj-save" class="small-btn primary" onclick="pjSave()" ${PJ.dirty ? "" : "disabled"}>${icon("save")} Save</button></div>
    <p class="ac-help">A scratch sheet for "where would we stand" — <strong>not the books</strong> (nothing here touches P&L, Partners or the Excel). Amounts can be numbers or formulas:
      <code>=R3*0.9</code>, <code>=SUM(R2:R8)</code>, <code>=GROUP("Customers Owe Us (AR)")</code>, <code>=R4*15%</code>. Give a row a month (2026-12) to put it in that month of the forecast.
      Money we owe or pay out shows <span class="neg">red</span> however it's typed; money coming in <span class="pos">green</span>. Type a new name in Group to make your own group (counted as typed: + in, − out).</p>
    <div class="btn-row" style="margin-bottom:8px;"><span class="muted small">Fill from the books:</span>
      ${[["banks", "Bank Balances"], ["loans", "Loan Balances"], ["ar", "AR"], ["ap", "AP"], ["monthly", "Monthly Averages"], ["recurring", "Recurring Payments"]].map(([k, l]) => `<button class="small-btn" onclick="pjFill('${k}')">${l}</button>`).join("")}</div>
    <table class="compact-table no-table-tools pj-grid"><thead><tr><th>#</th><th>Group</th><th>Name</th><th>Amount / Formula</th><th class="num">Value</th><th>Month</th><th>Note</th><th></th></tr></thead><tbody>
    ${doc.rows.map((r, k) => `<tr><td class="muted">R${k + 1}</td>
      <td><select onchange="pjPickGroup(${k}, this)">${pjGroups(doc).map(x => `<option ${x === r.group ? "selected" : ""}>${escapeHtml(x)}</option>`).join("")}<option value="__new">+ New Group…</option></select></td>
      <td><input value="${escapeHtml(r.name || "")}" onchange="pjSet(${k}, 'name', this.value)" placeholder="${r.group === "Vendors We Owe (AP)" ? "vendor" : r.group === "Customers Owe Us (AR)" ? "customer" : ""}"></td>
      <td><input value="${escapeHtml(r.a ?? "")}" onchange="pjSet(${k}, 'a', this.value)" style="font-family:monospace;"></td>
      <td class="ac-amt">${vals[k].err ? `<span class="neg small" title="${escapeHtml(vals[k].err)}">error</span>` : pjMoney(r.group, vals[k].v)}</td>
      <td><input value="${escapeHtml(r.month || "")}" placeholder="any" onchange="pjSet(${k}, 'month', this.value)" style="width:86px;"></td>
      <td><input value="${escapeHtml(r.note || "")}" onchange="pjSet(${k}, 'note', this.value)"></td>
      <td class="nowrap"><button class="small-btn icon-only" title="Make it real: a typed-in line in the books" onclick="pjReal(${k})">${icon("check")}</button>
        <button class="small-btn icon-only" title="Remove row" onclick="pjDel(${k})">${icon("x")}</button></td></tr>`).join("")}</tbody></table>
    <datalist id="pj-groups">${pjGroups(doc).map(x => `<option value="${escapeHtml(x)}">`).join("")}</datalist>
    <div class="btn-row" style="margin-top:6px;"><button class="small-btn" onclick="pjAdd()">${icon("plus")} Row</button></div>
    ${doc.rows.some(r => r.group === "Customers Owe Us (AR)") ? `<p class="ac-help" style="margin-top:8px;">AR is counted at the <strong>AR Collected</strong> % (${S.ar_pct}% now — change it under What If).
      If you know the amount and the month a customer will pay, put it under <em>Expected Money In</em> with the month instead.</p>` : ""}</div>
    <div class="ac-grid2"><div class="card"><h3 style="margin-top:0;">Where We'd Stand</h3>
      <table class="ac-steps" style="width:100%;"><tbody>
        <tr><td>Cash in bank</td><td class="ac-amt">${money(cash)}</td></tr>
        <tr><td>+ Customers owe us — AR (${S.ar_pct}% collected)</td><td class="ac-amt">${money(ar)}</td></tr>
        ${lent ? `<tr><td>+ Loans owed to us</td><td class="ac-amt">${money(lent)}</td></tr>` : ""}
        <tr><td>− Vendors we owe — AP</td><td class="ac-amt">${money(-ap)}</td></tr>
        <tr><td>− Loans we owe (to lenders)</td><td class="ac-amt">${money(-loans)}</td></tr>
        <tr><td>+ Expected money in − out</td><td class="ac-amt">${money(expIn - expOut)}</td></tr>
        <tr><td>− Partner draws planned</td><td class="ac-amt">${money(-draws)}</td></tr>
        ${other ? `<tr><td>Other${custom.length ? " + your groups" : ""}</td><td class="ac-amt">${money(other)}</td></tr>` : ""}
        <tr class="total"><td>Net Position</td><td class="ac-amt">${money(net)}</td></tr></tbody></table>
      ${partners.length ? `<h4 style="margin:12px 0 4px;">If We Shared It</h4><table class="compact-table no-table-tools"><tbody>${partners.map(p => `<tr><td>${escapeHtml(p.name)} <span class="muted small">${(p.share * 100).toFixed(1)}%</span></td><td class="ac-amt">${money(net * p.share)}</td></tr>`).join("")}</tbody></table>` : ""}
      <h4 style="margin:12px 0 4px;">What If</h4>
      <div class="ac-form"><label>AR Collected</label><div><input type="range" min="0" max="100" value="${S.ar_pct}" oninput="pjSetS('ar_pct', +this.value)"> ${S.ar_pct}%</div>
        <label>AR Comes In Over</label><div><input type="number" min="1" max="24" value="${S.ar_months}" onchange="pjSetS('ar_months', Math.max(1, +this.value))" style="width:70px;"> month(s)</div>
        <label>AP Paid Over</label><div><input type="number" min="1" max="24" value="${S.ap_months}" onchange="pjSetS('ap_months', Math.max(1, +this.value))" style="width:70px;"> month(s)</div>
        <label>Extra Loan Payment</label><div><input type="number" step="100" value="${S.extra_loan}" onchange="pjSetS('extra_loan', +this.value || 0)" style="width:110px;"> a month</div>
        <label>Forecast</label><div><input type="number" min="1" max="36" value="${S.months}" onchange="pjSetS('months', Math.min(36, Math.max(1, +this.value)))" style="width:70px;"> month(s)</div></div></div>
      <div class="card"><h3 style="margin-top:0;">Cash Month By Month</h3>
        <div class="ac-bars" style="height:120px;">${fc.map(x => `<div class="m" title="${monthLabel(x.m)}: ${fmtMoney(x.bal)}"><div class="pair"><div class="b ${x.bal >= 0 ? "in" : "out"}" style="height:${(Math.abs(x.bal) / maxB * 100).toFixed(1)}%;${low && x.m === low.m ? "outline:2px solid #dc2626;" : ""}"></div></div></div>`).join("")}</div>
        <div class="ac-bars-lab">${fc.map(x => `<span>${monthLabel(x.m).split(" ")[0]}</span>`).join("")}</div>
        <table class="compact-table no-table-tools" style="margin-top:8px;"><thead><tr><th>Month</th><th class="num">In</th><th class="num">Out</th><th class="num">Cash After</th></tr></thead><tbody>
        ${fc.map(x => `<tr ${low && x.m === low.m ? 'class="neg"' : ""}><td>${monthLabel(x.m)}${low && x.m === low.m ? " · lowest" : ""}</td><td class="ac-amt">${money(x.inn)}</td><td class="ac-amt">${money(-x.out)}</td><td class="ac-amt"><strong>${money(x.bal)}</strong></td></tr>`).join("")}</tbody></table></div></div>`;
  commentsCard("projections");
}
function pjDirty() { PJ.dirty = true; drawProjections(); }
function pjSet(k, f, v) { PJ.cur.doc.rows[k][f] = v; pjDirty(); }
async function pjPickGroup(k, sel) {
  if (sel.value !== "__new") return pjSetGroup(k, sel.value);
  const { value, el } = await askDialog({ title: "New Group", body: `<input id="pj-ng" style="width:100%;" placeholder="e.g. Equipment, Taxes, Customer Deposits">
    <p class="muted small">Counted as typed: + money in, − money out.</p>`, buttons: [{ label: "Add", value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  const v = value ? el.querySelector("#pj-ng").value.trim() : "";
  if (v) pjSetGroup(k, v); else drawProjections();
}
function pjSetGroup(k, v) {
  v = (v || "").trim() || "Other";
  PJ.cur.doc.rows[k].group = PJ_GROUPS.find(x => x.toLowerCase() === v.toLowerCase()) || v;
  if (PJ.cur.doc.rows[k].group === "Customers Owe Us (AR)") toast(`AR is counted at ${pjSettings().ar_pct}% (AR Collected, under What If). Know the amount and month? Use Expected Money In.`, null, 7000);
  pjDirty();
}
function pjMoney(group, v) {  // owed / going out: red whatever the sign typed; coming in: green
  if (PJ_OWE.has(group)) return `<span class="neg">${fmtMoney(-Math.abs(v))}</span>`;
  if (PJ_IN.has(group)) return `<span class="pos">${fmtMoney(Math.abs(v))}</span>`;
  return money(v);
}
function pjSetS(k, v) { PJ.cur.doc.settings = { ...pjSettings(), [k]: v }; pjDirty(); }
function pjAdd(row) { PJ.cur.doc.rows.push(row || { group: "Other", name: "", a: "" }); pjDirty(); }
function pjDel(k) { PJ.cur.doc.rows.splice(k, 1); pjDirty(); }
async function pjSave() { PJ.cur = await apiFetch(`/api/accounting/sheets/${PJ.cur.id}`, { method: "PUT", body: JSON.stringify({ doc: PJ.cur.doc }) }); PJ.dirty = false; toast("Saved"); drawProjections(); }
async function pjNew() { const x = await apiFetch("/api/accounting/sheets", { method: "POST", body: JSON.stringify({ kind: "projection", name: "New Projection", doc: { rows: [] } }) }); store.set("pj.cur", x.id); show("projections"); }
async function pjCopy() { if (PJ.dirty) await pjSave(); const x = await apiFetch(`/api/accounting/sheets/${PJ.cur.id}/copy`, { method: "POST" }); store.set("pj.cur", x.id); show("projections"); }
async function pjRename() { const { value, el } = await askDialog({ title: "Rename", body: `<input id="pj-n" value="${escapeHtml(PJ.cur.name)}" style="width:100%;">`, buttons: [{ label: "Save", value: true, cls: "primary" }, { label: "Cancel", value: null }] }); if (!value) return; PJ.cur = await apiFetch(`/api/accounting/sheets/${PJ.cur.id}`, { method: "PUT", body: JSON.stringify({ name: el.querySelector("#pj-n").value }) }); show("projections"); }
async function pjDelete() { if (!(await askDialog({ title: `Delete "${PJ.cur.name}"?`, body: "", buttons: [{ label: "Delete", value: true, cls: "danger" }, { label: "Cancel", value: null }] })).value) return; await apiFetch(`/api/accounting/sheets/${PJ.cur.id}`, { method: "DELETE" }); store.set("pj.cur", 0); show("projections"); }
async function pjFill(what) {
  const b = await apiFetch("/api/accounting/sheets-books?months=6");
  const add = rows => { rows.forEach(r => PJ.cur.doc.rows.push(r)); pjDirty(); toast(`${rows.length} row(s) added — edit them as you like`); };
  if (what === "banks") add(b.banks.map(x => ({ group: "Cash In Bank", name: x.name, a: x.balance != null ? String(x.balance) : "", note: x.has_opening ? "from the books" : "set an opening balance on Statements, or type it" })));
  if (what === "loans") add(b.loans.map(x => ({ group: x.owed >= 0 ? "Loans We Owe" : "Loans Owed To Us", name: x.name, a: String(Math.abs(x.owed)), note: "from Loans" })));
  if (what === "ar") add(b.ar.map(x => ({ group: "Customers Owe Us (AR)", name: x.name, a: String(x.amount), note: "AT-HUB, today" })));
  if (what === "ap") add(b.ap.map(x => ({ group: "Vendors We Owe (AP)", name: x.name, a: String(x.amount), note: "AT-HUB, today" })));
  if (what === "monthly") add([{ group: "Expected Money In", name: "Average monthly income", a: String(b.monthly.income), note: `${b.monthly.months} months to ${b.monthly.to}` },
    { group: "Expected Money Out", name: "Average monthly cost of goods", a: String(Math.abs(b.monthly.cogs)), note: `${b.monthly.months} months` },
    { group: "Expected Money Out", name: "Average monthly expenses", a: String(Math.abs(b.monthly.expense)), note: `${b.monthly.months} months` }]);
  if (what === "recurring") add(b.recurring.map(x => ({ group: "Expected Money Out", name: `${x.payee} (${x.how_often})`, a: String(Math.abs(x.per_month)), note: "a month, from Recurring" })));
}
async function pjReal(k) {
  const r = PJ.cur.doc.rows[k];
  const calc = makeCalc(PJ.cur.doc.rows);
  let v = 0; try { v = calc.val(k + 1); } catch (e) { alert(e.message); return; }
  const isAr = r.group === "Customers Owe Us (AR)", isAp = r.group === "Vendors We Owe (AP)";
  if (isAr || isAp) {
    if (!(await askDialog({ title: "Make It Real?", body: `<p>Add <strong>${escapeHtml(r.name || r.group)}</strong> ${fmtMoney(Math.abs(v))} to AP / AR as a typed-in item?</p>`, buttons: [{ label: "Add", value: true, cls: "primary" }, { label: "Cancel", value: null }] })).value) return;
    await apiFetch("/api/accounting/open-items", { method: "POST", body: JSON.stringify({ source: "manual", side: isAr ? "ar" : "ap", party: r.name || r.group, amount: Math.abs(v), note: r.note || "from Projections" }) });
    toast("Added to AP / AR"); return;
  }
  const out = PJ_OWE.has(r.group);
  await addLinePrefilled({ amount: (out ? -1 : 1) * Math.abs(v), description: r.name || r.group, note: r.note || "from Projections", date: r.month && /^\d{4}-\d\d$/.test(r.month) ? r.month + "-01" : null });
}
async function pjCompare() {
  if (PJ.list.length < 2) { toast("Make a second projection (Copy) to compare"); return; }
  const opts = PJ.list.map(x => `<option value="${x.id}">${escapeHtml(x.name)}</option>`).join("");
  const { value, el } = await askDialog({ title: "Compare Two", body: `<div class="ac-form"><label>This</label><select id="c1">${opts}</select><label>With</label><select id="c2">${opts}</select></div>`, buttons: [{ label: "Compare", value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  if (!value) return;
  const a = PJ.list.find(x => x.id === +el.querySelector("#c1").value), b = PJ.list.find(x => x.id === +el.querySelector("#c2").value);
  const tot = s => { pjMigrate(s.doc); const rows = s.doc.rows || [], c = makeCalc(rows); const o = {}; rows.forEach((r, k) => { let v = 0; try { v = c.val(k + 1); } catch {} o[r.group] = (o[r.group] || 0) + v; }); return o; };
  const ta = tot(a), tb = tot(b);
  await askDialog({ title: `${a.name} vs ${b.name}`, body: `<table class="compact-table no-table-tools"><thead><tr><th>Group</th><th class="num">${escapeHtml(a.name)}</th><th class="num">${escapeHtml(b.name)}</th><th class="num">Difference</th></tr></thead><tbody>
    ${[...new Set([...pjGroups(a.doc), ...pjGroups(b.doc)])].filter(g => ta[g] || tb[g]).map(g => `<tr><td>${g}</td><td class="ac-amt">${money(ta[g] || 0)}</td><td class="ac-amt">${money(tb[g] || 0)}</td><td class="ac-amt">${money((ta[g] || 0) - (tb[g] || 0))}</td></tr>`).join("")}</tbody></table>`, buttons: [{ label: "Close", value: null }], wide: true });
}
// a typed-in line with some of it filled in (Projections / Loans quick calc "Make It Real")
async function addLinePrefilled(pre) {
  const banks = META.sources.filter(s => s.is_active), book = banks.find(s => s.kind === "book");
  const body = `<p class="ac-help">Into the books: on a bank account it's an expected line (matched when the statement brings it), or a book entry that never will be on a statement.</p>
    <div class="ac-form"><label>Where</label><select id="ap-src">${banks.map(s => `<option value="${s.id}" ${book && s.id === book.id ? "selected" : ""}>${escapeHtml(s.name)}${s.kind === "book" ? " (not on a statement)" : ""}</option>`).join("")}</select>
      <label>Date</label><input type="date" id="ap-date" value="${pre.date || META.today}">
      <label>Amount</label><input type="number" step="0.01" id="ap-amt" value="${pre.amount}" title="+ money in, − money out">
      <label>What</label><input id="ap-desc" value="${escapeHtml(pre.description || "")}">
      <label>Account</label><select id="ap-acc">${accountOptions(pre.account_id || null)}</select>
      <label>Note</label><input id="ap-note" value="${escapeHtml(pre.note || "")}"></div>`;
  const p = askDialog({ title: "Make It Real", body, buttons: [{ label: "Add To The Books", value: true, cls: "primary" }, { label: "Cancel", value: null }], wide: true });
  setTimeout(() => { const s = document.getElementById("ap-acc"); if (s) s.onchange = async () => { if (s.value === "new") await pickNew(s, +document.getElementById("ap-amt").value); }; }, 0);
  const { value, el } = await p;
  if (!value) return null;
  const q = s => el.querySelector(s), src = META.sources.find(s => s.id === +q("#ap-src").value), a = q("#ap-acc").value;
  try {
    const t = await apiFetch("/api/accounting/txns", { method: "POST", body: JSON.stringify({ source_id: src.id, date: q("#ap-date").value, amount: parseFloat(q("#ap-amt").value), description: q("#ap-desc").value,
      account_id: a && a !== "new" ? +a : null, note: q("#ap-note").value, expected: src.kind !== "book" }) });
    toast("Added to the books"); refreshCount(); return t;
  } catch (e) { alert(e.message); return null; }
}

// ================================================================ Loans: quick calculations (scratch, per person)
let LC = null;
async function loansCalcCard(loanRows) {
  const box = document.createElement("div");
  box.className = "card";
  box.id = "lc-card";
  view().appendChild(box);
  const list = await apiFetch("/api/accounting/sheets?kind=loancalc");
  LC = list[0] || await apiFetch("/api/accounting/sheets", { method: "POST", body: JSON.stringify({ kind: "loancalc", doc: { rows: [] } }) });
  LC.loans = loanRows;
  drawLoansCalc();
}
function drawLoansCalc() {
  const box = document.getElementById("lc-card"); if (!box) return;
  const rows = LC.doc.rows = LC.doc.rows || [];
  const owed = {}; LC.loans.forEach(l => owed[l.name] = l.owed);
  const today = new Date(META.today + "T00:00:00");
  const interest = r => { if (!r.rate || !r.date || r.added) return 0; const days = Math.max(0, (today - new Date(r.date + "T00:00:00")) / 864e5); return Math.abs(+r.amount || 0) * (+r.rate / 100) * days / 365 * (r.kind === "borrow" ? 1 : 0); };
  const byL = {};
  rows.forEach(r => { const k = r.lender || "?"; byL[k] = byL[k] || { add: 0, int: 0 }; if (!r.added) byL[k].add += (r.kind === "borrow" ? 1 : -1) * Math.abs(+r.amount || 0); byL[k].int += interest(r); });
  const lenders = [...new Set([...LC.loans.map(l => l.name), ...rows.map(r => r.lender).filter(Boolean)])];
  box.innerHTML = `<div class="ac-filters"><h3 style="margin:0;">Quick Calculations</h3><span class="muted small">scratch — not the books, kept for you</span><span style="flex:1"></span>
      <button class="small-btn" onclick="lcPayoff()">Payoff Calculator</button><button class="small-btn" onclick="lcClear()">Clear All</button></div>
    <p class="ac-help">Try "what if we borrow / repay this". Totals below are owed today (from the books) plus these rows. <strong>Make It Real</strong> puts a row in the books.</p>
    <table class="compact-table no-table-tools"><thead><tr><th>Lender</th><th>Borrow / Repay</th><th class="num">Amount</th><th>Date</th><th>Rate %</th><th>Note</th><th></th></tr></thead><tbody>
    ${rows.map((r, k) => `<tr ${r.added ? 'style="opacity:.55"' : ""}><td><input list="lc-lenders" value="${escapeHtml(r.lender || "")}" onchange="lcSet(${k}, 'lender', this.value)" ${r.added ? "disabled" : ""}></td>
      <td><select onchange="lcSet(${k}, 'kind', this.value)" ${r.added ? "disabled" : ""}><option value="borrow" ${r.kind === "borrow" ? "selected" : ""}>Borrow</option><option value="repay" ${r.kind !== "borrow" ? "selected" : ""}>Repay</option></select></td>
      <td><input type="number" step="0.01" value="${r.amount ?? ""}" onchange="lcSet(${k}, 'amount', this.value)" style="width:110px;" ${r.added ? "disabled" : ""}></td>
      <td><input type="date" value="${r.date || ""}" onchange="lcSet(${k}, 'date', this.value)" ${r.added ? "disabled" : ""}></td>
      <td><input type="number" step="0.01" value="${r.rate ?? ""}" onchange="lcSet(${k}, 'rate', this.value)" style="width:70px;" ${r.added ? "disabled" : ""}></td>
      <td><input value="${escapeHtml(r.note || "")}" onchange="lcSet(${k}, 'note', this.value)" ${r.added ? "disabled" : ""}></td>
      <td class="nowrap">${r.added ? `<span class="ac-chip typed">Added</span>` : `<button class="small-btn" onclick="lcReal(${k})">Make It Real</button>`}<button class="small-btn icon-only" title="Remove" onclick="lcDel(${k})">${icon("x")}</button></td></tr>`).join("")}
    </tbody></table><datalist id="lc-lenders">${LC.loans.map(l => `<option value="${escapeHtml(l.name)}">`).join("")}</datalist>
    <div class="btn-row" style="margin-top:6px;"><button class="small-btn" onclick="lcAdd()">${icon("plus")} Row</button>${rows.some(r => !r.added && r.lender && r.amount) ? `<button class="small-btn" onclick="lcAllReal()">Make All Real</button>` : ""}</div>
    ${lenders.length ? `<table class="compact-table no-table-tools" style="margin-top:10px;"><thead><tr><th>Lender</th><th class="num">Owed Today</th><th class="num">+ These Rows</th><th class="num">Interest So Far</th><th class="num">Would Owe</th></tr></thead><tbody>
      ${lenders.map(n => { const o = owed[n] || 0, x = byL[n] || { add: 0, int: 0 }; return `<tr><td>${escapeHtml(n)}${owed[n] == null ? ' <span class="muted small">(new)</span>' : ""}</td><td class="ac-amt">${fmtMoney(o)}</td><td class="ac-amt">${money(x.add)}</td><td class="ac-amt">${x.int ? fmtMoney(x.int) : ""}</td><td class="ac-amt"><strong>${fmtMoney(o + x.add + x.int)}</strong></td></tr>`; }).join("")}
      <tr class="tot"><td>Total</td><td class="ac-amt">${fmtMoney(lenders.reduce((a, n) => a + (owed[n] || 0), 0))}</td><td class="ac-amt">${money(lenders.reduce((a, n) => a + ((byL[n] || {}).add || 0), 0))}</td>
        <td class="ac-amt">${fmtMoney(lenders.reduce((a, n) => a + ((byL[n] || {}).int || 0), 0))}</td><td class="ac-amt"><strong>${fmtMoney(lenders.reduce((a, n) => a + (owed[n] || 0) + ((byL[n] || {}).add || 0) + ((byL[n] || {}).int || 0), 0))}</strong></td></tr></tbody></table>` : ""}`;
}
let lcTimer = null;
function lcSave() { clearTimeout(lcTimer); lcTimer = setTimeout(() => apiFetch(`/api/accounting/sheets/${LC.id}`, { method: "PUT", body: JSON.stringify({ doc: LC.doc }) }).catch(() => {}), 500); }
function lcSet(k, f, v) { LC.doc.rows[k][f] = v; lcSave(); drawLoansCalc(); }
function lcAdd() { LC.doc.rows.push({ lender: "", kind: "borrow", amount: "", date: META.today, rate: "", note: "" }); lcSave(); drawLoansCalc(); }
function lcDel(k) { LC.doc.rows.splice(k, 1); lcSave(); drawLoansCalc(); }
async function lcClear() { if (!(await askDialog({ title: "Clear all rows?", body: "<p>Only the scratch rows — the books aren't touched.</p>", buttons: [{ label: "Clear", value: true, cls: "danger" }, { label: "Cancel", value: null }] })).value) return; LC.doc.rows = []; lcSave(); drawLoansCalc(); }
async function lcReal(k, quiet) {
  const r = LC.doc.rows[k];
  if (!r.lender || !(+r.amount)) { alert("Lender and amount"); return false; }
  let a = META.accounts.find(x => x.kind === "loan" && x.name.toLowerCase() === r.lender.trim().toLowerCase());
  if (!a) {
    if (!quiet && !(await askDialog({ title: "New Lender", body: `<p>There's no loan account called <strong>${escapeHtml(r.lender)}</strong> yet — make it?</p>`, buttons: [{ label: "Make It", value: true, cls: "primary" }, { label: "Cancel", value: null }] })).value) return false;
    a = await apiFetch("/api/accounting/accounts", { method: "POST", body: JSON.stringify({ name: r.lender.trim(), kind: "loan" }) });
    await loadMeta();
  }
  const t = await addLinePrefilled({ amount: (r.kind === "borrow" ? 1 : -1) * Math.abs(+r.amount), description: `${r.kind === "borrow" ? "Loan from" : "Loan payment to"} ${r.lender}`, note: r.note, account_id: a.id, date: r.date });
  if (!t) return false;
  r.added = true; r.txn_id = t.id; lcSave(); drawLoansCalc();
  return true;
}
async function lcAllReal() { for (let k = 0; k < LC.doc.rows.length; k++) if (!LC.doc.rows[k].added && LC.doc.rows[k].lender && +LC.doc.rows[k].amount) { if (!(await lcReal(k, false))) break; } show("loans"); }
async function lcPayoff() {
  const body = `<div class="ac-form"><label>Owed</label><input type="number" id="po-p" value="${LC.loans.reduce((a, l) => a + Math.max(0, l.owed), 0) || 10000}">
    <label>Rate % / Year</label><input type="number" step="0.01" id="po-r" value="8"><label>Pay A Month</label><input type="number" id="po-m" value="1000"><label>Extra A Month</label><input type="number" id="po-x" value="0"></div><div id="po-out" style="margin-top:10px;"></div>`;
  const dlg = askDialog({ title: "Payoff Calculator", body, buttons: [{ label: "Close", value: null }], wide: true });
  const run = () => {
    const P = +document.getElementById("po-p").value, r = +document.getElementById("po-r").value / 100 / 12, pay = +document.getElementById("po-m").value, extra = +document.getElementById("po-x").value;
    const sim = (pm) => { let b = P, n = 0, int = 0; const sched = []; while (b > 0.005 && n < 600) { const i = b * r; int += i; b = b + i - pm; n++; if (n <= 24 || b <= 0) sched.push({ n, i, b: Math.max(0, b) }); if (pm <= i) return { never: true }; } return { n, int, sched }; };
    const a = sim(pay), b = extra ? sim(pay + extra) : null;
    const out = document.getElementById("po-out"); if (!out) return;
    if (a.never) { out.innerHTML = `<p class="neg">${fmtMoney(pay)} a month doesn't cover the interest — it never gets paid off.</p>`; return; }
    const end = n => { const d = new Date(META.today + "T00:00:00"); d.setMonth(d.getMonth() + n); return d.toLocaleDateString(undefined, { month: "short", year: "numeric" }); };
    out.innerHTML = `<p>Paid off in <strong>${a.n} months</strong> (${end(a.n)}), interest ${fmtMoney(a.int)}.${b && !b.never ? ` With ${fmtMoney(extra)} extra: <strong>${b.n} months</strong> (${end(b.n)}), interest ${fmtMoney(b.int)} — saves ${a.n - b.n} months and ${fmtMoney(a.int - b.int)}.` : ""}</p>
      <table class="compact-table no-table-tools"><thead><tr><th>Month</th><th class="num">Interest</th><th class="num">Owed After</th></tr></thead><tbody>${a.sched.slice(0, 24).map(x => `<tr><td>${x.n}</td><td class="ac-amt">${fmtMoney(x.i)}</td><td class="ac-amt">${fmtMoney(x.b)}</td></tr>`).join("")}</tbody></table>`;
  };
  setTimeout(() => { document.querySelectorAll("#po-p, #po-r, #po-m, #po-x").forEach(i => i.oninput = run); run(); }, 0);
  await dlg;
}

// ================================================================ Notes: every comment
async function tabNotes() {
  const all = await apiFetch("/api/accounting/comments");
  const by = {};
  all.forEach(c => (by[c.section] = by[c.section] || []).push(c));
  const names = Object.fromEntries((window.TAB_LIST || []).map(([k, l]) => [k, l]));
  view().innerHTML = `<div class="card"><h3 style="margin-top:0;">Notes</h3><p class="ac-help">Every comment left on the Accounting tabs, newest first. Add one at the foot of any tab.</p>
    ${Object.entries(by).map(([sec, cs]) => `<h4 style="margin:12px 0 4px;">${escapeHtml(names[sec] || sec)}</h4>${cs.reverse().map(c => `<div class="ac-comment"><div>${escapeHtml(c.text).replace(/\n/g, "<br>")}</div>
      <div class="muted small">${escapeHtml(c.created_by || "")} · ${fmtWhen(c.created_at)} · ${c.period_key ? escapeHtml(c.period_key.replace("..", " – ")) : "every period"}</div></div>`).join("")}`).join("") || `<p class="muted">No comments yet.</p>`}</div>`;
}

window.MORE_LOADERS = { partneraccts: tabPartnerAccounts, payees: tabPayees, tax: tab1099, recurring: tabRecurring, ignored: tabIgnored, check: tabCheck,
  reconcile: tabReconcile, projections: tabProjections, notes: tabNotes };
