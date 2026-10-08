#!/bin/bash
# Every 5 min: units up, /api/health ok, live price fresh while the market is open, disk < 85%.
# Telegram alert at most once per 30 min.
set -u; . /opt/sean0algo/.env; fail=()
for u in sean0algo sean-algo vwap-st btc-rsi-ema macd-gold signal-resolver mongod nginx; do systemctl is-active --quiet "$u" || fail+=("$u down"); done
h=$(curl -fsS -m 10 http://127.0.0.1:8000/api/health 2>/dev/null) || { h='{}'; fail+=("health unreachable"); }
status=$(printf '%s' "$h" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("status",""), d.get("market_open",""))' 2>/dev/null)
[[ "${status%% *}" == ok ]] || fail+=("health=${status:-none}")
mo=${status##* }
pt=$(curl -fsS -m 10 http://127.0.0.1:8000/api/live/price 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("time") or 0)' 2>/dev/null || echo 0)
age=$(( $(date +%s) - pt ))
# Grace after a reopen (daily settlement break / Sunday): the newest tick is legitimately old until
# the stream catches up. The API reports seconds since the DST-aware New-York reopen — use the smaller.
sso=$(printf '%s' "$h" | python3 -c 'import sys,json; v=json.load(sys.stdin).get("seconds_since_open"); print(int(v) if v is not None else -1)' 2>/dev/null || echo -1)
(( sso >= 0 && sso < age )) && age=$sso
[[ "$mo" == True && $age -gt 300 ]] && fail+=("price stale ${age}s")
use=$(df --output=pcent / | tail -1 | tr -dc 0-9); (( use > 85 )) && fail+=("disk ${use}%")
if ((${#fail[@]})); then
  f=/run/sean-healthcheck.last
  if [[ ! -f $f || $(( $(date +%s) - $(stat -c %Y "$f") )) -gt 1800 ]]; then touch "$f"
    curl -fsS -m 10 -o /dev/null "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage" --data-urlencode chat_id="$TELEGRAM_CHAT_ID" --data-urlencode text="SEAN VPS healthcheck FAILED: ${fail[*]}"
  fi
  echo "FAIL: ${fail[*]}"; exit 1
fi
echo "ok"
