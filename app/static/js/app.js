/* BetFinder front-end. Vanilla JS, no build step.
 * Every value from the server is escaped before it is put into HTML. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const view = $("#view");
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const state = {
  status: null,
  scanner: { sport: "", value_only: false, sort: "value" },
  add: { sport: "football", fixture: null, create: false, market: "h2h", line: "", bookmaker: "danske_spil", result: null },
  bookmakers: null,
  markets: {},
  competitions: {},
  routeToken: 0,
};

const SPORT_LABEL = { football: "Football", basketball: "Basketball", handball: "Handball" };
const SPORT_ICON = { football: "⚽", basketball: "🏀", handball: "🤾" };
const RISK = { low: ["green", "Low risk"], medium: ["amber", "Medium risk"], high: ["orange", "High risk"], very_high: ["red", "Very high risk"] };
const STANCE = { supports: ["green", "Supports"], opposes: ["red", "Opposes"], neutral: ["", "Neutral"], no_data: ["", "No data"] };
const AGENT_STATUS = { ok: ["green", "OK"], partial: ["amber", "Partial"], no_data: ["", "No data"], error: ["red", "Error"] };
const FLAG = {
  possible_pricing_error: ["red", "Possible pricing error"], outlier_price: ["orange", "Outlier price"],
  above_market: ["blue", "Above market"], arbitrage: ["violet", "Arbitrage"],
};
const PALETTE = ["var(--accent)", "var(--blue)", "var(--amber)", "var(--violet)", "var(--red)"];

// ------------------------------------------------------------------ helpers
const fmtOdds = (o) => (o == null ? "–" : Number(o).toFixed(2));
const fmtPct = (p, d = 1) => (p == null ? "–" : (p * 100).toFixed(d) + "%");
const fmtEv = (x) => (x == null ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(1) + "%");
const evClass = (x) => (x != null && x > 0 ? "pos" : "neg");
const riskChip = (r) => (r ? `<span class="chip ${RISK[r][0]}">${RISK[r][1]}</span>` : "");
const chip = (cls, text) => `<span class="chip ${cls}">${esc(text)}</span>`;

function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  const now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diff === 0) return `Today ${time}`;
  if (diff === 1) return `Tomorrow ${time}`;
  if (diff === -1) return `Yesterday ${time}`;
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) + " " + time;
}
function ago(min) {
  if (min == null) return "–";
  if (min < 1) return "just now";
  if (min < 60) return `${Math.round(min)} min ago`;
  if (min < 1440) return `${Math.round(min / 60)} h ago`;
  return `${Math.round(min / 1440)} d ago`;
}
function agoIso(iso) { return iso ? ago((Date.now() - new Date(iso).getTime()) / 60000) : "never"; }

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  let token = null;
  try { token = localStorage.getItem("bf_token"); } catch (_) { /* storage blocked */ }
  if (token) headers["X-BetFinder-Token"] = token;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401) {
    const t = prompt("This BetFinder server requires an access token:");
    if (t) { try { localStorage.setItem("bf_token", t.trim()); } catch (_) {} return api(path, opts); }
  }
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

function skeleton(n = 3) { return Array.from({ length: n }, () => `<div class="skeleton"></div>`).join(""); }
function errorBox(err) { return `<div class="banner err"><div class="grow"><b>Something went wrong.</b> ${esc(err.message || err)}</div></div>`; }

function selLabel(market, sel, home, away, line) {
  const signed = (x) => (x > 0 ? `+${x}` : `${x}`);
  if (["h2h", "h2h3", "dnb"].includes(market)) return { home: home || "Home", away: away || "Away", draw: "Draw" }[sel] || sel;
  if (market === "totals") return `${sel === "over" ? "Over" : "Under"} ${line ?? ""}`;
  if (market === "spreads") {
    const l = Number(line || 0);
    return sel === "home" ? `${home || "Home"} ${signed(l)}` : `${away || "Away"} ${signed(-l)}`;
  }
  if (market === "btts") return `BTTS ${sel === "yes" ? "Yes" : "No"}`;
  return sel;
}
const MARKET_NAME = {
  h2h: (s) => (s === "basketball" ? "Moneyline (incl. OT)" : "Match result 1X2"), h2h3: () => "3-way (regulation)",
  dnb: () => "Draw no bet", totals: (s) => (s === "basketball" ? "Total points" : "Total goals"), spreads: () => "Handicap", btts: () => "Both teams to score",
};

// ------------------------------------------------------------- status/top
async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch (err) {
    state.status = null;
  }
  renderTop();
}

function renderTop() {
  const s = state.status;
  const pill = $("#sourcePill");
  const banner = $("#banner");
  if (!s) { pill.textContent = "Offline"; pill.className = "pill warn"; banner.innerHTML = ""; return; }
  const configured = s.providers.filter((p) => p.configured);
  const erroring = configured.filter((p) => p.last_error && (!p.last_ok || p.last_error_at > p.last_ok));
  let html = "";
  if (s.demo_mode) {
    pill.textContent = "Demo data"; pill.className = "pill demo";
    html += `<div class="banner demo"><div class="grow"><b>Demo mode.</b> All teams, bookmakers and odds shown are fictional examples. Switch it off under <a href="#sources">Sources</a>.</div></div>`;
  } else if (!configured.length) {
    pill.textContent = "Manual only"; pill.className = "pill warn";
    html += `<div class="banner warn"><div class="grow"><b>No live odds source connected.</b> BetFinder never invents odds. Add an API key under <a href="#sources">Sources</a>, <a href="#add">enter odds manually</a>, or try demo mode.</div></div>`;
  } else if (erroring.length) {
    pill.textContent = "Source error"; pill.className = "pill warn";
    html += `<div class="banner err"><div class="grow"><b>${esc(erroring[0].label)}:</b> ${esc(erroring[0].last_error)}</div></div>`;
  } else {
    pill.textContent = `Live · ${configured.length} source${configured.length > 1 ? "s" : ""}`; pill.className = "pill ok";
  }
  banner.innerHTML = html;
}

async function doRefresh() {
  const btn = $("#refreshBtn");
  if (btn.classList.contains("spin")) return;
  btn.classList.add("spin");
  try {
    const r = await api("/api/refresh", { method: "POST" });
    if (r.status === "busy") toast("A refresh is already running");
    else if (r.message) toast(r.message, 4500);
    else {
      const q = (r.providers || []).reduce((a, p) => a + (p.quotes || 0), 0);
      const f = (r.providers || []).reduce((a, p) => a + (p.fixtures || 0), 0);
      const errs = (r.providers || []).flatMap((p) => p.errors || []);
      toast(errs.length ? `Refreshed with ${errs.length} error(s): ${errs[0]}` : `Refreshed: ${f} matches, ${q} prices${r.demo_fixtures ? " (demo)" : ""}`, 4500);
    }
  } catch (err) {
    toast(err.message, 4500);
  } finally {
    btn.classList.remove("spin");
    await loadStatus();
    route();
  }
}

// --------------------------------------------------------------- scanner
async function viewScanner(token) {
  const f = state.scanner;
  const seg = ["", "football", "basketball", "handball"].map((s) =>
    `<button data-action="scanSport" data-v="${s}" class="${f.sport === s ? "on" : ""}">${s ? SPORT_ICON[s] + " " + SPORT_LABEL[s] : "All"}</button>`).join("");
  view.innerHTML = `
    <h1 class="page-title">Odds scanner</h1>
    <div class="page-sub">Bookmaker prices compared against a margin-free market price. Value is highlighted, and so are possible pricing errors.</div>
    <div class="filters">
      <div class="seg">${seg}</div>
      <button class="toggle-chip ${f.value_only ? "on" : ""}" data-action="scanValue">Qualifying value only</button>
      <select class="compact" data-change="scanSort" aria-label="Sort">
        <option value="value" ${f.sort === "value" ? "selected" : ""}>Sort: best value</option>
        <option value="kickoff" ${f.sort === "kickoff" ? "selected" : ""}>Sort: kick-off</option>
        <option value="confidence" ${f.sort === "confidence" ? "selected" : ""}>Sort: confidence</option>
      </select>
    </div>
    <div id="scanList">${skeleton(3)}</div>`;
  const q = new URLSearchParams({ sort: f.sort });
  if (f.sport) q.set("sport", f.sport);
  if (f.value_only) q.set("value_only", "true");
  try {
    const data = await api(`/api/scanner?${q}`);
    if (token !== state.routeToken) return;
    $("#scanList").innerHTML = renderScanList(data);
  } catch (err) {
    $("#scanList").innerHTML = errorBox(err);
  }
}

