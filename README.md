# BetFinder

BetFinder scans bookmaker odds and looks for:

- **pricing errors**: a price far out of line with the rest of the market
- **price differences between bookmakers** (including arbitrage)
- **value bets**: the estimated true probability is higher than the price implies

It covers **team sports** (football, basketball, handball, ice hockey, NFL, baseball), **combat sports** (UFC/MMA, boxing) and **racket sports** (tennis). Ten analysis agents assess each match independently, and **Daily Picks** builds up to three value coupons per day. The web app is mobile-first, works in **English and Danish**, can be installed on a phone's home screen, and has user accounts with roles.

> **Honesty rule:** BetFinder never invents odds, injuries, lineups or results. Every price and data point is labelled with its source and age (*Live API*, *Scraped*, *Manual*, *Historical* or *Missing*). There is no demo or sample data.

**Setting it up (new computer, fork, Vercel or your own server): see [SETUP.md](SETUP.md).**

---

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>, sign in with **admin / admin** and change the password straight away. Then add a free API key under *More → Sources & settings* and press ⟳. [SETUP.md](SETUP.md) has every step in detail.

## Features

- **Dashboard**: value bets, pricing errors, dropping odds, today's coupons and tracker performance at a glance.
- **Scanner**: every upcoming match with odds, grouped by category and sport, with filters for value, deviation level (5%+ / 10%+) and sorting.
- **Match analysis**: bookmaker comparison table, margin-free fair odds, price movement charts, all ten agents' findings and team news.
- **Daily Picks**: up to 3 coupons (adjustable 1–5) with different matches and at most 3 legs each. *Auto* mode mixes safe, value and bigger-odds coupons; *fixed* mode requires combined odds above a minimum (3.00 by default). A *Coming days* tab shows early-price picks for the days after today. Every leg shows the lowest odds at which it is still worth taking. Coupons are never forced: with nothing good you see "No qualifying value bets found today.", and when the stored odds are simply too old to judge, the page says that instead.
- **Add odds**: find a known match by typing team names (or create one), enter several bookmakers at once, or paste odds straight from a bookmaker's page.
- **Bet builder**: combine selections. Same-match legs use the sport model's joint probability (e.g. "home win + over 2.5" is positively correlated).
- **Arbitrage finder** with a stake calculator.
- **Agent analysis**: what each agent found across today's matches.
- **Tracker**: every account has its own tracker, with automatic settlement, ROI and **Closing Line Value (CLV)**.
- **Data usage**: whether odds come from API tokens or scraping, tokens used and left per provider, the last refresh's fallback chain, blocked sites, and a credit planner for The Odds API.
- **Phone alerts** via Telegram or ntfy (both free) for pricing errors, value bets, arbitrage and new coupons.
- **Accounts**: sign-in, "request an account", "forgot password", roles (Super admin, Editor, User), an activity log, and optional email of temporary passwords.
- **Leagues**: 107 competitions in the registry (26 switched on by default), from the Danish Superliga and the Big 5 to Herreligaen, EHF, NBA, NHL, SHL, UFC and ATP/WTA. Switch them on or off in the app.
- **Bookmakers**: 51 in the registry. Tick "Mine" for the bookmakers where you have an account, and picks only use their prices.

### Roles

| Role | Can |
|---|---|
| **User** | Dashboard, scanner, Daily Picks, match analysis and tools; track bets in their own tracker |
| **Editor** | + refresh data, enter odds and team news, see data usage |
| **Super admin** | + sources and API keys, settings, leagues, user accounts and requests |

## Where the odds come from: the fallback chain

