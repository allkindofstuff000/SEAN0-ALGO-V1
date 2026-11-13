#!/bin/bash
# Bootstrap SEAN-ALGO on a fresh Ubuntu VPS from a release tarball. Idempotent: safe to re-run.
#
# Build the tarball on the dev machine (repo root):
#   git archive HEAD | tar -x -C <stage>; cp -r frontend/dist/. <stage>/web/static/; tar -czf sean_release.tar.gz -C <stage> .
# Usage (as root on the VPS):
#   bootstrap_new_vps.sh /root/sean_release.tar.gz [/root/sean.env]
#
# What it does: base packages, MongoDB (only if missing), Python 3.12 venv (via uv when the
# distro Python is not 3.12 - the pinned numpy/pandas/numba wheels need 3.12), app tree in
# /opt/sean0algo, ops config (deploy/install_ops.sh), then starts the web server and the bots.
# It never touches other apps on the box: nginx is only reloaded, mongod is left as it is.
set -euo pipefail
REL=${1:?usage: bootstrap_new_vps.sh <release.tar.gz> [env file]}
ENVF=${2:-}
APP=/opt/sean0algo
PYVER=3.12
BOTS="sean-algo vwap-st macd-gold btc-rsi-ema crypto-rsi-ema@eth crypto-rsi-ema@sol nyorb@eth signal-resolver"
export DEBIAN_FRONTEND=noninteractive
export PATH="/root/.local/bin:$PATH"
export UV_PYTHON_INSTALL_DIR=/opt/python
say() { echo "[bootstrap $(date -u +%H:%M:%S)] $*"; }

[[ -f $REL ]] || { echo "release tarball not found: $REL" >&2; exit 2; }
touch /run/sean-healthcheck.mute            # no healthcheck pages while we build

say "packages"
apt-get update -qq
apt-get install -y -qq --no-upgrade nginx curl ca-certificates cron logrotate gnupg >/dev/null

if ! command -v mongod >/dev/null; then
  say "installing MongoDB 8.0"
  . /etc/os-release
  code=$VERSION_CODENAME; case "$code" in jammy|noble) ;; *) code=noble ;; esac
  curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | gpg --dearmor --yes -o /usr/share/keyrings/mongodb-server-8.0.gpg
  echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu $code/mongodb-org/8.0 multiverse" \
    > /etc/apt/sources.list.d/mongodb-org-8.0.list
  apt-get update -qq && apt-get install -y -qq mongodb-org >/dev/null
fi
systemctl enable --now mongod >/dev/null 2>&1 || true
command -v mongodump >/dev/null || say "WARNING: mongodump missing - ops/backup.sh will fail (install mongodb-database-tools)"

say "app tree -> $APP"
mkdir -p "$APP"
tar -xzf "$REL" -C "$APP"
mkdir -p "$APP/logs/heartbeat" "$APP/ops"
if [[ -n $ENVF ]]; then
  [[ -f $APP/.env ]] && cp -p "$APP/.env" "$APP/.env.bak_$(date -u +%Y%m%dT%H%M%S)"
  install -m 600 "$ENVF" "$APP/.env"
fi
[[ -f $APP/.env ]] || { echo "no $APP/.env - pass an env file as the 2nd argument" >&2; exit 2; }
chmod 600 "$APP/.env"

is_py() { "$1" -c "import sys; sys.exit(0 if '%d.%d' % sys.version_info[:2] == '$PYVER' else 1)" 2>/dev/null; }
if ! { [[ -x $APP/.venv/bin/python ]] && is_py "$APP/.venv/bin/python"; }; then
  say "python $PYVER venv"
  rm -rf "$APP/.venv"
  if is_py python3 && python3 -m venv "$APP/.venv" 2>/dev/null; then
    :
  else
    if ! command -v uv >/dev/null; then
      apt-get install -y -qq --no-upgrade pipx >/dev/null
      pipx install uv >/dev/null
    fi
    uv python install "$PYVER" -q
    uv venv --seed --python "$PYVER" "$APP/.venv" -q
  fi
fi
say "python deps ($("$APP/.venv/bin/python" --version))"
if command -v uv >/dev/null; then
  uv pip install -q --python "$APP/.venv/bin/python" -r "$APP/requirements.txt"
else
  "$APP/.venv/bin/python" -m pip install -q --upgrade pip
  "$APP/.venv/bin/python" -m pip install -q -r "$APP/requirements.txt"
fi

say "import check"
cd "$APP"
.venv/bin/python -m py_compile main.py vwap_st_live.py macd_gold_live.py btc_rsi_ema_live.py nyorb_live.py resolve_outcomes.py web/web_server.py
PYTHONPATH="$APP" .venv/bin/python - <<'PY'
import importlib
mods = ("core.heartbeat", "core.bot_state_file", "core.mongo_store", "core.btc_fetcher", "core.data_fetcher",
        "core.market_calendar", "core.telegram_bot", "core.signal_logic", "core.candle_engine",
        "strategies.nyorb.engine", "strategies.vwap_supertrend.config", "backtests.backtest_forex_engine",
        "fastapi", "uvicorn", "pymongo", "pandas_ta", "ccxt")
for m in mods:
    importlib.import_module(m)
print("imports ok:", len(mods))
PY

say "ops config"
# the nginx site uses $connection_upgrade - define the map once if nothing on the box does
grep -rqs 'connection_upgrade' /etc/nginx/nginx.conf /etc/nginx/conf.d/ 2>/dev/null || \
  printf 'map $http_upgrade $connection_upgrade { default upgrade; "" close; }\n' > /etc/nginx/conf.d/websocket_upgrade.conf
touch /run/sean-healthcheck.mute
bash "$APP/deploy/install_ops.sh"

say "starting web server"
systemctl restart sean0algo
ok=0
for _ in $(seq 1 30); do
  if curl -fsS -m 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ $ok == 1 ]] || { journalctl -u sean0algo -n 40 --no-pager; echo "web server did not come up" >&2; exit 1; }

say "starting bots"
touch /run/sean-healthcheck.mute
for u in $BOTS; do systemctl restart "$u"; done
sleep 8
for u in sean0algo $BOTS mongod nginx; do printf '  %-24s %s\n' "$u" "$(systemctl is-active "$u")"; done
say "done"
