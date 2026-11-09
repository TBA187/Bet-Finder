# BetFinder 1.3

BetFinder scans bookmaker odds and looks for:

- **pricing errors**: a price far out of line with the rest of the market
- **price differences between bookmakers**
- **value bets**: the estimated true probability is higher than the price implies

It covers **team sports** (football, basketball, handball, ice hockey, NFL, baseball), **combat sports** (UFC/MMA, boxing) and **racket sports** (tennis). Ten analysis agents assess each match independently. **Daily Picks** builds up to three value coupons per day. The interface is mobile-first, works in **English and Danish**, and can be installed on a phone's home screen.

> **Honesty rule:** BetFinder never invents odds, injuries, lineups or results. Every price and data point is labelled with its source and age: *Live API*, *Scraped*, *Manual*, *Historical* or *Missing*. There is no demo or sample data.

---

## Quick start (your PC)

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000> and follow the **Get started** box on the dashboard: get a free key from The Odds API (and OddsPapi), paste it under *More → Sources*, and press ⟳.

**Step-by-step guide for API keys and Vercel: [SETUP.md](SETUP.md).**

Run the tests with `pip install -r requirements-dev.txt` and then `pytest` (95 tests).

## Where the odds come from: the fallback chain

| # | Source | Method | Cost | What it gives |
|---|---|---|---|---|
| 1 | **The Odds API** | API credits | **Free 500 credits/month**. 20K $30, 100K $59/month | NordicBet, Betsson, Unibet, Pinnacle, Betfair, William Hill, 1xBet, LeoVegas, Svenska Spel, Mr Green, Expekt and more, plus UFC, boxing, tennis, NHL/SHL, NFL, MLB |
| 2 | **API-Sports** | API requests | Free plan covers past seasons only; Pro about $10–19/month per sport | Fixtures, stats, **xG**, form, head-to-head, injuries, suspensions and lineups (football); EHF handball, basketball and hockey data |
| 3 | **odds-api.io** | API requests | About £49/month (2 bookmakers of your choice) | The only feed found with **Danske Spil** and **Eurobet** (also Bet365, Bet25, Unibet DK) |
| 4 | **OddsPapi** | Free monthly requests | **Free 250 requests/month** | 300+ bookmakers incl. bet365 and Pinnacle. Used automatically when #1 has no key or no credits left, and by the "Fetch more bookmakers" button |
| 5 | **Polite scraper** | Scraping | Free | Public JSON odds that some bookmaker sites load in the browser (Kambi platform, e.g. Unibet). Runs only when #1–#4 deliver nothing (or always, if you choose) |
| – | **Manual entry** | Manual | Free | Any bookmaker. Paste odds copied from the bookmaker's page and they fill in automatically |

**Data usage page** (*More → Data usage*): shows whether odds currently come from **API tokens** or **scraping**, tokens used and left per provider (exact where the provider reports it), the last refresh's fallback chain, blocked sites, and a credit planner that recommends the cheapest The Odds API plan for your refresh interval.

**About scraping:** the scraper respects robots.txt, waits between requests, uses an honest user agent, and never tries to get around blocking (no proxies, headless browsers or CAPTCHA solving). A site that refuses access is paused for 24 hours and shown as *blocked*. Scraping can conflict with a bookmaker's terms of use; you can switch it off under Sources. Danske Spil does not offer such data, so use manual entry or odds-api.io for it.

### Recommended setup and cost

| Goal | Setup | Monthly cost |
|---|---|---|
| Try it / prototype | The Odds API free + OddsPapi free + scraping fallback + manual Danske Spil | **$0** |
| Serious daily use | The Odds API 20K (refresh hourly), manual Danske Spil | **≈ $30** |
| Danske Spil and Eurobet automatic | + odds-api.io Solo | **≈ $95** |
| Full stats, xG, injuries and lineups | + API-Football Pro | **≈ $115** |

## Analysis

1. **Odds Scanner Agent** removes each bookmaker's margin (Shin by default) and builds a **fair consensus price**, with sharp books (Pinnacle, exchanges) weighted highest. It also tracks movements, stale prices and arbitrage.
2. **Value Detection Agent** classifies prices against fair odds with a traffic light: **normal < 5%**, **warning 5–10%**, **alert ≥ 10%**. It flags outliers and *possible pricing errors*.
3. **Probability Agent** builds an independent **sport-specific model**:
   - football: Poisson / Dixon-Coles, with **xG blending**
   - ice hockey: Poisson with overtime
   - basketball, NFL and baseball: normal models
   - handball: normal model where draws are a real outcome
   - UFC, boxing and tennis: market-based strength model
4. **Form Agent**: momentum, i.e. recent form compared with season level.
5. **Home/Away Agent**: home/away effects beyond normal home advantage (not used for individual sports).
6. **Injury Agent** and 7. **Suspension Agent**: absences weighted by player importance.
8. **Lineup Agent**: confirmed or expected lineups and rotation.
9. **Statistical Agent**: stats-, **xG**- and head-to-head-implied margin and total compared with the market.
10. **Risk Agent**: a risk rating per selection.

The bookmaker consensus is the anchor, and agents can only adjust it within limits. A selection **qualifies** only if all of these hold:

- EV ≥ 3%
- the price is at or above the fair market price
- confidence ≥ 55
- risk ≤ medium

