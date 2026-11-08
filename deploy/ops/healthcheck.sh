#!/bin/bash
# Every 5 min: units up (unless deliberately stopped from the dashboard), /api/health ok,
# live gold price fresh while the market is open, every bot's heartbeat fresh (a unit can
# be `active` yet blind), no NEW automatic restarts (a slow crash loop never trips
# is-active), disk < 85%. Telegram alert at most once per 30 min — and the cooldown only
# starts when the alert actually went out.
set -u; . /opt/sean0algo/.env; fail=()
UNITS="sean0algo sean-algo vwap-st btc-rsi-ema crypto-rsi-ema@eth crypto-rsi-ema@sol nyorb@eth macd-gold signal-resolver mongod nginx"

# Deploy mute: deploy scripts touch this file; a run inside the next 5 min is skipped
# (the 2026-10-09 00:15 "sean0algo down / health unreachable" page was a deploy restart).
m=/run/sean-healthcheck.mute
if [[ -f $m && $(( $(date +%s) - $(stat -c %Y "$m") )) -lt 300 ]]; then echo "muted (deploy in progress)"; exit 0; fi

# Restart grace: a unit that (re)started < 2 min ago is still warming up — wait, then check.
up_us=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
for u in $UNITS; do
  t=$(systemctl show -p ActiveEnterTimestampMonotonic --value "$u" 2>/dev/null); t=${t:-0}
  if [[ $t -gt 0 && $(( up_us - t )) -lt 120000000 ]]; then sleep 20; break; fi
done

h=$(curl -fsS -m 10 --retry 2 --retry-delay 3 --retry-connrefused http://127.0.0.1:8000/api/health 2>/dev/null) || { h='{}'; fail+=("health unreachable"); }
read -r status mo sso stopped stalled <<<"$(printf '%s' "$h" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
v = d.get("seconds_since_open")
print(d.get("status") or "none", str(d.get("market_open", "?")), int(v) if v is not None else -1,
      ",".join(d.get("stopped_units") or []) or "-", ",".join(d.get("bot_stalled") or []) or "-")' 2>/dev/null || echo "none ? -1 - -")"
status=${status:-none}; mo=${mo:-?}; sso=${sso:--1}; stopped=${stopped:--}; stalled=${stalled:--}

for u in $UNITS; do
  [[ ",$stopped," == *",$u,"* ]] && continue   # stopped on purpose from the dashboard
  systemctl is-active --quiet "$u" || fail+=("$u down")
done
[[ "$status" == ok ]] || fail+=("health=$status")
[[ "$stalled" != "-" ]] && fail+=("bot stalled (no good cycle): $stalled")

pt=$(curl -fsS -m 10 http://127.0.0.1:8000/api/live/price 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("time") or 0)' 2>/dev/null || echo 0)
age=$(( $(date +%s) - pt ))
# Grace after a reopen (daily settlement break / Sunday): the newest tick is legitimately old until
# the stream catches up. The API reports seconds since the DST-aware New-York reopen — use the smaller.
(( sso >= 0 && sso < age )) && age=$sso
[[ "$mo" == True && $age -gt 300 ]] && fail+=("price stale ${age}s")

# Automatic restarts: OnFailure never fires for a restart that succeeds, and a crash every
# > RestartSec seconds is `active` almost all the time. Alert whenever the count grows.
nf=/run/sean-healthcheck.nrestarts
seed=0; [[ -s $nf ]] || seed=1   # first run after boot/install: record the baseline, do not page
touch "$nf"
for u in $UNITS; do
  n=$(systemctl show -p NRestarts --value "$u" 2>/dev/null); n=${n:-0}
  p=$(grep -s "^$u=" "$nf" | cut -d= -f2); p=${p:-0}
  (( seed == 0 && n > p )) && fail+=("$u auto-restarted (NRestarts=$n)")
  sed -i "\|^$u=|d" "$nf"; echo "$u=$n" >> "$nf"
done

use=$(df --output=pcent / | tail -1 | tr -dc 0-9); (( use > 85 )) && fail+=("disk ${use}%")

if ((${#fail[@]})); then
  f=/run/sean-healthcheck.last
  if [[ ! -f $f || $(( $(date +%s) - $(stat -c %Y "$f") )) -gt 1800 ]]; then
    curl -fsS -m 15 -o /dev/null "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" \
      --data-urlencode chat_id="$TELEGRAM_CHAT_ID" --data-urlencode text="SEAN VPS healthcheck FAILED: ${fail[*]}" && touch "$f"
  fi
  echo "FAIL: ${fail[*]}"; exit 1
fi
echo "ok"
