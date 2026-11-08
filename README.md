# BetFinder 1.1

BetFinder scans European bookmaker odds for **football, basketball and handball**. It looks for:

- **pricing errors**: a price far out of line with every other bookmaker
- **price differences between bookmakers**: where the best price is above the margin-free market price
- **value bets**: where the estimated true probability is higher than the price implies

Ten specialised analysis agents assess each match independently. A combination engine merges their views, and a **Daily Picks** section builds up to two value coupons per day.

> **Honesty rule:** BetFinder never invents odds and never pretends a bookmaker is connected. Every price shows its source (provider, manual or demo). A bookmaker without an accessible feed is labelled *Manual entry only*.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. Without an API key the app is empty, by design. You have three options:

1. **Add an API key** under *Sources* (see below), then press the refresh button.
2. **Enter odds manually** under *Add odds*, for any bookmaker, including Danske Spil and Eurobet.
3. **Turn on demo mode** under *Sources* to explore with fictional teams and bookmakers. Demo data is clearly marked and never counted in tracker statistics.

Run the tests with `pip install -r requirements-dev.txt` and then `pytest`.

## Data sources

| Source | What it provides | Notes |
|---|---|---|
| **The Odds API** (`ODDS_API_KEY`) | Live pre-match odds (1X2, totals, handicaps) from NordicBet, Betsson, Unibet, Pinnacle, Betfair Exchange, Matchbook, William Hill, 1xBet, Marathonbet, Tipico, LeoVegas, Coolbet, Betclic, Winamax, Codere and more | Free plan: 500 credits/month. Each refresh costs competitions × regions × markets credits (shown under *Sources*). Handball: only the German Handball-Bundesliga. |
| **API-Sports** (`API_SPORTS_KEY`) | Fixtures, odds, team statistics, recent form, head-to-head, results. For football also injuries, suspensions and confirmed lineups. Covers EHF Champions League / EURO / World Championship handball and EuroLeague / NBA basketball | One key covers all three sports, each with its own daily quota. **The free plan usually only covers past seasons**, so current matches need a paid plan. The provider's own error message is shown under *Sources*. |
| **Manual entry** | Any bookmaker, any supported market | Pick a known match from the search (no need to retype teams or kick-off), choose the bookmaker and type the odds. |
| **Demo** | Fictional teams and bookmakers | For trying the app only. |

**Danske Spil** and **Eurobet** have no public odds feed, and neither provider carries them. They are therefore *Manual entry only*. If API-Sports ever delivers one of them, it is picked up automatically by name.

You can enter API keys in the UI. They are stored in `data/runtime/settings.json`, which is git-ignored, and never sent back to the browser. On a server, use environment variables instead.

## How the analysis works

