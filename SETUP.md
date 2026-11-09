# BetFinder setup guide: live data on Vercel

BetFinder only uses **real data**: live API odds, polite scraping as a fallback, and prices you enter yourself. This guide takes about 20 minutes.

---

## 1. Get your API keys

You register on each provider's website with your email, and the key appears in your account or arrives by email. Start with the two free ones.

### The Odds API (free, recommended first)

Main odds feed: NordicBet, Betsson, Unibet, Pinnacle, Betfair and more, plus UFC, boxing, tennis and ice hockey.

1. Go to <https://the-odds-api.com> and click **Get API Key**.
2. Choose **Starter – FREE** (500 credits/month) and enter your email.
3. The key arrives by email within a minute. It looks like `a1b2c3d4e5f6...`.
4. Later, if you want more frequent refreshes, the 20K plan costs **$30/month**. The app's *Data usage* page shows how many credits you use.

### OddsPapi (free)

Adds bet365, Pinnacle and 300+ bookmakers. Used automatically when The Odds API credits run out, and by the "Fetch more bookmakers" button on a match.

1. Go to <https://oddspapi.io> and click **Get Free API Key**.
2. Sign up with email and password (no credit card).
3. Copy the API key from your dashboard. Free plan: **250 requests/month**.

### API-Sports (optional: statistics, xG, injuries, lineups)

1. Go to <https://dashboard.api-football.com/register> and create a free account.
2. The key is on the dashboard ("My Access"). The same key works for football, basketball, handball and hockey.
3. Note: the **free plan usually only covers past seasons**. For current matches you need the Pro plan (about $10–19/month per sport). Without it, the app still works: the Probability, Form, Injury and Lineup agents just report "no data".

### odds-api.io (optional, paid: Danske Spil and Eurobet automatically)

1. Go to <https://odds-api.io> and choose the **Solo** plan (about £49/month, 2 bookmakers).
2. Select **DanskeSpil** and **Eurobet IT** as your bookmakers.
3. Copy the API key.

Without it, enter Danske Spil odds under **Add odds**. You can paste the odds copied from Danske Spil's page.

### Telegram alerts (optional, free)

1. In Telegram, open **@BotFather**, send `/newbot` and follow the steps. You get a **bot token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser. The number at `"chat":{"id": ...}` is your **chat id**.

---

## 2. Storage on Vercel (required, free)

Vercel does not keep files between requests, so BetFinder needs a small database to remember settings, odds history and tracked bets.

1. In the Vercel dashboard, open your **BetFinder project → Storage**.
2. Click **Create Database**, choose **Upstash for Redis**, then **Free** plan and region **Frankfurt (eu-central-1)**.
3. Click **Connect to project** and tick all environments.
4. Vercel adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` automatically. BetFinder detects them by itself.

If this step is missing, the app shows a red "Storage is temporary" banner.

---

## 3. Environment variables on Vercel

Open your **BetFinder project → Settings → Environment Variables** and add:

| Name | Value |
|---|---|
| `ODDS_API_KEY` | your The Odds API key |
| `ODDSPAPI_KEY` | your OddsPapi key |
| `API_SPORTS_KEY` | (optional) API-Sports key |
| `ODDS_API_IO_KEY` | (optional) odds-api.io key |
| `TELEGRAM_BOT_TOKEN` | (optional) Telegram bot token |
| `CRON_SECRET` | a long random string, used by the scheduled refresh |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | (optional) email settings, so BetFinder can email temporary passwords to new users. Without them you copy the password yourself |

Generate a random string at e.g. <https://1password.com/password-generator>, or run `python -c "import secrets;print(secrets.token_urlsafe(32))"`.

The site is protected by accounts (username + password), so there is no shared access token any more.

Then go to **Deployments**, open the latest one and click **⋯ → Redeploy**, so the new variables take effect.

You can also paste keys on the website under *More → Sources*. With Upstash connected they are stored there. Environment variables are the safer place.

---

## 4. Automatic refresh

- **Vercel Cron** (in `vercel.json`) runs once a day at about 07:00 UTC. That is the free-plan limit.
- **GitHub Actions** (`.github/workflows/refresh.yml`) runs **every hour from 06:00 to 23:00 UTC** for free:
  1. On GitHub, open your repository → **Settings → Secrets and variables → Actions → New repository secret**.
  2. Add `BETFINDER_URL` = your site address, e.g. `https://betfinder-xyz.vercel.app` (no trailing `/`).
  3. Add `CRON_SECRET` = the same value as in Vercel.
  4. Check it works: **Actions** tab → *BetFinder refresh* → **Run workflow**.

**Mind your credits:** each refresh costs roughly 1–2 The Odds API credits per league that has matches. With the free 500 credits, hourly refreshes will run out after a few days. BetFinder then automatically switches to OddsPapi, and then to scraping (you see this on the *Data usage* page). Use the credit planner there, or switch off leagues you don't need under *Sources*.

---

## 5. Done: check it

1. Open your Vercel URL and sign in with **admin / admin**. Change the password straight away under *More → My account* (the red banner reminds you).
2. Under *More → Users & requests* you approve account requests and create accounts (roles: Super admin, Editor, User).
3. The dashboard's "Get started" box disappears once a key is configured.
4. Press **⟳**. *More → Data usage* shows which source delivered odds (API or scraping) and how many tokens are left.
5. On iPhone: Safari → Share → **Add to Home Screen**.
6. Under *Sources*, tick **Mine** for the bookmakers where you have a betting account.

## Running on your own PC instead

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Keys are entered on the website, and data is stored in `data/runtime/`. A VPS with Docker also works: see the README, section "Going live".
