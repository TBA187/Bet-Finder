/* BetFinder accounts: sign-in page, own account, user administration.
 * Loaded before app.js and uses its helpers (t, esc, api, chip, view, ...) at run time. */
"use strict";

const ROLES = ["user", "editor", "superadmin"];
const ROLE_CLS = { superadmin: "violet", editor: "blue", user: "" };
const roleChip = (r) => chip(ROLE_CLS[r] ?? "", t(`role_${r}`));
const roleOptions = (current) => ROLES.map((r) => `<option value="${r}" ${r === current ? "selected" : ""}>${esc(t(`role_${r}`))}</option>`).join("");
const langOptions = (current) => [["en", "English"], ["da", "Dansk"]].map(([v, l]) => `<option value="${v}" ${v === current ? "selected" : ""}>${l}</option>`).join("");
function initials(u) {
  const words = String(u.name || u.username || "?").trim().split(/\s+/);
  return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : words[0].slice(0, 2)).toUpperCase();
}
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString(locale(), { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–");
const can = (perm) => !!(state.user && state.user.permissions && state.user.permissions.includes(perm));

function passwordWarning(u) {
  if (!u || !u.must_change_password) return "";
  const text = u.default_password ? t("pw_default_warn") : t("pw_temp_warn", { d: esc(fmtDate(u.temp_password_expires_at)) });
  return `<div class="banner err"><div class="grow">${text} <a href="#account/password">${esc(t("pw_change_now"))}</a></div></div>`;
}

// ------------------------------------------------------------ sign-in page
const authState = { mode: "login", done: null };

function renderLogin(mode) {
  if (mode) { authState.mode = mode; authState.done = null; }
  applyRole();
  renderSidebar();
  $("#banner").innerHTML = "";
  const m = authState.mode;
  const field = (name, label, attrs = "", help = "") => `<div class="field"><label class="f" for="au_${name}">${esc(t(label))}</label>
    <input class="input" id="au_${name}" name="${name}" ${attrs}>${help ? `<div class="tiny faint" style="margin-top:3px">${esc(t(help))}</div>` : ""}</div>`;
  const honeypot = `<div class="hp" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>`;
  let body;
  if (authState.done) {
    body = `<div class="auth-done">✓ ${esc(t(authState.done))}</div>
      <button class="btn block" type="button" data-action="authMode" data-v="login">${esc(t("au_back_signin"))}</button>`;
  } else if (m === "request") {
    body = `<h1 class="auth-title">${esc(t("au_request_title"))}</h1><div class="small muted">${esc(t("au_request_d"))}</div>
      <form data-submit="requestAccount" novalidate>
        ${field("name", "au_name", 'autocomplete="name" required maxlength="80"')}
        ${field("username", "au_username", 'autocomplete="username" required minlength="3" maxlength="32" autocapitalize="none" spellcheck="false"', "au_username_h")}
        ${field("email", "au_email", 'type="email" autocomplete="email" required maxlength="120"')}
        <div class="field"><label class="f" for="au_message">${esc(t("au_message"))}</label><textarea class="input" id="au_message" name="message" maxlength="500" rows="3"></textarea></div>
        ${honeypot}<div id="authErr" class="auth-err" role="alert"></div>
        <button class="btn block" type="submit">${esc(t("au_send_request"))}</button></form>
      <div class="auth-links"><a href="#" data-action="authMode" data-v="login">‹ ${esc(t("au_back_signin"))}</a></div>`;
  } else if (m === "forgot") {
    body = `<h1 class="auth-title">${esc(t("au_forgot_title"))}</h1><div class="small muted">${esc(t("au_forgot_d"))}</div>
      <form data-submit="forgotPassword" novalidate>
        ${field("login", "au_login", 'autocomplete="username" required maxlength="120" autocapitalize="none" spellcheck="false"')}
        ${honeypot}<div id="authErr" class="auth-err" role="alert"></div>
        <button class="btn block" type="submit">${esc(t("au_forgot_send"))}</button></form>
      <div class="auth-links"><a href="#" data-action="authMode" data-v="login">‹ ${esc(t("au_back_signin"))}</a></div>`;
  } else {
    body = `<h1 class="auth-title">${esc(t("au_signin"))}</h1><div class="small muted">${esc(t("au_tagline"))}</div>
      <form data-submit="login" novalidate>
        ${field("login", "au_login", 'autocomplete="username" required maxlength="120" autocapitalize="none" spellcheck="false"')}
        ${field("password", "au_password", 'type="password" autocomplete="current-password" required maxlength="200"')}
        <label class="switch small" style="margin-top:12px"><input type="checkbox" name="remember" checked><span>${esc(t("au_remember"))}</span></label>
        <div id="authErr" class="auth-err" role="alert"></div>
        <button class="btn block" type="submit">${esc(t("au_signin"))}</button></form>
      <div class="auth-links"><a href="#" data-action="authMode" data-v="request">${esc(t("au_request_link"))}</a><a href="#" data-action="authMode" data-v="forgot">${esc(t("au_forgot_link"))}</a></div>`;
  }
  view.innerHTML = `<div class="auth-wrap"><div class="card auth-card">
      <div class="auth-brand"><svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M15.5 15.5 21 21" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><path d="M7.5 11.5l2 2 4-5" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg><span>BetFinder</span></div>
      ${body}</div>
    <div class="seg auth-lang"><button type="button" data-action="setLang" data-v="en" class="${state.lang === "en" ? "on" : ""}">${flag("en")} English</button><button type="button" data-action="setLang" data-v="da" class="${state.lang === "da" ? "on" : ""}">${flag("da")} Dansk</button></div>
    <div class="tiny faint auth-foot">${esc(t("au_foot"))}</div></div>`;
  const first = view.querySelector("input:not([type=checkbox]):not([tabindex='-1'])");
  if (first && window.matchMedia("(min-width: 700px)").matches) first.focus();
}

function setAuthErr(msg) { const el = $("#authErr"); if (el) el.textContent = msg || ""; }

async function authSubmit(form, fn) {
  const btn = form.querySelector("button[type=submit]");
  setAuthErr("");
  if (btn) btn.disabled = true;
  try { await fn(new FormData(form)); } catch (err) { setAuthErr(err.message); } finally { if (btn) btn.disabled = false; }
}

/** Call after any sign-in: set the account's language, then load the app. */
async function afterSignIn(me) {
  state.user = me;
  if (me.language !== state.lang) {
    try { state.user = await api("/api/account", { method: "PUT", body: { language: state.lang } }); } catch (_) { /* keep */ }
  }
  authState.mode = "login"; authState.done = null;
  await startApp();
}

function signedOut(message) {
  state.user = null;
  closeModal();
  renderLogin("login");
  if (message) setAuthErr(message);
}

// ------------------------------------------------------------ my account
async function viewAccount(token) {
  view.innerHTML = backBtn() + head(t("ac_title")) + skeleton(2);
  try {
    const me = await api("/api/auth/me");
    if (token !== state.routeToken) return;
    state.user = me; applyRole(); renderTop();
    view.innerHTML = backBtn() + renderAccount(me);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function renderAccount(u) {
  const f = (name, label, value, attrs = "") => `<div class="field"><label class="f" for="ac_${name}">${esc(t(label))}</label><input class="input" id="ac_${name}" name="${name}" value="${esc(value)}" ${attrs}></div>`;
  return head(t("ac_title"), esc(t("ac_sub"))) + `
  <div class="card"><div class="spread wrap"><div class="row" style="min-width:0"><span class="avatar big">${esc(initials(u))}</span>
      <div style="min-width:0"><b>${esc(u.name || u.username)}</b><div class="small muted">@${esc(u.username)}${u.email ? " · " + esc(u.email) : ""}</div></div></div>${roleChip(u.role)}</div>
    <div class="small muted" style="margin-top:8px">${esc(t(`role_${u.role}_d`))}</div>
    <dl class="kv" style="margin-top:10px"><dt>${esc(t("ac_last_login"))}</dt><dd>${esc(fmtDate(u.last_login_at))}</dd>
      <dt>${esc(t("ac_pw_changed_at"))}</dt><dd>${u.password_changed_at ? esc(fmtDate(u.password_changed_at)) : esc(t("never"))}</dd></dl></div>
  <div class="section-title" id="sec-password">${esc(t("ac_password"))}</div>
  <form class="card ${u.must_change_password ? "attention" : ""}" data-submit="changePassword">
    ${u.must_change_password ? `<div class="small" style="color:var(--amber);margin-bottom:4px">${u.default_password ? t("pw_default_warn") : t("pw_temp_warn", { d: esc(fmtDate(u.temp_password_expires_at)) })}</div>` : ""}
    <input type="text" name="username" value="${esc(u.username)}" autocomplete="username" hidden>
    <div class="field"><label class="f" for="ac_cur">${esc(t(u.must_change_password && !u.default_password ? "ac_current_temp" : "ac_current"))}</label><input class="input" id="ac_cur" type="password" name="current_password" autocomplete="current-password" required></div>
    <div class="grid-2"><div class="field"><label class="f" for="ac_new">${esc(t("ac_new"))}</label><input class="input" id="ac_new" type="password" name="new_password" autocomplete="new-password" minlength="8" required></div>
      <div class="field"><label class="f" for="ac_rep">${esc(t("ac_repeat"))}</label><input class="input" id="ac_rep" type="password" name="repeat" autocomplete="new-password" minlength="8" required></div></div>
    <div class="tiny faint" style="margin-top:6px">${esc(t("ac_pw_rules"))}</div>
    <button class="btn small" type="submit" style="margin-top:10px">${esc(t("ac_pw_button"))}</button></form>
  <div class="section-title" id="sec-profile">${esc(t("ac_profile"))}</div>
  <form class="card" data-submit="saveProfile">
    <div class="grid-2">${f("name", "ac_name", u.name, 'autocomplete="name" maxlength="80"')}
      ${f("username", "ac_username", u.username, 'autocomplete="username" required minlength="3" maxlength="32" autocapitalize="none" spellcheck="false"')}
      ${f("email", "ac_email", u.email, 'type="email" autocomplete="email" maxlength="120"')}
      <div class="field"><label class="f" for="ac_language">${esc(t("ac_language"))}</label><select class="input" id="ac_language" name="language">${langOptions(u.language)}</select></div></div>
    <button class="btn ghost small" type="submit" style="margin-top:10px">${esc(t("save"))}</button></form>
  <div class="section-title" id="sec-security">${esc(t("ac_security"))}</div>
  <div class="card"><div class="small muted">${esc(t("ac_others_d"))}</div>
    <div class="row wrap" style="margin-top:10px"><button class="btn ghost small" type="button" data-action="signOutOthers">${esc(t("ac_sign_out_others"))}</button>
      <button class="btn danger small" type="button" data-action="signOut">${esc(t("ac_sign_out"))}</button></div></div>`;
}

// -------------------------------------------------------- users & requests
const usersState = { tab: "requests" };

async function viewUsers(token) {
  view.innerHTML = backBtn() + head(t("us_title"), esc(t("us_sub"))) + skeleton(3);
  try {
    const [requests, users, me, activity] = await Promise.all([api("/api/admin/requests"), api("/api/admin/users"), api("/api/auth/me"),
      usersState.tab === "activity" ? api("/api/admin/activity") : Promise.resolve([])]);
    if (token !== state.routeToken) return;
    state.user = me; applyRole();
    if (usersState.tab === "requests" && !requests.length && users.length > 1 && !usersState.picked) usersState.tab = "users";
    view.innerHTML = backBtn() + renderUsers(requests, users, activity, me);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function renderUsers(requests, users, activity, me) {
  const tab = usersState.tab;
  const seg = [["requests", `${t("us_tab_requests")}${requests.length ? ` (${requests.length})` : ""}`], ["users", `${t("us_tab_users")} (${users.length})`],
    ["new", t("us_tab_new")], ["activity", t("us_tab_activity")]]
    .map(([k, l]) => `<button type="button" data-action="usersTab" data-v="${k}" class="${tab === k ? "on" : ""}">${esc(l)}</button>`).join("");
  const mail = me.email_configured ? `<div class="hint ok">${esc(t("us_email_ok"))}</div>` : `<div class="hint">${esc(t("us_no_email_setup"))}</div>`;
  let body = "";
  if (tab === "requests") {
    body = requests.length ? requests.map((r) => `<div class="card"><div class="spread"><b>${esc(r.name || r.username)}</b>${chip(r.kind === "account" ? "blue" : "amber", t(`us_kind_${r.kind}`))}</div>
      <div class="small muted">@${esc(r.username)}${r.email ? " · " + esc(r.email) : ""} · ${esc(fmtDate(r.created_at))}</div>
      ${r.message ? `<div class="small quote">${esc(r.message)}</div>` : ""}
      <div class="row wrap" style="margin-top:10px">
        ${r.kind === "account" ? `<label class="small muted" for="role_${esc(r.id)}">${esc(t("us_role"))}</label><select class="input inline-select" id="role_${esc(r.id)}">${roleOptions("user")}</select>
          <button class="btn small" type="button" data-action="approveReq" data-id="${esc(r.id)}">${esc(t("us_approve"))}</button>`
        : `<button class="btn small" type="button" data-action="approveReq" data-id="${esc(r.id)}">${esc(t("us_send_temp"))}</button>`}
        <button class="btn danger small" type="button" data-action="rejectReq" data-id="${esc(r.id)}">${esc(t("us_reject"))}</button></div></div>`).join("")
      : `<div class="empty">${esc(t("us_no_requests"))}</div>`;
    body = mail + body;
  } else if (tab === "users") {
    body = users.map((u) => {
      const self = u.id === me.id;
      const flags = [self ? chip("green", t("us_you")) : "", u.disabled ? chip("red", t("us_disabled")) : "",
        u.default_password ? chip("red", t("us_default")) : u.must_change_password ? chip("amber", t("us_temp")) : ""].join(" ");
      return `<div class="card ${u.disabled ? "dim" : ""}"><div class="spread wrap"><div class="row" style="min-width:0"><span class="avatar">${esc(initials(u))}</span>
          <div style="min-width:0"><b>${esc(u.name || u.username)}</b> <span class="small muted">@${esc(u.username)}</span>
          <div class="tiny muted">${u.email ? esc(u.email) + " · " : ""}${u.last_login_at ? esc(t("us_last_login", { t: fmtDate(u.last_login_at) })) : esc(t("us_never_logged"))}</div></div></div>${roleChip(u.role)}</div>
        ${flags.trim() ? `<div class="row wrap" style="margin-top:6px">${flags}</div>` : ""}
        <details class="market" style="margin-top:8px"><summary><span>${esc(t("us_edit"))}</span></summary>
          <form data-submit="saveUser" data-id="${esc(u.id)}"><div class="grid-2">
            <div class="field"><label class="f">${esc(t("ac_name"))}</label><input class="input" name="name" value="${esc(u.name)}" maxlength="80"></div>
            <div class="field"><label class="f">${esc(t("ac_username"))}</label><input class="input" name="username" value="${esc(u.username)}" required maxlength="32" autocapitalize="none"></div>
            <div class="field"><label class="f">${esc(t("ac_email"))}</label><input class="input" type="email" name="email" value="${esc(u.email)}" maxlength="120"></div>
            <div class="field"><label class="f">${esc(t("us_role"))}</label><select class="input" name="role">${roleOptions(u.role)}</select></div></div>
            <button class="btn ghost small" type="submit" style="margin-top:10px">${esc(t("save"))}</button></form>
          <div class="row wrap" style="margin-top:12px">
            <button class="btn ghost small" type="button" data-action="resetUserPw" data-id="${esc(u.id)}" data-u="${esc(u.username)}">${esc(t("us_reset_pw"))}</button>
            ${self ? "" : `<button class="btn ghost small" type="button" data-action="toggleUser" data-id="${esc(u.id)}" data-v="${u.disabled ? "enable" : "disable"}">${esc(t(u.disabled ? "us_enable" : "us_disable"))}</button>
            <button class="btn danger small" type="button" data-action="deleteUser" data-id="${esc(u.id)}" data-u="${esc(u.username)}">${esc(t("us_delete"))}</button>`}</div></details></div>`;
    }).join("");
  } else if (tab === "new") {
    body = mail + `<form class="card" data-submit="createUser"><div class="small muted">${esc(t("us_create_d"))}</div><div class="grid-2">
      <div class="field"><label class="f" for="nu_name">${esc(t("ac_name"))}</label><input class="input" id="nu_name" name="name" maxlength="80"></div>
      <div class="field"><label class="f" for="nu_username">${esc(t("ac_username"))}</label><input class="input" id="nu_username" name="username" required minlength="3" maxlength="32" autocapitalize="none" spellcheck="false"></div>
      <div class="field"><label class="f" for="nu_email">${esc(t("ac_email"))}</label><input class="input" id="nu_email" type="email" name="email" maxlength="120"></div>
      <div class="field"><label class="f" for="nu_role">${esc(t("us_role"))}</label><select class="input" id="nu_role" name="role">${roleOptions("user")}</select></div>
      <div class="field"><label class="f" for="nu_lang">${esc(t("ac_language"))}</label><select class="input" id="nu_lang" name="language">${langOptions(state.lang)}</select></div></div>
      <button class="btn small" type="submit" style="margin-top:12px">${esc(t("us_create"))}</button></form>`;
  } else {
    body = activity.length ? `<div class="card">${activity.map((e) => `<div class="list-row"><div style="min-width:0"><b>${esc(t(`ev_${e.event}`) !== `ev_${e.event}` ? t(`ev_${e.event}`) : e.event)}</b>
        <span class="small muted">${e.actor && e.actor !== e.target ? esc(e.actor) + " → " : ""}${esc(e.target || "")}${e.detail ? " · " + esc(e.detail) : ""}</span>
        <div class="tiny faint">${esc(fmtDate(e.at))}${e.ip ? " · " + esc(e.ip) : ""}</div></div></div>`).join("")}</div>`
      : `<div class="empty">${esc(t("us_activity_none"))}</div>`;
  }
  const roles = `<details class="market" style="margin-top:14px"><summary><span>${esc(t("us_roles_title"))}</span></summary>
    ${ROLES.slice().reverse().map((r) => `<div class="list-row"><div>${roleChip(r)} <span class="small muted">${esc(t(`role_${r}_d`))}</span></div></div>`).join("")}</details>`;
  return head(t("us_title"), esc(t("us_sub"))) + `<div class="seg wrap-seg">${seg}</div><div style="margin-top:12px">${body}</div>${roles}`;
}

// ------------------------------------------------- temporary password dialog
function openModal(html) {
  const m = $("#modal");
  m.innerHTML = `<div class="card modal-box" role="dialog" aria-modal="true" aria-labelledby="modalTitle">${html}</div>`;
  m.hidden = false;
  const btn = m.querySelector("button");
  if (btn) btn.focus();
}
function closeModal() { const m = $("#modal"); if (m) { m.hidden = true; m.innerHTML = ""; } }

function showTempPassword(r) {
  const u = r.user, e = r.email || {};
  const status = e.sent ? `<div class="hint ok">✓ ${esc(t("tp_sent", { e: e.to }))}</div>`
    : !e.to ? `<div class="hint">${esc(t("tp_no_email"))}</div>`
      : e.error === "not_configured" ? `<div class="hint">${esc(t("tp_not_configured"))}</div>`
        : `<div class="hint">${esc(t("tp_failed", { e: e.error || "" }))}</div>`;
  const mailto = e.to && !e.sent ? `mailto:${encodeURIComponent(e.to)}?subject=${encodeURIComponent(e.subject || "")}&body=${encodeURIComponent(e.body || "")}` : "";
  openModal(`<h2 id="modalTitle">${esc(t("tp_title"))}</h2><div class="small muted">${esc(t("tp_for", { u: u.username }))}</div>
    <div class="temp-pw">${esc(r.temp_password)}</div>
    <div class="small muted" style="text-align:center">${esc(t("tp_valid", { d: fmtDate(r.expires_at) }))}</div>
    ${status}<div class="tiny faint" style="margin-top:8px">${esc(t("tp_once"))}</div>
    <div class="row wrap" style="margin-top:12px">
      <button class="btn small" type="button" data-action="copyText" data-text="${esc(r.temp_password)}">${esc(t("tp_copy"))}</button>
      <button class="btn ghost small" type="button" data-action="copyText" data-text="${esc(e.body || "")}">${esc(t("tp_copy_msg"))}</button>
      ${mailto ? `<a class="btn ghost small" href="${esc(mailto)}">${esc(t("tp_mailto"))}</a>` : ""}
      <button class="btn ghost small" type="button" data-action="closeModal">${esc(t("close"))}</button></div>`);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (_) {
    const ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (_e) { /* ignore */ }
    ta.remove();
  }
  toast(t("copied"));
}

// ------------------------------------------------------------ handlers
const AUTH_ACTIONS = {
  authMode: (el) => renderLogin(el.dataset.v),
  usersTab: (el) => { usersState.tab = el.dataset.v; usersState.picked = true; if (location.hash === `#users/${el.dataset.v}`) route(); else location.hash = `#users/${el.dataset.v}`; },
  closeModal: () => { closeModal(); route(); },
  copyText: (el) => copyText(el.dataset.text || ""),
  signOut: async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (_) { /* signed out anyway */ }
    history.replaceState(null, "", location.pathname);
    signedOut();
  },
  signOutOthers: async () => {
    try { state.user = await api("/api/account/sign-out-others", { method: "POST" }); toast(t("ac_signed_out_others"), 4000); }
    catch (err) { toast(err.message, 5000); }
  },
  approveReq: async (el) => {
    const sel = $(`#role_${CSS.escape(el.dataset.id)}`);
    el.disabled = true;
    try {
      const r = await api(`/api/admin/requests/${encodeURIComponent(el.dataset.id)}/approve`, { method: "POST", body: { role: sel ? sel.value : "user" } });
      showTempPassword(r);
    } catch (err) { toast(err.message, 5000); el.disabled = false; }
  },
  rejectReq: async (el) => {
    const reason = prompt(t("us_reject_reason"), "");
    if (reason === null) return;
    try { await api(`/api/admin/requests/${encodeURIComponent(el.dataset.id)}/reject`, { method: "POST", body: { reason } }); toast(t("us_rejected")); route(); }
    catch (err) { toast(err.message, 5000); }
  },
  resetUserPw: async (el) => {
    if (!confirm(t("us_reset_confirm", { u: el.dataset.u }))) return;
    try { showTempPassword(await api(`/api/admin/users/${encodeURIComponent(el.dataset.id)}/reset-password`, { method: "POST" })); }
    catch (err) { toast(err.message, 5000); }
  },
  toggleUser: async (el) => {
    try { await api(`/api/admin/users/${encodeURIComponent(el.dataset.id)}`, { method: "PUT", body: { disabled: el.dataset.v === "disable" } }); toast(t("us_updated")); route(); }
    catch (err) { toast(err.message, 5000); }
  },
  deleteUser: async (el) => {
    if (!confirm(t("us_delete_confirm", { u: el.dataset.u }))) return;
    try { await api(`/api/admin/users/${encodeURIComponent(el.dataset.id)}`, { method: "DELETE" }); toast(t("us_deleted")); route(); }
    catch (err) { toast(err.message, 5000); }
  },
};

const AUTH_SUBMITS = {
  login: (form) => authSubmit(form, async (fd) => {
    const me = await api("/api/auth/login", { method: "POST", quiet: true,
      body: { login: String(fd.get("login") || "").trim(), password: String(fd.get("password") || ""), remember: fd.get("remember") === "on" } });
    await afterSignIn(me);
  }),
  requestAccount: (form) => authSubmit(form, async (fd) => {
    const body = { language: state.lang };
    for (const k of ["name", "username", "email", "message", "website"]) body[k] = String(fd.get(k) || "").trim();
    if (!body.name || !body.username || !body.email) throw new Error(t("au_fill_all"));
    await api("/api/auth/request-account", { method: "POST", quiet: true, body });
    authState.done = "au_request_sent"; renderLogin();
  }),
  forgotPassword: (form) => authSubmit(form, async (fd) => {
    const login = String(fd.get("login") || "").trim();
    if (!login) throw new Error(t("au_fill_all"));
    await api("/api/auth/forgot-password", { method: "POST", quiet: true, body: { login, language: state.lang, website: String(fd.get("website") || "") } });
    authState.done = "au_forgot_sent"; renderLogin();
  }),
  changePassword: async (form) => {
    const fd = new FormData(form);
    if (fd.get("new_password") !== fd.get("repeat")) return toast(t("ac_pw_mismatch"), 4000);
    try {
      state.user = await api("/api/account/password", { method: "POST", body: { current_password: fd.get("current_password"), new_password: fd.get("new_password") } });
      toast(t("ac_pw_changed"), 4000); renderTop(); route();
    } catch (err) { toast(err.message, 6000); }
  },
  saveProfile: async (form) => {
    const fd = new FormData(form), body = {};
    for (const k of ["name", "username", "email", "language"]) body[k] = String(fd.get(k) || "").trim();
    try {
      state.user = await api("/api/account", { method: "PUT", body });
      toast(t("ac_saved"));
      if (state.user.language !== state.lang) { state.lang = state.user.language; store.set("bf_lang", state.lang); applyLanguage(); state.markets = {}; state.competitions = {}; await loadStatus(); }
      route();
    } catch (err) { toast(err.message, 5000); }
  },
  saveUser: async (form) => {
    const fd = new FormData(form), body = {};
    for (const k of ["name", "username", "email", "role"]) body[k] = String(fd.get(k) || "").trim();
    try { await api(`/api/admin/users/${encodeURIComponent(form.dataset.id)}`, { method: "PUT", body }); toast(t("us_updated")); route(); }
    catch (err) { toast(err.message, 5000); }
  },
  createUser: async (form) => {
    const fd = new FormData(form), body = {};
    for (const k of ["name", "username", "email", "role", "language"]) body[k] = String(fd.get(k) || "").trim();
    try { const r = await api("/api/admin/users", { method: "POST", body }); usersState.tab = "users"; showTempPassword(r); }
    catch (err) { toast(err.message, 5000); }
  },
};
