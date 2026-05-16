# Universe Recorder — Railway Side-Service Setup

The recorder code is committed (`scripts/universe_dip_recorder.py` +
`recorder_entrypoint.py`). Railway CLI can't attach an external GitHub
repo to a new service, so the steps below use the dashboard.

## One-time setup (3-5 min)

1. **Open the Railway dashboard** for the `gracious-inspiration` project:
   ```
   railway open
   ```
   (or visit https://railway.com/project/bccd4d7c-a0de-4069-bc1d-da0e1ffb3548)

2. **Add a new service**: click `+ New` → `GitHub Repo` →
   select `jcoleman-droid/multichain-bot`.

3. **Name the service** `universe-recorder`.

4. **Override the start command**: open the new service's `Settings` tab →
   `Deploy` section → `Custom Start Command`:
   ```
   python recorder_entrypoint.py
   ```

5. **Mount the persistent volume** (same one the trading bot uses, so the
   data survives deploys):
   - `Settings` → `Volumes` → `Mount Volume`
   - Mount path: `/data`
   - If a `/data` volume already exists in the project, attach it; else
     create new with size 1 GB.

6. **Set env vars** (`Variables` tab):
   ```
   RECORDER_DATA_DIR = /data/universe_recorder
   RECORDER_CYCLE_S = 120
   RECORDER_OUTCOME_MIN = 30
   ```

7. **Deploy**: click `Deploy` (or push any commit to master).

## Verify it's working

```bash
railway service                            # link to universe-recorder
railway logs --tail 50
```

Look for lines like:
```
[INFO] Data dir: /data/universe_recorder
[INFO] Universe: 168 unique addresses (68 tokens, 100 pairs)
[INFO] DIP BABYBURNIE pair=ChtuDkh4 body=+1.9% cum5m=-8.4% pc_h6=106 ...
[INFO] Cycle done: 7 new dips detected, 73 skipped, 7 pending outcomes
```

## How to pull the data later

Once enough events accumulate (target: 24-48h for ~1000+ events):

```bash
# From local repo, with railway linked to universe-recorder service:
railway ssh "cat /data/universe_recorder/events.jsonl" > .universe_recorder/events.jsonl

# Or download via railway run + python helper.
```

Then run the analyzer:
```bash
python scripts/analyze_universe_events.py
```

## Cost estimate

The recorder is light: ~200 HTTP requests / 2min cycle = ~600/hour.
A Railway nano service runs about **$5-10/month**. Pause/delete the
service from the dashboard once you have enough data and want to stop.

## Architecture notes

- Standalone process — does NOT share state with the trading bot
- Uses the same `feeds/dexscreener_client.py` code for 1m candles
- Resumes cleanly on restart (persists `pending.json` + `seen_events.txt`)
- Writes one JSON line per resolved event to `events.jsonl` (append-only,
  safe to read while recorder runs)
