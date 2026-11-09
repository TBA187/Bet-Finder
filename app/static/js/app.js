/* BetFinder front-end. Vanilla JS, no build step.
 * Every value from the server is escaped before it is put into HTML. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const view = $("#view");
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* storage blocked */ } },
};

const state = {
  lang: store.get("bf_lang", "en"),
  user: null,
  status: null,
  catalog: null,
  scanner: { category: "", sport: "", value_only: false, sort: "value", min_deviation: "" },
  add: { sport: "football", fixture: null, create: false, market: "h2h", line: "", rows: 1, result: null },
  bookmakers: null, markets: {}, competitions: {}, routeToken: 0,
};

// ------------------------------------------------------------------ i18n
function t(key, vars) {
  const dict = window.BF_I18N[state.lang] || window.BF_I18N.en;
  let s = dict[key] ?? window.BF_I18N.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(v);
  return s;
}
const locale = () => (state.lang === "da" ? "da-DK" : "en-GB");
function sportLabel(s) { const sp = state.catalog && state.catalog.sports[s]; return sp ? (sp[state.lang] || sp.en) : s; }
function sportIcon(s) { const sp = state.catalog && state.catalog.sports[s]; return sp ? sp.icon : ""; }

// ------------------------------------------------------------------ helpers
const fmtOdds = (o) => (o == null ? "–" : Number(o).toFixed(2));
const fmtPct = (p, d = 1) => (p == null ? "–" : (p * 100).toFixed(d) + "%");
const fmtEv = (x) => (x == null ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(1) + "%");
const evClass = (x) => (x != null && x > 0 ? "pos" : "neg");
const chip = (cls, text) => `<span class="chip ${cls}">${esc(text)}</span>`;
const RISK_CLS = { low: "green", medium: "amber", high: "orange", very_high: "red" };
const riskChip = (r) => (r ? chip(RISK_CLS[r], t(`risk_${r}`)) : "");
const VS_CLS = { positive: "green", negative: "", uncertain: "amber" };
const valueChip = (v) => (v ? chip(VS_CLS[v], t(`vs_${v}`)) : "");
const DEV_CLS = { normal: "green", warning: "amber", alert: "red" };
const devChip = (d) => (d && d !== "normal" ? `<span class="chip ${DEV_CLS[d]}"><i class="light ${d}"></i>${esc(t(`dev_${d}`))}</span>` : "");
const FLAG_CLS = { possible_pricing_error: "red", outlier_price: "orange", above_market: "blue", arbitrage: "violet" };
const flagChip = (f) => (FLAG_CLS[f] ? chip(FLAG_CLS[f], t(`flag_${f}`)) : "");
const SRC_CLS = { live: "green", scraped: "orange", manual: "amber", historical: "blue", missing: "", confirmed: "green", unconfirmed: "amber" };
const srcChip = (s) => chip(SRC_CLS[s] ?? "", t(`src_${s}`));
function sourceKind(src) { return src === "manual" ? "manual" : src === "scraper" ? "scraped" : "live"; }

function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso), now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400000);
  const time = d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  if (diff === 0) return `${t("today")} ${time}`;
  if (diff === 1) return `${t("tomorrow")} ${time}`;
  if (diff === -1) return `${t("yesterday")} ${time}`;
  return d.toLocaleDateString(locale(), { weekday: "short", day: "numeric", month: "short" }) + " " + time;
}
function ago(min) {
  if (min == null) return "–";
  if (min < 1) return t("just_now");
  if (min < 60) return t("min_ago", { n: Math.round(min) });
  if (min < 1440) return t("h_ago", { n: Math.round(min / 60) });
  return t("d_ago", { n: Math.round(min / 1440) });
}
const agoIso = (iso) => (iso ? ago((Date.now() - new Date(iso).getTime()) / 60000) : t("never"));

function toast(msg, ms = 2800) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), ms);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const { quiet, ...rest } = opts;
  const res = await fetch(path, { ...rest, headers, credentials: "same-origin", body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) {
    const code = data && data.code;
    if (res.status === 401 && code === "auth_required" && !quiet && state.user) signedOut(t("au_session_expired"));
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
    const known = code && t(`ae_${code}`) !== `ae_${code}`;
    const err = new Error(known ? t(`ae_${code}`) : detail || `HTTP ${res.status}`);
    err.code = code; err.status = res.status;
    throw err;
  }
  return data;
}
const skeleton = (n = 3) => Array.from({ length: n }, () => `<div class="skeleton"></div>`).join("");
const errorBox = (err) => `<div class="banner err"><div class="grow"><b>${esc(t("error_title"))}</b> ${esc(err.message || err)}</div></div>`;
const head = (title, sub) => `<div class="page-head"><h1 class="page-title">${esc(title)}</h1>${pillHtml()}</div>${sub ? `<div class="page-sub">${sub}</div>` : ""}`;

/** The data-source badge sits on the right of every page title. */
function pillHtml() {
  const p = state.pill;
  if (!p || !state.user) return "";
  return `<button type="button" class="pill src-pill ${p.cls}" data-action="sourcePill" title="${esc(p.full)}" aria-label="${esc(p.full)}"><i class="pill-dot"></i>`
    + `<span class="p-full">${esc(p.full)}</span><span class="p-short">${esc(p.short)}</span><span class="p-tiny">${esc(p.tiny)}</span></button>`;
}
const backBtn = () => `<button class="back" data-action="back">${esc(t("back"))}</button>`;

function selLabel(market, sel, home, away, line) {
  const signed = (x) => (x > 0 ? `+${x}` : `${x}`);
  if (["h2h", "h2h3", "dnb"].includes(market)) return { home: home || "1", away: away || "2", draw: state.lang === "da" ? "Uafgjort" : "Draw" }[sel] || sel;
  if (market === "totals") return `${sel === "over" ? "Over" : "Under"} ${line ?? ""}`;
  if (market === "spreads") { const l = Number(line || 0); return sel === "home" ? `${home || "1"} ${signed(l)}` : `${away || "2"} ${signed(-l)}`; }
  if (market === "btts") return state.lang === "da" ? `Begge scorer ${sel === "yes" ? "Ja" : "Nej"}` : `BTTS ${sel === "yes" ? "Yes" : "No"}`;
  return sel;
}
function marketName(sport, m) {
  if (m === "h2h") return state.catalog && state.catalog.sports[sport] && state.catalog.sports[sport].draw ? t("mk_h2h_draw") : t("mk_h2h");
  return t(`mk_${m}`);
}
/** Odds found in pasted text: "2,10  3,40  3,25" or "1.95 / 1.85" (Danish commas allowed). */
function parsePastedOdds(text) {
  return (String(text).match(/\d{1,3}[.,]\d{1,3}/g) || []).map((x) => parseFloat(x.replace(",", "."))).filter((v) => v >= 1.01 && v <= 1000);
}

const PSTATE = { ok: ["green", "s_connected"], error: ["red", "s_error"], plan_limited: ["orange", "st_plan_limited"],
  exhausted: ["red", "r_exhausted"], pending: ["blue", "s_key_saved"], standby: ["blue", "s_standby"], not_configured: ["", "s_not_conf"] };
const providerState = (p) => chip(PSTATE[p.state] ? PSTATE[p.state][0] : "", t(PSTATE[p.state] ? PSTATE[p.state][1] : "s_not_conf"));
const providerHint = (p) => (p.hint ? `<div class="hint">${t(`hint_${p.hint}`)}</div>` : "");

// --------------------------------------------------------------- builder storage
const builder = {
  legs() { return store.get("bf_builder", []); },
  add(leg) {
    const legs = this.legs().filter((l) => !(l.fixture_id === leg.fixture_id && l.market_id === leg.market_id));
    legs.push(leg); store.set("bf_builder", legs.slice(-12));
  },
  remove(i) { const legs = this.legs(); legs.splice(i, 1); store.set("bf_builder", legs); },
  clear() { store.set("bf_builder", []); },
};

// ------------------------------------------------------------- top / chrome
/** Phones: the navigation bar can sit at the top (default) or the bottom; remembered on this device. */
function applyNavPos() {
  document.body.classList.toggle("nav-bottom", store.get("bf_nav_pos", "top") === "bottom");
  if (typeof trackTopbar === "function" && window.__fitTopbar) window.__fitTopbar();
}
function applyLanguage() {
  document.documentElement.lang = state.lang;
  $$(".topnav a").forEach((a) => { const sp = a.querySelector("span"); if (sp) sp.textContent = t(`nav_${a.dataset.tab}`); });
  renderToggles();
  renderSidebar();
  $("#userBtn").title = t("more_account");
  $("#userBtn").setAttribute("aria-label", t("more_account"));
  document.dispatchEvent(new Event("bf-lang"));
}

/** Show only what the signed-in account may use (the server checks it again). */
function applyRole() {
  const signed = !!state.user;
  document.body.classList.toggle("signed-out", !signed);
  $$(".topnav a").forEach((a) => { a.hidden = a.dataset.tab === "add" && !can("manual"); });
  $("#refreshBtn").hidden = !can("refresh");
  const ub = $("#userBtn");
  if (signed) {
    if (!ub.querySelector("svg")) ub.innerHTML = ICONS.account;
    const n = can("admin") ? state.user.pending_requests || 0 : 0;
    ub.dataset.badge = n ? String(n) : "";
  }
}

async function startApp() {
  if (state.user && state.user.language && state.user.language !== state.lang) { state.lang = state.user.language; store.set("bf_lang", state.lang); }
  if (state.user && state.user.theme && state.user.theme !== currentTheme()) applyTheme(state.user.theme);
  applyLanguage(); applyRole();
  if (!state.catalog || !Object.keys(state.catalog.sports || {}).length) {
    try { state.catalog = await api("/api/catalog"); } catch (_) { state.catalog = { categories: {}, sports: {} }; }
  }
  await loadStatus();
  route(true);
}

async function loadStatus() {
  if (!state.user) return;
  try { state.status = await api("/api/status"); } catch (_) { state.status = null; }
  renderTop();
}

function setPill(kind, cls, full, short, tiny) {
  state.pill = { kind, cls, full, short: short || full, tiny: tiny || short || full };
  $$(".src-pill").forEach((el) => { el.outerHTML = pillHtml(); });
}

function renderTop() {
  const s = state.status, banner = $("#banner");
  if (!state.user) { banner.innerHTML = ""; return; }
  const pw = passwordWarning(state.user);
  if (!s) { setPill("offline", "warn", t("offline")); banner.innerHTML = pw; return; }
  const apis = s.providers.filter((p) => p.configured && p.method === "api");
  const erroring = can("usage") ? apis.filter((p) => p.last_error && (!p.last_ok || p.last_error_at > p.last_ok)) : [];
  const methods = (s.last_refresh && s.last_refresh.odds_methods) || [];
  let html = pw;
  if (s.storage && !s.storage.persistent && can("admin")) html += `<div class="banner err"><div class="grow">${t("storage_warn")}</div></div>`;
  if (methods.length === 1 && methods[0] === "scraping") {
    setPill("scraping", "orange", t("pill_scraping"));
  } else if (!apis.length) {
    setPill("manual", "warn", t("pill_manual"));
    if ((location.hash || "#home") !== "#home" && can("admin")) html += `<div class="banner warn"><div class="grow">${t("banner_none")}</div></div>`;
  } else if (erroring.length) {
    setPill("error", "warn", t("pill_error"));
    html += `<div class="banner err"><div class="grow"><b>${esc(erroring[0].label)}:</b> ${esc(erroring[0].last_error)}</div></div>`;
  } else {
    const n = apis.length, live = t("pill_live_word");
    setPill("live", "ok", t(n === 1 ? "pill_live_1" : "pill_live_n", { n }), `${live} · ${n}`, live);
  }
  banner.innerHTML = html;
}

async function doRefresh() {
  const btn = $("#refreshBtn");
  if (btn.classList.contains("spin")) return;
  btn.classList.add("spin");
  toast(t("refreshing"), 60000);
  try {
    const r = await api("/api/refresh", { method: "POST" });
    if (r.status === "busy") toast(t("refreshing_busy"));
    else if (r.message) toast(r.message, 5000);
    else {
      const q = (r.providers || []).reduce((a, p) => a + (p.quotes || 0), 0);
      const f = (r.providers || []).reduce((a, p) => a + (p.fixtures || 0), 0);
      const errs = (r.providers || []).flatMap((p) => p.errors || []);
      const via = (r.odds_methods || []).includes("scraping") ? t("via_scraping") : (r.odds_methods || []).length ? t("via_api") : "";
      toast(errs.length ? t("refreshed_err", { n: errs.length, e: errs[0] }) : t("refreshed", { f, q, via }), 5000);
    }
  } catch (err) { toast(err.message, 5000); }
  finally { btn.classList.remove("spin"); await loadStatus(); route(); }
}

