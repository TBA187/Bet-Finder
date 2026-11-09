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

/** Theme and language switches in the top bar. */
function renderToggles() {
  const th = $("#themeToggle"), lg = $("#langToggle");
  if (th) {
    const cur = currentTheme();
    th.innerHTML = `<button type="button" data-theme-set="dark" class="${cur === "dark" ? "on" : ""}" aria-pressed="${cur === "dark"}" title="${esc(t("theme_dark"))}" aria-label="${esc(t("theme_dark"))}">${ICONS.moon}</button>
      <button type="button" data-theme-set="light" class="${cur === "light" ? "on" : ""}" aria-pressed="${cur === "light"}" title="${esc(t("theme_light"))}" aria-label="${esc(t("theme_light"))}">${ICONS.sun}</button>`;
  }
  if (lg) {
    lg.innerHTML = [["en", "English"], ["da", "Dansk"]].map(([k, l]) => `<button type="button" data-lang-set="${k}" class="${state.lang === k ? "on" : ""}" aria-pressed="${state.lang === k}" title="${l}" aria-label="${l}">${flag(k)}</button>`).join("");
  }
}

// --------------------------------------------------------------- side menu
const side = { collapsed: null, open: null, more: null };  // filled from this browser's storage on first use
function sideState() {
  if (side.open === null) {
    side.open = store.get("bf_side_open", {});   // category id -> false when the person closed it
    side.more = store.get("bf_side_more", {});   // category id -> true when "show all" is on
  }
  return side;
}
const SIDE_LIMIT = 5;
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
  const groups = [
    { title: "g_main", items: [
      { id: "home", href: "#home", icon: ICONS.home, label: "nav_home",
        subs: [["coupons", "sm_coupons"], ["value", "sm_value"], ["errors", "sm_errors"], ["drops", "sm_drops"], ["performance", "sm_performance"]] },
      { id: "scanner", href: "#scanner", icon: ICONS.scanner, label: "nav_scanner",
        subs: [["all", "sm_all_matches"], ["valueonly", "sm_value_only"], ...catSubs] },
      { id: "picks", href: "#picks", icon: ICONS.picks, label: "nav_picks", subs: [["coupons", "sm_picks_coupons"], ["singles", "sm_singles"], ["criteria", "sm_criteria"]] },
      { id: "add", href: "#add", icon: ICONS.add, label: "nav_add", perm: "manual" },
    ] },
    { title: "g_tools", items: [
      { id: "agents", href: "#agents", icon: ICONS.agents, label: "more_agents" },
      { id: "arbitrage", href: "#arbitrage", icon: ICONS.arbitrage, label: "more_arb" },
      { id: "builder", href: "#builder", icon: ICONS.builder, label: "more_builder" },
      { id: "tracker", href: "#tracker", icon: ICONS.tracker, label: "more_tracker", subs: [["summary", "sm_summary"], ["history", "sm_history"]] },
      { id: "usage", href: "#usage", icon: ICONS.usage, label: "more_usage", perm: "usage", hide: admin,
        subs: [["chain", "sm_chain"], ["providers", "sm_providers"], ["planner", "sm_planner"]] },
    ] },
    { title: "g_admin", perm: "admin", items: [
      { id: "admin", href: "#admin", icon: ICONS.admin, label: "nav_admin", badge: state.user && state.user.pending_requests,
        subs: [["#users/requests", "sm_requests"], ["#users/users", "sm_accounts"], ["#users/new", "sm_new_account"], ["#users/activity", "sm_activity"],
          ["#sources", "more_sources"], ["#usage", "more_usage"], ["#sources/providers", "sm_api_keys"], ["#sources/leagues", "sm_leagues"], ["#sources/analysis", "sm_analysis"]] },
    ] },
    { title: "g_account", items: [
      { id: "account", href: "#account", icon: ICONS.account, label: "more_account", subs: [["password", "sm_password"], ["profile", "sm_profile"], ["security", "sm_security"]] },
    ] },
  ];
  return groups.filter((g) => !g.perm || can(g.perm))
    .map((g) => ({ ...g, items: g.items.filter((i) => (!i.perm || can(i.perm)) && !i.hide) }))
    .filter((g) => g.items.length);
}

