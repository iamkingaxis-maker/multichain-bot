# Multi-Chain Memecoin Bot v2
### Solana | Base | BNB Chain
### Strategies: Scanner + Copy Trader + Scalper

---

## What's New in v2

The scalper runs as a third strategy alongside the scanner and copy trader.
It ONLY trades tokens already held by the other two strategies — no new token risk.

When a held token dips sharply, the scalper buys the dip with a small amount
and sells on the recovery bounce. The original position stays open untouched.

---

## How Capital Is Split (Default — $2,000)

```
Total: $2,000
  Scalper pool:   $400  (20% of total, split across chains)
  Remaining:      $1,600 split by chain:
    Solana (50%): $800  → Scanner + Copy Trader
    Base   (30%): $480  → Scanner + Copy Trader
    BNB    (20%): $320  → Scanner + Copy Trader

Scalper allocation per chain:
    Solana: $200 (50% of $400)
    Base:   $120 (30% of $400)
    BNB:    $80  (20% of $400)
```

---

## Scalper Settings (config.json)

| Setting | Default | Description |
|---|---|---|
| scalper_capital_pct | 0.20 | 20% of total capital for scalping |
| scalper_dip_threshold_pct | 4.0 | Buy when token drops 4% from recent peak |
| scalper_bounce_target_pct | 3.0 | Sell when price recovers 3% from entry |
| scalper_stop_loss_pct | 5.0 | Hard stop if price drops 5% from entry |
| scalper_max_hold_seconds | 300 | Force exit after 5 minutes regardless |
| scalper_max_concurrent | 3 | Max simultaneous scalp trades per chain |

### BNB Chain Note
BNB gas fees (~$0.05-$0.20) are higher than Solana/Base, so the scalper
automatically adds +1% to both the dip threshold and bounce target on BNB
to ensure trades are profitable after fees.

---

## Setup Instructions

### Step 1 — Install Dependencies
```
pip install -r requirements.txt
```

### Step 2 — Fill in config.json
Keys you need:
- solana_private_key — your Solana wallet
- evm_private_key — your MetaMask/EVM wallet (used for Base AND BNB)
- solana_rpc_url — get free key at helius.dev
- basescan_api_key — free at basescan.org/myapikey
- bscscan_api_key — free at bscscan.com/myapikey
- telegram_token — from @BotFather on Telegram
- telegram_chat_id — from @userinfobot on Telegram

### Step 3 — Paper Trade First
Leave private keys blank to simulate without real money.
Watch the Telegram alerts and terminal dashboard for a few days
before switching to live trading.

### Step 4 — Run
```
python main.py
```

---

## Dashboard
The terminal dashboard shows every 5 minutes:
- Overall PnL across all strategies
- Scanner stats (trades, win rate, PnL)
- Copy Trader stats
- Scalper stats + live capital per chain

---

## File Structure
```
multichain_bot_v2/
├── main.py                      # Entry point — wires all 3 strategies
├── config.json                  # All settings
├── requirements.txt
├── core/
│   ├── scalper.py               # NEW — position scalper
│   ├── multi_scanner.py         # Market cap scanner (all chains)
│   ├── trader.py                # Solana buy/sell
│   ├── copy_trader.py           # Solana copy trading
│   └── risk_manager.py          # Per-chain risk limits
├── chains/
│   ├── chain_config.py          # Solana, Base, BNB definitions
│   ├── evm_trader.py            # Base + BNB buy/sell
│   └── evm_copy_trader.py       # Base + BNB copy trading
├── dashboard/
│   └── tracker.py               # Tracks all 3 strategies separately
└── utils/
    ├── config.py                # Config loader
    └── telegram_bot.py          # Telegram alerts
```

---

## Disclaimer
Trading memecoins is extremely high risk. This bot does not guarantee profits.
Only use money you can afford to lose completely. Always paper trade first.