| # | Source | Cost | What it gives |
|---|---|---|---|
| 1 | **The Odds API** | **Free 500 credits/month**; 20K $30, 100K $59/month | NordicBet, Betsson, Unibet, Pinnacle, Betfair, William Hill, 1xBet, LeoVegas, Svenska Spel and more; UFC, boxing, tennis, NHL/SHL, NFL, MLB |
| 2 | **API-Sports** | Free plan covers past seasons only; Pro about $10–19/month per sport | Fixtures, statistics, **xG**, form, head-to-head, injuries, suspensions and lineups (football); basketball, handball and hockey data |
| 3 | **odds-api.io** | About £49/month (2 bookmakers of your choice) | The only feed found with **Danske Spil** and **Eurobet** |
| 4 | **OddsPapi** | **Free 250 requests/month** | 300+ bookmakers incl. bet365 and Pinnacle. Used when #1 has no key or no credits left, and by the "Fetch more bookmakers" button |
| 5 | **Polite scraper** | Free | Public JSON odds of the Kambi platform (e.g. Unibet). Runs only when #1–#4 deliver nothing (or always, if you choose) |
| – | **Manual entry** | Free | Any bookmaker, e.g. Danske Spil |

Sources 1–3 run in parallel on every refresh; 4 and 5 are fallbacks. Every refresh records which method actually delivered odds.

**About scraping:** the scraper respects robots.txt, waits between requests, uses an honest user agent and never tries to get around blocking (no proxies, headless browsers or CAPTCHA solving). A site that refuses access is paused for 24 hours and shown as *blocked*. Scraping can conflict with a bookmaker's terms of use; switch it off under *Sources* if you prefer.

### Recommended setup and cost

| Goal | Setup | Monthly cost |
|---|---|---|
| Try it | The Odds API free + OddsPapi free + scraping fallback + manual Danske Spil | **$0** |
| Serious daily use | The Odds API 20K (refresh hourly), manual Danske Spil | **≈ $30** |
| Danske Spil and Eurobet automatic | + odds-api.io Solo | **≈ $95** |
| Full statistics, xG, injuries and lineups | + API-Football Pro | **≈ $115** |

## How the analysis works

1. **Odds Scanner Agent** removes each bookmaker's margin (Shin by default; power, proportional and additive are available) and builds a **fair consensus price**, with sharp books (Pinnacle, exchanges) weighted highest. It also tracks movements, stale prices and arbitrage.
2. **Value Detection Agent** classifies prices against fair odds with a traffic light (**normal < 5%**, **warning 5–10%**, **alert ≥ 10%**) and flags outliers and *possible pricing errors*.
3. **Probability Agent** builds an independent **sport-specific model**: Poisson / Dixon-Coles with **xG blending** for football, Poisson with overtime for ice hockey, normal models for basketball, NFL, baseball and handball (where draws are a real outcome), and a market-based strength model for UFC, boxing and tennis.
4. **Form Agent**: recent form compared with season level.
5. **Home/Away Agent**: home/away effects beyond a normal home advantage (not used for individual sports).
6. **Injury Agent** and 7. **Suspension Agent**: absences weighted by player importance.
8. **Lineup Agent**: confirmed or expected lineups and rotation.
9. **Statistical Agent**: stats-, **xG**- and head-to-head-implied margin and total compared with the market.
10. **Risk Agent**: a risk rating per selection.

The bookmaker consensus is the anchor, and the agents can only move it within limits. A selection **qualifies** only when all of these hold (every threshold can be changed under *Sources & settings → Analysis & Daily Picks settings*):

- EV ≥ 1.5% where a sharp bookmaker (Pinnacle) or a liquid exchange sets the fair price, EV ≥ 3% in every other market
- the price is at or above the fair market price
- the price is at most 4% below the fair price of the *other* ordinary bookmakers. This is a safety net: when value rests on the sharp reference alone and the gap is that large, the reference is usually the one that is wrong
- confidence ≥ 55
- risk ≤ medium
- the price is at one of your bookmakers (if you ticked any)

Three rules keep the fair price itself honest:

- **A reference must have a real market.** An exchange only counts as sharp when its own overround is at most 5%; between 5% and 12% it is treated like any other bookmaker, and above that (or when it is far away from every other bookmaker) it is ignored. An exchange without liquidity shows prices nobody can bet.
- **Exchange commission is taken off first.** Betfair 5%, Matchbook and Smarkets 2% of winnings by default (`commission_pct` in `data/registry/bookmakers.json`).
- **Ice hockey: regulation time and overtime are different bets.** Bookmakers that price the 3-way regulation result also price totals and handicaps for regulation time. Those are never compared with overtime-inclusive prices.