1. **Odds Scanner Agent**: compares every bookmaker's price for identical matches and markets. It removes each bookmaker's margin (Shin's method by default; power, proportional and additive are also available) and builds a **consensus fair probability**. Sharp books (Pinnacle, exchanges) get the most weight, otherwise the median of all bookmakers is used. It also tracks odds movement, stale prices and cross-bookmaker arbitrage.
2. **Value Detection Agent**: flags value, outlier prices and *possible pricing errors* (bookmakers may void obvious errors, so these are marked as higher risk).
3. **Probability Agent**: an independent, **sport-specific** model built from team statistics:
   - football: Poisson goals model with Dixon-Coles correction
   - basketball: normal margin/total model, moneyline including overtime
   - handball: discretised normal model in which draws are a real outcome
4. **Form Agent**: recent form compared with each team's season level (momentum).
5. **Home/Away Agent**: unusually strong home teams or weak away teams.
6. **Injury Agent**: injured and doubtful players, weighted by importance.
7. **Suspension Agent**: suspended players.
8. **Lineup Agent**: confirmed or expected lineups, rotation, and absentees who turn up in the starting XI anyway.
9. **Statistical Agent**: stats-implied margin and total versus the market, plus head-to-head.
10. **Risk Agent**: a risk rating (low / medium / high / very high) per selection, based on variance, confidence, market depth, price reliability and agent disagreement.

Agents 3–9 only see raw data, never each other's conclusions, so they can disagree. The engine measures that disagreement, and strong disagreement lowers confidence. **Missing data lowers confidence; it is never filled in.**

**Combination.** The market consensus is the anchor. The sport model is fitted to it. The independent model is blended in with a limited weight, and agent evidence shifts the expected margin and total within a cap. Final probabilities are the consensus moved by the model's *delta*, so model errors can't create fake value.

**Value maths**

- implied probability = 1 / decimal odds
- EV % = (estimated probability × decimal odds − 1) × 100
- fair odds = 1 / estimated probability

A selection **qualifies** only if all of these hold:

- EV ≥ the minimum (default 3%)
- the price is at or above the margin-free market price (agents can't create value on their own)
- confidence ≥ 55
- risk ≤ medium
- the price is fresh

High odds alone never qualify.

## Daily Picks

- Up to **two coupons**, with **different matches** across them and at most **three legs** each.
- Combined odds must be **above 3.00**.
- Built only from qualifying selections in today's window (now until 06:00 local time).
- Ranked by expected log-growth (Kelly), which rewards edge and penalises long-shot combinations.
- Never forced. If only one coupon qualifies you get one. If none qualify the page says **"No qualifying value bets found today."** and explains which rules rejected the candidates.
- Every leg shows: match, sport and competition, market, selection, best bookmaker, odds, estimated probability, fair odds, value %, risk rating and a short explanation.

All thresholds can be changed under *Sources → Analysis settings*. Tick "Mine" next to the bookmakers you actually use and picks will only use their prices.

## Tracker and CLV

Use **Track coupon** or **Track this selection** to record a bet. Bets settle automatically when results arrive (from a provider or entered manually). **Closing Line Value** compares your odds with the margin-free consensus at kick-off, which is the best long-run test of whether the value detection works.

## Storage (JSON files, no database)

Everything is stored under `data/runtime/` (git-ignored). Writes are atomic: temp file, then fsync, then rename. A corrupt file is moved aside instead of crashing the app.

| File | Contents |
|---|---|
| `fixtures.json` | matches (merged across providers by fuzzy team names and kick-off time) |
| `odds.json` | current prices plus price history per bookmaker |
| `team_news.json` / `provider_news.json` | manual and provider absences and lineups |
| `team_stats.json`, `h2h.json` | cached statistics |
| `bets.json` | tracked bets |
| `daily_picks.json` | generated coupons (last 30 days) |
| `settings.json`, `competition_overrides.json` | your settings |
| `cache/` | provider response cache (saves API quota) |

The app is designed for a **single server process**: `uvicorn main:app` without `--workers`.

## Adding leagues and bookmakers

- **Competition:** add an entry to `data/registry/competitions.json`, with The Odds API sport key and/or the API-Sports league id or name, plus optional model parameters. No code changes needed.
- **Bookmaker:** add an entry to `data/registry/bookmakers.json`, with provider keys or aliases. Bookmakers a provider returns that are not in the registry still appear, marked as unregistered.

At the user's request, the Danish Superliga, Spanish La Liga and Swedish Allsvenskan are **not** included.

## Configuration

| Environment variable | Purpose |
|---|---|
| `ODDS_API_KEY` | The Odds API key |
| `API_SPORTS_KEY` | API-Sports key |
| `BETFINDER_TOKEN` | Set this when hosting publicly. All `/api` calls then require the header `X-BetFinder-Token`, and the UI asks for the token once. |
| `BETFINDER_DATA_DIR` | Alternative data folder |

## Known limitations

- Value estimates are probabilities, not certainties. Betting markets, especially sharp ones, are hard to beat. Use the tracker's CLV numbers to judge the system over time.
- Injury, suspension and lineup feeds exist for football only (API-Sports). For basketball and handball, enter team news manually in the match view.
- The Odds API results endpoint is used for settlement. For cup matches decided in extra time, check the settlement manually, because football markets settle on regular time.
- Player importance is unknown for provider injury data. Set it manually for better accuracy.

*Gambling involves risk. Only stake what you can afford to lose.*
