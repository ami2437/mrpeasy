// ---- Company Settings -> Email: the addresses AT-HUB sends from, which one each kind of email uses, health ----
// (server: routes/email_settings.py, services/mailer.py). Passwords are typed here once, kept encrypted, never shown.
const EmailSettings = {
  data: null,
  async load() {
    const el = document.getElementById("email-settings");
    if (!el) return;
    if (!AuthGuard.can("company")) { el.innerHTML = `<p class="muted">Only roles with Company Settings manage the email addresses.</p>`; return; }
    try { this.data = await apiFetch("/api/email/senders"); } catch (e) { el.innerHTML = `<div class="error">${escapeHtml(e.message)}</div>`; return; }
    this.render();
  },
  statusChip(s) {
    if (!s.active) return `<span class="tag cancelled">off</span>`;
    if (s.status === "ok") return `<span class="tag shipped" title="Last checked ${fmtWhen(s.last_check_at)}">working</span>`;
    if (s.status === "failing") return `<span class="tag overdue" title="${escapeHtml(s.last_error || "")}">failing since ${fmtWhen(s.failing_since)}</span>`;
    return `<span class="tag draft">not checked yet</span>`;
  },
  render() {
    const d = this.data, el = document.getElementById("email-settings");
    const opts = cur => `<option value="">${d.server.from ? `Server's own (${escapeHtml(d.server.from)})` : "— none —"}</option>` +
      d.senders.filter(s => s.active).map(s => `<option value="${s.id}" ${s.id === cur ? "selected" : ""}>${escapeHtml(s.address)}</option>`).join("");
    el.innerHTML = `
      <p class="muted small" style="margin-top:0;">Each address can log in with <strong>its own mailbox password</strong>, or use the server's shared sending login
        (a sending service with your domain verified). AT-HUB checks every address once a day and tells you here, and in Tasks, if one stops working —
        e.g. its password was changed on the mail server.</p>
      ${d.senders.some(s => s.status === "failing" && s.active) ? `<div class="la-banner">${icon("info")}<div><strong>${d.senders.filter(s => s.status === "failing" && s.active).map(s => escapeHtml(s.address)).join(", ")}</strong>
        can't send right now — update the password (or mail server) below. Emails that failed meanwhile can be sent again from the Emails page.</div></div>` : ""}
      <table class="compact-table no-table-tools em-table"><thead><tr><th>Address</th><th>Shown As</th><th>Login</th><th>Status</th><th></th></tr></thead><tbody>
      ${d.senders.map(s => `<tr class="${s.active ? "" : "order-done"}"><td><strong>${escapeHtml(s.address)}</strong>
          ${s.read_inbox ? `<div class="muted small">reads its inbox${s.attachments_to_desk ? " · files to the AI Desk" : ""}</div>` : ""}</td>
        <td>${escapeHtml(s.display_name || "")}</td>
        <td>${s.login === "own" ? `own mailbox${s.has_password ? "" : ` <span class="neg small">— no password</span>`}` : "server's login"}</td>
        <td>${this.statusChip(s)}</td>
        <td class="nowrap"><a class="link" onclick="EmailSettings.edit(${s.id})">Edit</a>
          ${s.login === "own" ? ` · <a class="link" onclick="EmailSettings.password(${s.id})">Password</a>` : ""}
          · <a class="link" onclick="EmailSettings.check(${s.id})">Check</a> · <a class="link" onclick="EmailSettings.test(${s.id})">Send Test</a></td></tr>`).join("")
        || `<tr><td colspan="5" class="muted">No addresses yet${d.server.from ? ` — everything goes from the server's own ${escapeHtml(d.server.from)}` : ""}.</td></tr>`}
      </tbody></table>
      <button class="small-btn" style="margin-top:8px;" data-icon="plus" onclick="EmailSettings.edit(null)">Add Address</button>
      <h4 style="margin:16px 0 6px;">Which Address Each Kind Of Email Uses</h4>
      <p class="muted small" style="margin-top:0;">A customer's or vendor's own card can choose differently, and anyone sending can still pick another one in the email form.</p>
      <div class="field-grid">${Object.entries(d.kinds).map(([k, l]) => `<div><label>${escapeHtml(l)}</label><select data-kind="${k}" onchange="EmailSettings.saveDefaults()">${opts(d.defaults[k])}</select></div>`).join("")}</div>
      <span id="em-saved" class="muted small"></span>`;
  },
  async saveDefaults() {
    const defaults = {};
    document.querySelectorAll("#email-settings select[data-kind]").forEach(s => { if (s.value) defaults[s.dataset.kind] = parseInt(s.value); });
    try { this.data.defaults = await apiFetch("/api/email/defaults", { method: "PUT", body: JSON.stringify({ defaults }) }); document.getElementById("em-saved").textContent = "Saved"; }
    catch (e) { alert(e.message); }
  },
  async edit(id) {
    const s = id ? this.data.senders.find(x => x.id === id) : { login: "own", smtp_security: "ssl", smtp_port: 465, save_sent: true, active: true, attachments_to_desk: true, inbox_folder: "INBOX" };
    const v = k => escapeHtml(s[k] == null ? "" : String(s[k]));
    const ck = k => s[k] ? "checked" : "";
    const { value, el } = await askDialog({ title: id ? "Edit Address" : "Add An Address", wide: true,
      body: `<div class="field-grid">
          <div><label>Address</label><input type="email" class="e-address" value="${v("address")}" placeholder="robert.jones@atindsupplies.com"></div>
          <div><label>Shown As (Name)</label><input type="text" class="e-display_name" value="${v("display_name")}" placeholder="Robert Jones, American Traders"></div>
          <div><label>Replies Go To (If Not This Address)</label><input type="email" class="e-reply_to" value="${v("reply_to")}"></div>
          <div class="wide"><label>Signature (Added Under Every Email From It)</label><textarea class="e-signature" rows="3">${v("signature")}</textarea></div>
          <div><label>How It Logs In</label><select class="e-login" onchange="this.closest('.ask-dialog').querySelector('.em-own').hidden = this.value !== 'own'">
            <option value="own" ${s.login === "own" ? "selected" : ""}>Its own mailbox password</option>
            <option value="server" ${s.login === "server" ? "selected" : ""}>The server's shared sending login</option></select></div>
          <div><label class="check-label"><input type="checkbox" class="e-bcc_me" ${ck("bcc_me")}> Send me a copy (BCC) of each email</label>
            <label class="check-label"><input type="checkbox" class="e-active" ${ck("active")}> In use</label></div>
        </div>
        <div class="em-own" ${s.login === "own" ? "" : "hidden"}>
          <h4 style="margin:12px 0 4px;">Mail Server</h4>
          <p class="muted small" style="margin-top:0;">For cPanel / web-host mail (atindsupplies.com): server <code>mail.atindsupplies.com</code>, port 465 (SSL), user = the full address.</p>
          <div class="field-grid">
            <div><label>Sending Server (SMTP)</label><input type="text" class="e-smtp_host" value="${v("smtp_host")}" placeholder="mail.atindsupplies.com"></div>
            <div><label>Port</label><input type="number" class="e-smtp_port" value="${v("smtp_port")}"></div>
            <div><label>Security</label><select class="e-smtp_security">${["ssl", "starttls", "none"].map(x => `<option ${x === s.smtp_security ? "selected" : ""}>${x}</option>`).join("")}</select></div>
            <div><label>Username (Blank = The Address)</label><input type="text" class="e-smtp_username" value="${v("smtp_username")}"></div>
            <div><label>Password ${s.has_password ? `<span class="muted small">(saved — type only to change)</span>` : ""}</label><input type="password" class="e-password" autocomplete="new-password"></div>
            <div><label>Reading Server (IMAP, Blank = Same)</label><input type="text" class="e-imap_host" value="${v("imap_host")}"></div>
          </div>
          <label class="check-label"><input type="checkbox" class="e-save_sent" ${ck("save_sent")}> Put a copy in this mailbox's Sent folder (shows in Outlook)</label>
          <label class="check-label"><input type="checkbox" class="e-read_inbox" ${ck("read_inbox")}> Read its inbox: replies show on their order / invoice / PO</label>
          <label class="check-label"><input type="checkbox" class="e-attachments_to_desk" ${ck("attachments_to_desk")}> …and documents emailed to it go to the AI Desk</label>
          <p class="muted small">AT-HUB only reads new mail — it never marks anything read, moves or deletes it.</p>
        </div>`,
      buttons: [{ label: id ? "Save" : "Add", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
    if (value !== "go") return;
    const g = k => el.querySelector(`.e-${k}`);
    const body = { address: g("address").value.trim(), display_name: g("display_name").value.trim() || null, reply_to: g("reply_to").value.trim() || null,
      signature: g("signature").value, login: g("login").value, bcc_me: g("bcc_me").checked, active: g("active").checked,
      smtp_host: g("smtp_host").value.trim() || null, smtp_port: parseInt(g("smtp_port").value) || null, smtp_security: g("smtp_security").value,
      smtp_username: g("smtp_username").value.trim() || null, imap_host: g("imap_host").value.trim() || null,
      save_sent: g("save_sent").checked, read_inbox: g("read_inbox").checked, attachments_to_desk: g("attachments_to_desk").checked,
      inbox_folder: s.inbox_folder || "INBOX", password: g("password").value || null };
    try {
      const saved = await apiFetch(id ? `/api/email/senders/${id}` : "/api/email/senders", { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
      await this.load();
      if (body.login === "own" && (body.password || !id)) this.check(saved.id);
    } catch (e) { alert(e.message); }
  },
  async password(id) {
    const s = this.data.senders.find(x => x.id === id);
    const { value, el } = await askDialog({ title: "New Password",
      body: `<p style="margin-top:0;"><strong>${escapeHtml(s.address)}</strong></p><p class="muted small">The mailbox's password (as set on the mail server). Saved encrypted, checked straight away.</p>
        <input type="password" class="np" autocomplete="new-password">`,
      buttons: [{ label: "Save And Check", value: "go", cls: "confirm-btn" }, { label: "Cancel", value: null, cls: "secondary" }] });
    if (value !== "go") return;
    try {
      const r = await apiFetch(`/api/email/senders/${id}/password`, { method: "POST", body: JSON.stringify({ password: el.querySelector(".np").value }) });
      toast(r.ok ? `${s.address}: working` : `${s.address}: ${r.error}`);
      this.load();
    } catch (e) { alert(e.message); }
  },
  async check(id) {
    const s = this.data.senders.find(x => x.id === id) || {};
    toast(`Checking ${s.address || "the address"}…`);
    try { const r = await apiFetch(`/api/email/senders/${id}/check`, { method: "POST" }); toast(r.ok ? `${s.address}: logs in fine` : `${s.address}: ${r.error}`); }
    catch (e) { alert(e.message); }
    this.load();
  },
  async test(id) {
    const me = (AuthGuard.getUser() || {}).email || "";
    const to = prompt("Send a test email to:", me);
    if (!to) return;
    try { await apiFetch(`/api/email/senders/${id}/test`, { method: "POST", body: JSON.stringify({ to }) }); toast("Test email sent"); }
    catch (e) { alert(e.message); }
    this.load();
  },
};
