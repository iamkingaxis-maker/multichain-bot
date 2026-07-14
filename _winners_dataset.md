# Winner-vs-Us comparison dataset (2026-06-26)

Consistently-profitable Solana memecoin wallets, decoded, vs OUR dip-buy fleet.
Source files agents should read:
- `_decode_winners_out.txt` — 9 decoded winner systems (full trade trips)
- `_decode_2tYcXQCf.txt` — 10th winner (2tYcX), decoded today, very active
- `_us_profile.json` — our complete fleet+probe profile (structured)
- `_full_trades.json` — our raw ~5000 trade legs (fields: address, bot_id, kind,
  fully_closed, entry_price, exit_price, pnl, pnl_pct, peak_pnl_pct, hold_secs, live_*)

## Decoded winners (n=10)
| wallet (8) | tokens | closed | sizing SOL (median) | hold med (min) | WR | win med | loss med | best | overlap: WE TRADED | exit style |
|---|---|---|---|---|---|---|---|---|---|---|
| DU25Xy | 18 | 14 | 1.50 | 9718 (~6.7d) | 71% | +62.7% | -48.9% | +178097% | 11/18 | hold days–weeks |
| C3zP   | 35 | 22 | 0.66 | 219 | 73% | +11.6% | -16.1% | +198% | **34/35** | price/discretion |
| B1zhrW | 42 | 28 | 1.00 | 87  | 57% | +85.6% | -13.0% | +186% | n/a (502) | price/discretion |
| Zsp75  | 28 | 25 | 0.93 | 114 | 52% | +22.1% | -13.0% | +155% | **21/28** | price/discretion |
| jStURX | 21 | 15 | 1.68 | 154 | 53% | +553.5% | -34.3% | +9.8M% | 13/21 | price/discretion |
| 7d54Pt | 46 | 8  | ~0   | 7   | 88% | +184.9% | -48.1% | +47544% | 1/46 | fast scalp |
| ArWird | 10 | 8  | 11.75| 395 | 75% | +19.2% | -13.6% | +162261% | 9/10 | hold |
| DaxfeJ | 25 | 18 | 0.77 | 9   | 44% | +6.9% | -7.9% | +32.2% | 9/25 | TIME-BOX ~6min |
| DznHqB | 8  | 6  | 23.98| 685 | 67% | +26.8% | -42.6% | +78.3% | 4/8 | hold |
| 2tYcX  | 116| 44 | 0.64 | 91  | 68% | +24.8% | -25.6% | +141% | 19/116 | price/discretion |

ALL winners use VARIABLE conviction sizing. Most hold 87–685 min; two scalp (7–9 min).

## US (baseline, from _us_profile.json)
- WR **29.3%**, median pnl **-4.9%**, mean -2.4%, sum -$7,672 (n=2210 closed). Fat-tail.
- Hold median **5.6 min** (winners 11.5, losers 3.7). Peak_pnl median **+1.0%**.
- **33.8% of our losers had gone green before reversing.**
- Entries: pc_h1 -22%, liq ~$38k, age ~60h. Sizing: FIXED tiers $5–200.
- Live probe: slippage med +0.81%, latency 1.38s, pnl tracks paper.

## The visible headline (to be rigorously verified, not assumed)
Winners HOLD hours and let winners run (positive win-medians, catch the fat tail);
we exit in ~5 min capping peak at +1%. On OVERLAP tokens (we traded 34/35 of C3zP,
21/28 of Zsp, 9/10 of ArW) the SAME tokens made them money — pointing at HOLD/EXIT,
not selection. Some winners also have a discovery gap (7d54 1/46, 2tYcX 19/116).