Each selection is marked **+EV**, **−EV** or **Insufficient data**. Every analysis shows when each data type was last updated.

Formulas:

- implied probability = 1 / odds
- EV % = (p × odds − 1) × 100
- fair odds = 1 / p
- stake = quarter-Kelly, shown in your currency (DKK by default)

## Features

- **Dashboard**: value bets, pricing errors, dropping odds, today's coupons and performance at a glance.
- **Scanner**: grouped by category and sport, with filters for value, deviation level (5%+ / 10%+) and sorting.
- **Daily Picks**: up to **3 coupons** (adjustable 1–5) with different matches, ≤ 3 legs each and combined odds > 3.00. They are never forced; if nothing qualifies you see "No qualifying value bets found today."
- **Add odds**: find a known match by typing team names, enter **several bookmakers at once**, or paste odds straight from a bookmaker's page.
- **Bet builder**: combine selections. Same-match legs use the sport model's joint probability (e.g. "home win + over 2.5" is positively correlated).
- **Arbitrage finder** with a stake calculator.
- **Agent analysis** page: what each agent found across today's matches.
- **Tracker**: automatic settlement, ROI and **Closing Line Value**.
- **Phone alerts** via Telegram or ntfy (both free) for pricing errors, value bets, arbitrage and new coupons.
- **Leagues**: 107 competitions (Danish Superliga, La Liga, Allsvenskan, the Big 5, Champions League, Herreligaen, Basketligaen, EHF, NHL, SHL, UFC, ATP/WTA and more). Switch them on or off in the app, or add more in `data/registry/competitions.json`.
- **Bookmakers**: 51 in the registry. Tick "Mine" for the bookmakers where you have a normal betting account, and picks will only use their prices.

## Going live on Vercel

See **[SETUP.md](SETUP.md)**. In short:

1. Connect **Upstash Redis** (free) under Vercel → Storage, because Vercel has no permanent disk.
2. Add your keys plus `CRON_SECRET` as environment variables, then redeploy.
3. Sign in with **admin / admin** and change the password at once (*More → My account*).
4. Add `BETFINDER_URL` and `CRON_SECRET` as GitHub secrets. The included GitHub Action then refreshes hourly, since the free Vercel plan's cron job runs only once a day.

`vercel.json`, `pyproject.toml` and `.vercelignore` are already set up: Frankfurt region, 300 s max duration and a daily cron job.

## Going live on your own server (Docker)

1. **Get a small server.** A VPS such as Hetzner CX22 (about €4/month) or DigitalOcean ($6/month) with Docker installed.
2. **Point a domain** (e.g. `bets.yourdomain.dk`) at the server's IP address.
3. **Configure:**
   ```bash
   cp .env.example .env
   ```
   Set `DOMAIN`, `CRON_SECRET` and the API keys you use. Sign in with **admin / admin** and change the password at once.
4. **Start:**
   ```bash
   docker compose up -d
   ```
   Caddy gets a free HTTPS certificate automatically. The data volume keeps your odds history, settings and tracker across restarts and updates.
5. In the app, set **auto refresh** (e.g. every 60 minutes between 08:00 and 24:00). Use the credit planner to stay inside your The Odds API plan.
6. Add **Telegram/ntfy** under Sources. You then get a message on your phone whenever a pricing error, strong value bet, arbitrage or new coupon appears.
7. On iPhone, open the site in Safari and choose *Share → Add to Home Screen* to use it like an app.

Update to a new version with `git pull && docker compose up -d --build`.

Alternatives: Railway or Render also work, as long as you attach a **persistent volume** for `data/runtime`. Run a single process only, because JSON storage is not designed for several workers.

## Storage (JSON files)

Everything is stored under `data/runtime/` (git-ignored): fixtures, odds and price history, team news, statistics, tracker, picks, settings and the provider cache. Writes are atomic, and a corrupt file is moved aside instead of crashing the app.

## Status of the old v1.0 roadmap

| Old roadmap item | Status |
|---|---|
| Pinnacle benchmark / de-margining | ✅ Sharp-weighted consensus with 4 margin-removal methods |
| Green/yellow/red error categories | ✅ Deviation traffic light (thresholds adjustable) |
| Quarter-Kelly stakes in DKK | ✅ Per selection and per coupon, in your currency |
| xG screening | ✅ API-Football match statistics or manual xG entry |
| Daily Picks with correlation protection | ✅ Different matches, independence enforced; now 3 coupons |
| CLV tracker | ✅ Real tracking, no invented history |
| Push notifications | ✅ Telegram and ntfy (more reliable on iPhone than web push), plus an installable app |
| Arbitrage detection | ✅ Pre-match arbitrage finder. ❌ Live "goal delay" exploitation is not built: it needs sub-second live data, and bookmakers void bets placed after events |
| Direct bookmaker APIs | ✅ The Odds API, odds-api.io (Danske Spil/Eurobet), OddsPapi, API-Sports. Pinnacle's own API needs a funded account and approval |
| Database | JSON files by choice. The storage layer is isolated, so SQLite can be added later |
| Bet builder simulator | ✅ Same-match correlation from the sport models |

*Gambling involves risk. Estimates are not guarantees. Only stake what you can afford to lose.*
