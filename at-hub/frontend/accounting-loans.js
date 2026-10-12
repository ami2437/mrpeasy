// Accounting -- Loans, first round (user, 2026-10-12; to be built up as they test). Loaded after accounting-more.js; uses
// accounting.html's helpers (view(), META, money(), day(), acc(), accountOptions(), doneToast(), loansCalcCard() ...).
// A card per loan; open one for its statement + timeline (bank lines, book entries, the events a person writes down),
// its details (lender, rate, payment ...), interest to a date, payments due vs made, and a check against the lender's
// own statement. Events that move money without the bank (paid by a partner, moved to another account, interest,
// an adjustment) make Book Entries so the balance follows; Undo / delete takes them back.

let LOAN_OPEN = null, LOAN_META = null;
async function tabLoansV2() {
  if (LOAN_OPEN) return loanPage(LOAN_OPEN);
  const d = await apiFetch("/api/accounting/loans-overview");
  LOAN_META = d;
  const open = d.loans.filter(l => l.open), closed = d.loans.filter(l => !l.open);
  const owed = open.reduce((a, l) => a + l.owed, 0);
  const spark = l => { const vals = l.months.map(m => m.balance), max = Math.max(1, ...vals.map(Math.abs));
    return `<div class="ac-bars" style="height:46px;border:none;">${vals.map((v, i) => `<div class="m" title="${monthLabel(l.months[i].month)}: ${fmtMoney(v)}"><div class="pair"><div class="b ${v > 0 ? "out" : "in"}" style="height:${(Math.abs(v) / max * 100).toFixed(0)}%;width:70%"></div></div></div>`).join("")}</div>`; };
  const card = l => `<div class="card" style="margin:0;cursor:pointer;" onclick="LOAN_OPEN=${l.account_id};show('loans')">
      <div class="ac-filters" style="margin-bottom:4px;"><h3 style="margin:0;">${icon("anchor")} ${escapeHtml(l.name)}</h3><span style="flex:1"></span>
        <span class="ac-chip ${l.open ? "check" : ""}">${l.open ? "Open" : "Closed"}</span>${l.late ? ` <span class="ac-chip flag" title="Payments missed or short">${l.late} late</span>` : ""}</div>
      ${l.details.lender ? `<div class="muted small">${escapeHtml(l.details.lender)}${l.details.rate ? ` · ${l.details.rate}%` : ""}${l.details.payment ? ` · ${fmtMoney(l.details.payment)} a month` : ""}</div>` : ""}
      <div class="ac-tiles" style="margin:8px 0;"><div class="ac-tile"><div class="l">Owed Now</div><div class="v ${l.owed > 0.004 ? "neg" : "pos"}">${fmtMoney(l.owed)}</div>
        ${l.owed < -0.004 ? `<div class="s" title="More was repaid than borrowed in the books -- the loan was taken before the first line here">set the opening balance</div>` : ""}</div>
        <div class="ac-tile"><div class="l">Borrowed / Repaid</div><div class="v" style="font-size:14px;">${money(l.borrowed)}<br>${money(l.repaid)}</div></div></div>
      ${l.months.length > 1 ? spark(l) : ""}
      <div class="small">${l.last_event ? `${icon("notePen")} ${day(l.last_event.date)} · ${escapeHtml(l.last_event.label)}${l.last_event.text ? ": " + escapeHtml(l.last_event.text.slice(0, 60)) : ""}` : `<span class="muted">No events written down yet</span>`}</div>
      ${l.next_payment ? `<div class="small ${l.next_payment.status === "missed" ? "neg" : ""}">Next payment ${fmtMoney(l.next_payment.expected)} due ${day(l.next_payment.due)}${l.next_payment.status === "missed" ? " — missed" : ""}</div>` : ""}
      ${l.history.length ? `<div class="muted small">${l.history.map(h => `${day(h.date)}: ${escapeHtml(h.what)}`).join(" · ")}</div>` : ""}</div>`;
  view().innerHTML = `<div class="card"><div class="ac-filters"><h3 style="margin:0;">Loans</h3><span style="flex:1"></span>
    <p class="ac-help">A card per lender (accounts of type Loans). Open one to see every line and everything that happened — and write down what isn't on a
      statement: a partner paid it off, the balance moved to another account, the rate changed, a call with the lender. Events that move money make book entries,
      so the balance follows. Closed loans are folded away below.</p>
    <div class="ac-tiles"><div class="ac-tile"><div class="l">Owed On Open Loans</div><div class="v neg">${fmtMoney(owed)}</div><div class="s">${open.length} open</div></div>
      <div class="ac-tile"><div class="l">Closed</div><div class="v">${closed.length}</div></div></div>
    <div class="ac-grid2">${open.map(card).join("") || `<p class="ac-empty">${icon("anchor")}No open loans.</p>`}</div>
    ${closed.length ? `<details style="margin-top:12px;"><summary><strong>Closed Loans</strong> <span class="muted small">${closed.length}</span></summary><div class="ac-grid2" style="margin-top:8px;">${closed.map(card).join("")}</div></details>` : ""}</div>`;
  const rows = await apiFetch(`/api/accounting/loans?end=${PERIOD.end}`);
  await loansCalcCard(rows);
}

