# BetFinder setup guide

This guide covers everything from a fresh computer to a live website:

1. [What you need](#1-what-you-need)
2. [Get the code (fork or clone)](#2-get-the-code)
3. [Run it on your computer](#3-run-it-on-your-computer)
4. [Get the API keys](#4-get-the-api-keys)
5. [Go live on Vercel (free)](#5-go-live-on-vercel)
6. [Or go live on your own server (Docker)](#6-or-go-live-on-your-own-server-docker)
7. [Environment variables](#7-environment-variables)
8. [Updating, backups and moving to a new computer](#8-updating-backups-and-moving-to-a-new-computer)
9. [Troubleshooting](#9-troubleshooting)

BetFinder only uses **real data**: live API odds, polite scraping as a fallback, and prices you enter yourself.

---

## 1. What you need

| What | Why | Where |
|---|---|---|
| **Python 3.11 or newer** (3.12 recommended) | Runs BetFinder | <https://www.python.org/downloads/>. On Windows tick **"Add python.exe to PATH"** during install |
| **Git** | Gets the code and updates | <https://git-scm.com/downloads> |
| A **GitHub** account | Fork the repository; Vercel deploys from it | <https://github.com> |
| A free **The Odds API** key | Main odds feed | See [step 4](#4-get-the-api-keys) |
| A free **OddsPapi** key (recommended) | Backup feed and extra bookmakers | See [step 4](#4-get-the-api-keys) |
| For the website: a free **Vercel** account, *or* a small server with Docker | Hosting | <https://vercel.com> |

Check your versions in a terminal:

```bash
python --version
git --version
```

## 2. Get the code

**Fork (your own copy on GitHub, needed for Vercel):** open the repository on GitHub and click **Fork**. Then clone *your* fork:

```bash
git clone https://github.com/<your-github-name>/Bet-Finder.git
cd Bet-Finder
```

**Or clone directly** (just to run it locally):

```bash
git clone https://github.com/TBA187/Bet-Finder.git
cd Bet-Finder
```

## 3. Run it on your computer

### 3.1 Install

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> If PowerShell refuses to run `activate`, skip it and call the venv's Python directly, e.g. `.venv\Scripts\python.exe -m pip install -r requirements.txt` and `.venv\Scripts\python.exe -m uvicorn main:app --port 8000`.

### 3.2 Start

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. Stop it with **Ctrl+C**.

### 3.3 First sign-in

1. Sign in with **admin / admin**. This account is created automatically the first time BetFinder starts.
2. Change the password straight away under *More → My account* (a red banner reminds you).

### 3.4 Add your API keys

Choose **one** of these ways:

- **In the app (easiest):** *More → Sources & settings*, paste each key and press *Save*, then *Test key*. Keys are stored in `data/runtime/settings.json`, which is git-ignored and never leaves your computer.
- **In a `.env` file:** copy `.env.example` to `.env`, fill in the keys, and start with:

  ```bash
  uvicorn main:app --host 127.0.0.1 --port 8000 --env-file .env
  ```

  Without `--env-file` the `.env` file is **not** read when you run locally (Docker reads it by itself). Keys from environment variables take priority over keys saved in the app, and the app then shows "set via environment".

Then press **⟳** (top right). *More → Data usage* shows which source delivered odds and how many credits are left.

### 3.5 Automatic refresh (local / Docker)

Under *More → Sources & settings → Analysis & Daily Picks settings* set **Auto refresh every (minutes)**, e.g. 60, and the active hours (e.g. 8 to 24). BetFinder then refreshes by itself while it is running. Use the credit planner on *Data usage* to stay inside your The Odds API plan.

### 3.6 Run the tests (optional)

```bash
pip install -r requirements-dev.txt
pytest
```

### 3.7 Where your data is

Everything you create locally (settings, API keys entered in the app, accounts, odds history, tracked bets) is in **`data/runtime/`**. It is git-ignored. Keep it private: it contains your keys and account data.

## 4. Get the API keys

Start with the two free ones. You register with your email on each provider's website, and the key appears in your account or arrives by email.

### The Odds API (free, recommended first)

Main odds feed: NordicBet, Betsson, Unibet, Pinnacle, Betfair and more, plus UFC, boxing, tennis and ice hockey.

1. Go to <https://the-odds-api.com> and click **Get API Key**.
2. Choose **Starter – FREE** (500 credits/month) and enter your email.
3. The key arrives by email within a minute.
4. For more frequent refreshes, the 20K plan costs **$30/month**.

**Mind your credits:** each refresh costs about 1–2 credits per league that has matches. With 500 free credits, hourly refreshes run out after a few days. BetFinder then switches to OddsPapi, and then to scraping (shown on *Data usage*). Switch off leagues you don't need under *Sources & settings → Leagues & competitions*.

### OddsPapi (free)

Adds bet365, Pinnacle and 300+ bookmakers. Used automatically when The Odds API has no key or no credits left, and by the "Fetch more bookmakers" button on a match.

1. Go to <https://oddspapi.io> and click **Get Free API Key**.
2. Sign up with email and password (no credit card).
3. Copy the API key from your dashboard. Free plan: **250 requests/month**.

### API-Sports (optional: statistics, xG, injuries, lineups)

1. Go to <https://dashboard.api-football.com/register> and create a free account.
2. The key is on the dashboard ("My Access"). The same key works for football, basketball, handball and hockey.
3. The **free plan only covers past seasons**. For current matches you need a paid plan (about $10–19/month per sport). Without it BetFinder still works; the statistics agents just report "no data".

### odds-api.io (optional, paid: Danske Spil and Eurobet automatically)

1. Go to <https://odds-api.io> and choose the **Solo** plan (about £49/month, 2 bookmakers).
2. Select **DanskeSpil** and **Eurobet IT** as your bookmakers.
3. Copy the API key.

Without it, enter Danske Spil odds under **Add odds** (you can paste the odds copied from Danske Spil's page).

### Phone alerts (optional, free)

**Telegram:**

1. In Telegram, open **@BotFather**, send `/newbot` and follow the steps. You get a **bot token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser. The number at `"chat":{"id": ...}` is your **chat id**.
4. Enter both under *Sources → Phone notifications* (or put the token in `TELEGRAM_BOT_TOKEN`) and press *Send test*.

**ntfy:** install the ntfy app, subscribe to a hard-to-guess topic name, and enter the same topic under *Sources → Phone notifications*.

### Email (optional)

Lets BetFinder email temporary passwords to new users. Set the `SMTP_*` environment variables ([section 7](#7-environment-variables)). Without them, the admin sees the temporary password and can copy it or open a ready-made email.

For Gmail: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=you@gmail.com`, and as `SMTP_PASSWORD` an **app password** (Google account → Security → App passwords).

## 5. Go live on Vercel

`vercel.json`, `pyproject.toml` and `.vercelignore` are already set up (Frankfurt region, 300 s maximum duration, a daily cron job). About 20 minutes.

### 5.1 Create the project

1. Fork the repository ([step 2](#2-get-the-code)).
2. On <https://vercel.com>: **Add New → Project**, import your fork, keep the defaults and click **Deploy**.

### 5.2 Storage (required, free)

Vercel does not keep files between requests, so BetFinder needs a small database for settings, accounts, odds history and tracked bets.

1. In the Vercel dashboard open your project → **Storage**.
2. **Create Database → Upstash for Redis → Free**, region **Frankfurt (eu-central-1)**.
3. **Connect to project** and tick all environments.
4. Vercel adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` (or the same names with your chosen prefix). BetFinder detects them by itself.

Without this, the app shows a red "Storage is temporary" banner and forgets everything.

### 5.3 Environment variables

Project → **Settings → Environment Variables**:

| Name | Value |
|---|---|
| `CRON_SECRET` | **required**: a long random string. Scheduled refreshes must send it |
| `ODDS_API_KEY` | your The Odds API key |
| `ODDSPAPI_KEY` | your OddsPapi key |
| `API_SPORTS_KEY` | optional |
| `ODDS_API_IO_KEY` | optional |
| `TELEGRAM_BOT_TOKEN` | optional |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | optional, for emails |

Generate a random string with `python -c "import secrets;print(secrets.token_urlsafe(32))"`.

Then **Deployments → latest → ⋯ → Redeploy**, so the new variables take effect.

(You can also paste keys on the website under *More → Sources & settings*; they are then stored in Upstash. Environment variables are the safer place.)

### 5.4 Automatic refresh

- **Vercel Cron** (`vercel.json`) calls the refresh once a day at about 07:00 UTC, the free-plan limit.
- **GitHub Actions** (`.github/workflows/refresh.yml`) calls it **every hour from 06:00 to 23:00 UTC**, for free:
  1. On GitHub open your fork → **Settings → Secrets and variables → Actions → New repository secret**.
  2. Add `BETFINDER_URL` = your site address, e.g. `https://betfinder-xyz.vercel.app` (no trailing `/`).
  3. Add `CRON_SECRET` = the same value as in Vercel.
  4. Check it: **Actions** tab → *BetFinder refresh* → **Run workflow**. GitHub turns scheduled workflows off in forks until you open the Actions tab once and enable them.

### 5.5 Check it

1. Open your Vercel URL and sign in with **admin / admin**. Change the password straight away.
2. Under *More → Users & requests* approve account requests or create accounts.
3. Press **⟳**. *More → Data usage* shows which source delivered odds.
4. Under *Sources & settings → Bookmakers*, tick **Mine** for the bookmakers where you have an account.
5. On iPhone: Safari → Share → **Add to Home Screen**.

## 6. Or go live on your own server (Docker)

1. **Get a small server**, e.g. Hetzner CX22 (about €4/month) or DigitalOcean ($6/month), with Docker installed.
2. **Point a domain** (e.g. `bets.yourdomain.dk`) at the server's IP address.
3. **Get the code and configure:**

   ```bash
   git clone https://github.com/<your-github-name>/Bet-Finder.git
   cd Bet-Finder
   cp .env.example .env
   ```

   Set `DOMAIN`, `CRON_SECRET` and the API keys you use in `.env`.
4. **Start:**

   ```bash
   docker compose up -d
   ```

   Caddy gets a free HTTPS certificate automatically. The `betfinder-data` volume keeps your data across restarts and updates.
5. Sign in with **admin / admin**, change the password, and set **auto refresh** under *Sources & settings → Analysis & Daily Picks settings* ([3.5](#35-automatic-refresh-local--docker)).

Railway or Render also work, as long as you attach a **persistent volume** for `data/runtime` and run a single process.

## 7. Environment variables

All are optional unless marked. On Vercel set them under *Settings → Environment Variables*; for Docker put them in `.env`; locally use `--env-file .env` ([3.4](#34-add-your-api-keys)).

| Variable | Purpose |
|---|---|
| `CRON_SECRET` | Secret for `/api/cron/refresh` (Vercel Cron, GitHub Actions, cron-job.org send it as `Authorization: Bearer …`). **Required for scheduled refreshes**: without it the endpoint refuses every call |
| `ODDS_API_KEY`, `ODDSPAPI_KEY`, `API_SPORTS_KEY`, `ODDS_API_IO_KEY` | Data source keys. Take priority over keys saved in the app |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for alerts (chat id is set in the app) |
| `SMTP_HOST`, `SMTP_PORT` (587 STARTTLS or 465 SSL), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Email for temporary passwords |
| `KV_REST_API_URL` + `KV_REST_API_TOKEN` | Upstash Redis (added by Vercel). `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`, or any `<PREFIX>_REST_API_URL` + `<PREFIX>_REST_API_TOKEN` pair, also work |
| `REDIS_URL` (or `STORAGE_URL`, `KV_URL`) | Any standard Redis server (`redis://` or `rediss://`), e.g. Redis Cloud |
| `BETFINDER_DATA_DIR` | Folder for local data (default `data`; files go in `<folder>/runtime`) |
| `BETFINDER_<SETTING>` | Default for any app setting, e.g. `BETFINDER_SCRAPER_MODE=always`, `BETFINDER_AUTO_REFRESH_MINUTES=60`, `BETFINDER_MY_BOOKMAKERS=danske_spil,nordicbet`. Changes made in the app still win |
| `DOMAIN` | Docker/Caddy only: your domain name |
| `VERCEL` | Set by Vercel itself; switches off the background refresh loop |

## 8. Updating, backups and moving to a new computer

**Update** to the newest code:

```bash
git pull
pip install -r requirements.txt        # local
docker compose up -d --build           # Docker
```

On Vercel, every push to your fork deploys automatically. To get changes from the original repository into your fork, use **Sync fork** on GitHub.

**Backups:**

- Local: copy the `data/runtime/` folder.
- Docker: `docker compose cp betfinder:/app/data/runtime ./backup-runtime`.
- Vercel: your data lives in the Upstash database and survives redeploys.

**Moving to a new computer:** follow steps 1–3, then copy `data/runtime/` (and `.env`, if you use one) from the old computer into the new project folder. You keep your settings, accounts, odds history and tracker.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| Red banner "Storage is temporary" | Connect Upstash Redis to the Vercel project ([5.2](#52-storage-required-free)) and redeploy |
| "No live data source delivered odds" | Add an API key under *Sources*, then press ⟳ |
| "API key rejected" | The key belongs to another service, or was copied incompletely. Use *Test key* |
| Credits run out quickly | Fewer leagues, fewer regions/markets under *Sources & settings → The Odds API options*, or a longer refresh interval. See the credit planner on *Data usage* |
| GitHub Action fails with 401 | `CRON_SECRET` in GitHub and in Vercel are not identical |
| Port 8000 is already in use | Start on another port: `uvicorn main:app --port 8001` |
| Keys in `.env` are ignored locally | Start with `--env-file .env` ([3.4](#34-add-your-api-keys)) |
| Forgot the admin password, no email set up | Another super admin can reset it under *Users & requests*. If there is none: locally stop BetFinder and delete `data/runtime/users.json`; on Vercel delete the key `betfinder:users` in the Upstash console and redeploy. **This deletes all accounts**; BetFinder then creates **admin / admin** again on start |
| The site looks old after an update | Reload once; the app version is part of every CSS/JS link |

*Gambling involves risk. Estimates are not guarantees. Only stake what you can afford to lose.*
