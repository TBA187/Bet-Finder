/* BetFinder navigation: left side menu, light/dark theme, language flags and the
 * Admin overview page.  Loaded before app.js; uses its helpers at run time. */
"use strict";

const svg = (body, size = 20) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
const ICONS = {
  home: svg('<path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z"/>'),
  scanner: svg('<path d="M4 19V9m5 10V5m5 14v-7m5 7V8"/>'),
  picks: svg('<path d="M5 4h14v16l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 9h6M9 13h4"/>'),
  add: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 8v8M8 12h8"/>'),
  agents: svg('<rect x="5" y="7" width="14" height="11" rx="3"/><path d="M12 3v4M9 12h.01M15 12h.01M9.5 15.5h5"/>'),
  arbitrage: svg('<path d="M4 8h13l-3-3M20 16H7l3 3"/>'),
  builder: svg('<path d="M12 3l8 4-8 4-8-4z"/><path d="M4 12l8 4 8-4M4 17l8 4 8-4"/>'),
  tracker: svg('<path d="M4 19h16"/><path d="M5 15l4-4 3 3 6-7"/><path d="M15 7h3v3"/>'),
  usage: svg('<path d="M4 15a8 8 0 1 1 16 0"/><path d="M12 15l4-5"/><path d="M4 19h16"/>'),
  sources: svg('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>'),
  users: svg('<circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5"/><path d="M16 5.2a3 3 0 0 1 0 5.6M17.5 14.7c1.6.5 2.6 1.9 3 4.3"/>'),
  admin: svg('<path d="M12 3l7 3v5c0 4.6-3 8.3-7 10-4-1.7-7-5.4-7-10V6z"/><path d="M9 12l2 2 4-4"/>'),
  account: svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="10" r="3"/><path d="M6.6 18.4c1.3-2.2 3.2-3.3 5.4-3.3s4.1 1.1 5.4 3.3"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>', 17),
  moon: svg('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>', 17),
  collapse: svg('<path d="M15 6l-6 6 6 6"/>', 18),
  expand: svg('<path d="M9 6l6 6-6 6"/>', 18),
  logout: svg('<path d="M10 4H5v16h5"/><path d="M14 8l4 4-4 4M18 12H9"/>'),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 15),
  foldUp: svg('<path d="M7 11l5-5 5 5M7 18l5-5 5 5"/>', 16),
  foldDown: svg('<path d="M7 6l5 5 5-5M7 13l5 5 5-5"/>', 16),
  chevron: svg('<path d="M6 9l6 6 6-6"/>', 15),
};
const flag = (lang) => `<i class="flag flag-${lang}" aria-hidden="true"></i>`;

// --------------------------------------------------------------------- theme
function currentTheme() { return document.documentElement.dataset.theme === "light" ? "light" : "dark"; }
function applyTheme(theme) {
  const t2 = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = t2;
  store.set("bf_theme", t2);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = t2 === "light" ? "#f4f6f9" : "#0f1218";
  renderToggles();
}
async function setTheme(theme) {
  applyTheme(theme);
  if (state.user) {
    try { state.user = await api("/api/account", { method: "PUT", body: { theme: currentTheme() } }); } catch (_) { /* kept in this browser */ }
  }
}

/** Languages offered in the top bar; add a line here to offer another one. */
const LANGS = [{ k: "en", short: "EN", name: "English" }, { k: "da", short: "DA", name: "Dansk" }];

/** One theme button (shows the active theme, switches to the other) and one language menu. */
function renderToggles() {
  const th = $("#themeToggle"), lg = $("#langToggle");
  if (th) {
    const dark = currentTheme() === "dark", tip = t(dark ? "theme_to_light" : "theme_to_dark");
    th.innerHTML = `<span class="theme-ico">${dark ? ICONS.moon : ICONS.sun}</span>`;
    th.title = tip; th.setAttribute("aria-label", tip);
  }
  if (lg) {
    const cur = LANGS.find((l) => l.k === state.lang) || LANGS[0];
    const tip = t("lang_title", { l: cur.name });
    lg.innerHTML = `<button type="button" class="lang-btn" data-lang-open aria-haspopup="menu" aria-expanded="false" title="${esc(tip)}" aria-label="${esc(tip)}">${flag(cur.k)}<b>${cur.short}</b><span class="lang-caret">${ICONS.chevron}</span></button>
      <div class="lang-pop" role="menu" hidden>${LANGS.map((l) => {
        const on = l.k === cur.k, tt = t(on ? "lang_current" : "lang_switch_to", { l: l.name });
        return `<button type="button" role="menuitemradio" aria-checked="${on}" class="${on ? "on" : ""}" data-lang-set="${l.k}" title="${esc(tt)}">${flag(l.k)}<span>${l.name}</span>${on ? ICONS.check : ""}</button>`;
      }).join("")}</div>`;
  }
}
function closeLangMenu() {
  const pop = $("#langToggle .lang-pop"), btn = $("#langToggle .lang-btn");
  if (pop) pop.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
}
function toggleLangMenu() {
  const pop = $("#langToggle .lang-pop"), btn = $("#langToggle .lang-btn");
  if (!pop) return;
  const open = pop.hidden;
  pop.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
}