async function loanPage(id) {
  const [st, meta] = await Promise.all([apiFetch(`/api/accounting/loans/${id}`), LOAN_META ? Promise.resolve(LOAN_META) : apiFetch("/api/accounting/loans-overview")]);
  LOAN_META = meta;
  const det = st.details || {};
  const kinds = meta.kinds, moves = new Set(meta.moves_money);
  const f = (k, label, type = "text", ph = "") => `<label>${label}</label><input id="ld-${k}" type="${type}" value="${escapeHtml(det[k] ?? "")}" placeholder="${ph}" step="any">`;
  const row = r => r.type === "event"
    ? `<tr style="background:var(--surface-2);"><td class="nowrap">${day(r.date)}</td><td><span class="ac-chip check">${icon("notePen")} ${escapeHtml(r.label)}</span></td>
        <td>${escapeHtml(r.text || "")}${r.other_account ? `<div class="small">${r.kind === "moved_in" ? "from" : "→"} <strong>${escapeHtml(r.other_account)}</strong></div>` : ""}<div class="muted small">${escapeHtml(r.created_by || "")}</div></td>
        <td class="ac-amt">${r.amount != null ? fmtMoney(r.amount) : ""}</td><td class="ac-amt">${fmtMoney(r.balance)}</td>
        <td>${r.kind === "moved_in" ? "" : `<button class="small-btn icon-only" title="Delete this event (and the book entries it made)" onclick="loanDelEvent(${r.id})">${icon("trash")}</button>`}</td></tr>`
    : `<tr><td class="nowrap">${day(r.date)}</td><td class="small">${r.by_event ? `<span class="ac-how workbook">${icon("file")}Book Entry</span>` : r.amount > 0 ? "Borrowed" : "Repaid"}</td>
        <td><div class="ac-desc">${escapeHtml(r.description)}</div>${r.note && !r.by_event ? `<div class="small">${icon("notePen")} ${escapeHtml(r.note)}</div>` : ""}<div class="muted small">${escapeHtml(r.source)}</div></td>
        <td class="ac-amt">${money(r.amount)}</td><td class="ac-amt"><strong>${fmtMoney(r.balance)}</strong></td><td></td></tr>`;
  view().innerHTML = `<div class="card"><div class="ac-filters"><button class="small-btn" onclick="LOAN_OPEN=null;show('loans')">← All Loans</button>
      <h3 style="margin:0;">${escapeHtml(st.account.path)}</h3><span class="ac-chip ${st.open ? "check" : ""}">${st.open ? "Open" : "Closed"}</span><span style="flex:1"></span>
      <button class="small-btn" onclick="downloadFile('/api/accounting/loans/${id}/export.xlsx', 'Loan ${escapeHtml(st.account.name)}.xlsx').catch(e => alert(e.message))">${icon("download")} Excel</button>
      <button class="small-btn" onclick="loanVerify(${id})">${icon("upload")} Check Against The Lender's Statement</button></div>
    <div class="ac-tiles"><div class="ac-tile"><div class="l">Owed Now</div><div class="v ${st.owed > 0.004 ? "neg" : "pos"}">${fmtMoney(st.owed)}</div><div class="s">opening ${fmtMoney(st.opening)}${st.owed < -0.004 ? ` — more repaid than borrowed here: <a class="link" href="#" onclick="editAccount(${id});return false;">set "Owed At Start"</a>` : ""}</div></div>
      <div class="ac-tile"><div class="l">Borrowed</div><div class="v">${fmtMoney(st.borrowed)}</div></div><div class="ac-tile"><div class="l">Repaid</div><div class="v">${fmtMoney(st.repaid)}</div></div>
      <div class="ac-tile"><div class="l">Moved / Adjusted</div><div class="v">${fmtMoney(st.moved_or_adjusted)}</div><div class="s">book entries from events</div></div></div>
    ${st.history.length ? `<p class="small">${st.history.map(h => `<span class="ac-chip">${day(h.date)} · ${escapeHtml(h.what)}</span>`).join(" ")}</p>` : ""}</div>
  <div class="ac-grid2"><div class="card"><h4 style="margin-top:0;">Write Down What Happened</h4>
      <div class="ac-form"><label>Date</label><input type="date" id="le-date" value="${META.today}">
        <label>What</label><select id="le-kind">${Object.entries(kinds).filter(([k]) => k !== "moved_in").map(([k, l]) => `<option value="${k}">${escapeHtml(l)}${moves.has(k) ? " ($)" : ""}</option>`).join("")}</select>
        <label>Amount</label><input type="number" step="0.01" id="le-amt" placeholder="only for $ events, or the new rate for Rate Changed">
        <label id="le-ol">To Account</label><select id="le-other">${accountOptions(null, { blank: "— pick —" }).replace(`<option value="new">+ New account…</option>`, "")}</select>
        <label>Note</label><textarea id="le-text" rows="2" placeholder="e.g. Niraj paid Sandy Spring off from his own account"></textarea></div>
      <p class="muted small" id="le-help"></p>
      <button class="small-btn primary" onclick="loanAddEvent(${id})">${icon("plus")} Add To The Timeline</button></div>
    <div class="card"><h4 style="margin-top:0;">Loan Details</h4>
      <div class="ac-form">${f("lender", "Lender")}${f("original", "Original Amount", "number")}${f("start", "Start Date", "date")}${f("rate", "Rate % / Year", "number")}
        <label>Rate Type</label><select id="ld-rate_type"><option value="simple" ${det.rate_type !== "compound" ? "selected" : ""}>Simple (daily)</option><option value="compound" ${det.rate_type === "compound" ? "selected" : ""}>Compound (monthly)</option></select>
        ${f("payment", "Payment A Month", "number")}${f("due_day", "Due Day", "number", "1-28")}${f("term_months", "Term (Months)", "number")}${f("secured_by", "Secured By")}
        <label>Partner</label><select id="ld-partner"><option value="">—</option>${META.settings.partners.map(p => `<option ${det.partner === p.name ? "selected" : ""}>${escapeHtml(p.name)}</option>`).join("")}</select>
        <label>Partner's Role</label><select id="ld-role"><option value="">—</option><option value="owes" ${det.role === "owes" ? "selected" : ""}>Partner owes it (personal guarantee)</option><option value="lent" ${det.role === "lent" ? "selected" : ""}>Partner lent it to the company</option></select>
        ${f("note", "Note")}</div>
      <div class="btn-row" style="margin-top:6px;"><button class="small-btn primary" onclick="loanSaveDetails(${id})">Save Details</button></div>
      <h4 style="margin:12px 0 4px;">Interest</h4><div class="ac-form"><label>Up To</label><div style="display:flex;gap:6px;"><input type="date" id="li-to" value="${META.today}"><button class="small-btn" onclick="loanInterest(${id})">Work It Out</button></div></div>
      <div id="li-out" class="small" style="margin-top:4px;"></div></div></div>
  <div class="card"><h4 style="margin-top:0;">Statement &amp; Timeline <span class="muted small">${st.rows.length} entries, oldest first</span></h4>
    <table class="compact-table no-table-tools"><thead><tr><th>Date</th><th>What</th><th>Description / Note</th><th class="num">Amount</th><th class="num">Owed After</th><th></th></tr></thead>
      <tbody><tr><td></td><td class="small">Opening</td><td class="muted small">owed before the first line here (set on the account)</td><td></td><td class="ac-amt">${fmtMoney(st.opening)}</td><td></td></tr>
      ${st.rows.map(row).join("") || `<tr><td colspan="6" class="ac-empty">Nothing yet.</td></tr>`}</tbody></table></div>
  ${st.schedule.length ? `<div class="card"><h4 style="margin-top:0;">Payments Due vs Made</h4><table class="compact-table no-table-tools"><thead><tr><th>Month</th><th>Due</th><th class="num">Expected</th><th class="num">Paid</th><th>Status</th></tr></thead><tbody>
    ${st.schedule.slice().reverse().map(s => `<tr><td>${monthLabel(s.month)}</td><td>${day(s.due)}</td><td class="ac-amt">${fmtMoney(s.expected)}</td><td class="ac-amt">${fmtMoney(s.paid)}</td>
      <td><span class="ac-chip ${s.status === "paid" ? "" : s.status === "missed" || s.status === "short" ? "flag" : "check"}">${s.status}</span></td></tr>`).join("")}</tbody></table></div>` : ""}`;
  const kindSel = document.getElementById("le-kind"), other = document.getElementById("le-other"), ol = document.getElementById("le-ol"), help = document.getElementById("le-help");
  const tip = { moved_out: "Moves the amount (default: the whole balance) to another account — another loan, or a partner's account. Both get a book entry; a loan gets the move on its timeline too.",
    paid_by_partner: "A partner paid the lender from their own money: the loan goes down, the partner's account (pick it) goes up by the same amount.",
    interest: "Adds interest to the loan; the other side goes to Loan Interest (an expense) unless you pick another account.",
    adjust: "Changes the balance by the amount (+ owes more, − owes less), nothing else.", rate: "Type the new rate in Amount — it updates the details too.",
    closed: "Just a note on the timeline — use Moved / Paid By A Partner / Adjust to bring the balance to zero." };
  const upd = () => { const k = kindSel.value, show_ = ["moved_out", "paid_by_partner", "interest"].includes(k); other.style.display = ol.style.display = show_ ? "" : "none";
    ol.textContent = k === "interest" ? "Expense Account" : k === "paid_by_partner" ? "Partner's Account" : "To Account"; help.textContent = tip[k] || "";
    if (k === "moved_out" && !document.getElementById("le-amt").value) document.getElementById("le-amt").value = st.owed.toFixed(2); };
  kindSel.onchange = upd; upd();
}
async function loanAddEvent(id) {
  const v = x => document.getElementById(x).value;
  try {
    const r = await apiFetch(`/api/accounting/loans/${id}/events`, { method: "POST", body: JSON.stringify({ date: v("le-date"), kind: v("le-kind"), amount: v("le-amt"), other_account_id: v("le-other") || null, text: v("le-text") }) });
    doneToast("Added to the timeline", r.change_id, () => loanPage(id)); loanPage(id);
  } catch (e) { alert(e.message); }
}
async function loanDelEvent(eid) {
  if (!(await askDialog({ title: "Delete This Event?", body: "<p>The event goes — and the book entries it made (and its other half on the other loan).</p>", buttons: [{ label: "Delete", value: true, cls: "danger" }, { label: "Cancel", value: null }] })).value) return;
  try { const r = await apiFetch(`/api/accounting/loan-events/${eid}`, { method: "DELETE" }); doneToast("Event deleted", r.change_id, () => loanPage(LOAN_OPEN)); loanPage(LOAN_OPEN); }
  catch (e) { alert(e.message); }
}
async function loanSaveDetails(id) {
  const body = {}; ["lender", "original", "start", "rate", "rate_type", "payment", "due_day", "term_months", "secured_by", "partner", "role", "note"].forEach(k => { const el = document.getElementById("ld-" + k); if (el) body[k] = el.value; });
  try { const r = await apiFetch(`/api/accounting/loans/${id}/details`, { method: "PUT", body: JSON.stringify(body) }); doneToast("Details saved", r.change_id, () => loanPage(id)); loanPage(id); }
  catch (e) { alert(e.message); }
}
async function loanInterest(id) {
  const to = document.getElementById("li-to").value, out = document.getElementById("li-out");
  try {
    const r = await apiFetch(`/api/accounting/loans/${id}/interest?to=${to}`);
    if (!r.rate) { out.textContent = r.note; return; }
    out.innerHTML = `${r.rate}% ${r.compound ? "compounded monthly" : "simple"} from ${day(r.from)} to ${day(r.to)}: <strong>${fmtMoney(r.interest)}</strong>
      ${r.interest ? `<button class="small-btn" onclick='loanAddInterest(${id}, ${JSON.stringify(r)})'>Add As A Book Entry</button>` : ""}`;
  } catch (e) { alert(e.message); }
}
async function loanAddInterest(id, r) {
  try { const x = await apiFetch(`/api/accounting/loans/${id}/events`, { method: "POST", body: JSON.stringify({ date: r.to, kind: "interest", amount: r.interest, text: `interest ${r.from} to ${r.to} at ${r.rate}%` }) });
    doneToast("Interest added", x.change_id, () => loanPage(id)); loanPage(id); } catch (e) { alert(e.message); }
}
function loanVerify(id) {
  const p = Object.assign(document.createElement("input"), { type: "file", accept: ".csv,.xlsx,.xlsm,.txt" });
  p.onchange = async () => {
    const file = p.files[0]; if (!file) return;
    const fd = new FormData(); fd.append("file", file); fd.append("tol_days", "5");
    let r; try { r = await apiUpload(`/api/accounting/loans/${id}/verify`, fd); } catch (e) { alert(e.message); return; }
    const t = (rows, ours) => `<table class="compact-table no-table-tools"><thead><tr><th>Date</th><th>Lender Says</th><th class="num">Amount</th>${ours ? "<th>AT-HUB</th><th class=\"num\">AT-HUB Amount</th>" : ""}</tr></thead><tbody>
      ${rows.map(x => `<tr><td>${day(x.date)}</td><td class="small">${escapeHtml(x.description || "")}</td><td class="ac-amt">${fmtMoney(x.amount)}</td>${ours ? `<td class="small">${escapeHtml(x.ours.description || "")} <span class="muted">${day(x.ours.date)}</span></td><td class="ac-amt">${fmtMoney(x.ours.amount)}</td>` : ""}</tr>`).join("") || `<tr><td colspan="5" class="muted">None.</td></tr>`}</tbody></table>`;
    await askDialog({ title: "Lender's Statement vs AT-HUB", wide: true, body: `<div class="ac-tiles"><div class="ac-tile"><div class="l">Matched</div><div class="v pos">${r.counts.matched}</div></div>
        <div class="ac-tile"><div class="l">Amount Differs</div><div class="v ${r.counts.amount_differs ? "neg" : ""}">${r.counts.amount_differs}</div></div>
        <div class="ac-tile"><div class="l">Only On Their Statement</div><div class="v ${r.counts.only_theirs ? "neg" : ""}">${r.counts.only_theirs}</div></div>
        <div class="ac-tile"><div class="l">Only In AT-HUB</div><div class="v ${r.counts.only_ours ? "neg" : ""}">${r.counts.only_ours}</div></div></div>
      <div style="max-height:56vh;overflow:auto;">
      ${r.amount_differs.length ? `<h4>Amount Differs</h4>${t(r.amount_differs, true)}` : ""}${r.only_theirs.length ? `<h4>Only On Their Statement</h4>${t(r.only_theirs)}` : ""}
      ${r.only_ours.length ? `<h4>Only In AT-HUB</h4><table class="compact-table no-table-tools"><tbody>${r.only_ours.map(o => `<tr><td>${day(o.date)}</td><td class="small">${escapeHtml(o.description)}</td><td class="ac-amt">${fmtMoney(o.amount)}</td></tr>`).join("")}</tbody></table>` : ""}
      <h4>Month By Month</h4><table class="compact-table no-table-tools"><thead><tr><th>Month</th><th class="num">Lender</th><th class="num">AT-HUB</th><th class="num">Difference</th></tr></thead><tbody>
      ${r.months.map(m => `<tr><td>${monthLabel(m.month)}</td><td class="ac-amt">${fmtMoney(m.theirs)}</td><td class="ac-amt">${fmtMoney(m.ours)}</td><td class="ac-amt ${Math.abs(m.difference) > 0.009 ? "neg" : ""}">${fmtMoney(m.difference)}</td></tr>`).join("")}</tbody></table>
      ${r.matched.length ? `<details><summary class="small">${r.matched.length} matched</summary>${t(r.matched, true)}</details>` : ""}</div>`, buttons: [{ label: "Close", value: null }] });
  };
  p.click();
}
window.MORE_LOADERS = { ...(window.MORE_LOADERS || {}), loans: tabLoansV2 };
