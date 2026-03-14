# Railway Deployment Guide
## Running the Bot 24/7 on Railway

Railway keeps your bot running continuously in the cloud.
No need to keep your PC on. Restarts automatically if it crashes.

---

## What Railway Costs

Railway's Starter plan: $5/month
  - 512MB RAM (enough for this bot)
  - Runs 24/7
  - Automatic restarts on failure
  - Real-time logs

Hobby plan: $20/month
  - 8GB RAM
  - Better for production use

Start on Starter and upgrade if needed.

---

## Step 1 — Create a Railway Account

Go to railway.app and sign up.
Connect your GitHub account when prompted — you'll need it.

---

## Step 2 — Push the Bot to GitHub

Railway deploys from GitHub. You need to put the bot code there first.

On your PC, open command prompt in the bot folder:

  cd C:\bots\multichain_bot_v13

Initialize git and push to GitHub:

  git init
  git add .
  git commit -m "Initial bot deployment"

Then go to github.com, create a new PRIVATE repository called
"multichain-bot", and follow GitHub's instructions to push:

  git remote add origin https://github.com/YOURUSERNAME/multichain-bot.git
  git branch -M main
  git push -u origin main

IMPORTANT: Make sure the repository is PRIVATE.
Your config.json does not contain private keys
(those go in Railway Variables) but keep it private anyway.

---

## Step 3 — Create a New Project on Railway

1. Go to railway.app
2. Click "New Project"
3. Click "Deploy from GitHub repo"
4. Select your "multichain-bot" repository
5. Railway will detect it's a Python project automatically

---

## Step 4 — Set Your Environment Variables

This is where your private keys and API keys go.
Railway keeps these encrypted and never exposes them in logs.

In your Railway project:
1. Click on your service
2. Click "Variables" tab
3. Add each variable below

REQUIRED VARIABLES:
  TELEGRAM_TOKEN              Your bot token from @BotFather
  TELEGRAM_CHAT_ID            Your chat ID from @userinfobot
  SOLANA_RPC_URL              https://mainnet.helius-rpc.com/?api-key=YOUR_KEY
  SOLANA_PRIVATE_KEY          Your Phantom/Exodus Solana private key
  EVM_PRIVATE_KEY             Your MetaMask/Exodus EVM private key

OPTIONAL BUT RECOMMENDED:
  BASESCAN_API_KEY            From basescan.org/myapikey
  BSCSCAN_API_KEY             From bscscan.com/myapikey
  BIRDEYE_API_KEY             From birdeye.so

PAPER TRADING (start here — no keys needed):
  Just set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID and SOLANA_RPC_URL
  Leave private keys blank for paper trading mode

COPY WALLETS (optional, comma-separated):
  SOLANA_COPY_WALLETS         wallet1,wallet2,wallet3
  BASE_COPY_WALLETS           0xwallet1,0xwallet2
  BNB_COPY_WALLETS            0xwallet1,0xwallet2

CAPITAL SETTINGS (optional — overrides config.json):
  TOTAL_CAPITAL               2000
  DAILY_LOSS_LIMIT            200
  ENABLE_SOLANA               true
  ENABLE_BASE                 true
  ENABLE_BNB                  true

---

## Step 5 — Deploy

After setting variables, Railway deploys automatically.

Watch the build logs — it should:
  1. Install Python 3.11
  2. Run: pip install -r requirements.txt
  3. Start: python main.py

The bot sends a Telegram message when it starts successfully.
If you don't get a Telegram message within 2 minutes, check the logs.

---

## Step 6 — View Logs

In Railway:
  Click your service → Click "Logs" tab

You'll see everything the bot logs including:
  - Scanner signals
  - Security checks
  - Trades (paper or live)
  - Errors

---

## Step 7 — Web Dashboard on Railway

The dashboard runs at port 8080 but Railway needs a public URL for it.

In Railway:
  1. Click your service
  2. Click "Settings"
  3. Click "Generate Domain"
  4. Railway gives you a URL like: yourbot.railway.app

Then access your dashboard at: https://yourbot.railway.app

Railway automatically routes port 8080 to this URL.

---

## Updating the Bot

When you want to push a code change:

  cd C:\bots\multichain_bot_v13
  git add .
  git commit -m "Description of change"
  git push

Railway automatically redeploys within 1-2 minutes.

---

## Persistent Data on Railway

Railway's filesystem resets on each deploy. This affects:
  - trades.json (trade history)
  - ml/training_data.json (ML classifier data)
  - ml/rug_classifier.pkl (trained model)
  - bot.log

To persist this data across deploys, add a Railway Volume:
  1. In your service → click "Add Volume"
  2. Mount path: /app/data
  3. Update bot to save files to /app/data/ instead of ./

For now during paper trading this doesn't matter much.
Before going live, set up the volume so your ML training
data accumulates properly.

---

## Environment vs Config.json Priority

The bot reads settings in this order:
  1. config.json (baseline settings)
  2. Railway environment variables (override config.json)

So you can keep non-sensitive settings in config.json
(capital split, TP tiers, thresholds) and put secrets
in Railway Variables (private keys, API keys).

You never need to put private keys in config.json.

---

## Recommended Railway Workflow

PHASE 1 — Paper trading (free tier ok):
  Set: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, SOLANA_RPC_URL
  Leave private keys blank
  Watch Telegram alerts for 2-3 weeks

PHASE 2 — Live trading (Starter $5/month):
  Add: SOLANA_PRIVATE_KEY, EVM_PRIVATE_KEY
  Fund your Exodus wallets
  Monitor dashboard daily

PHASE 3 — Scaling up (Hobby $20/month):
  Add more capital, more copy wallets
  ML classifier will be trained by now
  Upgrade plan if bot needs more resources

---

## Common Railway Issues

BOT WON'T START:
  Check logs for "CONFIG ERRORS"
  Most likely: TELEGRAM_TOKEN or SOLANA_RPC_URL missing

RESTARTS CONSTANTLY:
  Check logs for Python errors
  Likely an import error — check requirements.txt

NO TELEGRAM MESSAGES:
  Double-check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
  Make sure you messaged your bot first in Telegram

OUT OF MEMORY:
  Upgrade to Hobby plan
  Or disable one chain (ENABLE_BNB=false) to reduce load

DASHBOARD NOT LOADING:
  Make sure you generated a Railway domain
  Check that PORT env var is set (Railway sets it automatically)