// --------------------------------------------------------------- side menu
const side = { collapsed: null, open: null, more: null, spy: "", lockUntil: 0 };  // filled from this browser's storage on first use
function sideState() {
  if (side.open === null) {
    side.open = store.get("bf_side_open", {});   // category id -> false when the person closed it
    side.more = store.get("bf_side_more", {});   // category id -> true when "show all" is on
  }
  return side;
}
const SIDE_LIMIT = 5;
const CLOSED_BY_DEFAULT = ["sources"];   // categories that start collapsed until the person opens them
const isOpen = (id) => (CLOSED_BY_DEFAULT.includes(id) ? side.open[id] === true : side.open[id] !== false);
const isNarrow = () => window.matchMedia("(max-width: 899px)").matches;

function sideCollapsed() {
  sideState();
  if (side.collapsed === null) side.collapsed = isNarrow() ? true : store.get("bf_side_collapsed", false);
  return side.collapsed;
}

function menuModel() {
  const cats = (state.catalog && state.catalog.categories) || {};
  const catSubs = Object.entries(cats).sort((a, b) => a[1].order - b[1].order).map(([k, v]) => [`cat-${k}`, v[state.lang] || v.en]);
  const admin = can("admin");
  // subs: [id, label]. Ids scroll to a part of the page; the scanner ones are filters instead.
  const groups = [
    { title: null, items: [
      { id: "home", href: "#home", icon: ICONS.home, label: "nav_home",
        subs: [["coupons", "sm_coupons"], ["value", "sm_value"], ["errors", "sm_errors"], ["drops", "sm_drops"], ["performance", "sm_performance"]] },
      { id: "scanner", href: "#scanner", icon: ICONS.scanner, label: "nav_scanner", filters: true,
        subs: [["all", "sm_all_matches"], ["valueonly", "sm_value_only"], ...catSubs] },
      { id: "picks", href: "#picks", icon: ICONS.picks, label: "nav_picks", subs: [["coupons", "sm_picks_coupons"], ["singles", "sm_singles", () => state.picksSingles > 0], ["criteria", "sm_criteria"]] },
      { id: "add", href: "#add", icon: ICONS.add, label: "nav_add", perm: "manual" },
    ] },
    { title: "g_tools", items: [
      { id: "agents", href: "#agents", icon: ICONS.agents, label: "more_agents" },
      { id: "arbitrage", href: "#arbitrage", icon: ICONS.arbitrage, label: "more_arb" },
      { id: "builder", href: "#builder", icon: ICONS.builder, label: "more_builder" },
      { id: "tracker", href: "#tracker", icon: ICONS.tracker, label: "more_tracker" },
      { id: "usage", href: "#usage", icon: ICONS.usage, label: "more_usage", perm: "usage", hide: admin },
    ] },
    { title: "g_admin", perm: "admin", items: [
      { id: "admin", href: "#admin", icon: ICONS.admin, label: "nav_admin_overview" },
      { id: "users", href: "#users", icon: ICONS.users, label: "nav_manage_accounts", badge: state.user && state.user.pending_requests },
      { id: "sources", href: "#sources", icon: ICONS.sources, label: "more_sources",
        subs: [["providers", "sm_api_keys"], ["oddsopts", "s_odds_opts"], ["notify", "s_notify"], ["books", "s_books"], ["leagues", "sm_leagues"], ["analysis", "sm_analysis"]] },
      { id: "usage", href: "#usage", icon: ICONS.usage, label: "more_usage" },
    ] },
    { title: "g_account", items: [
      { id: "account", href: "#account", icon: ICONS.account, label: "nav_my_account" },
      { id: "logout", action: "signOut", icon: ICONS.logout, label: "nav_logout" },
    ] },
  ];
  return groups.filter((g) => !g.perm || can(g.perm))
    .map((g) => ({ ...g, items: g.items.filter((i) => (!i.perm || can(i.perm)) && !i.hide) }))
    .filter((g) => g.items.length);
}

