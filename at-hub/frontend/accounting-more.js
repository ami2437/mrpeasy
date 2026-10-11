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
  const t = x => `<td class="ac-amt">${money(x.withdrawn)}</td><td class="ac-amt">${money(x.invested)}</td><td class="ac-amt"><strong>${money(x.net)}</strong></td><td class="ac-amt">${x.lent ? money(x.lent) : ""}</td><td class="ac-amt">${x.repaid ? money(x.repaid) : ""}</td>`;
  const head = `<th class="num">Withdrawn</th><th class="num">Invested</th><th class="num">Net</th><th class="num">Lent To Co.</th><th class="num">Repaid</th>`;
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Partner Accounts — ${escapeHtml(d.year)}</h3><span style="flex:1"></span>
      <button onclick="downloadFile('/api/accounting/partner-accounts/export.xlsx?${qs()}', 'Partner Accounts.xlsx').catch(e => alert(e.message))">${icon("download")} Excel</button></div>
    <p class="ac-help">Who took how much out (withdrawn: money out of their partner accounts), who put money in (invested), and money a partner lent the company
      (loan accounts tagged with the partner). To get a partner's lines here, file them in an account of type Partner Draws with that partner (or a loan with the partner set).</p>
    <table class="compact-table no-table-tools"><thead><tr><th>Partner</th>${head}<th class="num">Share</th><th class="num">Remaining</th></tr></thead><tbody>
    ${d.partners.map(p => `<tr><td><strong>${escapeHtml(p.name)}</strong>${p.share_pct != null ? `<div class="muted small">${(p.share_pct * 100).toFixed(1)}%</div>` : ""}</td>${t(p.period)}
      <td class="ac-amt">${p.share != null ? fmtMoney(p.share) : ""}</td><td class="ac-amt">${p.remaining != null ? money(p.remaining) : ""}</td></tr>`).join("")}
    <tr class="tot"><td><strong>Total</strong></td>${t(d.partners.reduce((a, p) => { for (const k of ["withdrawn", "invested", "net", "lent", "repaid"]) a[k] += p.period[k]; return a; }, { withdrawn: 0, invested: 0, net: 0, lent: 0, repaid: 0 }))}
      <td class="ac-amt">${fmtMoney(d.to_share)}</td><td></td></tr></tbody></table></div>
    <div class="ac-grid2">${d.partners.map(p => `<div class="card"><div class="ac-filters"><h3 style="margin:0;">${escapeHtml(p.name)}</h3><span style="flex:1"></span>
        ${p.account_ids.length ? `<button class="small-btn" onclick='partnerLines(${JSON.stringify(p.account_ids)})'>Lines</button>` : ""}</div>
      <div class="ac-tiles"><div class="ac-tile"><div class="l">Withdrawn</div><div class="v neg">${fmtMoney(p.period.withdrawn)}</div><div class="s">all time ${fmtMoney(p.all_time.withdrawn)}</div></div>
        <div class="ac-tile"><div class="l">Invested</div><div class="v pos">${fmtMoney(p.period.invested)}</div><div class="s">all time ${fmtMoney(p.all_time.invested)}</div></div>
        <div class="ac-tile"><div class="l">Lent To The Company</div><div class="v">${fmtMoney(p.all_time.loan_owed)}</div><div class="s">still owed to them (all time)</div></div></div>
      <div class="muted small">${p.accounts.length ? "Accounts: " + p.accounts.map(escapeHtml).join(", ") : "No accounts for this partner yet."}</div>
      ${p.by_fy.length ? `<h4 style="margin:10px 0 4px;">By Fiscal Year</h4><table class="compact-table no-table-tools"><thead><tr><th>Year</th>${head}</tr></thead><tbody>${p.by_fy.map(x => `<tr><td>${escapeHtml(x.fy)}</td>${t(x)}</tr>`).join("")}</tbody></table>` : ""}
      ${p.by_month.length ? `<h4 style="margin:10px 0 4px;">By Month (This Period)</h4><table class="compact-table no-table-tools"><thead><tr><th>Month</th>${head}</tr></thead><tbody>${p.by_month.map(x => `<tr><td>${monthLabel(x.month)}</td>${t(x)}</tr>`).join("")}</tbody></table>` : ""}
    </div>`).join("")}</div>`;
}
function partnerLines(ids) { TXF = { account_id: ids[0] }; show("txns"); }

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
  document.getElementById("py-list").innerHTML = `<table class="compact-table no-table-tools"><thead><tr><th>Payee</th><th class="num">Lines</th><th class="num">In</th><th class="num">Out</th><th class="num">Net</th><th>Months</th><th>Filed In</th><th>Last</th></tr></thead><tbody>
    ${rows.slice(0, 400).map(r => `<tr><td><a class="link" href="#" onclick='TXF={q:${escapeHtml(JSON.stringify(r.payee.replace(/ \(.*\)$/, "")))}};show("txns");return false;'>${escapeHtml(r.payee)}</a></td>
      <td class="num">${r.count}</td><td class="ac-amt">${r.in ? money(r.in) : ""}</td><td class="ac-amt">${r.out ? money(r.out) : ""}</td><td class="ac-amt"><strong>${money(r.net)}</strong></td>
      <td class="num">${r.months}</td><td class="small">${r.accounts.map(a => `${escapeHtml(a.account)} <span class="muted">×${a.count}</span>`).join("<br>")}</td><td class="nowrap">${day(r.last)}</td></tr>`).join("")
    || `<tr><td colspan="8" class="muted">No payees.</td></tr>`}
    <tr class="tot"><td>Total (${rows.length})</td><td class="num">${rows.reduce((a, r) => a + r.count, 0)}</td><td class="ac-amt">${money(rows.reduce((a, r) => a + r.in, 0))}</td>
      <td class="ac-amt">${money(rows.reduce((a, r) => a + r.out, 0))}</td><td class="ac-amt">${money(rows.reduce((a, r) => a + r.net, 0))}</td><td colspan="3"></td></tr></tbody></table>`;
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
    <table class="compact-table no-table-tools"><thead><tr><th>Payee</th><th class="num">Paid In ${y}</th><th class="num">Payments</th><th>Filed In</th><th>First – Last</th><th>Needs A 1099?</th></tr></thead><tbody>
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
    <table class="compact-table no-table-tools"><thead><tr><th>Payee</th><th>Money</th><th>How Often</th><th class="num">Amount</th><th class="num">Per Month</th><th>Times</th><th>Last</th><th>Next</th><th>Filed In</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td><a class="link" href="#" onclick='TXF={q:${escapeHtml(JSON.stringify(r.payee.replace(/ \(.*\)$/, "")))}};show("txns");return false;'>${escapeHtml(r.payee)}</a></td>
      <td>${r.money === "out" ? "Out" : "In"}</td><td>${escapeHtml(r.how_often)}</td><td class="ac-amt">${fmtMoney(r.amount)}${r.same_amount ? "" : `<div class="muted small">varies</div>`}</td>
      <td class="ac-amt">${money(r.per_month)}</td><td class="num">${r.times}</td><td class="nowrap">${day(r.last)}</td>
      <td class="nowrap ${r.missed ? "neg" : ""}">${day(r.next)}${r.missed ? " · missed?" : ""}</td><td class="small">${escapeHtml(r.account || "—")}</td></tr>`).join("")
      || `<tr><td colspan="9" class="muted">Nothing regular found yet.</td></tr>`}
    ${rows.length ? `<tr class="tot"><td colspan="4">Per month (all of them)</td><td class="ac-amt">${money(rows.reduce((a, r) => a + r.per_month, 0))}</td><td colspan="4"></td></tr>` : ""}</tbody></table></div>`;
}

// ================================================================ Ignored (duplicates / estimates)
async function tabIgnored() {
  const d = await apiFetch("/api/accounting/ignored");
  const possible = d.rows.filter(r => r.status === "possible"), ign = d.rows.filter(r => r.status === "ignored"), rest = d.rows.filter(r => r.status === "restored");
  const why = { exact: "already in (same date, amount, wording)", near: "looked like a line already in", estimate: "a workbook AR / AP estimate" };
  const line = (r, tick) => `<tr>${tick ? `<td><input type="checkbox" class="ig-tick" data-id="${r.id}"></td>` : "<td></td>"}<td class="nowrap">${day(r.date)}<div class="muted small">${escapeHtml(r.source)}</div></td>
    <td>${escapeHtml(r.description || "")}${r.extra && r.extra.account ? `<div class="muted small">${escapeHtml(r.extra.account)} · ${escapeHtml(r.extra.category || "")}</div>` : ""}</td><td class="ac-amt">${money(r.amount || 0)}</td>
    <td class="small">${escapeHtml(why[r.reason] || r.reason)}${r.dup_of ? `<div class="ac-desc">vs ${day(r.dup_of.date)} · ${fmtMoney(r.dup_of.amount)} · ${escapeHtml(r.dup_of.description.slice(0, 60))}${r.dup_of.account ? ` · ${escapeHtml(r.dup_of.account)}` : ""}</div>` : ""}</td></tr>`;
  view().innerHTML = `<div class="card"><h3 style="margin-top:0;">Possible Duplicates <span class="muted small">${possible.length}</span></h3>
    <p class="ac-help">Lines from a file that look like lines already in — same amount, a few days apart, nearly the same wording. They're not in the books until you say
      <strong>Keep Both</strong>; <strong>Ignore</strong> keeps them out.</p>
    ${possible.length ? `<div class="btn-row" style="margin-bottom:6px;"><button class="small-btn" onclick="igDo('ignore', 'possible')">Ignore Ticked</button><button class="small-btn" onclick="igDo('keep', 'possible')">Keep Both (Ticked)</button>
      <button class="small-btn" onclick="document.querySelectorAll('#ig-possible .ig-tick').forEach(c => c.checked = true)">Tick All</button></div>` : ""}
    <table class="compact-table no-table-tools" id="ig-possible"><tbody>${possible.map(r => line(r, true)).join("") || `<tr><td class="muted">None waiting.</td></tr>`}</tbody></table></div>
    <div class="card"><h3 style="margin-top:0;">Ignored <span class="muted small">${ign.length} · ${fmtMoney(d.total_ignored)}</span></h3>
    <p class="ac-help">Not brought in: exact repeats from overlapping files, duplicates someone ignored, workbook estimates. Wasn't a duplicate? Tick it and <strong>Restore</strong> — it goes to Review.</p>
    ${ign.length ? `<div class="btn-row" style="margin-bottom:6px;"><button class="small-btn" onclick="igDo('restore', 'ignored')">Restore Ticked</button></div>` : ""}
    <table class="compact-table no-table-tools" id="ig-ignored"><tbody>${ign.slice(0, 500).map(r => line(r, true)).join("") || `<tr><td class="muted">Nothing ignored.</td></tr>`}</tbody></table>
    ${rest.length ? `<p class="muted small">${rest.length} restored earlier.</p>` : ""}</div>`;
}
async function igDo(action, where) {
  const ids = [...document.querySelectorAll(`#ig-${where} .ig-tick:checked`)].map(c => +c.dataset.id);
  if (!ids.length) { toast("Tick some lines"); return; }
  try { const r = await apiFetch("/api/accounting/ignored/decide", { method: "POST", body: JSON.stringify({ ids, action }) }); toast(`${r.done} done`); show("ignored"); refreshCount(); }
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
    ${Object.entries(by).map(([k, fs]) => `<div class="card" id="ck-${k}"><h3 style="margin-top:0;">${escapeHtml(fs[0].label)} <span class="muted small">${fs.length}</span></h3>
      <table class="compact-table no-table-tools"><tbody>${fs.map(f => `<tr data-key="${escapeHtml(f.key)}"><td class="nowrap">${f.txn ? day(f.txn.date) : ""}</td>
        <td>${f.txn ? `<span class="ac-who">${escapeHtml(f.txn.payee || "")}</span><div class="ac-desc">${escapeHtml(f.txn.description)}</div>` : ""}<div class="small">${escapeHtml(f.why)}</div>
          ${f.also.map(x => `<div class="ac-desc">vs ${day(x.date)} · ${fmtMoney(x.amount)} · ${escapeHtml(x.description.slice(0, 60))}</div>`).join("")}</td>
        <td class="small">${f.txn ? escapeHtml(f.txn.account || "Not filed") : ""}</td><td class="ac-amt">${f.txn ? money(f.txn.amount) : ""}</td>
        <td class="nowrap">${f.txn && f.suggest ? `<button class="small-btn" onclick="ckMove(${f.txn.id}, ${f.suggest.account_id}, this)">${icon("check")} Move To ${escapeHtml(f.suggest.account)}</button>` : ""}
          ${f.txn ? `<button class="small-btn" onclick="ckBack(${f.txn.id}, this)">Send Back</button><button class="small-btn" onclick="ckOk(${f.txn.id}, '${f.check}', this)">It's Right</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`).join("")}`;
}
const ckDone = (btn, msg) => { const tr = btn.closest("tr"); tr.style.opacity = .4; tr.querySelectorAll("button").forEach(b => b.disabled = true); toast(msg); };
async function ckMove(id, aid, btn) { await apiFetch(`/api/accounting/txns/${id}`, { method: "PUT", body: JSON.stringify({ account_id: aid }) }); ckDone(btn, `Moved to ${acc(aid).path}`); }
async function ckBack(id, btn) { await apiFetch("/api/accounting/txns/send-back", { method: "POST", body: JSON.stringify({ ids: [id], reason: "Check My Books" }) }); ckDone(btn, "Sent back to Review"); refreshCount(); }
async function ckOk(id, check, btn) { await apiFetch("/api/accounting/check/ok", { method: "POST", body: JSON.stringify({ txn_id: id, check }) }); ckDone(btn, "OK — won't be shown again"); }

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
        <div class="btn-row" style="margin-top:6px;"><button onclick="recParse()">${icon("check")} Read Lines</button><button onclick="recFile()">${icon("upload")} Pick A File</button><span id="rec-read" class="muted small"></span></div></div></div>
    <div id="rec-map"></div><div class="btn-row" style="margin-top:10px;"><button class="primary" id="rec-run" onclick="recRun()" disabled>${icon("check")} Match Them</button></div></div>
    <div id="rec-out"></div>
    ${hist.length ? `<div class="card"><h4 style="margin-top:0;">Earlier Checks</h4><table class="compact-table no-table-tools"><thead><tr><th>When</th><th>Accounts</th><th>Dates</th><th class="num">Lines</th><th>Result</th><th></th></tr></thead><tbody>
      ${hist.map(h => `<tr><td class="nowrap">${fmtWhen(h.created_at)}<div class="muted small">${escapeHtml(h.created_by || "")}</div></td><td class="small">${h.accounts.map(escapeHtml).join(", ")}</td>
        <td class="nowrap small">${day(h.start)} – ${day(h.end)}</td><td class="num">${h.lines}</td>
        <td class="small">${h.counts.same || 0} match · <span class="${h.counts.different ? "neg" : ""}">${h.counts.different || 0} different</span> · ${h.counts.qb_only || 0} QB only · ${h.counts.athub_only || 0} AT-HUB only</td>
        <td><button class="small-btn" onclick="recLoad(${h.id})">Open</button></td></tr>`).join("")}</tbody></table></div>` : ""}`;
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
function recFile() { const p = Object.assign(document.createElement("input"), { type: "file", accept: ".csv,.xlsx,.xlsm,.txt" }); p.onchange = () => p.files[0] && recParse(p.files[0]); p.click(); }
async function recRun() {
  const account_ids = [...document.querySelectorAll(".rec-acc:checked")].map(c => +c.value);
  if (!account_ids.length) { alert("Pick AT-HUB accounts on the left"); return; }
  store.set("rec.accounts", account_ids);
  const tol = +document.getElementById("rec-tol").value || 0; store.set("rec.tol", tol);
  const mapping = {}; document.querySelectorAll(".rec-m").forEach(s => { if (s.value) mapping[s.dataset.name] = +s.value; });
  try { REC.res = await apiFetch("/api/accounting/reconcile/run", { method: "POST", body: JSON.stringify({ account_ids, start: PERIOD.start, end: PERIOD.end, rows: REC.rows, mapping, tol_days: tol }) }); drawRec(); }
  catch (e) { alert(e.message); }
}
async function recLoad(id) {
  const r = await apiFetch(`/api/accounting/reconcile/${id}`);
  REC.rows = r.rows;
  document.querySelectorAll(".rec-acc").forEach(c => c.checked = r.account_ids.includes(+c.value));
  document.getElementById("rec-read").textContent = `${r.rows.length} QuickBooks line(s) from the earlier check (dates ${r.start} – ${r.end}).`;
  document.getElementById("rec-run").disabled = false;
  recRun();
}
function drawRec() {
  const d = REC.res;
  const qbRow = (r, fix) => `<tr><td class="nowrap">${day(r.date)}</td><td>${escapeHtml(r.name || "")}<div class="ac-desc">${escapeHtml(r.memo || "")}</div><div class="muted small">QuickBooks: ${escapeHtml(r.account || "")}${r.mapped_account ? ` → ${escapeHtml(r.mapped_account)}${r.mapped_by === "guess" ? " (guessed)" : ""}` : ""}</div></td>
    <td class="ac-amt">${fmtMoney(r.amount)}</td>
    <td>${r.athub ? `<span class="ac-who">${escapeHtml(r.athub.payee || "")}</span> <span class="muted small">${day(r.athub.date)}</span><div class="ac-desc">${escapeHtml(r.athub.description.slice(0, 70))}</div>
      <div class="small">AT-HUB: <strong>${escapeHtml(r.athub.account || "Not filed")}</strong></div>` : `<span class="muted">not found in AT-HUB</span>`}</td>
    <td class="nowrap">${fix && r.athub && r.mapped_account_id ? `<button class="small-btn" onclick="recMove(${r.athub.id}, ${r.mapped_account_id}, this)">${icon("check")} Move To ${escapeHtml(r.mapped_account)}</button>` : ""}</td></tr>`;
  const at = t => `<tr><td class="nowrap">${day(t.date)}</td><td><span class="ac-who">${escapeHtml(t.payee || "")}</span><div class="ac-desc">${escapeHtml(t.description)}</div></td><td class="ac-amt">${money(t.amount)}</td><td class="small">${escapeHtml(t.account || "")}</td>
    <td><button class="small-btn" onclick="recBack(${t.id}, this)">Send Back</button></td></tr>`;
  const sec = (key, title, help, html, open) => `<details class="card" ${open ? "open" : ""}><summary><strong>${title}</strong> <span class="muted small">${d.counts[key] || 0}</span></summary>
    <p class="ac-help" style="margin-top:6px;">${help}</p><table class="compact-table no-table-tools"><tbody>${html || `<tr><td class="muted">None.</td></tr>`}</tbody></table></details>`;
  document.getElementById("rec-out").innerHTML = `<div class="card"><h3 style="margin-top:0;">Result</h3>
    <div class="ac-tiles"><div class="ac-tile"><div class="l">Same Account</div><div class="v pos">${d.counts.same}</div></div>
      <div class="ac-tile"><div class="l">Different Account</div><div class="v ${d.counts.different ? "neg" : ""}">${d.counts.different}</div></div>
      <div class="ac-tile"><div class="l">QuickBooks Account Not Mapped</div><div class="v">${d.counts.unmapped}</div></div>
      <div class="ac-tile"><div class="l">Only In QuickBooks</div><div class="v">${d.counts.qb_only}</div></div>
      <div class="ac-tile"><div class="l">Only In AT-HUB</div><div class="v">${d.counts.athub_only}</div></div></div>
    <table class="compact-table no-table-tools"><thead><tr><th>Account</th><th class="num">AT-HUB</th><th class="num">QuickBooks</th><th class="num">Difference</th></tr></thead><tbody>
      ${d.totals.map(t => `<tr><td>${escapeHtml(t.account)}</td><td class="ac-amt">${fmtMoney(t.athub_abs)}</td><td class="ac-amt">${fmtMoney(t.qb_abs)}</td>
        <td class="ac-amt ${Math.abs(t.difference) > 0.009 ? "neg" : "pos"}"><strong>${fmtMoney(t.difference)}</strong></td></tr>`).join("")}</tbody></table></div>
    ${sec("different", "Different Account", "QuickBooks has the line in one account, AT-HUB in another. Move AT-HUB's if QuickBooks is right.", d.different.map(r => qbRow(r, true)).join(""), true)}
    ${sec("unmapped", "QuickBooks Account Not Mapped", "Matched a line, but the QuickBooks account isn't linked to an AT-HUB account yet — pick it above and match again.", d.unmapped.map(r => qbRow(r, false)).join(""), d.counts.unmapped > 0)}
    ${sec("qb_only", "Only In QuickBooks", "No AT-HUB line with that amount within the days allowed.", d.qb_only.map(r => qbRow(r, false)).join(""), d.counts.qb_only > 0)}
    ${sec("athub_only", "Only In AT-HUB", "In the picked AT-HUB accounts, but not in what was pasted from QuickBooks.", d.athub_only.map(at).join(""), d.counts.athub_only > 0)}
    ${sec("same", "Same Account", "These agree.", d.same.map(r => qbRow(r, false)).join(""), false)}`;
}
async function recMove(id, aid, btn) { await apiFetch(`/api/accounting/txns/${id}`, { method: "PUT", body: JSON.stringify({ account_id: aid }) }); ckDone(btn, `Moved to ${acc(aid).path}`); }
async function recBack(id, btn) { await apiFetch("/api/accounting/txns/send-back", { method: "POST", body: JSON.stringify({ ids: [id], reason: "Reconcile: not in QuickBooks" }) }); ckDone(btn, "Sent back to Review"); refreshCount(); }

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
const PJ_GROUPS = ["Cash In Bank", "AR Owed To Us", "AP We Owe", "Loans Owed", "Expected Money In", "Expected Money Out", "Partner Draws", "Other"];
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
  const S = pjSettings();
  const calc = makeCalc(doc.rows);
  const vals = doc.rows.map((r, k) => { try { return { v: calc.val(k + 1) }; } catch (e) { return { v: 0, err: e.message }; } });
  const g = name => doc.rows.reduce((s, r, k) => s + ((r.group || "") === name ? vals[k].v : 0), 0);
  const cash = g("Cash In Bank"), ar = g("AR Owed To Us") * S.ar_pct / 100, ap = Math.abs(g("AP We Owe")), loans = Math.abs(g("Loans Owed"));
  const expIn = g("Expected Money In"), expOut = Math.abs(g("Expected Money Out")), draws = Math.abs(g("Partner Draws")), other = g("Other");
  const net = cash + ar - ap - loans + expIn - expOut - draws + other;
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
      else if (r.group === "AR Owed To Us" && (has || k < S.ar_months)) inn += Math.abs(v) * S.ar_pct / 100 / (has ? 1 : S.ar_months);
      else if (r.group === "AP We Owe" && (has || k < S.ap_months)) out += Math.abs(v) / (has ? 1 : S.ap_months);
      else if (r.group === "Partner Draws" && has) out += 0;
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
      <code>=R3*0.9</code>, <code>=SUM(R2:R8)</code>, <code>=GROUP("AR Owed To Us")</code>, <code>=R4*15%</code>. Give a row a month (2026-12) to put it in that month of the forecast.</p>
    <div class="btn-row" style="margin-bottom:8px;"><span class="muted small">Fill from the books:</span>
      ${[["banks", "Bank Balances"], ["loans", "Loan Balances"], ["ar", "AR"], ["ap", "AP"], ["monthly", "Monthly Averages"], ["recurring", "Recurring Payments"]].map(([k, l]) => `<button class="small-btn" onclick="pjFill('${k}')">${l}</button>`).join("")}</div>
    <table class="compact-table no-table-tools pj-grid"><thead><tr><th>#</th><th>Group</th><th>Name</th><th>Amount / Formula</th><th class="num">Value</th><th>Month</th><th>Note</th><th></th></tr></thead><tbody>
    ${doc.rows.map((r, k) => `<tr><td class="muted">R${k + 1}</td>
      <td><select onchange="pjSet(${k}, 'group', this.value)">${PJ_GROUPS.map(x => `<option ${x === r.group ? "selected" : ""}>${x}</option>`).join("")}</select></td>
      <td><input value="${escapeHtml(r.name || "")}" onchange="pjSet(${k}, 'name', this.value)"></td>
      <td><input value="${escapeHtml(r.a ?? "")}" onchange="pjSet(${k}, 'a', this.value)" style="font-family:monospace;"></td>
      <td class="ac-amt">${vals[k].err ? `<span class="neg small" title="${escapeHtml(vals[k].err)}">error</span>` : money(vals[k].v)}</td>
      <td><input value="${escapeHtml(r.month || "")}" placeholder="any" onchange="pjSet(${k}, 'month', this.value)" style="width:86px;"></td>
      <td><input value="${escapeHtml(r.note || "")}" onchange="pjSet(${k}, 'note', this.value)"></td>
      <td class="nowrap"><button class="small-btn icon-only" title="Make it real: a typed-in line in the books" onclick="pjReal(${k})">${icon("check")}</button>
        <button class="small-btn icon-only" title="Remove row" onclick="pjDel(${k})">${icon("x")}</button></td></tr>`).join("")}</tbody></table>
    <div class="btn-row" style="margin-top:6px;"><button class="small-btn" onclick="pjAdd()">${icon("plus")} Row</button></div></div>
    <div class="ac-grid2"><div class="card"><h3 style="margin-top:0;">Where We'd Stand</h3>
      <table class="ac-steps" style="width:100%;"><tbody>
        <tr><td>Cash in bank</td><td class="ac-amt">${money(cash)}</td></tr>
        <tr><td>+ AR owed to us (${S.ar_pct}% collected)</td><td class="ac-amt">${money(ar)}</td></tr>
        <tr><td>− AP we owe</td><td class="ac-amt">${money(-ap)}</td></tr>
        <tr><td>− Loans owed</td><td class="ac-amt">${money(-loans)}</td></tr>
        <tr><td>+ Expected money in − out</td><td class="ac-amt">${money(expIn - expOut)}</td></tr>
        <tr><td>− Partner draws planned</td><td class="ac-amt">${money(-draws)}</td></tr>
        ${other ? `<tr><td>Other</td><td class="ac-amt">${money(other)}</td></tr>` : ""}
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
  if (what === "loans") add(b.loans.map(x => ({ group: "Loans Owed", name: x.name, a: String(x.owed), note: "owed, from Loans" })));
  if (what === "ar") add(b.ar.map(x => ({ group: "AR Owed To Us", name: x.name, a: String(x.amount), note: "AT-HUB, today" })));
  if (what === "ap") add(b.ap.map(x => ({ group: "AP We Owe", name: x.name, a: String(x.amount), note: "AT-HUB, today" })));
  if (what === "monthly") add([{ group: "Expected Money In", name: "Average monthly income", a: String(b.monthly.income), note: `${b.monthly.months} months to ${b.monthly.to}` },
    { group: "Expected Money Out", name: "Average monthly cost of goods", a: String(Math.abs(b.monthly.cogs)), note: `${b.monthly.months} months` },
    { group: "Expected Money Out", name: "Average monthly expenses", a: String(Math.abs(b.monthly.expense)), note: `${b.monthly.months} months` }]);
  if (what === "recurring") add(b.recurring.map(x => ({ group: "Expected Money Out", name: `${x.payee} (${x.how_often})`, a: String(Math.abs(x.per_month)), note: "a month, from Recurring" })));
}
async function pjReal(k) {
  const r = PJ.cur.doc.rows[k];
  const calc = makeCalc(PJ.cur.doc.rows);
  let v = 0; try { v = calc.val(k + 1); } catch (e) { alert(e.message); return; }
  const isAr = r.group === "AR Owed To Us", isAp = r.group === "AP We Owe";
  if (isAr || isAp) {
    if (!(await askDialog({ title: "Make It Real?", body: `<p>Add <strong>${escapeHtml(r.name || r.group)}</strong> ${fmtMoney(Math.abs(v))} to AP / AR as a typed-in item?</p>`, buttons: [{ label: "Add", value: true, cls: "primary" }, { label: "Cancel", value: null }] })).value) return;
    await apiFetch("/api/accounting/open-items", { method: "POST", body: JSON.stringify({ source: "manual", side: isAr ? "ar" : "ap", party: r.name || r.group, amount: Math.abs(v), note: r.note || "from Projections" }) });
    toast("Added to AP / AR"); return;
  }
  const out = ["Expected Money Out", "Partner Draws", "AP We Owe", "Loans Owed"].includes(r.group);
  await addLinePrefilled({ amount: (out ? -1 : 1) * Math.abs(v), description: r.name || r.group, note: r.note || "from Projections", date: r.month && /^\d{4}-\d\d$/.test(r.month) ? r.month + "-01" : null });
}
async function pjCompare() {
  if (PJ.list.length < 2) { toast("Make a second projection (Copy) to compare"); return; }
  const opts = PJ.list.map(x => `<option value="${x.id}">${escapeHtml(x.name)}</option>`).join("");
  const { value, el } = await askDialog({ title: "Compare Two", body: `<div class="ac-form"><label>This</label><select id="c1">${opts}</select><label>With</label><select id="c2">${opts}</select></div>`, buttons: [{ label: "Compare", value: true, cls: "primary" }, { label: "Cancel", value: null }] });
  if (!value) return;
  const a = PJ.list.find(x => x.id === +el.querySelector("#c1").value), b = PJ.list.find(x => x.id === +el.querySelector("#c2").value);
  const tot = s => { const rows = s.doc.rows || [], c = makeCalc(rows); const o = {}; rows.forEach((r, k) => { let v = 0; try { v = c.val(k + 1); } catch {} o[r.group] = (o[r.group] || 0) + v; }); return o; };
  const ta = tot(a), tb = tot(b);
  await askDialog({ title: `${a.name} vs ${b.name}`, body: `<table class="compact-table no-table-tools"><thead><tr><th>Group</th><th class="num">${escapeHtml(a.name)}</th><th class="num">${escapeHtml(b.name)}</th><th class="num">Difference</th></tr></thead><tbody>
    ${PJ_GROUPS.filter(g => ta[g] || tb[g]).map(g => `<tr><td>${g}</td><td class="ac-amt">${money(ta[g] || 0)}</td><td class="ac-amt">${money(tb[g] || 0)}</td><td class="ac-amt">${money((ta[g] || 0) - (tb[g] || 0))}</td></tr>`).join("")}</tbody></table>`, buttons: [{ label: "Close", value: null }], wide: true });
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