/** Which category and sub-item belong to the current address. */
function activeNav() {
  const [tab, ...rest] = (location.hash.replace(/^#/, "") || "home").split("/");
  const sub = rest[0] || "";
  if (tab === "match") return { cat: "scanner", sub: "" };
  if (["users", "sources", "admin"].includes(tab) || (tab === "usage" && can("admin"))) {
    return { cat: "admin", sub: tab === "admin" ? "" : sub ? `#${tab}/${sub}` : `#${tab}` };
  }
  return { cat: tab, sub };
}

function renderSidebar() {
  const el = $("#sidebar");
  if (!el) return;
  if (!state.user) { el.innerHTML = ""; return; }
  const collapsed = sideCollapsed();
  document.body.classList.toggle("side-collapsed", collapsed);
  const act = activeNav();
  const groups = menuModel().map((g) => {
    const items = g.items.map((it) => {
      const isActive = act.cat === it.id;
      const subs = it.subs || [];
      const open = subs.length && side.open[it.id] !== false;
      const all = side.more[it.id] || subs.length <= SIDE_LIMIT + 1;
      const shown = all ? subs : subs.slice(0, SIDE_LIMIT);
      const subHtml = subs.length && open ? `<div class="side-subs">${shown.map(([k, label]) => {
        const href = k.startsWith("#") ? k : `${it.href}/${k}`;
        const on = isActive && act.sub === (k.startsWith("#") ? k : k);
        const text = label.startsWith("sm_") || label.startsWith("more_") ? t(label) : label;
        return `<a class="side-sub ${on ? "on" : ""}" href="${esc(href)}">${esc(text)}</a>`;
      }).join("")}${subs.length > SIDE_LIMIT + 1 ? `<button type="button" class="side-more" data-side-more="${it.id}">${esc(t(all ? "sm_show_less" : "sm_show_more", { n: subs.length - SIDE_LIMIT }))}</button>` : ""}</div>` : "";
      const badge = it.badge ? `<span class="side-badge">${esc(String(it.badge))}</span>` : "";
      return `<div class="side-item ${isActive ? "on" : ""}"><div class="side-row">
          <a class="side-link" href="${it.href}" title="${esc(t(it.label))}"><span class="side-icon">${it.icon}${collapsed && badge ? '<i class="side-dot"></i>' : ""}</span><span class="side-label">${esc(t(it.label))}</span>${collapsed ? "" : badge}</a>
          ${subs.length && !collapsed ? `<button type="button" class="side-caret ${open ? "open" : ""}" data-side-open="${it.id}" aria-expanded="${open ? "true" : "false"}" aria-label="${esc(t(open ? "sm_collapse" : "sm_expand"))}">${ICONS.chevron}</button>` : ""}
        </div>${collapsed ? "" : subHtml}</div>`;
    }).join("");
    return `<div class="side-group"><div class="side-title">${esc(t(g.title))}</div>${items}</div>`;
  }).join("");
  el.innerHTML = `<button type="button" class="side-toggle" data-side-toggle aria-label="${esc(t(collapsed ? "sm_expand_menu" : "sm_collapse_menu"))}" title="${esc(t(collapsed ? "sm_expand_menu" : "sm_collapse_menu"))}">
      ${collapsed ? ICONS.expand : ICONS.collapse}<span class="side-label">${esc(t("sm_collapse_menu"))}</span></button><nav aria-label="${esc(t("sm_menu"))}">${groups}</nav>`;
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
  if (op) { const id = op.dataset.sideOpen; side.open[id] = side.open[id] === false; store.set("bf_side_open", side.open); renderSidebar(); return; }
  const mo = e.target.closest("[data-side-more]");
  if (mo) { const id = mo.dataset.sideMore; side.more[id] = !side.more[id]; store.set("bf_side_more", side.more); renderSidebar(); return; }
  const th = e.target.closest("[data-theme-set]");
  if (th) { setTheme(th.dataset.themeSet); return; }
  const lg = e.target.closest("[data-lang-set]");
  if (lg && lg.dataset.langSet !== state.lang) { setLanguage(lg.dataset.langSet); return; }
  // on phones the opened menu covers the page: close it after choosing a page
  if (e.target.closest("#sidebar a") && isNarrow() && !sideCollapsed()) setSideCollapsed(true);
});

/** Keeps the side menu just below the top bar, whatever height the bar has. */
function trackTopbar() {
  const bar = $(".topbar");
  const set = () => document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`);
  set();
  if (window.ResizeObserver) new ResizeObserver(set).observe(bar);
  window.addEventListener("resize", set);
}

/** After a page renders, jump to the part chosen in the side menu (e.g. #home/value). */
function scrollToSection(name, token) {
  if (!name) return;
  let tries = 0;
  const look = () => {
    if (token !== state.routeToken) return;
    const el = document.getElementById(`sec-${name}`);
    if (el) {
      const top = el.getBoundingClientRect().top + window.scrollY - (parseInt(getComputedStyle(document.documentElement).getPropertyValue("--topbar-h"), 10) || 70) - 12;
      window.scrollTo({ top, behavior: "smooth" });
    } else if (tries++ < 40) setTimeout(look, 100);
  };
  look();
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