const hashTab = () => (location.hash.replace(/^#/, "") || "home").split("/")[0];
/** Which menu entry belongs to the current address (the top bar and this menu share the address). */
function activeNav() { const tab = hashTab(); return tab === "match" ? "scanner" : tab; }

/** The scanner sub-entries are filters, so their highlight comes from the filter in use. */
function scannerSub() {
  const sc = state.scanner;
  if (sc.category) return `cat-${sc.category}`;
  return sc.value_only ? "valueonly" : "all";
}
const activeSubOf = (it) => (it.id !== activeNav() ? "" : it.filters ? (hashTab() === "scanner" ? scannerSub() : "") : side.spy);

function renderSidebar() {
  const el = $("#sidebar");
  if (!el) return;
  if (!state.user) { el.innerHTML = ""; return; }
  const collapsed = sideCollapsed();
  document.body.classList.toggle("side-collapsed", collapsed);
  const act = activeNav();
  const groups = menuModel().map((g) => {
    const items = g.items.map((it) => {
      const isActive = act === it.id;
      const subs = (it.subs || []).filter((x) => !x[2] || x[2]());
      const open = subs.length && isOpen(it.id);
      const all = side.more[it.id] || subs.length <= SIDE_LIMIT + 1;
      const shown = all ? subs : subs.slice(0, SIDE_LIMIT);
      const curSub = activeSubOf(it);
      const subHtml = subs.length && open ? `<div class="side-subs">${shown.map(([k, label]) => {
        const on = curSub === k;
        return `<a class="side-sub ${on ? "on" : ""}" href="${it.href}/${esc(k)}" data-nav-tab="${it.id}" data-${it.filters ? "filter" : "sec"}="${esc(k)}">${esc(t(label))}</a>`;
      }).join("")}${subs.length > SIDE_LIMIT + 1 ? `<button type="button" class="side-more" data-side-more="${it.id}">${esc(t(all ? "sm_show_less" : "sm_show_more", { n: subs.length - SIDE_LIMIT }))}</button>` : ""}</div>` : "";
      const badge = it.badge ? `<span class="side-badge">${esc(String(it.badge))}</span>` : "";
      const inner = `<span class="side-icon">${it.icon}${collapsed && badge ? '<i class="side-dot"></i>' : ""}</span><span class="side-label">${esc(t(it.label))}</span>${collapsed ? "" : badge}`;
      const link = it.action
        ? `<button type="button" class="side-link" data-action="${it.action}" title="${esc(t(it.label))}">${inner}</button>`
        : `<a class="side-link" href="${it.href}" data-nav-tab="${it.id}" title="${esc(t(it.label))}"${isActive ? ' aria-current="page"' : ""}>${inner}</a>`;
      return `<div class="side-item ${isActive ? "on" : ""} ${it.id === "logout" ? "logout" : ""}"><div class="side-row">${link}
          ${subs.length && !collapsed ? `<button type="button" class="side-caret ${open ? "open" : ""}" data-side-open="${it.id}" aria-expanded="${open ? "true" : "false"}" aria-label="${esc(t(open ? "sm_collapse" : "sm_expand"))}">${ICONS.chevron}</button>` : ""}
        </div>${collapsed ? "" : subHtml}</div>`;
    }).join("");
    return `<div class="side-group">${g.title ? `<div class="side-title">${esc(t(g.title))}</div>` : ""}${items}</div>`;
  }).join("");
  const withSubs = menuModel().flatMap((g) => g.items).filter((i) => (i.subs || []).length);
  const anyOpen = withSubs.some((i) => isOpen(i.id));
  const foldTip = t(anyOpen ? "sm_collapse_all" : "sm_expand_all");
  const fold = collapsed ? "" : `<button type="button" class="side-fold" data-side-fold aria-label="${esc(foldTip)}" title="${esc(foldTip)}">${anyOpen ? ICONS.foldUp : ICONS.foldDown}</button>`;
  el.innerHTML = `<div class="side-top"><button type="button" class="side-toggle" data-side-toggle aria-label="${esc(t(collapsed ? "sm_expand_menu" : "sm_collapse_menu"))}" title="${esc(t(collapsed ? "sm_expand_menu" : "sm_collapse_menu"))}">
      ${collapsed ? ICONS.expand : ICONS.collapse}<span class="side-label">${esc(t("sm_collapse_menu"))}</span></button>${fold}</div><nav aria-label="${esc(t("sm_menu"))}">${groups}</nav>`;
}

function setSideCollapsed(v) {
  side.collapsed = v;
  if (!isNarrow()) store.set("bf_side_collapsed", v);
  renderSidebar();
}

document.addEventListener("click", (e) => {
  const tg = e.target.closest("[data-side-toggle]");
  if (tg) { setSideCollapsed(!sideCollapsed()); return; }
  const op = e.target.closest("[data-side-open]");
  if (op) { const id = op.dataset.sideOpen; side.open[id] = !isOpen(id); store.set("bf_side_open", side.open); renderSidebar(); return; }
  if (e.target.closest("[data-side-fold]")) {
    const ids = menuModel().flatMap((g) => g.items).filter((i) => (i.subs || []).length).map((i) => i.id);
    const open = !ids.some((id) => isOpen(id));   // everything closed -> open all, otherwise close all
    ids.forEach((id) => { side.open[id] = open; });
    store.set("bf_side_open", side.open); renderSidebar(); return;
  }
  const mo = e.target.closest("[data-side-more]");
  if (mo) { const id = mo.dataset.sideMore; side.more[id] = !side.more[id]; store.set("bf_side_more", side.more); renderSidebar(); return; }
  if (e.target.closest("[data-theme-toggle]")) { setTheme(currentTheme() === "dark" ? "light" : "dark"); return; }
  if (e.target.closest("[data-lang-open]")) { toggleLangMenu(); return; }
  const lg = e.target.closest("[data-lang-set]");
  if (lg) { closeLangMenu(); if (lg.dataset.langSet !== state.lang) setLanguage(lg.dataset.langSet); return; }
  closeLangMenu();
  // a menu entry for the page we are already on never reloads it: it scrolls (or filters) in place
  const a = e.target.closest("#sidebar a[data-nav-tab]");
  if (a && a.dataset.navTab === hashTab()) {
    e.preventDefault();
    if (a.dataset.filter) { applyScannerFilter(a.dataset.filter); history.replaceState(null, "", "#scanner"); route(); }
    else if (a.dataset.sec) { scrollToSec(a.dataset.sec); history.replaceState(null, "", `#${a.dataset.navTab}/${a.dataset.sec}`); }
    else window.scrollTo({ top: 0, behavior: "smooth" });
  }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeLangMenu(); });

/** Keeps the side menu just below the top bar. On phones only the navigation row sticks, so the bar
 * is pulled up by the height of the logo row (negative top) and --topbar-h is what stays visible. */
function trackTopbar() {
  const bar = $(".topbar"), nav = $(".topnav");
  const set = () => {
    const phone = window.matchMedia("(max-width: 899px)").matches, bottom = document.body.classList.contains("nav-bottom");
    let pull = 0;
    if (phone && !bottom) pull = Math.max(0, nav.offsetTop - 6);
    bar.style.top = `-${pull}px`;
    document.documentElement.style.setProperty("--topbar-h", `${phone ? (bottom ? 0 : bar.offsetHeight - pull) : bar.offsetHeight}px`);
    document.documentElement.style.setProperty("--navbar-h", `${nav.offsetHeight}px`);
  };
  window.__fitTopbar = set;
  set();
  if (window.ResizeObserver) { new ResizeObserver(set).observe(bar); new ResizeObserver(set).observe(nav); }
  window.addEventListener("resize", set);
}
const topbarH = () => parseInt(getComputedStyle(document.documentElement).getPropertyValue("--topbar-h"), 10) || 70;

/** Smooth-scroll to a part of the current page (sections are the elements with id "sec-<name>"). */
function scrollToSec(name) {
  const el = document.getElementById(`sec-${name}`);
  if (!el) return false;
  side.lockUntil = Date.now() + 900;           // keep the clicked entry highlighted while the page glides there
  setSpy(name);
  setTimeout(updateSpy, 950);
  window.scrollTo({ top: Math.max(0, el.getBoundingClientRect().top + window.scrollY - topbarH() - 12), behavior: "smooth" });
  return true;
}

/** After a page renders, jump to the part chosen in the side menu (e.g. #home/value). */
function scrollToSection(name, token) {
  if (!name) return;
  let tries = 0;
  const look = () => {
    if (token !== state.routeToken) return;
    if (!scrollToSec(name) && tries++ < 40) setTimeout(look, 100);
  };
  look();
}

// ----------------------------------------------- highlight the part being read
function setSpy(name) {
  if (side.spy === name) return;
  side.spy = name;
  const act = activeNav();
  $$("#sidebar a.side-sub[data-sec]").forEach((a) => a.classList.toggle("on", a.dataset.navTab === act && a.dataset.sec === name));
}
function updateSpy() {
  spyQueued = false;
  if (Date.now() < side.lockUntil) return;
  const secs = $$('#view [id^="sec-"]');
  let cur = "";
  if (secs.length) {
    const line = topbarH() + 70;
    secs.forEach((s) => { if (s.getBoundingClientRect().top <= line) cur = s.id.slice(4); });
    const atBottom = window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
    if (atBottom) cur = secs[secs.length - 1].id.slice(4);
  }
  setSpy(cur);
}
let spyQueued = false;
function queueSpy() { if (!spyQueued) { spyQueued = true; requestAnimationFrame(updateSpy); } }

// ---------------------------------------------------------- back to top button
function initPageChrome() {
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "to-top"; btn.id = "toTop";
  btn.innerHTML = svg('<path d="M6 14l6-6 6 6"/>', 18);
  const setLabel = () => { btn.title = t("to_top"); btn.setAttribute("aria-label", t("to_top")); };
  setLabel();
  btn.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
  document.body.appendChild(btn);
  const onScroll = () => { btn.classList.toggle("show", window.scrollY > 420); queueSpy(); };
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", queueSpy);
  new MutationObserver(queueSpy).observe($("#view"), { childList: true, subtree: true });
  document.addEventListener("bf-lang", setLabel);
  onScroll();
}

// -------------------------------------------------------------- admin page
async function viewAdmin(token) {
  view.innerHTML = head(t("nav_admin"), esc(t("adm_sub"))) + skeleton(3);
  try {
    const [me, status] = await Promise.all([api("/api/auth/me"), api("/api/status")]);
    if (token !== state.routeToken) return;
    state.user = me; applyRole(); renderSidebar();
    const card = (href, icon, title, d, extra = "") => `<a class="card click menu-item" href="${href}"><span class="mi side-icon">${icon}</span><div><b>${esc(t(title))}</b> ${extra}<div class="small muted">${esc(t(d))}</div></div><span class="faint">›</span></a>`;
    const pending = me.pending_requests ? chip("red", String(me.pending_requests)) : "";
    const apis = status.providers.filter((p) => p.configured && p.method === "api").length;
    const tiles = `<div class="tiles" style="margin-bottom:12px">
      <a class="tile" href="#users/requests"><div class="k">${esc(t("adm_pending"))}</div><div class="v ${me.pending_requests ? "neg-red" : ""}">${me.pending_requests || 0}</div></a>
      <a class="tile" href="#usage"><div class="k">${esc(t("adm_sources"))}</div><div class="v">${apis}</div></a>
      <a class="tile" href="#scanner"><div class="k">${esc(t("adm_matches"))}</div><div class="v">${status.fixtures_with_odds}</div></a>
      <a class="tile" href="#usage"><div class="k">${esc(t("adm_storage"))}</div><div class="v small">${esc(status.storage.kind === "redis" ? "Redis" : t("adm_files"))}</div></a></div>`;
    view.innerHTML = head(t("nav_admin"), esc(t("adm_sub"))) + tiles +
      card("#users/requests", ICONS.users, "more_users", "more_users_d", pending) +
      card("#users/activity", ICONS.admin, "sm_activity", "adm_activity_d") +
      card("#sources", ICONS.sources, "more_sources", "more_sources_d") +
      card("#usage", ICONS.usage, "more_usage", "more_usage_d");
  } catch (err) { view.innerHTML += errorBox(err); }
}