function renderScanList(data) {
  if (!data.items.length) {
    const extra = data.fixtures_without_odds ? `<div class="small" style="margin-top:6px">${data.fixtures_without_odds} upcoming match(es) have no prices yet.</div>` : "";
    return `<div class="empty"><div class="big">No matches with odds to show</div>
      Refresh the data sources, <a href="#add">enter odds manually</a>, or change the filters.${extra}</div>`;
  }
  const head = `<div class="small muted" style="margin-bottom:8px">${data.items.length} match(es)${data.fixtures_without_odds ? ` · ${data.fixtures_without_odds} without prices` : ""}</div>`;
  return head + data.items.map(scanCard).join("");
}

function scanCard(it) {
  const fx = it.fixture;
  const h = it.headline;
  const err = it.flags.includes("possible_pricing_error");
  const good = h && h.qualifies;
  let headline = `<div class="headline"><span class="muted small">No positive expected value at current prices</span></div>`;
  if (h && h.ev_pct != null) {
    headline = `<div class="headline ${good ? "good" : ""}">
      <div style="min-width:0">
        <div class="sel">${esc(h.label)} <span class="muted small">· ${esc(h.market_label)}</span></div>
        <div class="small muted">${fmtOdds(h.best.odds)} at ${esc(h.best.name)} · fair ${fmtOdds(h.fair_odds)} · p ${fmtPct(h.final_prob)}</div>
      </div>
      <div style="text-align:right">
        <div class="ev ${evClass(h.ev_pct)}">${fmtEv(h.ev_pct)}</div>
        <div class="tiny muted">${good ? "qualifies" : "EV"}</div>
      </div></div>`;
  }
  const mm = it.main_market;
  let mini = "";
  if (mm) {
    mini = `<div class="mini-odds" style="--n:${mm.selections.length}">` + mm.selections.map((s) => {
      const b = mm.best[s];
      return `<div class="cell"><div class="lbl">${esc(mm.selection_labels[s])}</div>
        <div class="o odds">${b ? fmtOdds(b.odds) : "–"}</div><div class="bk">${b ? esc(b.name) : ""}</div></div>`;
    }).join("") + `</div>`;
  }
  const flags = [];
  for (const f of it.flags) if (FLAG[f]) flags.push(chip(FLAG[f][0], FLAG[f][1]));
  if (h && h.risk_rating && h.ev_pct > 0) flags.push(riskChip(h.risk_rating));
  if (fx.is_demo) flags.push(chip("violet", "Demo"));
  if (fx.manual) flags.push(chip("amber", "Manual match"));
  flags.push(chip("", `${it.n_bookmakers} bookmaker${it.n_bookmakers === 1 ? "" : "s"}`));
  flags.push(chip("", `${it.agents_with_data}/10 agents with data`));
  return `<div class="card click ${err ? "error-suspect" : good ? "value" : ""}" data-action="openMatch" data-id="${esc(fx.id)}">
    <div class="fx-meta"><span>${SPORT_ICON[fx.sport]} ${esc(it.competition.name)} · ${fmtTime(fx.kickoff)}</span>
      <span class="conf">Confidence ${it.confidence}<span class="meter"><i style="width:${it.confidence}%"></i></span></span></div>
    <div class="fx-teams">${esc(fx.home)}<span class="vs">vs</span>${esc(fx.away)}</div>
    ${headline}${mini}<div class="flags">${flags.join("")}</div></div>`;
}

// ------------------------------------------------------------ match view
async function viewMatch(token, id) {
  view.innerHTML = `<button class="back" data-action="back">‹ Back</button>${skeleton(4)}`;
  try {
    const a = await api(`/api/fixtures/${encodeURIComponent(id)}`);
    if (token !== state.routeToken) return;
    view.innerHTML = renderMatch(a);
  } catch (err) {
    view.innerHTML = `<button class="back" data-action="back">‹ Back</button>${errorBox(err)}`;
  }
}