// ----------------------------------------------------------------- dashboard
async function viewHome(token) {
  view.innerHTML = head(t("dash_title"), esc(t("dash_sub"))) + skeleton(3);
  try {
    const d = await api("/api/dashboard");
    if (token !== state.routeToken) return;
    view.innerHTML = renderHome(d);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function briefCard(b) {
  const fx = b.fixture;
  return `<div class="card click compact" data-action="openMatch" data-id="${esc(fx.id)}">
    <div class="fx-meta"><span>${sportIcon(fx.sport)} ${esc(b.competition)} · ${fmtTime(fx.kickoff)}</span><span>${devChip(b.deviation_level)}</span></div>
    <div class="spread" style="margin-top:4px"><div style="min-width:0"><b>${esc(b.label)}</b> <span class="muted small">· ${esc(fx.home)} vs ${esc(fx.away)}</span>
      <div class="small muted">${fmtOdds(b.odds)} ${esc(t("at"))} ${esc(b.bookmaker)} · ${esc(t("fair"))} ${fmtOdds(b.fair_odds)} ${srcChip(sourceKind(b.source))}</div></div>
      <div class="${evClass(b.ev_pct)}" style="font-weight:750;font-size:1.05rem">${fmtEv(b.ev_pct)}</div></div>
    <div class="flags">${valueChip(b.value_status)} ${riskChip(b.risk_rating)}</div></div>`;
}

function renderHome(d) {
  const c = d.counts, st = d.status;
  const methods = (st.last_refresh && st.last_refresh.odds_methods) || [];
  const method = methods.includes("scraping") && !methods.includes("api") ? t("u_m_scraping") : methods.length ? t("u_m_api") : t("u_m_manual");
  const tile = (k, v, href, cls = "") => `<a class="tile" href="${href}"><div class="k">${esc(k)}</div><div class="v ${cls}">${v}</div></a>`;
  const tr = d.tracker;
  const setup = st.any_live_source || !can("admin") ? "" : `<div class="card setup"><b>${esc(t("setup_title"))}</b><div class="small muted">${esc(t("setup_sub"))}</div>
    <ol class="small"><li>${t("setup_1")}</li><li>${t("setup_2")}</li><li>${t("setup_3")}</li><li>${t("setup_4")}</li></ol></div>`;
  return head(t("dash_title"), esc(t("dash_sub"))) + setup + `
    <div class="card status-strip"><div class="row wrap">
      <span class="small muted">${esc(t("method_now"))}:</span> <b>${esc(method)}</b>
      <span class="small muted">· ${esc(t("odds_updated", { t: agoIso(d.odds_updated_at) }))}</span>
      ${can("usage") ? `<a class="small" href="#usage" style="margin-left:auto">${esc(t("more_usage"))} ›</a>` : ""}</div></div>
    <div class="tiles" style="margin-top:10px">
      ${tile(t("t_matches"), c.with_odds, "#scanner")}${tile(t("t_value"), c.value_bets, "#scanner", c.value_bets ? "pos" : "")}
      ${tile(t("t_errors"), c.pricing_errors, "#scanner", c.pricing_errors ? "neg-red" : "")}${tile(t("t_arbs"), c.arbitrage, "#arbitrage")}
    </div>
    <div class="section-title spread" id="sec-coupons"><span>${esc(t("sec_coupons"))}</span><a class="small" href="#picks">${esc(t("open_picks"))}</a></div>
    ${d.picks.coupons.length ? `<div class="grid-coupons">${d.picks.coupons.map((cp) => `<a class="card coupon-mini" href="#picks">
        <div class="spread"><b>${esc(cp.title)}</b>${riskChip(cp.risk_rating)}</div>
        <div class="row" style="margin-top:6px"><span class="big odds">${fmtOdds(cp.combined_odds)}</span><span class="pos small">${fmtEv(cp.combined_ev_pct)}</span>
        <span class="small muted">${esc(t("c_legs", { n: cp.legs }))}</span></div></a>`).join("")}</div>`
      : `<div class="empty small">${esc(d.picks.message || "")}</div>`}
    <div class="section-title" id="sec-value">${esc(t("sec_top_value"))}</div>
    ${d.top_value.length ? d.top_value.map(briefCard).join("") : `<div class="empty small">${esc(t("none_value"))}</div>`}
    <div class="section-title" id="sec-errors">${esc(t("sec_errors"))}</div>
    ${d.pricing_errors.length ? d.pricing_errors.map(briefCard).join("") : `<div class="empty small">${esc(t("none_errors"))}</div>`}
    <div class="section-title" id="sec-drops">${esc(t("sec_drops"))}</div>
    ${d.dropping.length ? `<div class="card">${d.dropping.map((x) => `<div class="list-row click-row" data-action="openMatch" data-id="${esc(x.fixture.id)}">
        <div style="min-width:0"><b>${esc(x.label)}</b> <span class="small muted">· ${esc(x.fixture.home)} vs ${esc(x.fixture.away)}</span>
        <div class="tiny muted">${esc(x.bookmaker)}${x.sharp ? " · sharp" : ""}</div></div>
        <div class="odds small" style="text-align:right">${fmtOdds(x.from)} → <b>${fmtOdds(x.to)}</b><div class="${x.move_pct < 0 ? "pos" : "neg"} tiny">${fmtEv(x.move_pct)}</div></div></div>`).join("")}</div>`
      : `<div class="empty small">${esc(t("none_drops"))}</div>`}
    <div class="section-title spread" id="sec-performance"><span>${esc(t("sec_tracker"))}</span><a class="small" href="#tracker">${esc(t("see_all"))}</a></div>
    <div class="tiles">${tile(t("tr_tracked"), tr.tracked, "#tracker")}${tile(t("tr_roi"), tr.roi_pct != null ? fmtEv(tr.roi_pct) : "–", "#tracker", evClass(tr.roi_pct))}
      ${tile(t("tr_avg_clv"), fmtEv(tr.avg_clv_pct), "#tracker", evClass(tr.avg_clv_pct))}${tile(t("tr_beat"), tr.clv_beat_rate_pct != null ? tr.clv_beat_rate_pct + "%" : "–", "#tracker")}</div>
    <div class="footer-note">${esc(t("disclaimer"))}</div>`;
}

// ------------------------------------------------------------------- scanner
async function viewScanner(token) {
  const f = state.scanner, cats = state.catalog.categories, sports = state.catalog.sports;
  const catSeg = ["", ...Object.keys(cats)].map((c) => `<button data-action="scanCat" data-v="${c}" class="${f.category === c ? "on" : ""}">${esc(c ? cats[c][state.lang] || cats[c].en : t("cat_all"))}</button>`).join("");
  const sportList = Object.entries(sports).filter(([, v]) => !f.category || v.category === f.category);
  const sportSeg = sportList.length > 1 ? `<div class="seg">${[["", null], ...sportList].map(([k]) => `<button data-action="scanSport" data-v="${k}" class="${f.sport === k ? "on" : ""}">${k ? sportIcon(k) + " " + esc(sportLabel(k)) : esc(t("cat_all"))}</button>`).join("")}</div>` : "";
  view.innerHTML = head(t("scan_title"), esc(t("scan_sub"))) + `
    <div class="filters"><div class="seg">${catSeg}</div></div>
    <div class="filters">${sportSeg}</div>
    <div class="filters">
      <button class="toggle-chip ${f.value_only ? "on" : ""}" data-action="scanValue">${esc(t("f_value_only"))}</button>
      <select class="compact" data-change="scanDev" aria-label="Deviation">
        <option value="" ${f.min_deviation === "" ? "selected" : ""}>${esc(t("f_dev_all"))}</option>
        <option value="5" ${f.min_deviation === "5" ? "selected" : ""}>🟡 ${esc(t("f_dev_warn"))}</option>
        <option value="10" ${f.min_deviation === "10" ? "selected" : ""}>🔴 ${esc(t("f_dev_alert"))}</option></select>
      <select class="compact" data-change="scanSort" aria-label="Sort">
        ${["value", "kickoff", "confidence", "deviation"].map((s) => `<option value="${s}" ${f.sort === s ? "selected" : ""}>${esc(t(s === "confidence" ? "sort_conf" : s === "deviation" ? "sort_dev" : `sort_${s}`))}</option>`).join("")}
      </select></div>
    <div id="scanList">${skeleton(3)}</div>`;
  const q = new URLSearchParams({ sort: f.sort });
  if (f.sport) q.set("sport", f.sport);
  else if (f.category) q.set("category", f.category);
  if (f.value_only) q.set("value_only", "true");
  if (f.min_deviation) q.set("min_deviation", f.min_deviation);
  try {
    const data = await api(`/api/scanner?${q}`);
    if (token !== state.routeToken) return;
    $("#scanList").innerHTML = renderScanList(data);
  } catch (err) { $("#scanList").innerHTML = errorBox(err); }
}

function renderScanList(data) {
  if (!data.items.length) {
    return `<div class="empty"><div class="big">${esc(t("scan_empty"))}</div>${t("scan_empty_sub")}
      ${data.fixtures_without_odds ? `<div class="small" style="margin-top:6px">${esc(t("without_prices", { n: data.fixtures_without_odds }))}</div>` : ""}</div>`;
  }
  return `<div class="small muted" style="margin-bottom:8px">${esc(t("n_matches", { n: data.items.length }))}${data.fixtures_without_odds ? " · " + esc(t("without_prices", { n: data.fixtures_without_odds })) : ""}</div>` + data.items.map(scanCard).join("");
}

function scanCard(it) {
  const fx = it.fixture, h = it.headline;
  const err = it.flags.includes("possible_pricing_error"), good = h && h.qualifies;
  let headline = `<div class="headline"><span class="muted small">${esc(t("no_ev"))}</span></div>`;
  if (h && h.ev_pct != null) {
    headline = `<div class="headline ${good ? "good" : h.value_status === "uncertain" ? "unsure" : ""}">
      <div style="min-width:0"><div class="sel">${esc(h.label)} <span class="muted small">· ${esc(h.market_label)}</span></div>
        <div class="small muted">${fmtOdds(h.best.odds)} ${esc(t("at"))} ${esc(h.best.name)} · ${esc(t("fair"))} ${fmtOdds(h.fair_odds)} · p ${fmtPct(h.final_prob)}</div></div>
      <div style="text-align:right"><div class="ev ${evClass(h.ev_pct)}">${fmtEv(h.ev_pct)}</div><div class="tiny muted">${esc(good ? t("qualifies") : t("ev"))}</div></div></div>`;
  }
  const mm = it.main_market;
  const mini = mm ? `<div class="mini-odds" style="--n:${mm.selections.length}">` + mm.selections.map((s) => {
    const b = mm.best[s];
    return `<div class="cell"><div class="lbl">${esc(mm.selection_labels[s])}</div><div class="o odds">${b ? fmtOdds(b.odds) : "–"}</div><div class="bk">${b ? esc(b.name) : ""}</div></div>`;
  }).join("") + `</div>` : "";
  const flags = [];
  if (h) flags.push(valueChip(h.value_status), devChip(h.deviation_level));
  for (const f of it.flags) flags.push(flagChip(f));
  if (h && h.risk_rating && h.ev_pct > 0) flags.push(riskChip(h.risk_rating));
  for (const s of [...new Set(it.sources.map(sourceKind))]) flags.push(srcChip(s));
  if (fx.manual) flags.push(chip("amber", t("manual_match")));
  flags.push(chip("", t("books", { n: it.n_bookmakers })), chip("", t("agents_data", { n: it.agents_with_data })));
  return `<div class="card click ${err ? "error-suspect" : good ? "value" : ""}" data-action="openMatch" data-id="${esc(fx.id)}">
    <div class="fx-meta"><span>${sportIcon(fx.sport)} ${esc(it.competition.name)} · ${fmtTime(fx.kickoff)}</span>
      <span class="conf">${esc(t("confidence"))} ${it.confidence}<span class="meter"><i style="width:${it.confidence}%"></i></span></span></div>
    <div class="fx-teams">${esc(fx.home)}<span class="vs">vs</span>${esc(fx.away)}</div>
    ${headline}${mini}<div class="flags">${flags.join("")}</div>
    <div class="tiny faint" style="margin-top:6px">${esc(t("odds_updated", { t: agoIso(it.odds_updated_at) }))}</div></div>`;
}

// --------------------------------------------------------------- match view
async function viewMatch(token, id) {
  view.innerHTML = backBtn() + skeleton(4);
  try {
    const a = await api(`/api/fixtures/${encodeURIComponent(id)}`);
    if (token !== state.routeToken) return;
    view.innerHTML = renderMatch(a);
  } catch (err) { view.innerHTML = backBtn() + errorBox(err); }
}

function renderMatch(a) {
  const fx = a.fixture, p = a.confidence_parts, desc = a.model.describe;
  const started = new Date(fx.kickoff) <= new Date();
  let model = `<div class="v">–</div><div class="tiny muted">${esc(t("m_no_model"))}</div>`;
  let modelTitle = t("m_expected");
  if (desc && desc.kind === "binary") {
    modelTitle = t("m_win_probs");
    model = `<div class="v">${fmtPct(desc.win_home, 0)} – ${fmtPct(desc.win_away, 0)}</div>`;
  } else if (desc) {
    model = `<div class="v">${desc.expected_home} – ${desc.expected_away}</div><div class="tiny muted">total ${desc.expected_total}${a.disagreement != null ? " · " + esc(t("m_disagree", { d: a.disagreement.toFixed(2) })) : ""}</div>`;
  }
  const warn = a.warnings.length ? `<details class="market" style="margin-top:10px"><summary><span>⚠ ${esc(t("m_warnings", { n: a.warnings.length }))}</span></summary>
      <ul class="small muted" style="margin:0;padding-left:18px">${a.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : "";
  const dataRows = a.data_status.map((d) => `<div class="list-row"><span>${esc(t(`kind_${d.kind}`))}</span>
      <span class="row">${srcChip(d.status)}<span class="tiny muted">${d.sources.length ? esc(d.sources.join(", ")) + " · " : ""}${d.updated_at ? agoIso(d.updated_at) : ""}</span></span></div>`).join("");
  const sels = a.selections.filter((s) => s.ev_pct != null).sort((x, y) => y.ev_pct - x.ev_pct);
  return backBtn() + `
  <div class="card">
    <div class="fx-meta"><span>${sportIcon(fx.sport)} ${esc(a.competition.name)} · ${fmtTime(fx.kickoff)}</span><span>${fx.status !== "scheduled" ? chip("amber", fx.status) : ""}</span></div>
    <h1 class="fx-teams" style="font-size:1.25rem">${esc(fx.home)}<span class="vs">vs</span>${esc(fx.away)}</h1>
    <div class="grid-2">
      <div class="tile static"><div class="k">${esc(t("m_conf"))}</div><div class="v">${a.confidence}<span class="small muted">/100</span></div>
        <div class="tiny muted">${esc(t("m_conf_parts", { m: p.market, d: p.data, a: p.agreement, f: p.freshness }))}</div></div>
      <div class="tile static"><div class="k">${esc(modelTitle)}</div>${model}</div>
    </div>
    ${warn}
    <details class="market" style="margin-top:10px"><summary><span>${esc(t("m_data"))}</span><span class="small muted">${esc(t("odds_updated", { t: agoIso(a.odds_updated_at) }))}</span></summary>${dataRows}</details>
    ${started || !can("refresh") ? "" : `<button class="btn ghost small" style="margin-top:10px" data-action="fetchMore" data-fid="${esc(fx.id)}">${esc(t("m_more_books"))}</button>`}
  </div>
  <div class="section-title">${esc(t("m_value"))}</div>
  <div class="card sel-list">${sels.length ? sels.slice(0, 14).map((s) => selRow(s, started, fx)).join("") : `<div class="muted small">${esc(t("m_no_estimate"))}</div>`}</div>
  <div class="section-title">${esc(t("m_compare"))}</div>
  ${a.markets.map((m, i) => marketTable(m, a.selections, i === 0)).join("") || `<div class="empty">–</div>`}
  ${movementSection(a)}
  <div class="section-title">${esc(t("m_agents"))}</div>
  <div class="grid-agents">${a.agents.map(agentCard).join("")}</div>
  <div class="section-title">${esc(t("m_news"))}</div>
  ${teamNewsSection(a, started)}
  <div class="footer-note">${esc(t("disclaimer"))}</div>`;
}

function selRow(s, started, fx) {
  const flags = (s.pricing_flags || []).map(flagChip).join(" ");
  const showWhy = s.qualifies || s.ev_pct > 0;
  return `<div class="sel-row">
    <div><div class="name">${esc(s.label)} <span class="muted small">· ${esc(s.market_label)}</span></div></div>
    <div class="${evClass(s.ev_pct)}" style="font-weight:750;text-align:right">${fmtEv(s.ev_pct)}</div>
    <div class="facts">
      <span>${esc(t("best"))} <b class="odds">${fmtOdds(s.best.odds)}</b> ${esc(s.best.name)}</span>
      <span>${esc(t("implied"))} <b>${s.implied_pct.toFixed(1)}%</b></span>
      <span>${esc(t("est_prob"))} <b>${fmtPct(s.final_prob)}</b></span>
      <span>${esc(t("fair"))} <b class="odds">${fmtOdds(s.fair_odds)}</b></span>
      <span>${esc(t("vs_market"))} ${fmtEv(s.market_ev_pct)}</span>
      ${s.stake_amount > 0 ? `<span>${esc(t("stake"))} <b>${s.stake_amount} ${esc(s.currency)}</b> (${s.kelly_pct}%)</span>` : ""}
    </div>
    <div class="facts">${valueChip(s.value_status)} ${devChip(s.deviation_level)} ${riskChip(s.risk_rating)} ${s.qualifies ? chip("green", t("qualifies")) : ""} ${flags} ${srcChip(sourceKind(s.best.source))} ${!s.best_is_allowed ? chip("amber", t("not_mine")) : ""}</div>
    ${showWhy ? `<div class="why">${esc(s.explanation)}${!s.qualifies && s.rejections.length ? `<br><span class="faint">${esc(t("not_qualifying"))}: ${esc(s.rejections.join("; "))}</span>` : ""}</div>` : ""}
    <div class="row" style="grid-column:1/-1">
      ${!started && s.ev_pct > 0 ? `<button class="btn ghost small" data-action="trackSel" data-fid="${esc(s.fixture_id)}" data-mid="${esc(s.market_id)}" data-sel="${esc(s.selection)}">${esc(t("track"))}</button>` : ""}
      ${started ? "" : `<button class="btn ghost small" data-action="addBuilder" data-fid="${esc(s.fixture_id)}" data-mid="${esc(s.market_id)}" data-sel="${esc(s.selection)}" data-label="${esc(s.label)}" data-match="${esc(fx.home + " vs " + fx.away)}" data-odds="${s.best.odds}">${esc(t("add_builder"))}</button>`}
    </div></div>`;
}

function marketTable(m, selections, open) {
  const sels = m.selections, cons = m.consensus.probs, fin = {};
  for (const s of selections) if (s.market_id === m.market_id) fin[s.selection] = s;
  const rows = m.books.map((b) => {
    const cells = sels.map((s) => {
      const o = b.prices[s];
      if (o == null) return `<td class="faint">–</td>`;
      const q = (m.quotes[s] || []).find((v) => v.bookmaker === b.bookmaker);
      const isBest = m.best[s] && m.best[s].bookmaker === b.bookmaker;
      const flagged = q && (q.flags.includes("outlier") || q.flags.includes("possible_error"));
      return `<td class="odds ${isBest ? "best" : ""} ${flagged ? "flagged" : ""}">${fmtOdds(o)}${q && q.edge_vs_fair_pct != null ? `<span class="sub">${fmtEv(q.edge_vs_fair_pct)}</span>` : ""}</td>`;
    }).join("");
    const dot = b.source.includes("manual") ? "manual" : b.source.includes("scraper") ? "scraped" : b.type;
    return `<tr><td><span class="bk-name"><span class="dot ${esc(dot)}"></span>${esc(b.name)}</span><span class="sub tiny faint">${b.stale ? esc(t("stale")) + " · " : ""}${ago(b.age_min)}</span></td>${cells}
      <td class="faint">${b.margin_pct != null ? b.margin_pct.toFixed(1) + "%" : "–"}</td></tr>`;
  }).join("");
  const consRow = cons ? `<tr class="summary"><td>${esc(t("tbl_fair"))}</td>${sels.map((s) => `<td class="odds">${fmtOdds(1 / cons[s])}<span class="sub">${fmtPct(cons[s])}</span></td>`).join("")}<td></td></tr>` : "";
  const finRow = `<tr class="summary"><td>${esc(t("tbl_final"))}</td>${sels.map((s) => { const f = fin[s]; return `<td class="odds">${f && f.fair_odds ? fmtOdds(f.fair_odds) : "–"}<span class="sub">${f ? fmtPct(f.final_prob) : ""}</span></td>`; }).join("")}<td></td></tr>`;
  const meta = t("tbl_meta", { n: m.consensus.n_books, c: m.consensus.method || "–", m: m.avg_margin_pct ?? "–" }) + (m.arbitrage_pct ? ` · ARBITRAGE +${m.arbitrage_pct}%` : "");
  return `<details class="market" ${open ? "open" : ""}><summary><span>${esc(m.label)}</span><span class="small muted">${esc(t("books", { n: m.books.length }))}</span></summary>
    <div class="small muted" style="margin-bottom:6px">${esc(meta)}</div>
    <div class="table-wrap"><table class="odds-table"><thead><tr><th>${esc(t("tbl_bookmaker"))}</th>${sels.map((s) => `<th>${esc(m.selection_labels[s])}</th>`).join("")}<th>${esc(t("tbl_margin"))}</th></tr></thead>
    <tbody>${rows}${consRow}${finRow}</tbody></table></div><div class="tiny faint" style="margin-top:6px">${t("tbl_legend")}</div></details>`;
}

const PALETTE = ["var(--accent)", "var(--blue)", "var(--amber)", "var(--violet)", "var(--red)"];
function movementSection(a) {
  const m = a.markets[0];
  if (!m) return "";
  const blocks = [];
  for (const s of m.selections) {
    const series = (m.quotes[s] || []).filter((q) => q.history && q.history.length > 1).slice(0, 5);
    if (!series.length) continue;
    const pts = series.flatMap((q) => q.history.map(([ts, o]) => [new Date(ts).getTime(), o]));
    const t0 = Math.min(...pts.map((x) => x[0])), t1 = Math.max(...pts.map((x) => x[0]));
    const o0 = Math.min(...pts.map((x) => x[1])), o1 = Math.max(...pts.map((x) => x[1]));
    const X = (v) => (t1 === t0 ? 50 : ((v - t0) / (t1 - t0)) * 100), Y = (o) => (o1 === o0 ? 17 : 32 - ((o - o0) / (o1 - o0)) * 30);
    const lines = series.map((q, i) => `<polyline style="stroke:${PALETTE[i % 5]}" points="${q.history.map(([ts, o]) => `${X(new Date(ts).getTime()).toFixed(2)},${Y(o).toFixed(2)}`).join(" ")}"/>`).join("");
    const legend = series.map((q, i) => `<span class="tiny" style="color:${PALETTE[i % 5]}">${esc(q.name)} ${fmtOdds(q.opening_odds)}→${fmtOdds(q.odds)} (${fmtEv(q.move_pct)})</span>`).join(" ");
    blocks.push(`<div style="margin-top:8px"><div class="small" style="font-weight:600">${esc(m.selection_labels[s])}</div><svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none" role="img" aria-label="${esc(t("m_movement"))}">${lines}</svg><div class="row wrap">${legend}</div></div>`);
  }
  return blocks.length ? `<div class="section-title">${esc(t("m_movement"))} · ${esc(m.label)}</div><div class="card">${blocks.join("")}<div class="tiny faint" style="margin-top:8px">${esc(t("movement_note"))}</div></div>` : "";
}

function agentCard(ag) {
  const cls = { ok: "green", partial: "amber", no_data: "", error: "red" }[ag.status] || "";
  const show = ag.status === "ok" || ag.status === "partial";
  const bar = (v, l, r) => { const w = Math.abs(v) * 50, left = v >= 0 ? 50 : 50 - w; return `<div class="lean"><span>${esc(l)}</span><div class="bar"><i style="left:${left}%;width:${w}%"></i></div><span>${esc(r)}</span></div>`; };
  const leans = show && ["probability", "form", "home_away", "injuries", "suspensions", "lineups", "statistics"].includes(ag.agent)
    ? bar(ag.lean_home, t("lean_away"), t("lean_home")) + (Math.abs(ag.lean_total) > 0.01 ? bar(ag.lean_total, t("lean_less"), t("lean_more")) : "") : "";
  return `<div class="agent ${ag.status === "no_data" ? "dim" : ""}">
    <div class="top"><span class="nm">${esc(ag.name)}</span><span class="row">${chip(cls, t(`ag_${ag.status}`))}${ag.status !== "no_data" ? `<span class="tiny muted">${esc(t("ag_conf", { c: Math.round(ag.confidence * 100) }))}</span>` : ""}</span></div>
    <div class="summary">${esc(ag.summary)}</div>
    ${ag.findings.length ? `<ul>${ag.findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    ${ag.warnings.map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}${leans}
    ${ag.data_sources.length ? `<div class="tiny faint" style="margin-top:6px">${esc(t("ag_sources", { s: ag.data_sources.join(", ") }))}</div>` : ""}</div>`;
}

function teamNewsSection(a, started) {
  const fx = a.fixture, tn = a.team_news;
  const team = (side) => (side === "home" ? fx.home : fx.away);
  const manual = tn.absences.map((x) => `<div class="list-row"><div><b>${esc(x.player)}</b> <span class="muted small">${esc(team(x.team))} · ${esc(t(`k_${x.kind}`))} · ${esc(t(`i_${x.importance}`))}${x.reason ? " · " + esc(x.reason) : ""}</span></div>
      ${x.source === "manual" && can("manual") ? `<button class="btn danger small" data-action="delAbsence" data-fid="${esc(fx.id)}" data-aid="${esc(x.id)}">${esc(t("remove"))}</button>` : srcChip(sourceKind(x.source))}</div>`).join("");
  const prov = tn.provider.absences.map((x) => `<div class="list-row"><div><b>${esc(x.player)}</b> <span class="muted small">${esc(team(x.team))} · ${esc(t(`k_${x.kind}`))}${x.reason ? " · " + esc(x.reason) : ""}</span></div>${srcChip("live")}</div>`).join("");
  const lu = (side) => { const l = tn.lineups[side]; return `<div class="field"><label class="f">${esc(t("tn_lineup", { t: team(side) }))}</label><div class="row">
      <select class="input" data-lineup="${side}" data-field="status">${["unknown", "expected", "confirmed"].map((s) => `<option value="${s}" ${l.status === s ? "selected" : ""}>${esc(t(`lu_${s}`))}</option>`).join("")}</select>
      <select class="input" data-lineup="${side}" data-field="rotated"><option value="" ${l.rotated == null ? "selected" : ""}>${esc(t("rot_unknown"))}</option>
        <option value="true" ${l.rotated === true ? "selected" : ""}>${esc(t("rot_yes"))}</option><option value="false" ${l.rotated === false ? "selected" : ""}>${esc(t("rot_no"))}</option></select></div></div>`; };
  const xg = (tn.xg || {});
  const xgForm = fx.sport === "football" ? `<form data-submit="saveXg" data-fid="${esc(fx.id)}" style="margin-top:16px"><label class="f">${esc(t("xg_title"))}</label>
      ${["home", "away"].map((side) => `<div class="row" style="margin-top:6px"><span class="small" style="min-width:110px">${esc(team(side))}</span>
        <input class="input" name="${side}_for" type="number" step="0.01" min="0" max="10" placeholder="${esc(t("xg_for"))}" value="${xg[side] ? xg[side].for : ""}">
        <input class="input" name="${side}_against" type="number" step="0.01" min="0" max="10" placeholder="${esc(t("xg_against"))}" value="${xg[side] ? xg[side].against : ""}"></div>`).join("")}
      <div class="tiny faint" style="margin-top:4px">${esc(t("xg_note"))}</div><button class="btn ghost small" type="submit" style="margin-top:8px">${esc(t("xg_save"))}</button></form>` : "";
  const provHead = prov ? `<div class="small muted" style="margin-bottom:4px">${esc(t("tn_from", { s: tn.provider.absences_source }))}</div>${prov}`
    : tn.provider.absences_source ? `<div class="small muted">${esc(t("tn_none_reported", { s: tn.provider.absences_source }))}</div>` : `<div class="small muted">${esc(t("tn_no_feed"))}</div>`;
  if (!can("manual")) return `<div class="card">${provHead}${manual ? `<div class="small muted" style="margin:10px 0 4px">${esc(t("tn_manual"))}</div>${manual}` : ""}</div>`;
  return `<div class="card">${provHead}
    ${manual ? `<div class="small muted" style="margin:10px 0 4px">${esc(t("tn_manual"))}</div>${manual}` : ""}
    <form data-submit="addAbsence" data-fid="${esc(fx.id)}" style="margin-top:12px"><div class="grid-2">
      <div class="field"><label class="f">${esc(t("tn_team"))}</label><select class="input" name="team"><option value="home">${esc(fx.home)}</option><option value="away">${esc(fx.away)}</option></select></div>
      <div class="field" style="margin-top:0"><label class="f">${esc(t("tn_player"))}</label><input class="input" name="player" required maxlength="80"></div>
      <div class="field" style="margin-top:0"><label class="f">${esc(t("tn_type"))}</label><select class="input" name="kind">${["injury", "suspension", "doubtful", "other"].map((k) => `<option value="${k}">${esc(t(`k_${k}`))}</option>`).join("")}</select></div>
      <div class="field" style="margin-top:0"><label class="f">${esc(t("tn_importance"))}</label><select class="input" name="importance">${["key", "regular", "rotation", "unknown"].map((k) => `<option value="${k}" ${k === "regular" ? "selected" : ""}>${esc(t(`i_${k}`))}</option>`).join("")}</select></div></div>
      <div class="field"><label class="f">${esc(t("tn_reason"))}</label><input class="input" name="reason" maxlength="120"></div>
      <button class="btn ghost small" type="submit" style="margin-top:10px">${esc(t("tn_add"))}</button></form>
    <div style="margin-top:16px">${lu("home")}${lu("away")}<button class="btn ghost small" data-action="saveLineups" data-fid="${esc(fx.id)}" style="margin-top:10px">${esc(t("tn_save_lineups"))}</button></div>
    ${xgForm}
    ${started ? `<form data-submit="setResult" data-fid="${esc(fx.id)}" style="margin-top:18px"><label class="f">${esc(t("result_title"))}</label>
      <div class="row"><input class="input" name="score_home" type="number" min="0" required placeholder="${esc(fx.home)}"><input class="input" name="score_away" type="number" min="0" required placeholder="${esc(fx.away)}">
      <button class="btn small" type="submit">${esc(t("save"))}</button></div></form>` : ""}</div>`;
}

// --------------------------------------------------------------- daily picks
function picksQuery() {
  const mode = store.get("bf_picks_mode", null), min = store.get("bf_picks_min", null);
  const q = new URLSearchParams();
  if (mode) q.set("mode", mode);
  if (mode === "fixed" && min) q.set("min_odds", min);
  return q.toString() ? `?${q}` : "";
}

async function viewPicks(token) {
  view.innerHTML = head(t("picks_title")) + skeleton(2);
  try {
    const d = await api(`/api/daily-picks${picksQuery()}`);
    if (token !== state.routeToken) return;
    state.picksSingles = (d.singles || []).length; renderSidebar();
    view.innerHTML = renderPicks(d);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function renderPicks(d) {
  const c = d.criteria;
  let body;
  if (d.message) {
    const rej = Object.entries(d.diagnostics.rejections || {}).map(([k, v]) => `<li>${esc(t(`rej_${k}`))}: ${v}</li>`).join("");
    body = `<div class="empty"><div class="big">${esc(d.message)}</div><div class="small">${esc(t("picks_checked", { m: d.diagnostics.matches_in_window, q: d.diagnostics.qualifying_selections }))}</div>
      ${d.diagnostics.note ? `<div class="small" style="margin-top:6px">${esc(d.diagnostics.note)}</div>` : ""}
      ${rej ? `<div class="small" style="margin-top:10px;text-align:left;display:inline-block">${esc(t("picks_rejected"))}<ul style="margin:4px 0 0">${rej}</ul></div>` : ""}</div>`;
  } else {
    body = d.coupons.map(couponCard).join("");
    if (d.coupons.length < c.max_coupons) body += `<div class="small muted" style="margin-top:10px">${esc(t("picks_fewer", { n: d.coupons.length }))}</div>`;
  }
  const singles = d.singles.length ? `<div class="section-title" id="sec-singles">${esc(t("picks_singles"))}</div><div class="card sel-list">${d.singles.map((l) => `
    <div class="sel-row click-row" data-action="openMatch" data-id="${esc(l.fixture_id)}"><div><div class="name">${esc(l.selection_label)}</div><div class="small muted">${esc(l.match)} · ${esc(l.market_label)}</div></div>
    <div class="pos" style="font-weight:750">${fmtEv(l.ev_pct)}</div><div class="facts"><span><b class="odds">${fmtOdds(l.odds)}</b> ${esc(l.bookmaker_name)}</span><span>p <b>${fmtPct(l.probability)}</b></span><span>${esc(t("fair"))} <b>${fmtOdds(l.fair_odds)}</b></span>${riskChip(l.risk_rating)}</div></div>`).join("")}</div>` : "";
  const auto = c.coupon_odds_mode !== "fixed";
  const modeBar = `<div class="card picks-mode"><div class="row wrap"><b class="small">${esc(t("cr_coupon"))}</b>
      <div class="seg"><button type="button" data-action="picksMode" data-v="auto" class="${auto ? "on" : ""}">${esc(t("pm_auto"))}</button><button type="button" data-action="picksMode" data-v="fixed" class="${auto ? "" : "on"}">${esc(t("pm_fixed"))}</button></div>
      ${auto ? "" : `<label class="small muted" for="pmMin">${esc(t("pm_min"))}</label><input class="input" id="pmMin" type="number" min="1.1" max="50" step="0.1" value="${Number(c.coupon_min_odds).toFixed(2)}" style="max-width:96px" data-change="picksMin">`}</div>
    <div class="tiny muted" style="margin-top:6px">${esc(t(auto ? "pm_auto_d" : "pm_fixed_d", { o: Number(c.coupon_min_odds).toFixed(2) }))}</div></div>`;
  return head(t("picks_title"), esc(t("picks_sub", { n: c.max_coupons })) + "<br>" + esc(t("picks_window", { w: `${fmtTime(d.window.from)} → ${fmtTime(d.window.to)}` }))) + modeBar + `<div id="sec-coupons"></div>` + body + singles + `
    <div class="section-title" id="sec-criteria">${esc(t("criteria"))}</div><div class="card small"><dl class="kv">
      <dt>${esc(t("cr_ev"))}</dt><dd>${c.min_ev_pct}%</dd><dt>${esc(t("cr_edge"))}</dt><dd>≥ ${c.min_market_edge_pct}%</dd>
      <dt>${esc(t("cr_conf"))}</dt><dd>${c.min_confidence}</dd><dt>${esc(t("cr_risk"))}</dt><dd>${esc(t(`risk_${c.max_risk}`))}</dd>
      <dt>${esc(t("cr_prob"))}</dt><dd>${fmtPct(c.min_leg_probability, 0)}</dd><dt>${esc(t("cr_coupon"))}</dt><dd>${c.coupon_odds_mode === "fixed" ? `&gt; ${Number(c.coupon_min_odds).toFixed(2)}` : esc(t("pm_auto"))}, ≤ ${c.coupon_max_legs} ${esc(t("cr_legs"))}</dd>
      <dt>${esc(t("cr_books"))}</dt><dd>${c.my_bookmakers.length ? esc(c.my_bookmakers.join(", ")) : esc(t("all"))}</dd></dl>
      <div style="margin-top:10px"><a href="#sources">${esc(t("change_criteria"))}</a></div></div>
    <div class="footer-note">${t("picks_footer")}</div>`;
}

function couponCard(cp) {
  const legs = cp.legs.map((l) => `<div class="leg"><div class="lt"><div><div class="match">${esc(l.match)}</div><div class="small muted">${sportIcon(l.sport)} ${esc(sportLabel(l.sport))} · ${esc(l.competition)} · ${fmtTime(l.kickoff)}</div></div>${riskChip(l.risk_rating)}</div>
    <div style="margin-top:6px"><b>${esc(l.selection_label)}</b> <span class="muted small">· ${esc(l.market_label)}</span> ${l.tier === "strong" ? chip("blue", t("tier_strong")) : ""} ${devChip(l.deviation_level)} ${srcChip(sourceKind(l.source))}</div>
    <div class="facts"><div class="fact"><div class="k">${esc(t("l_odds"))}</div><div class="v odds">${fmtOdds(l.odds)}</div></div>
      <div class="fact"><div class="k">${esc(t("l_book"))}</div><div class="v" style="font-size:0.8rem">${esc(l.bookmaker_name)}</div></div>
      <div class="fact"><div class="k">${esc(t("l_prob"))}</div><div class="v">${fmtPct(l.probability)}</div></div>
      <div class="fact"><div class="k">${esc(t("l_fair"))}</div><div class="v odds">${fmtOdds(l.fair_odds)}</div></div>
      <div class="fact"><div class="k">${esc(t("l_value"))}</div><div class="v pos">${fmtEv(l.ev_pct)}</div></div>
      <div class="fact"><div class="k">${esc(t("l_conf"))}</div><div class="v">${l.confidence}</div></div></div>
    <div class="small muted">${esc(l.explanation)}</div><a class="small" href="#match/${encodeURIComponent(l.fixture_id)}">${esc(t("full_analysis"))}</a></div>`).join("");
  const PROF = { safe: ["green", "pf_safe_d"], value: ["blue", "pf_value_d"], big: ["violet", "pf_big_d"] };
  const prof = PROF[cp.profile];
  return `<div class="card coupon"><div class="coupon-head"><div><h2 style="font-size:1.1rem">${esc(cp.title)}</h2>
      <div class="small muted">${esc(t("c_legs", { n: cp.legs.length }))} · ${riskChip(cp.risk_rating)}</div>
      ${prof ? `<div class="tiny muted" style="margin-top:3px">${esc(t(prof[1]))}</div>` : ""}</div>
      <div class="coupon-total"><div class="big odds">${fmtOdds(cp.combined_odds)}</div><div class="tiny muted">${esc(t("c_combined"))}</div></div></div>
    <div class="grid-3" style="margin-bottom:6px"><div class="fact"><div class="k">${esc(t("c_winprob"))}</div><div class="v">${fmtPct(cp.combined_probability)}</div></div>
      <div class="fact"><div class="k">${esc(t("c_fair"))}</div><div class="v odds">${fmtOdds(cp.combined_fair_odds)}</div></div>
      <div class="fact"><div class="k">${esc(t("c_ev"))}</div><div class="v pos">${fmtEv(cp.combined_ev_pct)}</div></div></div>
    ${legs}<div class="spread" style="margin-top:10px"><span class="small muted">${esc(t("c_kelly", { p: cp.stake_pct, a: cp.stake_amount, cur: cp.currency || "" }))}</span>
      <button class="btn small" data-action="trackCoupon" data-id="${esc(cp.id)}">${esc(t("c_track"))}</button></div></div>`;
}

// ------------------------------------------------------------------ add odds
async function ensureBookmakers() { if (!state.bookmakers) state.bookmakers = await api("/api/bookmakers"); return state.bookmakers; }
async function ensureMarkets(sport) { if (!state.markets[sport]) state.markets[sport] = await api(`/api/markets/${sport}`); return state.markets[sport]; }
async function ensureCompetitions(sport) { if (!state.competitions[sport]) state.competitions[sport] = (await api(`/api/competitions?sport=${sport}`)).items; return state.competitions[sport]; }

async function viewAdd(token) {
  view.innerHTML = head(t("add_title")) + skeleton(2);
  try {
    const st = state.add;
    const [books, markets, comps] = await Promise.all([ensureBookmakers(), ensureMarkets(st.sport), ensureCompetitions(st.sport)]);
    if (token !== state.routeToken) return;
    if (!markets.find((m) => m.market === st.market)) st.market = markets[0].market;
    view.innerHTML = renderAdd(books, markets, comps);
    updateMargins();
  } catch (err) { view.innerHTML += errorBox(err); }
}

function sportSelect(current) {
  const cats = state.catalog.categories;
  return `<select class="input" data-change="addSport">${Object.entries(cats).sort((a, b) => a[1].order - b[1].order).map(([ck, cv]) =>
    `<optgroup label="${esc(cv[state.lang] || cv.en)}">${Object.entries(state.catalog.sports).filter(([, v]) => v.category === ck).map(([k]) =>
      `<option value="${k}" ${k === current ? "selected" : ""}>${sportIcon(k)} ${esc(sportLabel(k))}</option>`).join("")}</optgroup>`).join("")}</select>`;
}

function renderAdd(books, markets, comps) {
  const st = state.add, fx = st.fixture;
  const prio = books.filter((b) => b.priority), rest = books.filter((b) => !b.priority && !b.unregistered);
  const market = markets.find((m) => m.market === st.market) || markets[0];
  const home = fx ? fx.home : st.home || "1", away = fx ? fx.away : st.away || "2";
  let matchBlock;
  if (fx) {
    matchBlock = `<div class="headline good"><div><div class="sel">${esc(fx.home)} vs ${esc(fx.away)}</div><div class="small muted">${esc(fx.competition)} · ${fmtTime(fx.kickoff)}</div></div><button type="button" class="btn ghost small" data-action="addClearFixture">${esc(t("change"))}</button></div>`;
  } else if (st.create) {
    matchBlock = `<div class="grid-2"><div class="field"><label class="f">${esc(t("add_home"))}</label><input class="input" id="addHome" maxlength="80" value="${esc(st.home || "")}"></div>
      <div class="field" style="margin-top:0"><label class="f">${esc(t("add_away"))}</label><input class="input" id="addAway" maxlength="80" value="${esc(st.away || "")}"></div></div>
      <div class="field"><label class="f">${esc(t("add_comp"))}</label><select class="input" id="addComp">${comps.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${esc(c.country)})</option>`).join("")}<option value="">${esc(t("add_other"))}</option></select></div>
      <div class="field"><label class="f">${esc(t("add_kickoff"))}</label><input class="input" type="datetime-local" id="addKickoff"></div>
      <button type="button" class="btn ghost small" style="margin-top:10px" data-action="addToggleCreate">${esc(t("add_search_instead"))}</button>`;
  } else {
    matchBlock = `<div class="field"><label class="f" for="addSearch">${esc(t("add_find"))}</label><input class="input" id="addSearch" data-input="addSearch" autocomplete="off" placeholder="${esc(t("add_placeholder"))}"></div>
      <div id="addResults"></div><button type="button" class="btn ghost small" style="margin-top:10px" data-action="addToggleCreate">${esc(t("add_create"))}</button>`;
  }
  const opt = (b, sel) => `<option value="${esc(b.key)}" ${sel === b.key ? "selected" : ""}>${esc(b.name)}${b.state === "manual_only" ? esc(t("manual_only_suffix")) : ""}</option>`;
  const defaults = ["danske_spil", "nordicbet", "eurobet", "bet365", "unibet"];
  const rows = Array.from({ length: st.rows }, (_, r) => `<div class="price-row" data-row="${r}">
      <div class="spread"><select class="input" data-book="${r}"><optgroup label="${esc(t("prio"))}">${prio.map((b) => opt(b, defaults[r] || "")).join("")}</optgroup><optgroup label="${esc(t("others"))}">${rest.map((b) => opt(b, defaults[r] || "")).join("")}</optgroup></select>
      ${st.rows > 1 ? `<button type="button" class="btn danger small" data-action="addRemoveRow" data-row="${r}">${esc(t("add_remove_row"))}</button>` : ""}</div>
      <textarea class="input paste" rows="1" data-input="addPaste" data-row="${r}" placeholder="${esc(t("add_paste_ph"))}"></textarea>
      <div class="grid-3" style="margin-top:8px;grid-template-columns:repeat(${market.selections.length},1fr)">${market.selections.map((s) => `<div><label class="f" data-sel-label="${s}">${esc(selLabel(st.market, s, home, away, st.line || "?"))}</label>
        <input class="input odds" type="number" inputmode="decimal" step="0.01" min="1.01" data-price="${s}" data-input="addPrice"></div>`).join("")}</div>
      <div class="small muted margin-preview" style="margin-top:6px">${esc(t("add_margin_hint"))}</div></div>`).join("");
  return head(t("add_title"), esc(t("add_sub"))) + `<form class="card" data-submit="addOdds">
    <div class="field" style="margin-top:0"><label class="f">Sport</label>${sportSelect(st.sport)}</div>
    <div style="margin-top:12px">${matchBlock}</div>
    <div class="field"><label class="f">${esc(t("add_market"))}</label><select class="input" id="addMarket" data-change="addMarket">${markets.map((m) => `<option value="${m.market}" ${m.market === st.market ? "selected" : ""}>${esc(marketName(st.sport, m.market))}</option>`).join("")}</select></div>
    ${market.needs_line ? `<div class="field"><label class="f">${esc(st.market === "spreads" ? t("add_line_sp") : t("add_line"))}</label><input class="input" id="addLine" type="number" step="0.25" value="${esc(st.line)}" data-input="addLine"></div>` : ""}
    <div class="field"><label class="f">${esc(t("add_book"))}</label><div class="tiny faint" style="margin-bottom:6px">${esc(t("add_paste_hint"))}</div>${rows}</div>
    <button type="button" class="btn ghost small" data-action="addRow" style="margin-top:8px">${esc(t("add_row"))}</button>
    <button class="btn block" type="submit" style="margin-top:14px">${esc(t("add_submit"))}</button></form>
    <div id="addResult">${st.result ? renderAddResult(st.result) : ""}</div>`;
}

function updateMargins() {
  for (const row of $$(".price-row")) {
    const vals = $$("[data-price]", row).map((i) => parseFloat(i.value));
    const valid = vals.filter((v) => v >= 1.01), box = $(".margin-preview", row);
    if (!valid.length) { box.textContent = t("add_margin_hint"); continue; }
    const implied = valid.map((v) => (100 / v).toFixed(1) + "%").join(" / ");
    if (valid.length === vals.length) {
      const margin = (valid.reduce((a, v) => a + 1 / v, 0) - 1) * 100;
      box.innerHTML = `${esc(t("add_implied", { i: implied }))} · ${t("add_margin", { m: margin.toFixed(2) })}${margin < 0 ? esc(t("add_margin_neg")) : ""}`;
    } else box.textContent = t("add_implied", { i: implied });
  }
}

function onPaste(el) {
  const odds = parsePastedOdds(el.value);
  const row = el.closest(".price-row");
  const inputs = $$("[data-price]", row);
  if (!odds.length) return;
  inputs.forEach((inp, i) => { if (odds[i] != null) inp.value = odds[i].toFixed(2); });
  updateMargins();
  if (odds.length >= inputs.length) toast(t("add_paste_done", { n: Math.min(odds.length, inputs.length) }));
}

function renderAddResult(r) {
  const fx = r.fixture;
  const blocks = r.bookmakers.map((bk, i) => {
    const rows = r.selections.map((s) => {
      const own = ((r.market && r.market.quotes[s.selection]) || []).find((q) => q.bookmaker === bk);
      if (!own) return "";
      const p = s.final_prob, ev = p != null ? (p * own.odds - 1) * 100 : null;
      const bestTxt = s.best_overall.bookmaker !== bk ? t("best_elsewhere", { o: fmtOdds(s.best_overall.odds), b: s.best_overall.name }) : t("best_is_this");
      return `<div class="sel-row"><div class="name">${esc(s.label)}</div><div class="${evClass(ev)}" style="font-weight:750">${fmtEv(ev)}</div>
        <div class="facts"><span>${esc(t("your_price"))} <b class="odds">${fmtOdds(own.odds)}</b></span><span>${esc(t("implied"))} <b>${own.implied_pct.toFixed(1)}%</b></span>
        <span>${esc(t("est_prob"))} <b>${fmtPct(p)}</b></span><span>${esc(t("fair"))} <b class="odds">${fmtOdds(s.fair_odds)}</b></span><span>${esc(t("best"))}: ${esc(bestTxt)}</span></div></div>`;
    }).join("");
    return `<div style="margin-top:10px"><b>${esc(r.bookmaker_names[i])}</b><div class="sel-list">${rows}</div></div>`;
  }).join("");
  const single = r.market && r.market.consensus.n_books <= 1;
  return `<div class="card" style="margin-top:12px"><div class="spread"><div><b>${esc(t("add_saved_title", { b: r.bookmaker_names.join(", ") }))}</b> <span class="muted small">· ${esc(fx.home)} vs ${esc(fx.away)}</span></div>${chip("blue", r.matched_by)}</div>
    ${single ? `<div class="banner warn" style="margin-top:8px"><div class="grow">${esc(t("add_single"))}</div></div>` : ""}${blocks}
    <a class="btn ghost small" style="margin-top:10px" href="#match/${encodeURIComponent(fx.id)}">${esc(t("open_analysis"))}</a></div>`;
}

let searchTimer = null;
function onAddSearch(input) {
  clearTimeout(searchTimer);
  const q = input.value.trim(), box = $("#addResults");
  if (q.length < 2) { box.innerHTML = ""; return; }
  searchTimer = setTimeout(async () => {
    try {
      const items = await api(`/api/fixtures/search?q=${encodeURIComponent(q)}&sport=${state.add.sport}`);
      state.add._search = items;
      box.innerHTML = items.length ? `<div class="results-list">${items.map((f, i) => `<button type="button" data-action="addPick" data-i="${i}"><b>${esc(f.home)} vs ${esc(f.away)}</b><div class="small muted">${esc(f.competition)} · ${fmtTime(f.kickoff)}</div></button>`).join("")}</div>`
        : `<div class="small muted" style="margin-top:6px">${esc(t("add_none_found"))}</div>`;
    } catch (err) { box.innerHTML = errorBox(err); }
  }, 250);
}

async function submitAddOdds(form) {
  const st = state.add;
  const rows = $$(".price-row", form).map((row) => {
    const prices = {};
    for (const i of $$("[data-price]", row)) if (i.value) prices[i.dataset.price] = parseFloat(i.value);
    return { bookmaker: $("[data-book]", row).value, prices };
  }).filter((r) => Object.keys(r.prices).length);
  if (!rows.length) return toast(t("add_need_price"));
  const body = { market: st.market, rows };
  if (st.market === "totals" || st.market === "spreads") {
    const l = $("#addLine").value;
    if (l === "") return toast(t("add_need_line"));
    body.line = parseFloat(l);
  }
  if (st.fixture) body.fixture_id = st.fixture.id;
  else if (st.create) {
    Object.assign(body, { sport: st.sport, home: $("#addHome").value.trim(), away: $("#addAway").value.trim() });
    const comp = $("#addComp").value;
    if (comp) body.competition_id = comp; else body.competition_name = "Other";
    const k = $("#addKickoff").value;
    if (k) body.kickoff = new Date(k).toISOString();
    st.home = body.home; st.away = body.away;
  } else return toast(t("add_need_match"));
  try {
    const r = await api("/api/manual/odds-multi", { method: "POST", body });
    st.result = r;
    st.fixture = { id: r.fixture.id, home: r.fixture.home, away: r.fixture.away, competition: r.fixture.competition, kickoff: r.fixture.kickoff };
    st.create = false; state.bookmakers = null;
    toast(t("add_saved"));
    route();
  } catch (err) { toast(err.message, 5000); }
}

// ---------------------------------------------------------------------- more
function viewMore() {
  const item = (href, title, d, icon, extra = "") => `<a class="card click menu-item" href="${href}"><span class="mi">${icon}</span><div><b>${esc(t(title))}</b> ${extra}<div class="small muted">${esc(t(d))}</div></div><span class="faint">›</span></a>`;
  const pending = can("admin") && state.user.pending_requests ? chip("red", String(state.user.pending_requests)) : "";
  view.innerHTML = head(t("more_title")) +
    item("#agents", "more_agents", "more_agents_d", "🧠") + item("#arbitrage", "more_arb", "more_arb_d", "♻") +
    item("#builder", "more_builder", "more_builder_d", "🧩") + item("#tracker", "more_tracker", "more_tracker_d", "📈") +
    (can("usage") ? item("#usage", "more_usage", "more_usage_d", "📊") : "") +
    (can("admin") ? item("#sources", "more_sources", "more_sources_d", "⚙") + item("#users", "more_users", "more_users_d", "👥", pending) : "") +
    item("#account", "more_account", "more_account_d", "👤") +
    `<div class="card mobile-only navpos" style="margin-top:10px"><b class="navpos-title">${esc(t("navpos_title"))}</b>
      <div class="navpos-row"><div class="small muted navpos-desc">${esc(t("navpos_desc"))}</div>
      <div class="seg">${["top", "bottom"].map((v) => `<button type="button" data-action="navPos" data-v="${v}" class="${store.get("bf_nav_pos", "top") === v ? "on" : ""}">${esc(t(`navpos_${v}`))}</button>`).join("")}</div></div></div>
    <div class="card spread" style="margin-top:10px"><b>${esc(t("language"))}</b><div class="seg"><button data-action="setLang" data-v="en" class="${state.lang === "en" ? "on" : ""}">${flag("en")} English</button><button data-action="setLang" data-v="da" class="${state.lang === "da" ? "on" : ""}">${flag("da")} Dansk</button></div></div>
    <button class="btn ghost block" type="button" data-action="signOut" style="margin-top:12px">${esc(t("ac_sign_out"))} (${esc(state.user.username)})</button>`;
}

// -------------------------------------------------------------------- agents
async function viewAgents(token) {
  view.innerHTML = backBtn() + head(t("agents_title"), esc(t("agents_sub"))) + skeleton(3);
  try {
    const list = await api("/api/agents");
    if (token !== state.routeToken) return;
    view.innerHTML = backBtn() + head(t("agents_title"), esc(t("agents_sub"))) + `<div class="grid-agents">${list.map((a) => {
      const pct = a.total ? Math.round((a.with_data / a.total) * 100) : 0;
      return `<div class="agent"><div class="top"><span class="nm">${esc(a.name || a.agent)}</span><span class="tiny muted">${esc(t("agents_cov", { a: a.with_data, t: a.total }))}</span></div>
        <div class="summary muted">${esc(t(`desc_${a.agent}`))}</div><div class="meter wide"><i style="width:${pct}%"></i></div>
        ${a.avg_confidence != null ? `<div class="tiny muted" style="margin-top:4px">${esc(t("agents_avg", { c: Math.round(a.avg_confidence * 100) }))}</div>` : ""}
        ${a.highlights.length ? `<ul>${a.highlights.map((h) => `<li><a href="#match/${encodeURIComponent(h.fixture_id)}">${esc(h.match)}</a>: ${esc(h.warning || h.summary)}</li>`).join("")}</ul>` : `<div class="tiny faint" style="margin-top:6px">${esc(t("agents_none"))}</div>`}</div>`;
    }).join("")}</div>`;
  } catch (err) { view.innerHTML += errorBox(err); }
}

// ----------------------------------------------------------------- arbitrage
async function viewArbitrage(token) {
  const stake = store.get("bf_arb_stake", 100);
  const top = backBtn() + head(t("arb_title"), esc(t("arb_sub"))) + `<div class="card row"><label class="f" style="margin:0">${esc(t("arb_stake"))}</label><input class="input" style="max-width:140px" type="number" min="1" step="1" value="${stake}" data-change="arbStake"></div>`;
  view.innerHTML = top + skeleton(2);
  try {
    const list = await api(`/api/arbitrage?stake=${encodeURIComponent(stake)}`);
    if (token !== state.routeToken) return;
    view.innerHTML = top + (list.length ? list.map((x) => `<div class="card" style="margin-top:10px">
      <div class="fx-meta"><span>${sportIcon(x.fixture.sport)} ${esc(x.competition)} · ${fmtTime(x.fixture.kickoff)}</span></div>
      <div class="spread"><a href="#match/${encodeURIComponent(x.fixture.id)}"><b>${esc(x.fixture.home)} vs ${esc(x.fixture.away)}</b></a><span class="pos" style="font-weight:750">+${x.profit_pct}%</span></div>
      <div class="small muted">${esc(x.market_label)} · ${esc(t("arb_profit", { p: x.profit_pct, a: x.profit_amount }))}</div>
      <ul class="small" style="margin:8px 0 0;padding-left:18px">${x.legs.map((l) => `<li>${esc(t("arb_place", { s: l.stake, l: l.label, o: fmtOdds(l.odds), b: l.bookmaker }))} ${l.flags.includes("stale") ? chip("amber", t("stale")) : ""}</li>`).join("")}</ul></div>`).join("")
      : `<div class="empty" style="margin-top:10px">${esc(t("arb_none"))}</div>`);
  } catch (err) { view.innerHTML += errorBox(err); }
}

// ------------------------------------------------------------------- builder
async function viewBuilder(token) {
  const legs = builder.legs();
  const offered = store.get("bf_builder_offered", "");
  let html = backBtn() + head(t("b_title"), esc(t("b_sub")));
  if (!legs.length) { view.innerHTML = html + `<div class="empty">${esc(t("b_empty"))}</div>`; return; }
  html += `<div class="card">${legs.map((l, i) => `<div class="list-row"><div><b>${esc(l.label)}</b> <span class="small muted">· ${esc(l.match)}</span></div>
      <span class="row"><span class="odds">${fmtOdds(l.odds)}</span><button class="btn danger small" data-action="builderRemove" data-i="${i}">${esc(t("remove"))}</button></span></div>`).join("")}
    <div class="row wrap" style="margin-top:10px"><label class="f" style="margin:0">${esc(t("b_offered"))}</label><input class="input" style="max-width:120px" id="builderOffered" type="number" step="0.01" min="1.01" value="${esc(offered)}">
      <button class="btn small" data-action="builderCalc">${esc(t("b_calc"))}</button><button class="btn ghost small" data-action="builderClear">${esc(t("b_clear"))}</button></div></div><div id="builderOut">${skeleton(1)}</div>`;
  view.innerHTML = html;
  await builderCalc(token);
}

async function builderCalc(token) {
  const legs = builder.legs();
  const offered = parseFloat(($("#builderOffered") || {}).value) || null;
  store.set("bf_builder_offered", offered || "");
  try {
    const r = await api("/api/builder", { method: "POST", body: { legs: legs.map((l) => ({ fixture_id: l.fixture_id, market_id: l.market_id, selection: l.selection })), offered_odds: offered } });
    if (token != null && token !== state.routeToken) return;
    const groups = r.groups.filter((g) => g.legs > 1).map((g) => g.modelled
      ? `<div class="small">${esc(t("b_corr", { r: g.correlation, i: fmtPct(g.independent_probability) }))}</div>` : `<div class="small" style="color:var(--amber)">${esc(t("b_corr_not"))}</div>`).join("");
    $("#builderOut").innerHTML = `<div class="tiles" style="margin-top:10px">
      <div class="tile static"><div class="k">${esc(t("b_naive"))}</div><div class="v odds">${fmtOdds(r.naive_combined_odds)}</div></div>
      <div class="tile static"><div class="k">${esc(t("b_prob"))}</div><div class="v">${fmtPct(r.probability)}</div></div>
      <div class="tile static"><div class="k">${esc(t("b_fair"))}</div><div class="v odds">${fmtOdds(r.fair_odds)}</div></div>
      <div class="tile static"><div class="k">${esc(t("b_ev", { o: fmtOdds(r.offered_odds || r.naive_combined_odds) }))}</div><div class="v ${evClass(r.ev_pct)}">${fmtEv(r.ev_pct)}</div></div></div>
      <div class="card" style="margin-top:10px">${groups}<div class="tiny faint">${esc(t("disclaimer"))}</div></div>`;
  } catch (err) { $("#builderOut").innerHTML = errorBox(err); }
}

// ------------------------------------------------------------------- tracker
async function viewTracker(token) {
  view.innerHTML = backBtn() + head(t("tr_title")) + skeleton(2);
  try {
    const d = await api("/api/tracker");
    if (token !== state.routeToken) return;
    view.innerHTML = backBtn() + renderTracker(d);
  } catch (err) { view.innerHTML += errorBox(err); }
}

const trackerState = { tab: "summary" };
function renderTracker(d) {
  const s = d.summary;
  const tile = (k, v, cls = "") => `<div class="tile static"><div class="k">${esc(t(k))}</div><div class="v ${cls}">${v}</div></div>`;
  const items = d.items.map((it) => {
    const cls = { won: "green", half_won: "green", lost: "red", half_lost: "orange", void: "", pending: "blue", partial: "amber" }[it.status] || "";
    const legs = it.legs.map((l) => `<div class="small" style="margin-top:4px"><b>${esc(l.selection_label)}</b> @ <span class="odds">${fmtOdds(l.odds)}</span> ${esc(l.bookmaker_name)}
      <span class="muted">· ${esc(l.match)} · ${fmtTime(l.kickoff)}</span> ${l.result ? chip(l.result.includes("win") ? "green" : l.result.includes("lose") ? "red" : "", l.result.replace("_", " ")) : ""}
      ${l.clv_pct != null ? `<span class="${evClass(l.clv_pct)}"> · ${esc(t("tr_clv"))} ${fmtEv(l.clv_pct)}</span>` : ""}${l.ev_pct != null ? `<span class="muted"> · ${esc(t("tr_ev_pick"))} ${fmtEv(l.ev_pct)}</span>` : ""}</div>`).join("");
    return `<div class="card"><div class="spread"><b>${esc(it.title)}</b>${chip(cls, t(`st_${it.status}`))}</div>
      <div class="small muted">${fmtTime(it.created_at)} · ${esc(t("tr_meta", { s: it.stake, o: fmtOdds(it.combined_odds) }))}${it.profit != null ? ` · ${esc(t("tr_profit_l"))} <span class="${evClass(it.profit)}">${it.profit > 0 ? "+" : ""}${it.profit}</span>` : ""}</div>
      ${legs}<div class="row wrap" style="margin-top:10px">
        ${it.status === "pending" ? ["won", "lost", "void"].map((v) => `<button class="btn ghost small" data-action="settle" data-id="${esc(it.id)}" data-v="${v}">${esc(t(v === "won" ? "tr_won" : v === "lost" ? "tr_lost" : "tr_void"))}</button>`).join("")
          : it.manual_result ? `<button class="btn ghost small" data-action="settle" data-id="${esc(it.id)}" data-v="pending">${esc(t("tr_reopen"))}</button>` : ""}
        <button class="btn danger small" data-action="delTracked" data-id="${esc(it.id)}">${esc(t("tr_delete"))}</button></div></div>`;
  }).join("");
  const tab = trackerState.tab;
  const seg = ["summary", "history"].map((k) => `<button type="button" data-action="trackerTab" data-v="${k}" class="${tab === k ? "on" : ""}">${esc(t(k === "summary" ? "sm_summary" : "sm_history"))}</button>`).join("");
  const summary = `<div class="tiles">
    ${tile("tr_tracked", s.tracked)}${tile("tr_settled", s.settled)}${tile("tr_hit", s.hit_rate_pct != null ? s.hit_rate_pct + "%" : "–")}${tile("tr_roi", s.roi_pct != null ? fmtEv(s.roi_pct) : "–", evClass(s.roi_pct))}
    ${tile("tr_avg_ev", fmtEv(s.avg_ev_pct), evClass(s.avg_ev_pct))}${tile("tr_avg_clv", fmtEv(s.avg_clv_pct), evClass(s.avg_clv_pct))}
    ${tile("tr_beat", s.clv_beat_rate_pct != null ? s.clv_beat_rate_pct + "%" : "–")}${tile("tr_profit", s.profit, evClass(s.profit))}</div>`;
  const history = `${items || `<div class="empty"><div class="big">${esc(t("tr_empty"))}</div>${esc(t("tr_empty_sub"))}</div>`}`;
  return head(t("tr_title"), esc(t("tr_sub"))) + `<div class="seg" style="margin-bottom:12px">${seg}</div>` + (tab === "history" ? history : summary);
}

// --------------------------------------------------------------------- usage
async function viewUsage(token) {
  view.innerHTML = backBtn() + head(t("u_title"), esc(t("u_sub"))) + skeleton(3);
  try {
    const [u, plan, st] = await Promise.all([api("/api/usage"), api("/api/credits-plan"), api("/api/status")]);
    if (token !== state.routeToken) return;
    view.innerHTML = backBtn() + renderUsage(u, plan, st);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function tokenBar(tk, method) {
  if (!tk) return `<span class="small muted">${esc(t(method === "scraping" ? "u_no_tokens" : "u_no_data"))}</span>`;
  if (tk.note) return `<span class="small muted">${esc(t(`tk_${tk.note}`))}</span>`;
  if (tk.remaining_by_sport) return Object.entries(tk.remaining_by_sport).map(([s, r]) => `<div class="small">${esc(t("u_by_sport", { s: sportLabel(s), r }))}</div>`).join("") + `<div class="tiny faint">${esc(t("u_exact"))}</div>`;
  if (tk.limit == null) return `<span class="small muted">–</span>`;
  const used = tk.used || 0, limit = tk.limit, remaining = tk.remaining ?? Math.max(0, limit - used);
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return `<div class="usage-bar ${pct > 85 ? "hot" : pct > 60 ? "warm" : ""}"><i style="width:${pct}%"></i></div>
    <div class="small">${esc(t("u_left", { r: Math.round(remaining), l: Math.round(limit), u: tk.unit, p: t(`period_${tk.period}`) }))}</div>
    <div class="tiny faint">${esc(tk.exact ? t("u_exact") : t("u_estimate"))}</div>`;
}

function renderUsage(u, plan, st) {
  const methods = u.last_refresh.odds_methods || [];
  const now = methods.includes("api") ? t("u_m_api") : methods.includes("scraping") ? t("u_m_scraping") : t("u_m_manual");
  const nowCls = methods.includes("scraping") && !methods.includes("api") ? "orange" : methods.includes("api") ? "green" : "amber";
  const mchip = (m) => chip(m === "scraping" ? "orange" : "blue", m === "scraping" ? t("u_m_scraping") : t("u_m_api"));
  const chain = (u.last_refresh.chain || []).map((c) => `<div class="list-row"><span><b>${esc(c.provider)}</b> ${mchip(c.method)}</span>
      <span class="small">${c.ran ? chip("green", t("u_ran")) : chip("", t("u_skipped"))} <span class="muted">${esc(t(`r_${c.reason}`))}${c.ran && c.quotes != null ? ` · ${c.quotes}` : ""}</span></span></div>`).join("");
  const rows = u.providers.map((p) => `<div class="card"><div class="spread"><b>${esc(p.label)}</b><span class="row">${mchip(p.method)}
      ${providerState(p)}</span></div>${providerHint(p)}
    <div style="margin-top:8px">${tokenBar(p.tokens, p.method)}</div>
    <dl class="kv" style="margin-top:8px"><dt>${esc(t("u_today"))}</dt><dd>${p.requests_today}</dd><dt>${esc(t("u_month"))}</dt><dd>${p.requests_month}</dd>
      <dt>${esc(t("u_last"))}</dt><dd>${agoIso(p.last_request_at)}</dd>${p.last_error ? `<dt>${esc(t("s_last_error"))}</dt><dd class="err-text">${esc(p.last_error)}</dd>` : ""}</dl>
    ${Object.keys(p.blocked_hosts || {}).length ? `<div class="small" style="margin-top:6px;color:var(--orange)">${esc(t("u_blocked"))}: ${Object.entries(p.blocked_hosts).map(([h, b]) => `${esc(h)} (${esc(b.reason || "")})`).join(", ")}</div>` : ""}</div>`).join("");
  const price = plan.price_usd ? t("per_month", { p: plan.price_usd }) : t("free");
  return head(t("u_title"), esc(t("u_sub"))) + `<div class="card spread"><span>${esc(t("u_method_now"))}</span>${chip(nowCls, now)}</div>
    <div class="section-title" id="sec-chain">${esc(t("u_chain"))}</div><div class="card">${chain || `<span class="small muted">${esc(t("never"))}</span>`}</div>
    <div class="section-title" id="sec-providers">${esc(t("s_providers"))}</div>${rows}
    <div class="section-title" id="sec-planner">${esc(t("u_planner"))}</div><div class="card small">${t("u_plan_line", { c: plan.credits_per_refresh, r: plan.refreshes_per_day, m: plan.monthly_credits })}
      <div style="margin-top:6px">${t("u_plan_rec", { p: esc(plan.recommended_plan), price: esc(price) })}</div><div class="tiny faint" style="margin-top:4px">${esc(plan.assumption)}</div></div>`;
}

// ------------------------------------------------------------------- sources
const NUM_SETTINGS = [["min_ev_pct", 0.5], ["min_market_edge_pct", 0.5], ["min_confidence", 1], ["min_leg_probability", 0.01], ["max_leg_odds", 0.1],
  ["coupon_min_odds", 0.1], ["coupon_max_legs", 1], ["max_coupons", 1], ["deviation_warning_pct", 0.5], ["deviation_alert_pct", 0.5], ["suspicious_ev_pct", 1],
  ["stale_minutes", 10], ["model_weight", 0.05], ["max_logit_shift", 0.05], ["bankroll", 10], ["kelly_fraction", 0.05], ["lookahead_hours", 6],
  ["day_end_hour", 1], ["auto_refresh_minutes", 5], ["active_hours_start", 1], ["active_hours_end", 1], ["retention_days", 1]];

async function viewSources(token) {
  view.innerHTML = backBtn() + head(t("s_title")) + skeleton(3);
  try {
    const [status, settings, books, comps] = await Promise.all([api("/api/status"), api("/api/settings"), api("/api/bookmakers"), api("/api/competitions")]);
    if (token !== state.routeToken) return;
    state.status = status; renderTop(); state.bookmakers = books;
    view.innerHTML = backBtn() + renderSources(status, settings, books, comps);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function providerCard(p, settings) {
  const keyName = { the_odds_api: "odds_api_key", api_sports: "api_sports_key", odds_api_io: "odds_api_io_key", oddspapi: "oddspapi_key" }[p.provider];
  let stc = providerState(p);
  let keyBox = "";
  if (keyName) {
    const sk = settings[keyName];
    keyBox = sk.source === "environment" ? `<div class="small muted" style="margin-top:10px">${esc(t("s_env", { h: sk.hint }))}</div>` : `
      <form data-submit="saveKey" data-key="${keyName}" class="row" style="margin-top:10px"><input class="input" type="password" name="key" autocomplete="off" placeholder="${esc(sk.configured ? t("s_saved_hint", { h: sk.hint }) : t("s_paste"))}">
      <button class="btn small" type="submit">${esc(t("save"))}</button>${sk.configured ? `<button class="btn danger small" type="button" data-action="clearKey" data-key="${keyName}">${esc(t("remove"))}</button>` : ""}</form>`;
    if (sk.configured) keyBox += `<button class="btn ghost small" style="margin-top:8px" type="button" data-action="testKey" data-p="${p.provider}">${esc(t("s_test"))}</button>`;
  }
  let extra = "";
  if (p.provider === "odds_api_io") extra = listSetting("odds_api_io_bookmakers", settings.odds_api_io_bookmakers, t("s_bookmakers_list"));
  if (p.provider === "oddspapi") extra = listSetting("oddspapi_bookmakers", settings.oddspapi_bookmakers, t("s_bookmakers_list"));
  if (p.provider === "scraper") {
    stc = chip(settings.scraper_mode === "off" ? "" : "orange", t(`sm_${settings.scraper_mode}`).split(" (")[0]);
    extra = `<form data-submit="saveScraper" style="margin-top:10px"><div class="field"><label class="f">${esc(t("s_scraper_mode"))}</label><select class="input" name="scraper_mode">${["off", "fallback", "always"].map((m) => `<option value="${m}" ${settings.scraper_mode === m ? "selected" : ""}>${esc(t(`sm_${m}`))}</option>`).join("")}</select></div>
      <div class="grid-2"><div class="field"><label class="f">${esc(t("s_kambi_ops"))}</label><input class="input" name="scraper_kambi_operators" value="${esc(settings.scraper_kambi_operators.join(", "))}"></div>
      <div class="field" style="margin-top:12px"><label class="f">${esc(t("s_kambi_market"))}</label><input class="input" name="scraper_kambi_market" value="${esc(settings.scraper_kambi_market)}"></div></div>
      <button class="btn ghost small" type="submit" style="margin-top:8px">${esc(t("save"))}</button></form>`;
  }
  return `<div class="card"><div class="spread"><b>${esc(p.label)}</b>${stc}</div>${providerHint(p)}<div class="small muted" style="margin-top:4px">${t(`p_${p.provider}`)}</div>
    <dl class="kv" style="margin-top:10px"><dt>${esc(t("s_last_ok"))}</dt><dd>${agoIso(p.last_ok)}</dd><dt>${esc(t("s_requests"))}</dt><dd>${p.requests_today}</dd>
      ${p.last_error ? `<dt>${esc(t("s_last_error"))}</dt><dd class="err-text">${esc(p.last_error)}</dd>` : ""}</dl>${keyBox}${extra}</div>`;
}

function listSetting(key, values, label) {
  return `<form data-submit="saveList" data-key="${key}" class="field"><label class="f">${esc(label)}</label><div class="row"><input class="input" name="v" value="${esc((values || []).join(", "))}"><button class="btn ghost small" type="submit">${esc(t("save"))}</button></div></form>`;
}

function renderSources(status, settings, books, comps) {
  const ST = { connected: ["green", "b_connected"], scraped: ["orange", "b_scraped"], configured_no_data: ["blue", "b_configured"], provider_not_configured: ["amber", "b_needs_key"], manual_only: ["", "b_manual"] };
  const bookRows = books.map((b) => `<div class="list-row"><div style="min-width:0"><div><b>${esc(b.name)}</b> ${b.priority ? chip("blue", t("prio")) : ""} ${b.type !== "soft" ? chip("violet", b.type) : ""}</div>
      <div class="tiny muted">${b.sources.length ? esc(t("s_feeds", { f: b.sources.join(", ") })) : esc(t("s_no_feed"))}${b.last_automated_quote ? " · " + esc(t("s_last_price", { t: agoIso(b.last_automated_quote) })) : ""}${b.manual_quotes ? " · " + esc(t("s_manual_prices", { n: b.manual_quotes })) : ""}</div>
      ${b.limitation ? `<div class="tiny" style="color:var(--amber);margin-top:2px">${esc(b.limitation)}</div>` : ""}</div>
      <div style="text-align:right;flex-shrink:0">${chip(ST[b.state][0], t(ST[b.state][1]))}<label class="switch tiny" style="margin-top:6px"><span>${esc(t("s_mine"))}</span><input type="checkbox" data-change="myBook" value="${esc(b.key)}" ${b.mine ? "checked" : ""}></label></div></div>`).join("");
  const cats = state.catalog.categories;
  const compBlocks = Object.entries(cats).sort((a, b) => a[1].order - b[1].order).map(([ck, cv]) => {
    const sports = Object.keys(state.catalog.sports).filter((s) => state.catalog.sports[s].category === ck);
    return `<div class="cat-title">${esc(cv[state.lang] || cv.en)}</div>` + sports.map((sp) => {
      const list = comps.items.filter((c) => c.sport === sp);
      if (!list.length) return "";
      const on = list.filter((c) => c.enabled).length;
      return `<details class="market"><summary><span>${sportIcon(sp)} ${esc(sportLabel(sp))}</span><span class="small muted">${on}/${list.length}</span></summary>${list.map((c) => `<div class="list-row"><div><div>${esc(c.name)} <span class="muted small">${esc(c.country)}</span></div>
        <div class="tiny muted">${[c.odds_source ? t("s_odds_feed") : null, c.stats_source ? t("s_stats_feed") : null].filter(Boolean).join(" · ") || esc(t("s_manual_feed"))}</div></div>
        <label class="switch"><input type="checkbox" data-change="toggleComp" value="${esc(c.id)}" ${c.enabled ? "checked" : ""}></label></div>`).join("")}</details>`;
    }).join("");
  }).join("");
  const nums = NUM_SETTINGS.map(([k, step]) => `<div class="field"><label class="f" for="set_${k}">${esc(t(`set_${k}`))}</label><input class="input" id="set_${k}" name="${k}" type="number" step="${step}" value="${esc(settings[k])}">
      ${t(`set_${k}_h`) !== `set_${k}_h` ? `<div class="tiny faint" style="margin-top:3px">${esc(t(`set_${k}_h`))}</div>` : ""}</div>`).join("");
  const sw = (name, on) => `<label class="switch small"><input type="checkbox" name="${name}" ${on ? "checked" : ""}><span>${esc(t(`s_n_${name.replace("notify_on_", "")}`))}</span></label>`;
  const tg = settings.notify_telegram_token;
  const plan = comps.credit_plan;
  return head(t("s_title"), esc(t("s_sub"))) + `
  <div class="section-title" id="sec-providers">${esc(t("s_providers"))}</div>${status.providers.map((p) => providerCard(p, settings)).join("")}
  <div class="card"><div class="spread"><b>${esc(t("s_manual"))}</b>${chip("green", t("s_always"))}</div><div class="small muted" style="margin-top:4px">${t("s_manual_d")}</div></div>
  <div class="section-title" id="sec-oddsopts">${esc(t("s_odds_opts"))}</div>
  <form class="card" data-submit="saveOddsApi"><div class="small muted">${t("s_cost", { c: plan.credits_per_refresh })} <a href="#usage">${esc(t("u_planner"))} ›</a></div>
    <div class="row wrap" style="margin-top:10px"><span class="small" style="width:70px">${esc(t("s_regions"))}</span>${["eu", "uk", "se", "fr"].map((r) => `<label class="switch small"><input type="checkbox" name="odds_api_regions" value="${r}" ${settings.odds_api_regions.includes(r) ? "checked" : ""}><span>${r.toUpperCase()}</span></label>`).join(" ")}</div>
    <div class="row wrap" style="margin-top:8px"><span class="small" style="width:70px">${esc(t("s_markets"))}</span>${["h2h", "totals", "spreads"].map((m) => `<label class="switch small"><input type="checkbox" name="odds_api_markets" value="${m}" ${settings.odds_api_markets.includes(m) ? "checked" : ""}><span>${m}</span></label>`).join(" ")}</div>
    <button class="btn ghost small" type="submit" style="margin-top:10px">${esc(t("save"))}</button></form>
  <div class="section-title" id="sec-notify">${esc(t("s_notify"))}</div>
  <form class="card" data-submit="saveNotify"><div class="small muted">${esc(t("s_notify_d"))}</div>
    <div class="grid-2" style="margin-top:8px"><div class="field"><label class="f">${esc(t("s_tg_token"))}</label><input class="input" type="password" name="notify_telegram_token" autocomplete="off" placeholder="${tg.configured ? esc(t("s_saved_hint", { h: tg.hint })) : ""}"></div>
      <div class="field" style="margin-top:12px"><label class="f">${esc(t("s_tg_chat"))}</label><input class="input" name="notify_telegram_chat_id" value="${esc(settings.notify_telegram_chat_id)}"></div>
      <div class="field" style="margin-top:0"><label class="f">${esc(t("s_ntfy_topic"))}</label><input class="input" name="notify_ntfy_topic" value="${esc(settings.notify_ntfy_topic)}"></div>
      <div class="field" style="margin-top:0"><label class="f">${esc(t("s_ntfy_server"))}</label><input class="input" name="notify_ntfy_server" value="${esc(settings.notify_ntfy_server)}"></div></div>
    <div class="row wrap" style="margin-top:10px">${sw("notify_on_errors", settings.notify_on_errors)}${sw("notify_on_value", settings.notify_on_value)}${sw("notify_on_picks", settings.notify_on_picks)}${sw("notify_on_arbitrage", settings.notify_on_arbitrage)}</div>
    <div class="field"><label class="f">${esc(t("s_n_min_ev"))}</label><input class="input" name="notify_min_ev_pct" type="number" step="0.5" value="${esc(settings.notify_min_ev_pct)}"></div>
    <div class="row" style="margin-top:10px"><button class="btn ghost small" type="submit">${esc(t("save"))}</button><button class="btn ghost small" type="button" data-action="notifyTest">${esc(t("s_n_test"))}</button></div></form>
  <div class="section-title" id="sec-books">${esc(t("s_books"))}</div><div class="card"><div class="small muted" style="margin-bottom:6px">${esc(t("s_books_d"))}</div>${bookRows}</div>
  <div class="section-title" id="sec-leagues">${esc(t("s_competitions"))}</div><div class="small muted" style="margin-bottom:8px">${esc(t("s_comp_d", { e: comps.items.filter((c) => c.enabled).length, t: comps.items.length }))}</div>${compBlocks}
  <div class="section-title" id="sec-analysis">${esc(t("s_analysis"))}</div>
  <form class="card" data-submit="saveSettings">
    <div class="grid-2"><div class="field"><label class="f">${esc(t("s_lang"))}</label><select class="input" name="language"><option value="en" ${settings.language === "en" ? "selected" : ""}>English</option><option value="da" ${settings.language === "da" ? "selected" : ""}>Dansk</option></select></div>
      <div class="field" style="margin-top:12px"><label class="f">${esc(t("s_currency"))}</label><select class="input" name="currency">${["DKK", "EUR", "SEK", "NOK", "GBP", "USD"].map((c) => `<option ${settings.currency === c ? "selected" : ""}>${c}</option>`).join("")}</select></div></div>
    <div class="field"><label class="f">${esc(t("s_max_risk"))}</label><select class="input" name="max_risk">${["low", "medium", "high", "very_high"].map((r) => `<option value="${r}" ${settings.max_risk === r ? "selected" : ""}>${esc(t(`risk_${r}`))}</option>`).join("")}</select></div>
    <div class="field"><label class="f">${esc(t("set_coupon_odds_mode"))}</label><select class="input" name="coupon_odds_mode">${["auto", "fixed"].map((m) => `<option value="${m}" ${settings.coupon_odds_mode === m ? "selected" : ""}>${esc(t(m === "auto" ? "pm_auto" : "pm_fixed"))}</option>`).join("")}</select>
      <div class="tiny faint" style="margin-top:3px">${esc(t("set_coupon_odds_mode_h"))}</div></div>
    <div class="field"><label class="f">${esc(t("s_devig"))}</label><select class="input" name="devig_method">${[["shin", "Shin"], ["power", "Power"], ["multiplicative", "Proportional"], ["additive", "Additive"]].map(([v, l]) => `<option value="${v}" ${settings.devig_method === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="field"><label class="f">${esc(t("s_tz"))}</label><input class="input" name="timezone" value="${esc(settings.timezone)}"></div>
    ${nums}<button class="btn block" type="submit" style="margin-top:14px">${esc(t("s_save"))}</button></form>
  <div class="footer-note">${t("footer", { v: esc(status.version) })}</div>`;
}

// ------------------------------------------------------------------- actions
async function setLanguage(lang) {
  state.lang = lang; store.set("bf_lang", lang);
  applyLanguage();
  if (!state.user) { renderLogin(); return; }
  try { state.user = await api("/api/account", { method: "PUT", body: { language: lang } }); } catch (_) { /* offline */ }
  state.markets = {}; state.competitions = {};
  await loadStatus(); route();
}
async function askStake() { const v = prompt(t("stake_prompt"), "1"); return v === null ? null : parseFloat(v) || 1; }

const actions = {
  ...AUTH_ACTIONS,
  sourcePill: () => {
    if (can("usage")) { location.hash = "#usage"; return; }
    const kind = (state.pill && state.pill.kind) || "offline";   // everyone else gets a short explanation
    openModal(`<h2 id="modalTitle">${esc(t("pill_info_title"))}</h2><div class="small" style="margin-top:8px;line-height:1.5">${esc(t(`pill_info_${kind}`))}</div>
      <div class="small muted" style="margin-top:8px;line-height:1.5">${esc(t("pill_info_more"))}</div>
      <div class="row" style="margin-top:14px"><button class="btn ghost small" type="button" data-action="closeModal">${esc(t("close"))}</button></div>`);
  },
  navPos: (el) => { store.set("bf_nav_pos", el.dataset.v); applyNavPos(); route(); },
  trackerTab: (el) => { trackerState.tab = el.dataset.v; route(); },
  openMatch: (el) => { location.hash = `#match/${encodeURIComponent(el.dataset.id)}`; },
  back: () => { if (history.length > 1) history.back(); else location.hash = "#home"; },
  setLang: (el) => setLanguage(el.dataset.v),
  scanCat: (el) => { state.scanner.category = el.dataset.v; state.scanner.sport = ""; route(); },
  scanSport: (el) => { state.scanner.sport = el.dataset.v; route(); },
  scanValue: () => { state.scanner.value_only = !state.scanner.value_only; route(); },
  addPick: (el) => { state.add.fixture = state.add._search[Number(el.dataset.i)]; state.add.result = null; route(); },
  addClearFixture: () => { state.add.fixture = null; state.add.result = null; route(); },
  addToggleCreate: () => { state.add.create = !state.add.create; state.add.fixture = null; route(); },
  addRow: () => { state.add.rows = Math.min(8, state.add.rows + 1); route(); },
  addRemoveRow: () => { state.add.rows = Math.max(1, state.add.rows - 1); route(); },
  addBuilder: (el) => { builder.add({ fixture_id: el.dataset.fid, market_id: el.dataset.mid, selection: el.dataset.sel, label: el.dataset.label, match: el.dataset.match, odds: parseFloat(el.dataset.odds) }); toast(t("added_builder")); },
  builderRemove: (el) => { builder.remove(Number(el.dataset.i)); route(); },
  builderClear: () => { builder.clear(); route(); },
  builderCalc: () => builderCalc(null),
  picksMode: (el) => { store.set("bf_picks_mode", el.dataset.v); route(); },
  fetchMore: async (el) => {
    el.disabled = true;
    try { const r = await api(`/api/fixtures/${encodeURIComponent(el.dataset.fid)}/fetch-more`, { method: "POST" }); toast(t("m_more_books_done", { n: r.quotes, b: r.budget_left }), 5000); route(); }
    catch (err) { toast(err.message, 5000); el.disabled = false; }
  },
  trackSel: async (el) => {
    const stake = await askStake(); if (stake === null) return;
    try { await api("/api/tracker/selection", { method: "POST", body: { fixture_id: el.dataset.fid, market_id: el.dataset.mid, selection: el.dataset.sel, stake } }); toast(t("tracked_ok")); }
    catch (err) { toast(err.message, 5000); }
  },
  trackCoupon: async (el) => {
    const stake = await askStake(); if (stake === null) return;
    try { await api("/api/tracker/coupon", { method: "POST", body: { coupon_id: el.dataset.id, stake } }); toast(t("tracked_ok")); } catch (err) { toast(err.message, 5000); }
  },
  settle: async (el) => { try { await api(`/api/tracker/${el.dataset.id}/settle`, { method: "POST", body: { status: el.dataset.v } }); route(); } catch (err) { toast(err.message); } },
  delTracked: async (el) => { if (!confirm(t("tr_confirm_delete"))) return; try { await api(`/api/tracker/${el.dataset.id}`, { method: "DELETE" }); route(); } catch (err) { toast(err.message); } },
  delAbsence: async (el) => { try { await api(`/api/fixtures/${encodeURIComponent(el.dataset.fid)}/absences/${el.dataset.aid}`, { method: "DELETE" }); route(); } catch (err) { toast(err.message); } },
  saveLineups: async (el) => {
    try {
      for (const side of ["home", "away"]) {
        const status = $(`[data-lineup="${side}"][data-field="status"]`).value, rot = $(`[data-lineup="${side}"][data-field="rotated"]`).value;
        await api(`/api/fixtures/${encodeURIComponent(el.dataset.fid)}/lineups/${side}`, { method: "PUT", body: { status, rotated: rot === "" ? null : rot === "true" } });
      }
      toast(t("s_saved")); route();
    } catch (err) { toast(err.message); }
  },
  clearKey: async (el) => { if (!confirm(t("s_remove_key"))) return; try { await api("/api/settings", { method: "PUT", body: { [el.dataset.key]: "" } }); toast(t("s_key_removed")); await loadStatus(); route(); } catch (err) { toast(err.message); } },
  testKey: async (el) => {
    el.disabled = true;
    try {
      const r = await api(`/api/providers/${el.dataset.p}/test`, { method: "POST" });
      const extra = r.info && r.info.plan ? ` (plan: ${r.info.plan})` : r.info && r.info.remaining ? ` (${r.info.remaining} credits left)` : "";
      toast(t(`test_${r.code}`) + extra + (r.detail ? ` – ${r.detail}` : ""), 7000);
    } catch (err) { toast(err.message, 5000); }
    el.disabled = false;
  },
  notifyTest: async () => { try { const r = await api("/api/notify/test", { method: "POST" }); toast(r.results.join(" · "), 5000); } catch (err) { toast(err.message, 5000); } },
};

const changes = {
  picksMin: (el) => { const v = parseFloat(el.value); if (v >= 1.1 && v <= 50) { store.set("bf_picks_min", v); route(); } },
  scanSort: (el) => { state.scanner.sort = el.value; route(); },
  scanDev: (el) => { state.scanner.min_deviation = el.value; route(); },
  arbStake: (el) => { store.set("bf_arb_stake", parseFloat(el.value) || 100); route(); },
  addSport: (el) => { Object.assign(state.add, { sport: el.value, fixture: null, result: null, market: "h2h", line: "" }); route(); },
  addMarket: (el) => { state.add.market = el.value; state.add.line = ""; route(); },
  myBook: async () => {
    const mine = $$('[data-change="myBook"]').filter((i) => i.checked).map((i) => i.value);
    try { await api("/api/settings", { method: "PUT", body: { my_bookmakers: mine } }); toast(t("s_saved")); } catch (err) { toast(err.message); }
  },
  toggleComp: async (el) => {
    try { await api(`/api/competitions/${encodeURIComponent(el.value)}`, { method: "PUT", body: { enabled: el.checked } }); state.competitions = {}; toast(t(el.checked ? "s_comp_on" : "s_comp_off")); }
    catch (err) { toast(err.message); el.checked = !el.checked; }
  },
};

const inputs = {
  addSearch: onAddSearch,
  addPrice: updateMargins,
  addPaste: onPaste,
  addLine: (el) => {
    state.add.line = el.value;
    const fx = state.add.fixture;
    for (const lbl of $$("[data-sel-label]")) lbl.textContent = selLabel(state.add.market, lbl.dataset.selLabel, fx ? fx.home : state.add.home || "1", fx ? fx.away : state.add.away || "2", el.value || "?");
  },
};

const putSettings = async (body, msg) => { try { await api("/api/settings", { method: "PUT", body }); toast(msg || t("s_saved")); route(); } catch (err) { toast(err.message, 5000); } };
const splitList = (v) => String(v || "").split(",").map((x) => x.trim()).filter(Boolean);

const submits = {
  ...AUTH_SUBMITS,
  addOdds: submitAddOdds,
  addAbsence: async (form) => {
    try { await api(`/api/fixtures/${encodeURIComponent(form.dataset.fid)}/absences`, { method: "POST", body: Object.fromEntries(new FormData(form)) }); toast(t("s_saved")); route(); } catch (err) { toast(err.message); }
  },
  saveXg: async (form) => {
    const fd = new FormData(form);
    try {
      for (const side of ["home", "away"]) {
        const f = fd.get(`${side}_for`), a = fd.get(`${side}_against`);
        await api(`/api/fixtures/${encodeURIComponent(form.dataset.fid)}/xg/${side}`, { method: "PUT", body: { xg_for: f === "" ? null : Number(f), xg_against: a === "" ? null : Number(a) } });
      }
      toast(t("s_saved")); route();
    } catch (err) { toast(err.message); }
  },
  setResult: async (form) => {
    const fd = new FormData(form);
    try { await api(`/api/fixtures/${encodeURIComponent(form.dataset.fid)}/result`, { method: "POST", body: { score_home: Number(fd.get("score_home")), score_away: Number(fd.get("score_away")) } }); toast(t("s_saved")); route(); } catch (err) { toast(err.message); }
  },
  saveKey: async (form) => {
    const key = new FormData(form).get("key").trim();
    if (!key) return toast(t("s_paste"));
    try { await api("/api/settings", { method: "PUT", body: { [form.dataset.key]: key } }); toast(t("s_key_saved_toast")); await loadStatus(); route(); } catch (err) { toast(err.message); }
  },
  saveList: (form) => putSettings({ [form.dataset.key]: splitList(new FormData(form).get("v")) }),
  saveScraper: (form) => { const fd = new FormData(form); return putSettings({ scraper_mode: fd.get("scraper_mode"), scraper_kambi_operators: splitList(fd.get("scraper_kambi_operators")), scraper_kambi_market: fd.get("scraper_kambi_market").trim().toUpperCase() }); },
  saveOddsApi: (form) => { const fd = new FormData(form); return putSettings({ odds_api_regions: fd.getAll("odds_api_regions"), odds_api_markets: fd.getAll("odds_api_markets") }); },
  saveNotify: (form) => {
    const fd = new FormData(form), body = {};
    for (const k of ["notify_telegram_chat_id", "notify_ntfy_topic", "notify_ntfy_server"]) body[k] = fd.get(k).trim();
    if (fd.get("notify_telegram_token").trim()) body.notify_telegram_token = fd.get("notify_telegram_token").trim();
    for (const k of ["notify_on_errors", "notify_on_value", "notify_on_picks", "notify_on_arbitrage"]) body[k] = fd.get(k) === "on";
    body.notify_min_ev_pct = Number(fd.get("notify_min_ev_pct"));
    return putSettings(body, t("s_n_saved"));
  },
  saveSettings: async (form) => {
    const fd = new FormData(form), body = {};
    for (const [k, v] of fd.entries()) body[k] = ["max_risk", "devig_method", "timezone", "language", "currency", "coupon_odds_mode"].includes(k) ? v : Number(v);
    await putSettings(body);  // "language" here is the default for notifications; each account picks its own
  },
};

document.addEventListener("click", (e) => { const el = e.target.closest("[data-action]"); if (!el) return; const fn = actions[el.dataset.action]; if (fn) { e.preventDefault(); fn(el, e); } });
document.addEventListener("change", (e) => { const el = e.target.closest("[data-change]"); if (el && changes[el.dataset.change]) changes[el.dataset.change](el, e); });
document.addEventListener("input", (e) => { const el = e.target.closest("[data-input]"); if (el && inputs[el.dataset.input]) inputs[el.dataset.input](el, e); });
document.addEventListener("submit", (e) => { const f = e.target.closest("[data-submit]"); if (f && submits[f.dataset.submit]) { e.preventDefault(); submits[f.dataset.submit](f, e); } });
$("#refreshBtn").addEventListener("click", doRefresh);
$("#userBtn").addEventListener("click", () => { location.hash = can("admin") && state.user.pending_requests ? "#users" : "#account"; });


// -------------------------------------------------------------------- router
const MORE_TABS = ["more", "agents", "arbitrage", "builder", "tracker", "usage", "sources", "account", "users", "admin"];
const NEEDS = { add: "manual", usage: "usage", sources: "admin", users: "admin", admin: "admin" };
/** The scanner entries in the side menu are filters; they only apply when chosen, never on a re-render. */
function applyScannerFilter(section) {
  if (section === "all") Object.assign(state.scanner, { category: "", sport: "", value_only: false });
  else if (section === "valueonly") Object.assign(state.scanner, { category: "", sport: "", value_only: true });
  else if (section.startsWith("cat-")) Object.assign(state.scanner, { category: section.slice(4), sport: "" });
}
const USERS_TABS = ["requests", "users", "new", "activity"];

/** fromHash: the address was just changed (link, menu, reload). A plain route() only redraws the page
 * after something inside it changed, so the address must not overrule the choice just made there. */
function route(fromHash = false) {
  const hash = location.hash.replace(/^#/, "") || "home";
  const [tab, ...rest] = hash.split("/");
  const token = ++state.routeToken;
  side.spy = "";
  if (!fromHash) {  // keep the page where it is while it redraws
    const h = view.offsetHeight;
    view.style.minHeight = `${h}px`;
    setTimeout(() => { view.style.minHeight = ""; }, 1200);
  } else view.style.minHeight = "";
  if (!state.user) { renderLogin(); return; }
  const nav = tab === "match" ? "scanner" : MORE_TABS.includes(tab) ? "more" : tab;
  $$(".topnav a").forEach((a) => a.classList.toggle("on", a.dataset.tab === nav));
  const views = { home: viewHome, scanner: viewScanner, picks: viewPicks, add: viewAdd, more: viewMore, agents: viewAgents,
    arbitrage: viewArbitrage, builder: viewBuilder, tracker: viewTracker, usage: viewUsage, sources: viewSources,
    account: viewAccount, users: viewUsers, admin: viewAdmin };
  if (NEEDS[tab] && !can(NEEDS[tab])) {
    renderSidebar();
    view.innerHTML = head(t("no_access_title")) + `<div class="empty">${esc(t("no_access"))}</div>`;
    return;
  }
  let section = rest[0] || "";
  if (fromHash && tab === "scanner" && section) { applyScannerFilter(section); section = ""; }
  if (tab === "tracker" && ["summary", "history"].includes(section)) {
    if (fromHash) trackerState.tab = section;
    section = "";
  }
  if (tab === "users" && USERS_TABS.includes(section)) {  // the tabs of that page live in the address
    if (fromHash) { usersState.tab = section; usersState.picked = true; }
    section = "";
  }
  if (tab === "match" && rest.length) viewMatch(token, decodeURIComponent(rest.join("/")));
  else (views[tab] || viewHome)(token);
  renderSidebar();
  if (!fromHash) return;
  if (section && tab !== "match") scrollToSection(section, token);
  else if (!rest.length || tab !== "match") window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => route(true));

(async function init() {
  applyNavPos();
  trackTopbar();
  initPageChrome();
  applyLanguage();
  try { state.user = await api("/api/auth/me", { quiet: true }); } catch (_) { state.user = null; }
  if (state.user) await startApp();
  else renderLogin("login");
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
