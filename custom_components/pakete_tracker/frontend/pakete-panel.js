/**
 * Pakete Tracker – Seitenleisten-App für Home Assistant.
 * Bewusst ohne Build-Schritt / externe Abhängigkeiten.
 */

const STATUS = {
  unknown: { label: "Unbekannt", icon: "mdi:help-circle-outline", color: "var(--secondary-text-color)" },
  pre_transit: { label: "Angekündigt", icon: "mdi:clipboard-clock-outline", color: "#8e8e93" },
  in_transit: { label: "Unterwegs", icon: "mdi:truck-fast-outline", color: "#2f80ed" },
  out_for_delivery: { label: "In Zustellung", icon: "mdi:truck-delivery-outline", color: "#f2994a" },
  ready_for_pickup: { label: "Abholbereit", icon: "mdi:storefront-outline", color: "#9b51e0" },
  delivered: { label: "Zugestellt", icon: "mdi:package-variant-closed-check", color: "#27ae60" },
  exception: { label: "Problem", icon: "mdi:alert-circle-outline", color: "#eb5757" },
};

const CONNECTION = {
  connected: { label: "Verbunden", cls: "ok" },
  email: { label: "Über E-Mail", cls: "info" },
  manual: { label: "Nur manuell", cls: "muted" },
  not_connected: { label: "Nicht angemeldet", cls: "muted" },
  error: { label: "Fehler", cls: "err" },
};

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function dayLabel(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((d - today) / 86400000);
  if (diff === 0) return "Heute";
  if (diff === 1) return "Morgen";
  if (diff === -1) return "Gestern";
  return d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" });
}

class PaketeTrackerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._unsub = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menuButton) this._menuButton.hass = hass;
    if (!this._unsub && hass) this._subscribe();
    if (!this._built) this._build();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuButton) this._menuButton.narrow = narrow;
  }

  set panel(_p) {}

  disconnectedCallback() {
    if (this._unsub) {
      this._unsub.then((unsub) => unsub()).catch(() => {});
      this._unsub = null;
    }
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeMessage(
      (data) => {
        this._data = data;
        this._render();
      },
      { type: "pakete_tracker/subscribe" }
    );
    this._unsub.catch((err) => {
      this._unsub = null;
      this._data = { loaded: false, carriers: [], error: err.message || String(err) };
      this._render();
    });
  }

  _build() {
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="toolbar">
        <span class="menu"></span>
        <div class="title">Pakete</div>
        <button class="icon-btn" data-action="refresh" title="Aktualisieren"><ha-icon icon="mdi:refresh"></ha-icon></button>
        <button class="primary" data-action="open-add"><ha-icon icon="mdi:plus"></ha-icon><span>Paket</span></button>
      </div>
      <div class="content"><div class="loading">Lade Sendungen …</div></div>
      <div class="dialog-host"></div>
    `;
    this._menuButton = document.createElement("ha-menu-button");
    this._menuButton.hass = this._hass;
    this._menuButton.narrow = this._narrow;
    this.shadowRoot.querySelector(".menu").appendChild(this._menuButton);
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("submit", (ev) => this._onSubmit(ev));
  }

  // ------------------------------------------------------------------ //

  _render() {
    if (!this._built) return;
    const content = this.shadowRoot.querySelector(".content");
    const data = this._data;

    if (!data || !data.loaded) {
      content.innerHTML = `
        <div class="empty-state">
          <ha-icon icon="mdi:package-variant-remove"></ha-icon>
          <p>Der Pakete Tracker ist nicht eingerichtet.</p>
          <p class="sub">Einstellungen → Geräte &amp; Dienste → Integration hinzufügen → „Pakete Tracker“.</p>
          ${data && data.error ? `<p class="sub">${esc(data.error)}</p>` : ""}
        </div>`;
      return;
    }

    const all = data.carriers.flatMap((c) => c.packages);
    const active = all.filter((p) => p.status !== "delivered");
    const todayIso = new Date().toLocaleDateString("sv-SE");
    const today = active.filter((p) => p.expected_delivery === todayIso || p.status === "out_for_delivery");
    const delivered = all.filter((p) => p.status === "delivered");

    content.innerHTML = `
      <div class="summary">
        ${this._tile("mdi:truck-fast-outline", active.length, "Unterwegs")}
        ${this._tile("mdi:calendar-today", today.length, "Heute erwartet")}
        ${this._tile("mdi:package-variant-closed-check", delivered.length, "Zugestellt")}
      </div>
      ${!data.email_configured ? this._emailHint(data) : ""}
      <div class="grid">
        ${data.carriers.map((c) => this._carrierCard(c, data.is_admin)).join("")}
      </div>
      <div class="footer">
        ${data.last_update ? `Zuletzt aktualisiert: ${new Date(data.last_update).toLocaleString("de-DE")}` : ""}
      </div>
    `;
  }

  _tile(icon, value, label) {
    return `<div class="tile"><ha-icon icon="${icon}"></ha-icon><div><div class="value">${value}</div><div class="label">${label}</div></div></div>`;
  }

  _emailHint(data) {
    const needs = data.carriers.some((c) => ["amazon", "dpd", "gls", "hermes"].includes(c.id));
    if (!needs) return "";
    return `
      <div class="hint">
        <ha-icon icon="mdi:email-search-outline"></ha-icon>
        <div>
          <b>Tipp:</b> Verbinde dein E-Mail-Postfach, damit Amazon-Bestellungen und Sendungen von DPD, GLS
          und Hermes automatisch erkannt werden.
          ${data.is_admin ? `<a href="/config/integrations/integration/pakete_tracker">Jetzt einrichten</a>` : ""}
        </div>
      </div>`;
  }

  _carrierCard(carrier, isAdmin) {
    const conn = CONNECTION[carrier.connection] || CONNECTION.manual;
    const active = carrier.packages.filter((p) => p.status !== "delivered");
    const initials = carrier.name.slice(0, 3).toUpperCase();
    const rows = carrier.packages.length
      ? carrier.packages.map((p) => this._packageRow(p)).join("")
      : `<div class="no-packages">Keine Sendungen</div>`;
    const loginHint =
      carrier.connection === "not_connected" && isAdmin
        ? `<a class="login" href="/config/integrations/integration/pakete_tracker">Anmelden unter Konfigurieren →</a>`
        : "";
    return `
      <div class="card">
        <div class="card-header">
          <div class="logo" style="background:${esc(carrier.color)};color:${esc(carrier.text_color)}">${esc(initials)}</div>
          <div class="carrier">
            <div class="name">${esc(carrier.name)}</div>
            <span class="chip ${conn.cls}" title="${esc(carrier.error || "")}">${conn.label}</span>
          </div>
          <div class="count" title="Pakete unterwegs">${active.length}</div>
        </div>
        ${carrier.error ? `<div class="error">${esc(carrier.error)}</div>` : ""}
        ${loginHint}
        <div class="packages">${rows}</div>
      </div>`;
  }

  _packageRow(p) {
    const st = STATUS[p.status] || STATUS.unknown;
    const title = p.name || p.tracking_number;
    const sub = [p.name ? p.tracking_number : null, p.status_text].filter(Boolean).join(" · ");
    const when = p.status === "delivered" ? dayLabel(p.last_update && p.last_update.slice(0, 10)) : dayLabel(p.expected_delivery);
    return `
      <div class="package">
        <ha-icon icon="${st.icon}" style="color:${st.color}"></ha-icon>
        <div class="info">
          <div class="pname">${esc(title)}</div>
          <div class="psub">${esc(sub)}</div>
        </div>
        <div class="meta">
          <span class="status" style="color:${st.color}">${st.label}</span>
          ${when ? `<span class="when">${esc(when)}</span>` : ""}
        </div>
        <div class="actions">
          ${p.url ? `<a class="icon-btn small" href="${esc(p.url)}" target="_blank" rel="noreferrer" title="Beim Dienstleister öffnen"><ha-icon icon="mdi:open-in-new"></ha-icon></a>` : ""}
          <button class="icon-btn small" data-action="remove" data-carrier="${esc(p.carrier)}" data-number="${esc(p.tracking_number)}" title="Ausblenden"><ha-icon icon="mdi:close"></ha-icon></button>
        </div>
      </div>`;
  }

  // ------------------------------------------------------------------ //

  _openAdd() {
    const carriers = (this._data && this._data.carriers) || [];
    const host = this.shadowRoot.querySelector(".dialog-host");
    host.innerHTML = `
      <div class="backdrop" data-action="close">
        <form class="dialog">
          <h2>Paket hinzufügen</h2>
          <label>Dienstleister
            <select name="carrier" required>
              ${carriers.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}
            </select>
          </label>
          <label>Sendungsnummer <small>(Amazon: Bestellnummer)</small>
            <input name="tracking_number" required minlength="4" maxlength="40" autocomplete="off">
          </label>
          <label>Bezeichnung <small>(optional)</small>
            <input name="name" maxlength="80" placeholder="z. B. Neue Kopfhörer">
          </label>
          <div class="form-error"></div>
          <div class="buttons">
            <button type="button" class="text" data-action="close">Abbrechen</button>
            <button type="submit" class="primary">Hinzufügen</button>
          </div>
        </form>
      </div>`;
    host.querySelector("input[name=tracking_number]").focus();
  }

  _closeDialog() {
    this.shadowRoot.querySelector(".dialog-host").innerHTML = "";
  }

  async _onSubmit(ev) {
    ev.preventDefault();
    const form = ev.target;
    const data = Object.fromEntries(new FormData(form).entries());
    if (!data.name) delete data.name;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await this._hass.callService("pakete_tracker", "add_package", data);
      this._closeDialog();
    } catch (err) {
      form.querySelector(".form-error").textContent = err.message || String(err);
      btn.disabled = false;
    }
  }

  async _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset && el.dataset.action);
    if (!target) return;
    const action = target.dataset.action;
    if (action === "close" && ev.target !== target) return; // Klick im Dialog, nicht auf Hintergrund
    switch (action) {
      case "open-add":
        this._openAdd();
        break;
      case "close":
        this._closeDialog();
        break;
      case "refresh":
        target.classList.add("spin");
        try {
          await this._hass.callService("pakete_tracker", "refresh", {});
        } finally {
          target.classList.remove("spin");
        }
        break;
      case "remove":
        if (confirm(`Sendung ${target.dataset.number} aus der Übersicht entfernen?`)) {
          await this._hass.callService("pakete_tracker", "remove_package", {
            carrier: target.dataset.carrier,
            tracking_number: target.dataset.number,
          });
        }
        break;
    }
  }
}

const STYLES = `
  :host { display:block; min-height:100vh; background:var(--primary-background-color); color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display:flex; align-items:center; gap:8px; height:56px; padding:0 12px; background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff); position:sticky; top:0; z-index:2; }
  .toolbar .title { flex:1; font-size:20px; font-weight:400; margin-left:4px; }
  button { font:inherit; cursor:pointer; }
  .icon-btn { display:inline-flex; align-items:center; justify-content:center; width:40px; height:40px; border:none; border-radius:50%; background:transparent; color:inherit; text-decoration:none; }
  .icon-btn:hover { background:rgba(127,127,127,.15); }
  .icon-btn.small { width:32px; height:32px; color:var(--secondary-text-color); --mdc-icon-size:18px; }
  .icon-btn.spin ha-icon { animation:spin 1s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .primary { display:inline-flex; align-items:center; gap:4px; border:none; border-radius:18px; padding:6px 14px 6px 10px; background:var(--accent-color, #03a9f4); color:#fff; font-weight:500; }
  .toolbar .primary { background:rgba(255,255,255,.2); }
  .text { border:none; background:none; color:var(--primary-color); padding:8px 12px; font-weight:500; }
  .content { max-width:1200px; margin:0 auto; padding:16px; }
  .loading, .footer { color:var(--secondary-text-color); text-align:center; padding:16px; font-size:13px; }
  .summary { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:16px; }
  .tile { display:flex; align-items:center; gap:12px; padding:14px 16px; background:var(--card-background-color); border-radius:var(--ha-card-border-radius,12px); box-shadow:var(--ha-card-box-shadow,none); border:1px solid var(--divider-color); }
  .tile ha-icon { color:var(--primary-color); --mdc-icon-size:28px; }
  .tile .value { font-size:24px; font-weight:500; line-height:1; }
  .tile .label { font-size:13px; color:var(--secondary-text-color); margin-top:2px; }
  .hint { display:flex; gap:12px; align-items:flex-start; padding:12px 16px; margin-bottom:16px; border-radius:12px; background:rgba(47,128,237,.1); font-size:14px; }
  .hint ha-icon { color:#2f80ed; flex-shrink:0; }
  .hint a, .login { color:var(--primary-color); font-weight:500; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); gap:16px; }
  .card { background:var(--card-background-color); border-radius:var(--ha-card-border-radius,12px); box-shadow:var(--ha-card-box-shadow,none); border:1px solid var(--divider-color); overflow:hidden; }
  .card-header { display:flex; align-items:center; gap:12px; padding:14px 16px; border-bottom:1px solid var(--divider-color); }
  .logo { width:44px; height:44px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:13px; letter-spacing:.5px; flex-shrink:0; }
  .carrier { flex:1; min-width:0; }
  .carrier .name { font-size:17px; font-weight:500; }
  .chip { display:inline-block; font-size:11px; padding:1px 8px; border-radius:10px; margin-top:3px; }
  .chip.ok { background:rgba(39,174,96,.15); color:#27ae60; }
  .chip.info { background:rgba(47,128,237,.15); color:#2f80ed; }
  .chip.muted { background:rgba(127,127,127,.15); color:var(--secondary-text-color); }
  .chip.err { background:rgba(235,87,87,.15); color:#eb5757; }
  .count { min-width:32px; height:32px; padding:0 6px; border-radius:16px; background:var(--primary-color); color:var(--text-primary-color,#fff); display:flex; align-items:center; justify-content:center; font-weight:500; box-sizing:border-box; }
  .error { margin:8px 16px 0; padding:8px 10px; border-radius:8px; background:rgba(235,87,87,.1); color:#eb5757; font-size:12px; }
  .login { display:block; padding:10px 16px 0; font-size:13px; text-decoration:none; }
  .packages { padding:4px 0; }
  .no-packages { padding:16px; color:var(--secondary-text-color); font-size:14px; text-align:center; }
  .package { display:flex; align-items:center; gap:12px; padding:10px 8px 10px 16px; }
  .package + .package { border-top:1px solid var(--divider-color); }
  .info { flex:1; min-width:0; }
  .pname { font-size:14px; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .psub { font-size:12px; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .meta { display:flex; flex-direction:column; align-items:flex-end; font-size:12px; white-space:nowrap; }
  .status { font-weight:500; }
  .when { color:var(--secondary-text-color); }
  .actions { display:flex; }
  .empty-state { text-align:center; padding:64px 16px; color:var(--secondary-text-color); }
  .empty-state ha-icon { --mdc-icon-size:64px; }
  .empty-state .sub { font-size:13px; }
  .backdrop { position:fixed; inset:0; background:rgba(0,0,0,.5); display:flex; align-items:center; justify-content:center; z-index:10; padding:16px; }
  .dialog { background:var(--card-background-color); color:var(--primary-text-color); border-radius:16px; padding:20px 24px; width:100%; max-width:420px; display:flex; flex-direction:column; gap:14px; }
  .dialog h2 { margin:0 0 4px; font-size:20px; font-weight:400; }
  .dialog label { display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--secondary-text-color); }
  .dialog input, .dialog select { font:inherit; font-size:15px; padding:10px; border-radius:8px; border:1px solid var(--divider-color); background:var(--secondary-background-color, transparent); color:var(--primary-text-color); }
  .form-error { color:#eb5757; font-size:13px; min-height:1em; }
  .buttons { display:flex; justify-content:flex-end; gap:8px; }
  @media (max-width:600px) {
    .summary { grid-template-columns:1fr 1fr 1fr; gap:8px; }
    .tile { flex-direction:column; text-align:center; gap:4px; padding:10px; }
    .grid { grid-template-columns:1fr; }
    .content { padding:8px; }
  }
`;

customElements.define("pakete-tracker-panel", PaketeTrackerPanel);