function renderMatch(a) {
  const fx = a.fixture;
  const started = new Date(fx.kickoff) <= new Date();
  const parts = a.confidence_parts;
  const warn = a.warnings.length ? `<details class="market" style="margin-top:10px"><summary><span>⚠ ${a.warnings.length} warning(s)</span></summary>
      <ul class="small muted" style="margin:0;padding-left:18px">${a.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : "";
  const sels = a.selections.filter((s) => s.ev_pct != null).sort((x, y) => y.ev_pct - x.ev_pct);
  const desc = a.model.describe;
  return `
  <button class="back" data-action="back">‹ Back</button>
  <div class="card">
    <div class="fx-meta"><span>${SPORT_ICON[fx.sport]} ${esc(a.competition.name)} · ${fmtTime(fx.kickoff)}</span>
      <span>${fx.is_demo ? chip("violet", "Demo") : ""} ${fx.status !== "scheduled" ? chip("amber", fx.status) : ""}</span></div>
    <h1 class="fx-teams" style="font-size:1.25rem">${esc(fx.home)}<span class="vs">vs</span>${esc(fx.away)}</h1>
    <div class="grid-2">
      <div class="tile" style="padding:9px 11px"><div class="k">Analysis confidence</div><div class="v">${a.confidence}<span class="small muted">/100</span></div>
        <div class="tiny muted">market ${parts.market}/40 · data ${parts.data}/35 · agreement ${parts.agreement}/15 · freshness ${parts.freshness}/10</div></div>
      <div class="tile" style="padding:9px 11px"><div class="k">Expected score (model)</div>
        <div class="v">${desc ? `${desc.expected_home} – ${desc.expected_away}` : "–"}</div>
        <div class="tiny muted">${desc ? `${desc.unit}, total ${desc.expected_total}` : "No market or model available"}${a.disagreement != null ? ` · agent disagreement ${a.disagreement.toFixed(2)}` : ""}</div></div>
    </div>
    ${warn}
  </div>

  <div class="section-title">Value overview</div>
  <div class="card sel-list">${sels.length ? sels.slice(0, 12).map((s) => selRow(s, started)).join("") : `<div class="muted small">No probability estimate is available yet (no complete bookmaker market and no model).</div>`}</div>

  <div class="section-title">Bookmaker comparison</div>
  ${a.markets.map((m, i) => marketTable(m, a.selections, i === 0)).join("") || `<div class="empty">No prices stored.</div>`}

  ${movementSection(a)}

  <div class="section-title">Analysis agents (10)</div>
  <div class="grid-agents">${a.agents.map(agentCard).join("")}</div>

  <div class="section-title">Team news</div>
  ${teamNewsSection(a, started)}
  <div class="footer-note">Probabilities are estimates, not guarantees. Bet responsibly.</div>`;
}

function selRow(s, started) {
  const flags = (s.pricing_flags || []).map((f) => (FLAG[f] ? chip(FLAG[f][0], FLAG[f][1]) : "")).join(" ");
  const notAllowed = !s.best_is_allowed ? `<span class="chip amber">not at your bookmakers</span>` : "";
  return `<div class="sel-row">
    <div><div class="name">${esc(s.label)} <span class="muted small">· ${esc(s.market_label)}</span></div></div>
    <div class="${evClass(s.ev_pct)}" style="font-weight:750;text-align:right">${fmtEv(s.ev_pct)}</div>
    <div class="facts">
      <span>Best <b class="odds">${fmtOdds(s.best.odds)}</b> ${esc(s.best.name)}</span>
      <span>Implied <b>${s.implied_pct.toFixed(1)}%</b></span>
      <span>Est. prob <b>${fmtPct(s.final_prob)}</b></span>
      <span>Fair <b class="odds">${fmtOdds(s.fair_odds)}</b></span>
      <span>vs market ${fmtEv(s.market_ev_pct)}</span>
      <span>Conf ${s.confidence}</span>
    </div>
    <div class="facts">${riskChip(s.risk_rating)} ${s.qualifies ? chip("green", "Qualifies") : ""} ${flags} ${notAllowed}</div>
    ${s.qualifies || s.ev_pct > 0 ? `<div class="why">${esc(s.explanation)}${!s.qualifies && s.rejections.length ? `<br><span class="faint">Not qualifying: ${esc(s.rejections.join("; "))}</span>` : ""}</div>` : ""}
    ${started || !(s.ev_pct > 0) ? "" : `<div style="grid-column:1/-1"><button class="btn ghost small" data-action="trackSel" data-fid="${esc(s.fixture_id)}" data-mid="${esc(s.market_id)}" data-sel="${esc(s.selection)}">Track this selection</button></div>`}
  </div>`;
}

function marketTable(m, selections, open) {
  const sels = m.selections;
  const cons = m.consensus.probs;
  const finalBySel = {};
  for (const s of selections) if (s.market_id === m.market_id) finalBySel[s.selection] = s;
  const rows = m.books.map((b) => {
    const cells = sels.map((s) => {
      const o = b.prices[s];
      if (o == null) return `<td class="faint">–</td>`;
      const q = (m.quotes[s] || []).find((v) => v.bookmaker === b.bookmaker);
      const isBest = m.best[s] && m.best[s].bookmaker === b.bookmaker;
      const flagged = q && (q.flags.includes("outlier") || q.flags.includes("possible_error"));
      const edge = q && q.edge_vs_fair_pct != null ? `<span class="sub">${fmtEv(q.edge_vs_fair_pct)}</span>` : "";
      return `<td class="odds ${isBest ? "best" : ""} ${flagged ? "flagged" : ""}">${fmtOdds(o)}${edge}</td>`;
    }).join("");
    const dotCls = b.source.includes("manual") ? "manual" : b.type;
    return `<tr><td><span class="bk-name"><span class="dot ${esc(dotCls)}"></span>${esc(b.name)}</span>
      <span class="sub tiny faint">${b.stale ? "stale · " : ""}${ago(b.age_min)}</span></td>${cells}
      <td class="faint">${b.margin_pct != null ? b.margin_pct.toFixed(1) + "%" : "–"}</td></tr>`;
  }).join("");
  const consRow = cons ? `<tr class="summary"><td>Fair (no margin)</td>${sels.map((s) => `<td class="odds">${fmtOdds(1 / cons[s])}<span class="sub">${fmtPct(cons[s])}</span></td>`).join("")}<td></td></tr>` : "";
  const finalRow = `<tr class="summary"><td>Final estimate</td>${sels.map((s) => {
    const f = finalBySel[s];
    return `<td class="odds">${f && f.fair_odds ? fmtOdds(f.fair_odds) : "–"}<span class="sub">${f ? fmtPct(f.final_prob) : ""}</span></td>`;
  }).join("")}<td></td></tr>`;
  const meta = [`${m.consensus.n_books} complete book(s)`, m.consensus.method ? `consensus: ${m.consensus.method}` : "no consensus",
    m.avg_margin_pct != null ? `avg margin ${m.avg_margin_pct}%` : null, m.arbitrage_pct ? `ARBITRAGE +${m.arbitrage_pct}%` : null].filter(Boolean).join(" · ");
  return `<details class="market" ${open ? "open" : ""}><summary><span>${esc(m.label)}</span><span class="small muted">${m.books.length} books</span></summary>
    <div class="small muted" style="margin-bottom:6px">${esc(meta)}</div>
    <div class="table-wrap"><table class="odds-table"><thead><tr><th>Bookmaker</th>${sels.map((s) => `<th>${esc(m.selection_labels[s])}</th>`).join("")}<th>Margin</th></tr></thead>
    <tbody>${rows}${consRow}${finalRow}</tbody></table></div>
    <div class="tiny faint" style="margin-top:6px">Green is the best price. ⚑ marks an outlier or possible error. Small numbers show the edge against the fair price. Dots: <span class="dot sharp"></span> sharp, <span class="dot exchange"></span> exchange, <span class="dot manual"></span> manual.</div>
  </details>`;
}

function movementSection(a) {
  const m = a.markets[0];
  if (!m) return "";
  const blocks = [];
  for (const s of m.selections) {
    const series = (m.quotes[s] || []).filter((q) => q.history && q.history.length > 1).slice(0, 5);
    if (!series.length) continue;
    const pts = series.flatMap((q) => q.history.map(([t, o]) => [new Date(t).getTime(), o]));
    const t0 = Math.min(...pts.map((p) => p[0])), t1 = Math.max(...pts.map((p) => p[0]));
    const o0 = Math.min(...pts.map((p) => p[1])), o1 = Math.max(...pts.map((p) => p[1]));
    const x = (t) => (t1 === t0 ? 50 : ((t - t0) / (t1 - t0)) * 100);
    const y = (o) => (o1 === o0 ? 17 : 32 - ((o - o0) / (o1 - o0)) * 30);
    const lines = series.map((q, i) => `<polyline style="stroke:${PALETTE[i % PALETTE.length]}" points="${q.history.map(([t, o]) => `${x(new Date(t).getTime()).toFixed(2)},${y(o).toFixed(2)}`).join(" ")}"/>`).join("");
    const legend = series.map((q, i) => `<span class="tiny" style="color:${PALETTE[i % PALETTE.length]}">${esc(q.name)} ${fmtOdds(q.opening_odds)}→${fmtOdds(q.odds)} (${fmtEv(q.move_pct)})</span>`).join(" ");
    blocks.push(`<div style="margin-top:8px"><div class="small" style="font-weight:600">${esc(m.selection_labels[s])}</div>
      <svg class="spark" viewBox="0 0 100 34" preserveAspectRatio="none" role="img" aria-label="Odds movement">${lines}</svg><div class="row wrap">${legend}</div></div>`);
  }
  if (!blocks.length) return "";
  return `<div class="section-title">Odds movement · ${esc(m.label)}</div><div class="card">${blocks.join("")}
    <div class="tiny faint" style="margin-top:8px">The price history builds up with every refresh. A falling line means the market is backing that selection.</div></div>`;
}

function agentCard(ag) {
  const [cls, label] = AGENT_STATUS[ag.status] || ["", ag.status];
  const showLean = ag.status === "ok" || ag.status === "partial";
  const leanBar = (v, l, r) => {
    const w = Math.abs(v) * 50;
    const left = v >= 0 ? 50 : 50 - w;
    return `<div class="lean"><span>${l}</span><div class="bar"><i style="left:${left}%;width:${w}%"></i></div><span>${r}</span></div>`;
  };
  const leans = showLean && ["probability", "form", "home_away", "injuries", "suspensions", "lineups", "statistics"].includes(ag.agent)
    ? leanBar(ag.lean_home, "Away", "Home") + (Math.abs(ag.lean_total) > 0.01 ? leanBar(ag.lean_total, "Less", "More") : "") : "";
  return `<div class="agent">
    <div class="top"><span class="nm">${esc(ag.name)}</span><span class="row">${chip(cls, label)}${ag.status !== "no_data" ? `<span class="tiny muted">conf ${Math.round(ag.confidence * 100)}%</span>` : ""}</span></div>
    <div class="summary">${esc(ag.summary)}</div>
    ${ag.findings.length ? `<ul>${ag.findings.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    ${ag.warnings.map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}
    ${leans}
    ${ag.data_sources.length ? `<div class="tiny faint" style="margin-top:6px">Sources: ${esc(ag.data_sources.join(", "))}</div>` : ""}
  </div>`;
}

function teamNewsSection(a, started) {
  const fx = a.fixture;
  const tn = a.team_news;
  const teamName = (side) => (side === "home" ? fx.home : fx.away);
  const manual = tn.absences.map((x) => `<div class="list-row"><div><b>${esc(x.player)}</b> <span class="muted small">${esc(teamName(x.team))} · ${esc(x.kind)} · ${esc(x.importance)}${x.reason ? " · " + esc(x.reason) : ""}</span></div>
      ${x.source === "manual" ? `<button class="btn danger small" data-action="delAbsence" data-fid="${esc(fx.id)}" data-aid="${esc(x.id)}">Remove</button>` : chip("", x.source)}</div>`).join("");
  const prov = tn.provider.absences.map((x) => `<div class="list-row"><div><b>${esc(x.player)}</b> <span class="muted small">${esc(teamName(x.team))} · ${esc(x.kind)}${x.reason ? " · " + esc(x.reason) : ""}</span></div>${chip("blue", x.source)}</div>`).join("");
  const lu = (side) => {
    const l = tn.lineups[side];
    return `<div class="field"><label class="f">${esc(teamName(side))} lineup</label>
      <div class="row">
        <select class="input" data-lineup="${side}" data-field="status">
          ${["unknown", "expected", "confirmed"].map((s) => `<option value="${s}" ${l.status === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>
        <select class="input" data-lineup="${side}" data-field="rotated">
          <option value="" ${l.rotated == null ? "selected" : ""}>Rotation: unknown</option>
          <option value="true" ${l.rotated === true ? "selected" : ""}>Rotating / resting players</option>
          <option value="false" ${l.rotated === false ? "selected" : ""}>Full-strength</option>
        </select>
      </div></div>`;
  };
  return `<div class="card">
    ${prov ? `<div class="small muted" style="margin-bottom:4px">From ${esc(tn.provider.absences_source)}</div>${prov}` : tn.provider.absences_source ? `<div class="small muted">${esc(tn.provider.absences_source)}: no absences reported.</div>` : `<div class="small muted">No automatic injury feed for this match. Add known absences below and the Injury and Suspension agents will use them.</div>`}
    ${manual ? `<div class="small muted" style="margin:10px 0 4px">Entered manually</div>${manual}` : ""}
    <form data-submit="addAbsence" data-fid="${esc(fx.id)}" style="margin-top:12px">
      <div class="grid-2">
        <div class="field"><label class="f">Team</label><select class="input" name="team"><option value="home">${esc(fx.home)}</option><option value="away">${esc(fx.away)}</option></select></div>
        <div class="field" style="margin-top:0"><label class="f">Player</label><input class="input" name="player" required maxlength="80" placeholder="Name"></div>
        <div class="field" style="margin-top:0"><label class="f">Type</label><select class="input" name="kind"><option value="injury">Injury</option><option value="suspension">Suspension</option><option value="doubtful">Doubtful</option><option value="other">Other</option></select></div>
        <div class="field" style="margin-top:0"><label class="f">Importance</label><select class="input" name="importance"><option value="key">Key player</option><option value="regular" selected>Regular starter</option><option value="rotation">Rotation</option><option value="unknown">Unknown</option></select></div>
      </div>
      <div class="field"><label class="f">Reason (optional)</label><input class="input" name="reason" maxlength="120" placeholder="e.g. hamstring, red card"></div>
      <button class="btn ghost small" type="submit" style="margin-top:10px">Add absence</button>
    </form>
    <div style="margin-top:16px" data-fid="${esc(fx.id)}" id="lineupBox">${lu("home")}${lu("away")}
      <button class="btn ghost small" data-action="saveLineups" data-fid="${esc(fx.id)}" style="margin-top:10px">Save lineup info</button></div>
    ${started ? `<form data-submit="setResult" data-fid="${esc(fx.id)}" style="margin-top:18px">
      <label class="f">Final score (regular time) to settle tracked bets</label>
      <div class="row"><input class="input" name="score_home" type="number" min="0" required placeholder="${esc(fx.home)}">
      <input class="input" name="score_away" type="number" min="0" required placeholder="${esc(fx.away)}">
      <button class="btn small" type="submit">Save</button></div></form>` : ""}
  </div>`;
}

// ------------------------------------------------------------ daily picks
async function viewPicks(token) {
  view.innerHTML = `<h1 class="page-title">Daily Picks</h1><div class="page-sub">Up to two coupons with different matches and at most three legs each, built only from qualifying value bets.</div>${skeleton(2)}`;
  try {
    const d = await api("/api/daily-picks");
    if (token !== state.routeToken) return;
    view.innerHTML = renderPicks(d);
  } catch (err) {
    view.innerHTML += errorBox(err);
  }
}

function renderPicks(d) {
  const c = d.criteria;
  const win = `${fmtTime(d.window.from)} → ${fmtTime(d.window.to)}`;
  let body = "";
  if (d.message) {
    const rej = Object.entries(d.diagnostics.rejections || {}).map(([k, v]) => `<li>${esc(k)}: ${v}</li>`).join("");
    body = `<div class="empty"><div class="big">${esc(d.message)}</div>
      <div class="small">Checked ${d.diagnostics.matches_in_window} match(es) in the window. ${d.diagnostics.qualifying_selections} qualifying selection(s).</div>
      ${d.diagnostics.note ? `<div class="small" style="margin-top:6px">${esc(d.diagnostics.note)}</div>` : ""}
      ${rej ? `<div class="small" style="margin-top:10px;text-align:left;display:inline-block">Positive-EV selections were rejected for:<ul style="margin:4px 0 0">${rej}</ul></div>` : ""}
    </div>`;
  } else {
    body = d.coupons.map(couponCard).join("");
    if (d.coupons.length === 1) body += `<div class="small muted" style="margin-top:10px">Only one coupon met the criteria today. BetFinder never forces a second one.</div>`;
  }
  const singles = d.singles.length ? `<div class="section-title">Other qualifying singles</div><div class="card sel-list">${d.singles.map((l) => `
    <div class="sel-row"><div><div class="name">${esc(l.selection_label)}</div><div class="small muted">${esc(l.match)} · ${esc(l.market_label)}</div></div>
    <div class="pos" style="font-weight:750">${fmtEv(l.ev_pct)}</div>
    <div class="facts"><span><b class="odds">${fmtOdds(l.odds)}</b> ${esc(l.bookmaker_name)}</span><span>p <b>${fmtPct(l.probability)}</b></span><span>fair <b>${fmtOdds(l.fair_odds)}</b></span>${riskChip(l.risk_rating)}</div></div>`).join("")}</div>` : "";
  return `<h1 class="page-title">Daily Picks</h1>
    <div class="page-sub">Window: ${win}. Coupons are rebuilt from current prices each time you open this page.</div>
    ${body}${singles}
    <div class="section-title">Criteria</div>
    <div class="card small"><dl class="kv">
      <dt>Min. EV per leg</dt><dd>${c.min_ev_pct}%</dd>
      <dt>Price vs market fair price</dt><dd>≥ ${c.min_market_edge_pct}%</dd>
      <dt>Min. confidence</dt><dd>${c.min_confidence}</dd>
      <dt>Max. risk</dt><dd>${esc(String(c.max_risk).replace("_", " "))}</dd>
      <dt>Min. leg probability</dt><dd>${fmtPct(c.min_leg_probability, 0)}</dd>
      <dt>Coupon odds</dt><dd>&gt; ${Number(c.coupon_min_odds).toFixed(2)}, ≤ ${c.coupon_max_legs} legs</dd>
      <dt>Bookmakers</dt><dd>${c.my_bookmakers.length ? esc(c.my_bookmakers.join(", ")) : "all"}</dd>
    </dl><div style="margin-top:10px"><a href="#sources">Change criteria</a></div></div>
    <div class="footer-note">High odds alone never qualify. Each leg needs positive expected value, enough confidence and acceptable risk.<br>Estimates can be wrong. Only stake what you can afford to lose.</div>`;
}

function couponCard(cp) {
  const legs = cp.legs.map((l) => `<div class="leg">
    <div class="lt"><div><div class="match">${esc(l.match)}</div><div class="small muted">${SPORT_ICON[l.sport]} ${esc(SPORT_LABEL[l.sport])} · ${esc(l.competition)} · ${fmtTime(l.kickoff)}</div></div>${riskChip(l.risk_rating)}</div>
    <div style="margin-top:6px"><b>${esc(l.selection_label)}</b> <span class="muted small">· ${esc(l.market_label)}</span></div>
    <div class="facts">
      <div class="fact"><div class="k">Odds</div><div class="v odds">${fmtOdds(l.odds)}</div></div>
      <div class="fact"><div class="k">Bookmaker</div><div class="v" style="font-size:0.8rem">${esc(l.bookmaker_name)}</div></div>
      <div class="fact"><div class="k">Est. prob</div><div class="v">${fmtPct(l.probability)}</div></div>
      <div class="fact"><div class="k">Fair odds</div><div class="v odds">${fmtOdds(l.fair_odds)}</div></div>
      <div class="fact"><div class="k">Value</div><div class="v pos">${fmtEv(l.ev_pct)}</div></div>
      <div class="fact"><div class="k">Confidence</div><div class="v">${l.confidence}</div></div>
    </div>
    <div class="small muted">${esc(l.explanation)}</div>
    <a class="small" href="#match/${encodeURIComponent(l.fixture_id)}">Full analysis ›</a>
  </div>`).join("");
  return `<div class="card coupon">
    <div class="coupon-head"><div><h2 style="font-size:1.1rem">${esc(cp.title)} ${cp.is_demo ? chip("violet", "Demo") : ""}</h2>
      <div class="small muted">${cp.legs.length} leg(s) · ${riskChip(cp.risk_rating)}</div></div>
      <div class="coupon-total"><div class="big odds">${fmtOdds(cp.combined_odds)}</div><div class="tiny muted">combined odds</div></div></div>
    <div class="grid-3" style="margin-bottom:6px">
      <div class="fact"><div class="k">Win prob</div><div class="v">${fmtPct(cp.combined_probability)}</div></div>
      <div class="fact"><div class="k">Fair odds</div><div class="v odds">${fmtOdds(cp.combined_fair_odds)}</div></div>
      <div class="fact"><div class="k">EV</div><div class="v pos">${fmtEv(cp.combined_ev_pct)}</div></div>
    </div>
    ${legs}
    <div class="spread" style="margin-top:10px"><span class="small muted">Kelly stake suggestion: ${cp.stake_pct}% of bankroll (${cp.stake_amount})</span>
      <button class="btn small" data-action="trackCoupon" data-id="${esc(cp.id)}">Track coupon</button></div>
  </div>`;
}

// --------------------------------------------------------------- add odds
async function ensureBookmakers() {
  if (!state.bookmakers) state.bookmakers = await api("/api/bookmakers");
  return state.bookmakers;
}
async function ensureMarkets(sport) {
  if (!state.markets[sport]) state.markets[sport] = await api(`/api/markets/${sport}`);
  return state.markets[sport];
}
async function ensureCompetitions(sport) {
  if (!state.competitions[sport]) state.competitions[sport] = (await api(`/api/competitions?sport=${sport}`)).items;
  return state.competitions[sport];
}

async function viewAdd(token) {
  view.innerHTML = `<h1 class="page-title">Add odds manually</h1><div class="page-sub">For bookmakers without a data feed (Danske Spil, Eurobet…) or to check a price you see.</div>${skeleton(2)}`;
  try {
    const st = state.add;
    const [books, markets, comps] = await Promise.all([ensureBookmakers(), ensureMarkets(st.sport), ensureCompetitions(st.sport)]);
    if (token !== state.routeToken) return;
    if (!markets.find((m) => m.market === st.market)) st.market = markets[0].market;
    view.innerHTML = renderAdd(books, markets, comps);
    updateMarginPreview();
  } catch (err) {
    view.innerHTML += errorBox(err);
  }
}

function renderAdd(books, markets, comps) {
  const st = state.add;
  const fx = st.fixture;
  const seg = ["football", "basketball", "handball"].map((s) => `<button type="button" data-action="addSport" data-v="${s}" class="${st.sport === s ? "on" : ""}">${SPORT_ICON[s]} ${SPORT_LABEL[s]}</button>`).join("");
  const prio = books.filter((b) => b.priority);
  const rest = books.filter((b) => !b.priority && !b.unregistered);
  const opt = (b) => `<option value="${esc(b.key)}" ${st.bookmaker === b.key ? "selected" : ""}>${esc(b.name)}${b.state === "manual_only" ? " (manual only)" : ""}</option>`;
  const market = markets.find((m) => m.market === st.market) || markets[0];
  const home = fx ? fx.home : st.home || "Home";
  const away = fx ? fx.away : st.away || "Away";
  let matchBlock;
  if (fx) {
    matchBlock = `<div class="headline good"><div><div class="sel">${esc(fx.home)} vs ${esc(fx.away)}</div><div class="small muted">${esc(fx.competition)} · ${fmtTime(fx.kickoff)}</div></div>
      <button type="button" class="btn ghost small" data-action="addClearFixture">Change</button></div>`;
  } else if (st.create) {
    matchBlock = `<div class="grid-2">
        <div class="field"><label class="f">Home team</label><input class="input" id="addHome" maxlength="80" value="${esc(st.home || "")}" required></div>
        <div class="field" style="margin-top:0"><label class="f">Away team</label><input class="input" id="addAway" maxlength="80" value="${esc(st.away || "")}" required></div>
      </div>
      <div class="field"><label class="f">Competition</label><select class="input" id="addComp">
        ${comps.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${esc(c.country)})</option>`).join("")}<option value="">Other / not listed</option></select></div>
      <div class="field"><label class="f">Kick-off (your local time)</label><input class="input" type="datetime-local" id="addKickoff" required></div>
      <button type="button" class="btn ghost small" style="margin-top:10px" data-action="addToggleCreate">‹ Search known matches instead</button>`;
  } else {
    matchBlock = `<div class="field"><label class="f" for="addSearch">Find the match (type team names)</label>
      <input class="input" id="addSearch" data-input="addSearch" autocomplete="off" placeholder="e.g. Arsenal Chelsea"></div>
      <div id="addResults"></div>
      <button type="button" class="btn ghost small" style="margin-top:10px" data-action="addToggleCreate">Match not listed? Create it</button>`;
  }
  const lineField = market.needs_line ? `<div class="field"><label class="f">${st.market === "spreads" ? "Home handicap (e.g. -1.5)" : "Line (e.g. 2.5)"}</label>
      <input class="input" id="addLine" type="number" step="0.25" value="${esc(st.line)}" data-input="addLine" required></div>` : "";
  const priceInputs = market.selections.map((s) => `<div class="field" style="margin-top:0"><label class="f">${esc(selLabel(st.market, s, home, away, st.line || "?"))}</label>
      <input class="input odds" type="number" inputmode="decimal" step="0.01" min="1.01" data-price="${s}" data-input="addPrice" placeholder="odds"></div>`).join("");
  return `<h1 class="page-title">Add odds manually</h1>
  <div class="page-sub">For bookmakers without a data feed (Danske Spil, Eurobet…) or to check a price you see. Pick a known match and you only need to type the odds.</div>
  <form class="card" data-submit="addOdds">
    <div class="seg" style="margin-bottom:12px">${seg}</div>
    ${matchBlock}
    <div class="grid-2" style="margin-top:12px">
      <div class="field"><label class="f">Bookmaker</label><select class="input" id="addBook" data-change="addBook">
        <optgroup label="Priority">${prio.map(opt).join("")}</optgroup><optgroup label="Other European bookmakers">${rest.map(opt).join("")}</optgroup></select></div>
      <div class="field" style="margin-top:0"><label class="f">Market</label><select class="input" id="addMarket" data-change="addMarket">
        ${markets.map((m) => `<option value="${m.market}" ${m.market === st.market ? "selected" : ""}>${esc(MARKET_NAME[m.market](st.sport))}</option>`).join("")}</select></div>
    </div>
    ${lineField}
    <div class="grid-3" style="margin-top:12px;grid-template-columns:repeat(${market.selections.length},1fr)">${priceInputs}</div>
    <div id="marginPreview" class="small muted" style="margin-top:8px">Enter all prices to see the bookmaker margin.</div>
    <button class="btn block" type="submit" style="margin-top:14px">Save odds and analyse</button>
  </form>
  <div id="addResult">${st.result ? renderAddResult(st.result) : ""}</div>`;
}

function updateMarginPreview() {
  const box = $("#marginPreview");
  if (!box) return;
  const vals = $$("[data-price]").map((i) => parseFloat(i.value));
  const valid = vals.filter((v) => v >= 1.01);
  if (!valid.length) { box.textContent = "Enter all prices to see the bookmaker margin."; return; }
  const implied = valid.map((v) => (100 / v).toFixed(1) + "%").join(" / ");
  if (valid.length === vals.length) {
    const margin = (valid.reduce((a, v) => a + 1 / v, 0) - 1) * 100;
    box.innerHTML = `Implied: ${esc(implied)} · Margin <b>${margin.toFixed(2)}%</b>${margin < 0 ? " (below 0: check the prices)" : ""}`;
  } else box.textContent = `Implied: ${implied}`;
}

function renderAddResult(r) {
  const fx = r.fixture;
  const rows = r.selections.filter((s) => s.best).map((s) => {
    const own = (r.market.quotes[s.selection] || []).find((q) => q.bookmaker === r.bookmaker);
    if (!own) return "";
    const p = s.final_prob;
    const ev = p != null ? (p * own.odds - 1) * 100 : null;
    const bestElsewhere = s.best_overall.bookmaker !== r.bookmaker ? `${fmtOdds(s.best_overall.odds)} at ${esc(s.best_overall.name)}` : "this is the best price";
    return `<div class="sel-row"><div class="name">${esc(s.label)}</div><div class="${evClass(ev)}" style="font-weight:750">${fmtEv(ev)}</div>
      <div class="facts"><span>Your price <b class="odds">${fmtOdds(own.odds)}</b></span><span>Implied <b>${own.implied_pct.toFixed(1)}%</b></span>
      <span>Est. prob <b>${fmtPct(p)}</b></span><span>Fair <b class="odds">${fmtOdds(s.fair_odds)}</b></span><span>Best: ${bestElsewhere}</span></div></div>`;
  }).join("");
  const single = r.market && r.market.consensus.n_books <= 1;
  return `<div class="card" style="margin-top:12px">
    <div class="spread"><div><b>Saved: ${esc(r.bookmaker_name)}</b> <span class="muted small">· ${esc(fx.home)} vs ${esc(fx.away)}</span></div>${chip("blue", r.matched_by)}</div>
    ${r.margin_pct != null ? `<div class="small muted" style="margin-top:4px">Bookmaker margin on this market: ${r.margin_pct}%</div>` : ""}
    ${single ? `<div class="banner warn" style="margin-top:8px"><div class="grow">This is the only bookmaker priced for this market. Without other prices or a statistical model, the fair probability is just this bookmaker's own price minus its margin, so value can't be detected. Add a second bookmaker (ideally a sharp one like Pinnacle) for a real comparison.</div></div>` : ""}
    <div class="sel-list" style="margin-top:6px">${rows || `<div class="small muted">No estimate available.</div>`}</div>
    <a class="btn ghost small" style="margin-top:10px" href="#match/${encodeURIComponent(fx.id)}">Open full analysis</a>
  </div>`;
}

let searchTimer = null;
function onAddSearch(input) {
  clearTimeout(searchTimer);
  const q = input.value.trim();
  const box = $("#addResults");
  if (q.length < 2) { box.innerHTML = ""; return; }
  searchTimer = setTimeout(async () => {
    try {
      const items = await api(`/api/fixtures/search?q=${encodeURIComponent(q)}&sport=${state.add.sport}`);
      state.add._search = items;
      box.innerHTML = items.length ? `<div class="results-list">${items.map((f, i) => `<button type="button" data-action="addPick" data-i="${i}">
          <b>${esc(f.home)} vs ${esc(f.away)}</b> ${f.is_demo ? chip("violet", "Demo") : ""}<div class="small muted">${esc(f.competition)} · ${fmtTime(f.kickoff)}</div></button>`).join("")}</div>`
        : `<div class="small muted" style="margin-top:6px">No known match found. Use "Match not listed? Create it".</div>`;
    } catch (err) { box.innerHTML = errorBox(err); }
  }, 250);
}

async function submitAddOdds(form) {
  const st = state.add;
  const prices = {};
  for (const i of $$("[data-price]", form)) if (i.value) prices[i.dataset.price] = parseFloat(i.value);
  if (!Object.keys(prices).length) return toast("Enter at least one price");
  const body = { bookmaker: $("#addBook").value, market: st.market, prices };
  if (st.market === "totals" || st.market === "spreads") {
    const l = $("#addLine").value;
    if (l === "") return toast("Enter the line");
    body.line = parseFloat(l);
  }
  if (st.fixture) body.fixture_id = st.fixture.id;
  else if (st.create) {
    body.sport = st.sport;
    body.home = $("#addHome").value.trim();
    body.away = $("#addAway").value.trim();
    const comp = $("#addComp").value;
    if (comp) body.competition_id = comp; else body.competition_name = "Other";
    const k = $("#addKickoff").value;
    if (k) body.kickoff = new Date(k).toISOString();
    st.home = body.home; st.away = body.away;
  } else return toast("Pick a match first, or create it");
  try {
    const r = await api("/api/manual/odds", { method: "POST", body });
    st.result = r;
    st.fixture = { id: r.fixture.id, home: r.fixture.home, away: r.fixture.away, competition: r.fixture.competition, kickoff: r.fixture.kickoff };
    st.create = false;
    state.bookmakers = null;
    toast("Odds saved");
    route();
  } catch (err) { toast(err.message, 5000); }
}

// --------------------------------------------------------------- tracker
async function viewTracker(token) {
  view.innerHTML = `<h1 class="page-title">Tracker</h1><div class="page-sub">Your tracked selections, settled automatically, with Closing Line Value.</div>${skeleton(2)}`;
  try {
    const d = await api("/api/tracker");
    if (token !== state.routeToken) return;
    view.innerHTML = renderTracker(d);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function renderTracker(d) {
  const s = d.summary;
  const tile = (k, v, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
  const items = d.items.map((it) => {
    const stCls = { won: "green", half_won: "green", lost: "red", half_lost: "orange", void: "", pending: "blue", partial: "amber" }[it.status] || "";
    const legs = it.legs.map((l) => `<div class="small" style="margin-top:4px"><b>${esc(l.selection_label)}</b> @ <span class="odds">${fmtOdds(l.odds)}</span> ${esc(l.bookmaker_name)}
      <span class="muted">· ${esc(l.match)} · ${fmtTime(l.kickoff)}</span>
      ${l.result ? chip(l.result.includes("win") ? "green" : l.result.includes("lose") ? "red" : "", l.result.replace("_", " ")) : ""}
      ${l.clv_pct != null ? `<span class="${evClass(l.clv_pct)}"> · CLV ${fmtEv(l.clv_pct)}</span>` : ""}
      ${l.ev_pct != null ? `<span class="muted"> · EV at pick ${fmtEv(l.ev_pct)}</span>` : ""}</div>`).join("");
    return `<div class="card">
      <div class="spread"><b>${esc(it.title)}</b>${chip(stCls, it.status.replace("_", " "))}</div>
      <div class="small muted">${fmtTime(it.created_at)} · stake ${it.stake} · odds ${fmtOdds(it.combined_odds)}${it.profit != null ? ` · profit <span class="${evClass(it.profit)}">${it.profit > 0 ? "+" : ""}${it.profit}</span>` : ""} ${it.is_demo ? chip("violet", "Demo") : ""}</div>
      ${legs}
      <div class="row wrap" style="margin-top:10px">
        ${it.status === "pending" ? `<button class="btn ghost small" data-action="settle" data-id="${esc(it.id)}" data-v="won">Mark won</button>
        <button class="btn ghost small" data-action="settle" data-id="${esc(it.id)}" data-v="lost">Mark lost</button>
        <button class="btn ghost small" data-action="settle" data-id="${esc(it.id)}" data-v="void">Void</button>` : it.manual_result ? `<button class="btn ghost small" data-action="settle" data-id="${esc(it.id)}" data-v="pending">Reopen</button>` : ""}
        <button class="btn danger small" data-action="delTracked" data-id="${esc(it.id)}">Delete</button>
      </div></div>`;
  }).join("");
  return `<h1 class="page-title">Tracker</h1>
    <div class="page-sub">Selections you track are settled automatically from results. CLV compares your odds with the margin-free closing price, which is the best long-run test of whether the value detection works. Demo items are excluded from the totals.</div>
    <div class="tiles">
      ${tile("Tracked", s.tracked)}${tile("Settled", s.settled)}
      ${tile("Hit rate", s.hit_rate_pct != null ? s.hit_rate_pct + "%" : "–")}${tile("ROI", s.roi_pct != null ? fmtEv(s.roi_pct) : "–", evClass(s.roi_pct))}
      ${tile("Avg EV at pick", fmtEv(s.avg_ev_pct), evClass(s.avg_ev_pct))}${tile("Avg CLV", fmtEv(s.avg_clv_pct), evClass(s.avg_clv_pct))}
      ${tile("Beat closing line", s.clv_beat_rate_pct != null ? s.clv_beat_rate_pct + "%" : "–")}${tile("Profit (units)", s.profit, evClass(s.profit))}
    </div>
    <div class="section-title">History</div>
    ${items || `<div class="empty"><div class="big">Nothing tracked yet</div>Use "Track coupon" in Daily Picks or "Track this selection" in a match analysis.</div>`}`;
}

// --------------------------------------------------------------- sources
const SETTING_FIELDS = [
  ["min_ev_pct", "Minimum EV per selection (%)", 0.5],
  ["min_market_edge_pct", "Price vs margin-free market price (%)", 0.5, "0 means the price must at least match the fair market price, so the agents can't create value on their own. -100 disables this."],
  ["min_confidence", "Minimum confidence (0-100)", 1],
  ["min_leg_probability", "Minimum probability per coupon leg (0-1)", 0.01],
  ["max_leg_odds", "Maximum odds per coupon leg", 0.1],
  ["coupon_min_odds", "Coupon combined odds must be above", 0.1],
  ["coupon_max_legs", "Maximum legs per coupon (1-3)", 1],
  ["suspicious_ev_pct", "Flag as possible pricing error above EV (%)", 1],
  ["stale_minutes", "Treat prices as stale after (minutes)", 10],
  ["model_weight", "Statistical model weight (0-0.6)", 0.05, "How strongly the independent model can move the market probability."],
  ["max_logit_shift", "Maximum adjustment by agents (logit units)", 0.05],
  ["bankroll", "Bankroll (for stake suggestions)", 10],
  ["kelly_fraction", "Kelly fraction (0.25 = quarter Kelly)", 0.05],
  ["lookahead_hours", "Scanner window (hours ahead)", 6],
  ["day_end_hour", "Daily Picks day ends at (hour, local)", 1],
  ["auto_refresh_minutes", "Auto refresh every (minutes, 0 = off)", 5],
  ["retention_days", "Keep finished matches for (days)", 1],
];

async function viewSources(token) {
  view.innerHTML = `<h1 class="page-title">Sources &amp; settings</h1>${skeleton(3)}`;
  try {
    const [status, settings, books, comps] = await Promise.all([api("/api/status"), api("/api/settings"), api("/api/bookmakers"), api("/api/competitions")]);
    if (token !== state.routeToken) return;
    state.status = status; renderTop();
    state.bookmakers = books;
    view.innerHTML = renderSources(status, settings, books, comps);
  } catch (err) { view.innerHTML += errorBox(err); }
}

function providerCard(p, settings) {
  const keyName = p.name === "the_odds_api" ? "odds_api_key" : "api_sports_key";
  const sk = settings[keyName];
  let stateChip = chip("amber", "Not configured");
  if (p.configured && p.last_error && (!p.last_ok || p.last_error_at > p.last_ok)) stateChip = chip("red", "Error");
  else if (p.configured && p.last_ok) stateChip = chip("green", "Connected");
  else if (p.configured) stateChip = chip("blue", "Key saved, refresh to connect");
  const remaining = p.remaining == null ? "–" : typeof p.remaining === "object" ? Object.entries(p.remaining).map(([k, v]) => `${k} ${v}`).join(", ") : p.remaining;
  const about = p.name === "the_odds_api"
    ? `Live pre-match odds from NordicBet, Betsson, Unibet, Pinnacle, Betfair, William Hill and more. Free plan: 500 credits/month. <a href="https://the-odds-api.com" target="_blank" rel="noopener">the-odds-api.com</a>`
    : `Fixtures, odds, team statistics, recent form, head-to-head, injuries/suspensions and lineups for football, basketball and handball (incl. EHF competitions). The free plan usually covers past seasons only, so a paid plan is needed for current matches. <a href="https://api-sports.io" target="_blank" rel="noopener">api-sports.io</a>`;
  return `<div class="card">
    <div class="spread"><b>${esc(p.label)}</b>${stateChip}</div>
    <div class="small muted" style="margin-top:4px">${about}</div>
    <dl class="kv" style="margin-top:10px">
      <dt>Last success</dt><dd>${agoIso(p.last_ok)}</dd>
      <dt>Requests today</dt><dd>${p.requests_today}</dd>
      <dt>Quota remaining</dt><dd>${esc(remaining)}</dd>
      ${p.last_error ? `<dt>Last error</dt><dd style="color:var(--red);white-space:normal">${esc(p.last_error)}</dd>` : ""}
    </dl>
    ${sk.source === "environment" ? `<div class="small muted" style="margin-top:10px">API key set via environment variable (${sk.hint}).</div>` : `
    <form data-submit="saveKey" data-key="${keyName}" class="row" style="margin-top:10px">
      <input class="input" type="password" name="key" autocomplete="off" placeholder="${sk.configured ? "Key saved " + esc(sk.hint) + " · paste to replace" : "Paste API key"}">
      <button class="btn small" type="submit">Save</button>
      ${sk.configured ? `<button class="btn danger small" type="button" data-action="clearKey" data-key="${keyName}">Remove</button>` : ""}
    </form>`}
  </div>`;
}

function renderSources(status, settings, books, comps) {
  const STATE = {
    connected: ["green", "Automated"], configured_no_data: ["blue", "Feed configured, no prices yet"],
    provider_not_configured: ["amber", "Needs API key"], manual_only: ["", "Manual entry only"],
  };
  const bookRows = books.map((b) => `<div class="list-row">
      <div style="min-width:0"><div><b>${esc(b.name)}</b> ${b.priority ? chip("blue", "priority") : ""} ${b.type !== "soft" ? chip("violet", b.type) : ""}</div>
        <div class="tiny muted">${b.sources.length ? "Feeds: " + esc(b.sources.join(", ")) : "No feed"}${b.last_automated_quote ? " · last price " + agoIso(b.last_automated_quote) : ""}${b.manual_quotes ? ` · ${b.manual_quotes} manual price(s)` : ""}</div>
        ${b.limitation ? `<div class="tiny" style="color:var(--amber);margin-top:2px">${esc(b.limitation)}</div>` : ""}</div>
      <div style="text-align:right;flex-shrink:0">${chip(STATE[b.state][0], STATE[b.state][1])}
        <label class="switch tiny" style="margin-top:6px"><span>Mine</span><input type="checkbox" data-change="myBook" value="${esc(b.key)}" ${b.mine ? "checked" : ""}></label></div>
    </div>`).join("");
  const bySport = { football: [], basketball: [], handball: [] };
  for (const c of comps.items) bySport[c.sport].push(c);
  const compBlocks = Object.entries(bySport).map(([sport, list]) => `<div class="card"><b>${SPORT_ICON[sport]} ${SPORT_LABEL[sport]}</b>
    ${list.map((c) => `<div class="list-row"><div><div>${esc(c.name)} <span class="muted small">${esc(c.country)}</span></div>
      <div class="tiny muted">${[c.odds_source ? "odds: The Odds API" : null, c.stats_source ? "fixtures/stats/odds: API-Sports" : null].filter(Boolean).join(" · ") || "manual only"}</div></div>
      <label class="switch"><input type="checkbox" data-change="toggleComp" value="${esc(c.id)}" ${c.enabled ? "checked" : ""}></label></div>`).join("")}</div>`).join("");
  const fields = SETTING_FIELDS.map(([k, label, step, help]) => `<div class="field"><label class="f" for="set_${k}">${esc(label)}</label>
      <input class="input" id="set_${k}" name="${k}" type="number" step="${step}" value="${esc(settings[k])}">${help ? `<div class="tiny faint" style="margin-top:3px">${esc(help)}</div>` : ""}</div>`).join("");
  const regions = ["eu", "uk", "se"].map((r) => `<label class="switch small"><input type="checkbox" name="odds_api_regions" value="${r}" ${settings.odds_api_regions.includes(r) ? "checked" : ""}><span>${r.toUpperCase()}</span></label>`).join(" ");
  const mkts = ["h2h", "totals", "spreads"].map((m) => `<label class="switch small"><input type="checkbox" name="odds_api_markets" value="${m}" ${settings.odds_api_markets.includes(m) ? "checked" : ""}><span>${m}</span></label>`).join(" ");
  return `<h1 class="page-title">Sources &amp; settings</h1>
  <div class="page-sub">BetFinder only shows prices it actually received. Bookmakers without an accessible feed are labelled "Manual entry only".</div>

  <div class="section-title">Data providers</div>
  ${status.providers.map((p) => providerCard(p, settings)).join("")}
  <div class="card">
    <div class="spread"><b>Manual entry</b>${chip("green", "Always available")}</div>
    <div class="small muted" style="margin-top:4px">Enter prices from any bookmaker under <a href="#add">Add odds</a>. They are compared with every other price for the same match and market.</div>
  </div>
  <div class="card">
    <div class="spread"><div><b>Demo mode</b><div class="small muted">Load fictional matches and bookmakers to explore the features. Never mixed into tracker statistics.</div></div>
      <label class="switch"><input type="checkbox" data-change="demoMode" ${settings.demo_mode ? "checked" : ""}></label></div>
  </div>

  <div class="section-title">The Odds API options</div>
  <form class="card" data-submit="saveOddsApi">
    <div class="small muted">Each refresh costs (enabled competitions with a feed) × regions × markets credits. Currently about <b>${comps.odds_api_cost_per_refresh}</b> credits.</div>
    <div class="row wrap" style="margin-top:10px"><span class="small" style="width:70px">Regions</span>${regions}</div>
    <div class="row wrap" style="margin-top:8px"><span class="small" style="width:70px">Markets</span>${mkts}</div>
    <button class="btn ghost small" type="submit" style="margin-top:10px">Save</button>
  </form>

  <div class="section-title">Bookmakers</div>
  <div class="card">
    <div class="small muted" style="margin-bottom:6px">Tick "Mine" for the bookmakers you can bet with. Daily Picks then only use their prices. Tick none to use all of them.</div>
    ${bookRows}
  </div>

  <div class="section-title">Competitions</div>
  ${compBlocks}
  <div class="small muted" style="margin-top:8px">Excluded at your request: ${esc(comps.excluded_by_request.join(", "))}. To add a competition, add an entry to <code>data/registry/competitions.json</code>.</div>

  <div class="section-title">Analysis &amp; Daily Picks settings</div>
  <form class="card" data-submit="saveSettings">
    <div class="field"><label class="f" for="set_max_risk">Maximum risk for qualifying bets</label><select class="input" id="set_max_risk" name="max_risk">
      ${["low", "medium", "high", "very_high"].map((r) => `<option value="${r}" ${settings.max_risk === r ? "selected" : ""}>${r.replace("_", " ")}</option>`).join("")}</select></div>
    <div class="field"><label class="f" for="set_devig">Margin removal method</label><select class="input" id="set_devig" name="devig_method">
      ${[["shin", "Shin (recommended)"], ["power", "Power"], ["multiplicative", "Proportional"], ["additive", "Additive"]].map(([v, l]) => `<option value="${v}" ${settings.devig_method === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
    <div class="field"><label class="f" for="set_tz">Timezone</label><input class="input" id="set_tz" name="timezone" value="${esc(settings.timezone)}"></div>
    ${fields}
    <button class="btn block" type="submit" style="margin-top:14px">Save settings</button>
  </form>
  <div class="footer-note">BetFinder ${esc(status.version)} · data stored as JSON files in data/runtime<br>Gambling involves risk. Only stake what you can afford to lose.</div>`;
}

// ---------------------------------------------------------------- actions
const actions = {
  openMatch: (el) => { location.hash = `#match/${encodeURIComponent(el.dataset.id)}`; },
  back: () => { if (history.length > 1) history.back(); else location.hash = "#scanner"; },
  scanSport: (el) => { state.scanner.sport = el.dataset.v; route(); },
  scanValue: () => { state.scanner.value_only = !state.scanner.value_only; route(); },
  addSport: (el) => { Object.assign(state.add, { sport: el.dataset.v, fixture: null, result: null, market: "h2h", line: "" }); route(); },
  addPick: (el) => { state.add.fixture = state.add._search[Number(el.dataset.i)]; state.add.result = null; route(); },
  addClearFixture: () => { state.add.fixture = null; state.add.result = null; route(); },
  addToggleCreate: () => { state.add.create = !state.add.create; state.add.fixture = null; route(); },
  trackSel: async (el) => {
    const stake = prompt("Stake to record (units):", "1");
    if (stake === null) return;
    try {
      await api("/api/tracker/selection", { method: "POST", body: { fixture_id: el.dataset.fid, market_id: el.dataset.mid, selection: el.dataset.sel, stake: parseFloat(stake) || 1 } });
      toast("Tracked: see the Tracker tab");
    } catch (err) { toast(err.message, 5000); }
  },
  trackCoupon: async (el) => {
    const stake = prompt("Stake to record (units):", "1");
    if (stake === null) return;
    try {
      await api("/api/tracker/coupon", { method: "POST", body: { coupon_id: el.dataset.id, stake: parseFloat(stake) || 1 } });
      toast("Coupon tracked");
    } catch (err) { toast(err.message, 5000); }
  },
  settle: async (el) => {
    try { await api(`/api/tracker/${el.dataset.id}/settle`, { method: "POST", body: { status: el.dataset.v } }); route(); } catch (err) { toast(err.message); }
  },
  delTracked: async (el) => {
    if (!confirm("Delete this tracked item?")) return;
    try { await api(`/api/tracker/${el.dataset.id}`, { method: "DELETE" }); route(); } catch (err) { toast(err.message); }
  },
  delAbsence: async (el) => {
    try { await api(`/api/fixtures/${encodeURIComponent(el.dataset.fid)}/absences/${el.dataset.aid}`, { method: "DELETE" }); route(); } catch (err) { toast(err.message); }
  },
  saveLineups: async (el) => {
    const fid = el.dataset.fid;
    try {
      for (const side of ["home", "away"]) {
        const status = $(`[data-lineup="${side}"][data-field="status"]`).value;
        const rot = $(`[data-lineup="${side}"][data-field="rotated"]`).value;
        await api(`/api/fixtures/${encodeURIComponent(fid)}/lineups/${side}`, { method: "PUT", body: { status, rotated: rot === "" ? null : rot === "true" } });
      }
      toast("Lineup info saved");
      route();
    } catch (err) { toast(err.message); }
  },
  clearKey: async (el) => {
    if (!confirm("Remove this API key?")) return;
    try { await api("/api/settings", { method: "PUT", body: { [el.dataset.key]: "" } }); toast("Key removed"); await loadStatus(); route(); } catch (err) { toast(err.message); }
  },
};

const changes = {
  scanSort: (el) => { state.scanner.sort = el.value; route(); },
  addBook: (el) => { state.add.bookmaker = el.value; },
  addMarket: (el) => { state.add.market = el.value; state.add.line = ""; route(); },
  myBook: async () => {
    const mine = $$('[data-change="myBook"]').filter((i) => i.checked).map((i) => i.value);
    try { await api("/api/settings", { method: "PUT", body: { my_bookmakers: mine } }); toast(mine.length ? `Using ${mine.length} bookmaker(s) for picks` : "Using all bookmakers"); } catch (err) { toast(err.message); }
  },
  toggleComp: async (el) => {
    try { await api(`/api/competitions/${encodeURIComponent(el.value)}`, { method: "PUT", body: { enabled: el.checked } }); state.competitions = {}; toast(el.checked ? "Competition enabled" : "Competition disabled"); }
    catch (err) { toast(err.message); el.checked = !el.checked; }
  },
  demoMode: async (el) => {
    try {
      await api("/api/settings", { method: "PUT", body: { demo_mode: el.checked } });
      toast(el.checked ? "Demo data loaded" : "Demo data removed");
      state.add.fixture = null; state.add.result = null;
      await loadStatus();
      route();
    } catch (err) { toast(err.message); el.checked = !el.checked; }
  },
};

const inputs = {
  addSearch: onAddSearch,
  addPrice: updateMarginPreview,
  addLine: (el) => {
    state.add.line = el.value;
    const fx = state.add.fixture;
    for (const i of $$("[data-price]")) {
      const lbl = i.closest(".field").querySelector("label");
      lbl.textContent = selLabel(state.add.market, i.dataset.price, fx ? fx.home : state.add.home || "Home", fx ? fx.away : state.add.away || "Away", el.value || "?");
    }
  },
};

const submits = {
  addOdds: submitAddOdds,
  addAbsence: async (form) => {
    const body = Object.fromEntries(new FormData(form));
    try { await api(`/api/fixtures/${encodeURIComponent(form.dataset.fid)}/absences`, { method: "POST", body }); toast("Absence added: analysis updated"); route(); }
    catch (err) { toast(err.message); }
  },
  setResult: async (form) => {
    const fd = new FormData(form);
    try {
      await api(`/api/fixtures/${encodeURIComponent(form.dataset.fid)}/result`, { method: "POST", body: { score_home: Number(fd.get("score_home")), score_away: Number(fd.get("score_away")) } });
      toast("Result saved, tracked bets settled"); route();
    } catch (err) { toast(err.message); }
  },
  saveKey: async (form) => {
    const key = new FormData(form).get("key").trim();
    if (!key) return toast("Paste a key first");
    try { await api("/api/settings", { method: "PUT", body: { [form.dataset.key]: key } }); toast("Key saved. Press refresh to fetch data."); await loadStatus(); route(); }
    catch (err) { toast(err.message); }
  },
  saveOddsApi: async (form) => {
    const fd = new FormData(form);
    try { await api("/api/settings", { method: "PUT", body: { odds_api_regions: fd.getAll("odds_api_regions"), odds_api_markets: fd.getAll("odds_api_markets") } }); toast("Saved"); route(); }
    catch (err) { toast(err.message, 5000); }
  },
  saveSettings: async (form) => {
    const fd = new FormData(form);
    const body = {};
    for (const [k, v] of fd.entries()) body[k] = ["max_risk", "devig_method", "timezone"].includes(k) ? v : Number(v);
    try { await api("/api/settings", { method: "PUT", body }); toast("Settings saved"); route(); } catch (err) { toast(err.message, 5000); }
  },
};

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const fn = actions[el.dataset.action];
  if (fn) { e.preventDefault(); fn(el, e); }
});
document.addEventListener("change", (e) => {
  const el = e.target.closest("[data-change]");
  if (el && changes[el.dataset.change]) changes[el.dataset.change](el, e);
});
document.addEventListener("input", (e) => {
  const el = e.target.closest("[data-input]");
  if (el && inputs[el.dataset.input]) inputs[el.dataset.input](el, e);
});
document.addEventListener("submit", (e) => {
  const form = e.target.closest("[data-submit]");
  if (form && submits[form.dataset.submit]) { e.preventDefault(); submits[form.dataset.submit](form, e); }
});
$("#refreshBtn").addEventListener("click", doRefresh);
$("#sourcePill").addEventListener("click", () => { location.hash = "#sources"; });

// ----------------------------------------------------------------- router
function route() {
  const hash = location.hash.replace(/^#/, "") || "scanner";
  const [tab, ...rest] = hash.split("/");
  const token = ++state.routeToken;
  $$(".tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === (tab === "match" ? "scanner" : tab)));
  const views = { scanner: viewScanner, picks: viewPicks, add: viewAdd, tracker: viewTracker, sources: viewSources };
  if (tab === "match" && rest.length) viewMatch(token, decodeURIComponent(rest.join("/")));
  else (views[tab] || viewScanner)(token);
  if (!rest.length) window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
loadStatus().then(route);