Where these numbers come from: a backtest of this exact recipe on 20,750 football matches (17 leagues, 2022–2026), run with `python tools/backtest_value_rule.py`.

- **1.5%.** In the top five leagues, edges of 1.5–3% beat the closing line as well as larger ones (about 80% of the time, closing-line value around +3.6%), and there are three times as many of them as edges above 3%.
- **Exchange limits.** Of 8,407 Betfair Exchange quotes, those with an overround up to 5% were exactly as accurate as Pinnacle. Above 12% (6% of all quotes) they were useless, on average 19 percentage points away from Pinnacle. Counting those as "sharp" was the main source of false value.
- **4% safety net.** When all ordinary bookmakers lag a sharp move, the price sits 2–4% below their fair price and the value is real (closing-line value about +3%). Beyond 4% it was close to zero (+0.7%, and only a handful of cases).

The backtest covers football 1X2 with UK-facing bookmakers only, and realised profit was inconclusive at that sample size. Treat the thresholds as a starting point and watch your own CLV in the tracker.

Formulas: implied probability = 1 / odds · EV % = (p × odds − 1) × 100 · fair odds = 1 / p · exchange odds after commission = 1 + (odds − 1) × (1 − commission) · stake = quarter-Kelly, shown in your currency (DKK by default).

## Project structure

```
main.py                    entry point (uvicorn main:app)
app/
  main.py                  FastAPI app, access rules, background auto-refresh
  config.py                version, paths, all user settings with defaults and validation
  i18n.py                  server-side texts (English/Danish)
  api/                     REST endpoints (routes.py) and accounts (auth_routes.py)
  agents/                  the ten analysis agents
  core/                    data models, markets, odds maths, team-name matching, sport catalogue
  providers/               The Odds API, API-Sports, odds-api.io, OddsPapi, scraper, HTTP cache
  services/                analysis engine, daily picks, tracker, refresh, accounts, alerts, email
  sports/                  probability models per sport + bet settlement
  storage/json_store.py    JSON files locally, Redis on Vercel (same interface)
  static/, templates/      the web app (vanilla JS, no build step)
data/registry/             bookmakers.json and competitions.json (edit these to add more)
data/runtime/              your local data: settings, odds history, accounts, tracker (git-ignored)
tests/                     pytest suite
tools/                     backtest of the value rule on public football results (not part of the app)
deploy/Caddyfile, Dockerfile, docker-compose.yml   self-hosting with automatic HTTPS
vercel.json, pyproject.toml, .vercelignore          Vercel hosting
.github/workflows/refresh.yml                      hourly refresh for Vercel
```

## Storage

- **Locally / Docker:** JSON files in `data/runtime/`. Writes are atomic, and a corrupt file is moved aside instead of crashing the app. Run a **single process** only: the file storage is not designed for several workers.
- **Vercel:** the disk is not permanent, so BetFinder uses **Upstash Redis** (or any Redis via `REDIS_URL`) automatically when its environment variables are present. Without it the app shows a red "Storage is temporary" banner.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests never use the internet, real API keys or your Redis: those environment variables are cleared for every test.

- **Version:** `APP_VERSION` in `app/config.py` and `version` in `pyproject.toml`. Bump both for a release; the version is also added to the CSS/JS links, so browsers load the new files.
- **Dependencies** are listed twice on purpose: `requirements.txt` (local, Docker) and `pyproject.toml` (Vercel). Keep them the same.
- **Leagues and bookmakers** live in `data/registry/*.json`. Each competition lists its provider ids (`the_odds_api` sport key, `api_sports` league id) and optional model parameters.
- **Texts:** interface texts are in `app/static/js/i18n.js`, analysis texts in `app/i18n.py`, both in English and Danish.

## Limitations

- Pre-match only. Live "goal delay" arbitrage is not built: it needs sub-second live data, and bookmakers void bets placed after events.
- Pinnacle's own API needs a funded account and approval, so Pinnacle prices come through The Odds API and OddsPapi.
- The free API-Sports plan only covers past seasons, so the statistics agents report "no data" without a paid plan.

*Gambling involves risk. Estimates are not guarantees. Only stake what you can afford to lose.*
