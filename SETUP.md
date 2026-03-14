# Multi-Chain Memecoin Bot v10
## Setup Guide for Windows + Claude Code

---

## What You Need Before Starting

- Python 3.11+ installed (python.org)
- Claude Code installed (npm install -g @anthropic/claude-code)
- Node.js 18+ installed (nodejs.org)
- A text editor (VS Code recommended)

---

## Step 1 — Extract the Bot

Unzip `multichain_bot_v10.zip` to a folder on your PC.
Recommended location: `C:\bots\multichain_bot_v10\`

---

## Step 2 — Open Command Prompt in the Bot Folder

Option A — File Explorer:
  Navigate to C:\bots\multichain_bot_v10\
  Hold Shift + Right-click in the folder
  Select "Open PowerShell window here" or "Open command window here"

Option B — Command Prompt:
  Press Win + R, type cmd, press Enter
  Type: cd C:\bots\multichain_bot_v10
  Press Enter

---

## Step 3 — Install Python Dependencies

In your command prompt, run:

  pip install -r requirements.txt

This installs:
  aiohttp      — async HTTP requests
  solders      — Solana wallet and transactions
  web3         — Base and BNB Chain transactions
  scikit-learn — ML rug classifier
  numpy        — ML math

If pip is not recognized, try:
  python -m pip install -r requirements.txt

---

## Step 4 — Fill In Your Config

Open config.json in VS Code or Notepad.
Replace every placeholder with your real values:

  "solana_private_key"        Your Solana wallet private key (from Phantom)
  "evm_private_key"           Your MetaMask private key (Base + BNB)
  "solana_rpc_url"            Get free key at helius.dev
  "basescan_api_key"          Get free key at basescan.org/myapikey
  "bscscan_api_key"           Get free key at bscscan.com/myapikey
  "birdeye_api_key"           Get free key at birdeye.so
  "telegram_token"            From @BotFather on Telegram
  "telegram_chat_id"          From @userinfobot on Telegram

IMPORTANT: Leave private keys blank first to run in paper trading mode.
The bot works fully without real keys — it just simulates trades.

---

## Step 5 — Run the Backtest First

Before touching real money, test your strategy settings:

  python backtest\run_backtest.py --all-chains --find-optimal

This will:
  - Fetch historical token data from DexScreener
  - Replay your scanner scoring against that data
  - Find the optimal score threshold for each chain
  - Print a full performance report
  - Save results to JSON files

Look for profit factor above 1.5 and win rate above 50%
before moving to paper trading.

---

## Step 6 — Paper Trade First (2-3 Weeks)

With private keys left blank, run the bot:

  python main.py

The bot will:
  - Scan all 3 chains for signals
  - Send real Telegram alerts for every signal it finds
  - Simulate trades without spending real money
  - Track performance in trades.json
  - Show live dashboard at http://localhost:8080

Check the dashboard daily. If the bot is finding tokens you'd
buy manually and avoiding ones you'd skip, it's ready to go live.

---

## Step 7 — Open Claude Code in the Bot Folder

In your command prompt (in the bot folder):

  claude

Claude Code opens in the bot directory and has full access
to all 49 files. You can ask it to:

  - Fix any errors that appear in bot.log
  - Adjust any settings or logic
  - Add new features
  - Explain what any file does
  - Debug specific issues

Example Claude Code prompts:
  "Fix the error in bot.log"
  "Change the stall detection threshold to 30 minutes"
  "Why isn't the pyramid logic triggering?"
  "Add a daily summary report to Telegram at 9am"

---

## Step 8 — Go Live

When paper trading looks good:

1. Add your real private keys to config.json
2. Fund your wallets:
   - Solana wallet: load with SOL for gas + trading capital
   - EVM wallet: load with ETH (Base) and BNB for gas + trading capital
3. Run the bot: python main.py
4. Watch Telegram for the startup message
5. Open http://localhost:8080 for the live dashboard

---

## Daily Operation

STARTING THE BOT:
  cd C:\bots\multichain_bot_v10
  python main.py

STOPPING THE BOT:
  Ctrl+C in the command prompt
  OR send /kill in your Telegram bot chat

CHECKING PERFORMANCE:
  Open http://localhost:8080 in any browser
  OR check trades.json for full trade history

EMERGENCY STOP:
  Send /kill to your Telegram bot
  All positions close immediately

RESUME AFTER KILL:
  Send /resume to your Telegram bot
  OR restart with python main.py

---

## File Structure Reference

  main.py                     Start here — runs everything
  config.json                 All your settings
  bot.log                     Full log of everything the bot does
  trades.json                 Every trade recorded
  ml/training_data.json       ML classifier training data (builds over time)
  ml/rug_classifier.pkl       Trained ML model (appears after 200+ trades)

  core/
    position_manager.py       Your TP/SL/stall/pyramid rules
    scalper.py                Scalper — trades within existing positions
    signal_evaluator.py       Your scanner criteria (HH+HL, vol accel, etc)
    enhanced_copy_trader.py   Your copy trading rules
    multi_source_scanner.py   DexScreener + Birdeye dual-source scanner

  security/
    honeypot.py               GoPlus pre-buy security gate
    tax_detector.py           Auto slippage adjustment

  execution/
    kill_switch.py            /kill Telegram command
    mev_protector.py          Flashbots + private RPC routing
    gas_oracle.py             Real-time gas management

  backtest/
    run_backtest.py           Run this before going live

---

## Common Issues

BOT WON'T START:
  Check bot.log for the error
  Run: python main.py 2>&1 | more
  Most likely: missing API key in config.json

TELEGRAM ALERTS NOT WORKING:
  Check telegram_token and telegram_chat_id in config.json
  Make sure you messaged your bot in Telegram first

TRADES NOT EXECUTING:
  Check that private keys are in config.json
  Check that wallets have enough SOL/ETH/BNB for gas
  Check bot.log for swap errors

DASHBOARD NOT LOADING:
  Go to http://localhost:8080 (not https)
  Make sure nothing else is using port 8080
  Change dashboard_port in config.json if needed

ML CLASSIFIER NOT ACTIVATING:
  Normal — it needs 200+ labeled trades first
  It runs in heuristic mode until then
  Check ml/training_data.json to see how many samples collected

---

## Running 24/7 on Railway

For continuous operation without keeping your PC on,
see RAILWAY_DEPLOY.md for the full Railway setup guide.

Quick summary:
  1. Push code to a private GitHub repo
  2. Connect Railway to that repo
  3. Set environment variables in Railway's Variables tab
  4. Railway deploys and runs 24/7 automatically

## Support

Bring bot.log and config.json (with keys removed) to Claude Code.
Claude Code can read all 49 files and debug any issue directly.

Command to open Claude Code in bot folder:
  cd C:\bots\multichain_bot_v10 && claude
