// iOS-style contact cards, shared by Customers and Vendors.
// A page includes the shell (contactCardShell()) and calls ContactCards.init({...}) with what differs:
// endpoint, label suggestions, what each labelled email is used for, the documents list, extra sections.

function contactCardShell(newLabel) {
  return `
    <div class="contacts">
      <div class="card c-list">
        <div class="c-list-head">
          <input type="text" id="cc-search" placeholder="Search name, person, email, phone, city, tag…">
          <div class="c-tag-filter" id="cc-tag-filter"></div>
        </div>
        <div class="c-rows" id="cc-list"><div class="muted" style="padding:14px;">Loading…</div></div>
      </div>
      <div class="card" id="cc-card"><div class="empty-pick">Pick one to see the card. <a class="link" onclick="ContactCards.create()">${escapeHtml(newLabel)}</a></div></div>
    </div>
    <datalist id="cc-dl-tags"></datalist>`;
}

const ContactCards = {
  cfg: null,
  rows: [],
  currentId: null,  // record on the card (null while a new one is being entered)
  draft: null,      // { name, is_active, details } while editing
  tagFilter: null,

  SECTIONS: [
    { key: "phones", title: "Phone", href: v => `tel:${v.replace(/[^\d+]/g, "")}` },
    { key: "emails", title: "Email", href: v => `mailto:${v}` },
    { key: "addresses", title: "Address", multiline: true, href: v => `https://maps.google.com/?q=${encodeURIComponent(v)}` },
    { key: "websites", title: "Website", href: v => (/^https?:/i.test(v) ? v : `https://${v}`) },
  ],

  /* cfg: { endpoint, noun, labels: {phones, emails, addresses, websites, roles}, hints: {key: text},
            usedFor(rec, key, entry, index) -> [notes], newDraft() -> details, loadExtra() -> Promise,
            headExtra(rec) -> html, quickExtra(rec) -> html, body(rec) -> html (stats/docs/sections), afterRender(rec) } */
  async init(cfg) {
    this.cfg = cfg;
    document.getElementById("cc-search").addEventListener("input", () => this.renderList());
    const lists = Object.entries(cfg.labels).map(([k, opts]) => `<datalist id="cc-dl-${k}">${opts.map(o => `<option>${escapeHtml(o)}</option>`).join("")}</datalist>`);
    document.body.insertAdjacentHTML("beforeend", lists.join(""));
    await this.load();
    const id = parseInt(new URLSearchParams(location.search).get("id"));
    if (id && this.rows.some(r => r.id === id)) this.show(id);
  },

  async load() {
    try {
      const [rows] = await Promise.all([apiFetch(this.cfg.endpoint), this.cfg.loadExtra ? this.cfg.loadExtra() : null]);
      this.rows = rows.sort((a, b) => a.name.localeCompare(b.name));
      this.renderList();
    } catch (err) {
      document.getElementById("cc-list").innerHTML = `<div class="error" style="padding:14px;">${escapeHtml(err.message)}</div>`;
    }
  },

  initials: name => (name || "?").replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("") || "?",
  avatar(rec, cls = "") {
    const hue = [212, 262, 330, 20, 150, 190, 40, 290][(rec.id || 0) % 8];
    return `<div class="avatar ${cls}" style="background:linear-gradient(160deg, hsl(${hue} 55% 62%), hsl(${hue} 50% 46%))">${escapeHtml(this.initials(rec.name))}</div>`;
  },
  city(rec) {
    const a = (rec.details.addresses.find(r => /ship|pickup/i.test(r.label)) || rec.details.addresses[0] || {}).value || "";
    const lines = a.split("\n").map(s => s.trim()).filter(Boolean).filter(l => !/^(united states|usa)$/i.test(l));
    return lines.length > 1 ? lines[lines.length - 1] : "";
  },
  searchText(rec) {
    const d = rec.details;
    return [rec.name, rec.code, ...d.tags, d.notes, ...this.SECTIONS.flatMap(s => d[s.key].map(r => `${r.label} ${r.value}`)),
      ...d.people.map(p => `${p.name} ${p.role} ${p.phone} ${p.email}`)].join(" ").toLowerCase();
  },
  allTags() { return [...new Set(this.rows.flatMap(r => r.details.tags))].sort((a, b) => a.localeCompare(b)); },

  // ---- list ----
  renderList() {
    const q = document.getElementById("cc-search").value.trim().toLowerCase();
    const tags = this.allTags();
    document.getElementById("cc-tag-filter").innerHTML = tags.map((t, i) =>
      `<span class="chip filter ${this.tagFilter === t ? "on" : ""}" onclick="ContactCards.toggleTag(${i})">${escapeHtml(t)}</span>`).join("");
    document.getElementById("cc-dl-tags").innerHTML = tags.map(t => `<option>${escapeHtml(t)}</option>`).join("");
    const list = this.rows.filter(r => (!q || this.searchText(r).includes(q)) && (!this.tagFilter || r.details.tags.includes(this.tagFilter)));
    const el = document.getElementById("cc-list");
    if (!list.length) { el.innerHTML = `<div class="muted" style="padding:14px;">${this.rows.length ? "Nothing matches." : "None yet."}</div>`; return; }
    let letter = "";
    el.innerHTML = list.map(r => {
      const L = (r.name[0] || "#").toUpperCase().replace(/[^A-Z]/, "#");
      const head = L !== letter ? `<div class="c-letter">${(letter = L)}</div>` : "";
      const person = r.details.people[0] ? r.details.people[0].name : "";
      return `${head}<div class="c-row ${r.id === this.currentId ? "active" : ""}" onclick="ContactCards.show(${r.id})">${this.avatar(r)}
        <div class="c-main"><div class="c-name">${escapeHtml(r.name)}${r.is_active === false ? ' <span class="muted small">(inactive)</span>' : ""}</div>
        <div class="c-sub">${escapeHtml([r.code, person, this.city(r)].filter(Boolean).join(" · ") || r.details.tags.join(", "))}</div></div></div>`;
    }).join("");
  },
  toggleTag(i) {
    const t = this.allTags()[i];
    this.tagFilter = this.tagFilter === t ? null : t;
    this.renderList();
  },

  // ---- view ----
  show(id) {
    this.currentId = id;
    this.draft = null;
    history.replaceState(null, "", `?id=${id}`);
    this.renderList();
    const rec = this.rows.find(r => r.id === id);
    const d = rec.details;
    const firstOf = key => (d[key][0] || {}).value;
    const quick = (label, glyph, href) => href
      ? `<a href="${escapeHtml(href)}" ${href.startsWith("http") ? 'target="_blank"' : ""}><b>${glyph}</b>${label}</a>`
      : `<span class="off"><b>${glyph}</b>${label}</span>`;
    const S = this.SECTIONS;
    const usedFor = (key, r, i) => {
      const notes = this.cfg.usedFor(rec, key, r, i);
      return notes.length ? `<div class="used-for">${notes.join(" · ")}</div>` : "";
    };

    document.getElementById("cc-card").innerHTML = `
      <div class="card-actions"><button class="secondary small-btn" onclick="ContactCards.edit()">Edit</button></div>
      <div class="card-head">
        <div style="display:flex; justify-content:center;">${this.avatar(rec, "big")}</div>
        <h2>${escapeHtml(rec.name)}</h2>
        ${this.cfg.headExtra ? this.cfg.headExtra(rec) : ""}
        <div class="chips">${d.tags.map(t => `<span class="chip">${escapeHtml(t)}</span>`).join("") || '<span class="muted small">No tags</span>'}</div>
        <div class="quick">
          ${quick("call", "☏", firstOf("phones") && S[0].href(firstOf("phones")))}
          ${quick("email", "✉", firstOf("emails") && S[1].href(firstOf("emails")))}
          ${quick("map", "⌖", firstOf("addresses") && S[2].href(firstOf("addresses")))}
          ${this.cfg.quickExtra ? this.cfg.quickExtra(rec) : ""}
        </div>
      </div>

      ${d.people.length ? `<div class="grp"><div class="grp-title">People</div>${d.people.map(p => `
        <div class="ent"><div class="lab">${escapeHtml(p.role || "contact")}</div>
          <div class="val"><div class="who">${escapeHtml(p.name || "")}</div>
            ${[p.phone && `<a class="link" href="${S[0].href(p.phone)}">${escapeHtml(p.phone)}</a>`, p.email && `<a class="link" href="mailto:${escapeHtml(p.email)}">${escapeHtml(p.email)}</a>`].filter(Boolean).join(" · ")}</div><div></div></div>`).join("")}</div>` : ""}

      ${S.filter(s => d[s.key].length).map(s => `<div class="grp"><div class="grp-title">${s.title}</div>${d[s.key].map((r, i) => `
        <div class="ent"><div class="lab">${escapeHtml(r.label || "other")}${usedFor(s.key, r, i)}</div>
          <div class="val"><a class="link" href="${escapeHtml(s.href(r.value))}" ${s.key === "addresses" || s.key === "websites" ? 'target="_blank"' : ""}>${escapeHtml(r.value)}</a></div>
          <div>${s.key === "addresses" || s.key === "emails" ? `<a class="link small" onclick="ContactCards.copy('${s.key}', ${i})">copy</a>` : ""}</div></div>`).join("")}</div>`).join("")}

      ${d.notes ? `<div class="grp"><div class="grp-title">Notes</div><div class="ent" style="grid-template-columns:1fr;"><div class="val">${escapeHtml(d.notes)}</div></div></div>` : ""}
      ${this.cfg.body ? this.cfg.body(rec) : ""}
    `;
    if (this.cfg.afterRender) this.cfg.afterRender(rec);
  },
  copy(key, i) {
    navigator.clipboard.writeText(this.rows.find(r => r.id === this.currentId).details[key][i].value);
  },

  // ---- edit ----
  create() {
    this.currentId = null;
    this.draft = { name: "", is_active: true, details: this.cfg.newDraft() };
    this.renderList();
    this.renderEdit();
  },
  edit() {
    const rec = this.rows.find(r => r.id === this.currentId);
    this.draft = JSON.parse(JSON.stringify({ name: rec.name, is_active: rec.is_active, details: rec.details }));
    this.renderEdit();
  },
  cancel() {
    if (this.currentId) return this.show(this.currentId);
    this.draft = null;
    document.getElementById("cc-card").innerHTML = `<div class="empty-pick">Pick one to see the card.</div>`;
  },
  // inputs write straight into the draft: ContactCards.set('emails', 2, 'label', value)
  set(key, i, field, value) {
    if (key === null) this.draft[field] = value;
    else if (i === null) this.draft.details[key] = value;
    else this.draft.details[key][i][field] = value;
  },
  remove(key, i) { this.draft.details[key].splice(i, 1); this.renderEdit(); },
  add(key) {
    if (key === "people") this.draft.details.people.push({ name: "", role: "", phone: "", email: "" });
    else {
      const used = this.draft.details[key].map(r => r.label);
      this.draft.details[key].push({ label: this.cfg.labels[key].find(o => !used.includes(o)) || "other", value: "" });
    }
    this.renderEdit();
    const rows = document.querySelectorAll(`[data-sec="${key}"] .ent`);
    const last = rows[rows.length - 1];
    if (last) (last.querySelector(".person input, div:nth-child(2) input, textarea") || {}).focus?.();
  },
  addTag(v) {
    const t = v.trim();
    if (t && !this.draft.details.tags.some(x => x.toLowerCase() === t.toLowerCase())) this.draft.details.tags.push(t);
    this.renderEdit();
    document.querySelector("#cc-card .chips input").focus();
  },
  removeTag(i) { this.draft.details.tags.splice(i, 1); this.renderEdit(); },

  renderEdit() {
    const d = this.draft.details;
    const a = v => escapeHtml(v || "");
    const hint = key => this.cfg.hints && this.cfg.hints[key] ? ` <span class="used-for">${this.cfg.hints[key]}</span>` : "";
    document.getElementById("cc-card").innerHTML = `
      <div class="card-actions">
        <button class="secondary small-btn" onclick="ContactCards.cancel()">Cancel</button>
        <button class="small-btn" onclick="ContactCards.save()">Done</button>
      </div>
      <div class="card-head">
        <div style="display:flex; justify-content:center;">${this.avatar({ id: this.currentId || 0, name: this.draft.name }, "big")}</div>
        <div style="margin:10px 0;"><input class="name-input" placeholder="Company name" value="${a(this.draft.name)}" oninput="ContactCards.set(null, null, 'name', this.value)"></div>
        <div class="chips">${d.tags.map((t, i) => `<span class="chip">${escapeHtml(t)} <span class="x" title="Remove tag" onclick="ContactCards.removeTag(${i})">×</span></span>`).join("")}
          <input list="cc-dl-tags" placeholder="+ add tag" onkeydown="if (event.key === 'Enter' && this.value.trim()) ContactCards.addTag(this.value)"></div>
      </div>

      <div class="grp" data-sec="people"><div class="grp-title">People${hint("people")}</div>
        ${d.people.map((p, i) => `<div class="ent"><div class="lab"><input list="cc-dl-roles" placeholder="role" value="${a(p.role)}" oninput="ContactCards.set('people', ${i}, 'role', this.value)"></div>
          <div class="person">
            <input placeholder="Name" value="${a(p.name)}" oninput="ContactCards.set('people', ${i}, 'name', this.value)">
            <input placeholder="Phone" value="${a(p.phone)}" oninput="ContactCards.set('people', ${i}, 'phone', this.value)">
            <input placeholder="Email" value="${a(p.email)}" oninput="ContactCards.set('people', ${i}, 'email', this.value)" style="grid-column: span 2;">
          </div>
          <button class="minus" title="Remove" onclick="ContactCards.remove('people', ${i})">−</button></div>`).join("")}
        <div class="add-row" onclick="ContactCards.add('people')">⊕ add person</div>
      </div>

      ${this.SECTIONS.map(s => `<div class="grp" data-sec="${s.key}"><div class="grp-title">${s.title}${hint(s.key)}</div>
        ${d[s.key].map((r, i) => `<div class="ent"><div class="lab"><input list="cc-dl-${s.key}" value="${a(r.label)}" oninput="ContactCards.set('${s.key}', ${i}, 'label', this.value)"></div>
          <div>${s.multiline ? `<textarea rows="3" oninput="ContactCards.set('${s.key}', ${i}, 'value', this.value)">${a(r.value)}</textarea>`
                             : `<input value="${a(r.value)}" oninput="ContactCards.set('${s.key}', ${i}, 'value', this.value)">`}</div>
          <button class="minus" title="Remove" onclick="ContactCards.remove('${s.key}', ${i})">−</button></div>`).join("")}
        <div class="add-row" onclick="ContactCards.add('${s.key}')">⊕ add ${s.title.toLowerCase()}</div></div>`).join("")}

      <div class="grp"><div class="grp-title">Notes</div><div class="ent" style="grid-template-columns:1fr;">
        <textarea rows="4" placeholder="Hours, packing rules, anything worth remembering…" oninput="ContactCards.set('notes', null, null, this.value)">${a(d.notes)}</textarea></div></div>

      ${this.currentId ? `<label style="display:flex; gap:8px; align-items:center; margin-top:8px;"><input type="checkbox" ${this.draft.is_active !== false ? "checked" : ""} onchange="ContactCards.set(null, null, 'is_active', this.checked)" style="width:auto;"> Active</label>` : ""}
      <div id="cc-error" class="error"></div>
    `;
  },

  async save() {
    const err = document.getElementById("cc-error");
    err.textContent = "";
    const pending = document.querySelector("#cc-card .chips input");  // a tag typed but not Entered still counts
    if (pending && pending.value.trim() && !this.draft.details.tags.includes(pending.value.trim())) this.draft.details.tags.push(pending.value.trim());
    if (!this.draft.name.trim()) { err.textContent = "Company name is required."; return; }
    const body = { name: this.draft.name.trim(), details: this.draft.details };
    if (this.currentId) body.is_active = this.draft.is_active !== false;
    try {
      const saved = await apiFetch(this.currentId ? `${this.cfg.endpoint}${this.currentId}` : this.cfg.endpoint,
                                   { method: this.currentId ? "PUT" : "POST", body: JSON.stringify(body) });
      await this.load();
      this.show(saved.id);
    } catch (e) {
      err.textContent = e.message;
    }
  },
};
