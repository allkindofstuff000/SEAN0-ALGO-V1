#!/bin/bash
# ── SEAN0-ALGO VPS Startup Script ────────────────────────────────────────────
# Run this once on your VPS to install deps and start the dashboard.
# Usage:  bash start_server.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== SEAN0-ALGO Dashboard ==="
echo "Working dir: $SCRIPT_DIR"

# ── 1. Install / upgrade dependencies ───────────────────────────────────────
echo ""
echo "[1] Installing Python dependencies..."
pip install -r requirements.txt --quiet

# ── 2. Create logs dir if missing ───────────────────────────────────────────
mkdir -p logs

# ── 3. Check .env exists ─────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "WARNING: .env file not found. Copy your .env before starting the bot."
fi

# ── 4. Start the web server ──────────────────────────────────────────────────
PORT="${PORT:-8000}"
echo ""
echo "[2] Starting dashboard on port $PORT..."
echo "    Open: http://$(hostname -I | awk '{print $1}'):$PORT"
echo ""

exec python web_server.py
