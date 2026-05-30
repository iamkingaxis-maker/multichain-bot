#!/bin/bash
# Safe deploy — smoke test → deploy → health check
# Usage: bash scripts/deploy.sh

set -e

echo ""
echo "══════════════════════════════════════════════════════"
echo "  MULTICHAIN BOT — SAFE DEPLOY"
echo "══════════════════════════════════════════════════════"

# ── Step 1: Smoke test ────────────────────────────────────
echo ""
echo "Step 1/3  Running smoke test..."
python scripts/smoke_test.py
if [ $? -ne 0 ]; then
    echo ""
    echo "❌  Smoke test failed — deploy aborted."
    echo "    Fix the failures above, then re-run: bash scripts/deploy.sh"
    exit 1
fi

# ── Step 2: Deploy ────────────────────────────────────────
echo ""
echo "Step 2/3  Deploying to Railway..."
MSYS_NO_PATHCONV=1 railway up --detach

# ── Step 3: Post-deploy health check ─────────────────────
echo ""
echo "Step 3/3  Health check (waiting 3 min for deploy to settle)..."
python scripts/post_deploy_check.py --wait 180

echo ""
echo "══════════════════════════════════════════════════════"
