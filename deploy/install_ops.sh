#!/bin/bash
# Install / refresh the SEAN-ALGO ops config from this repo onto the VPS.
# Idempotent: only copies files that differ, then daemon-reload + nginx reload.
# It NEVER restarts the trading units — after changing service code run
#   systemctl restart <unit>   for the owning unit only.
# Usage (as root on the VPS, from a checkout or copy of the repo):  deploy/install_ops.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
APP=/opt/sean0algo
changed=0
put() { # put <src> <dst> <mode>
  if ! cmp -s "$1" "$2" 2>/dev/null; then install -D -m "$3" "$1" "$2"; echo "updated $2"; changed=1; fi
}
for u in "$HERE"/systemd/*.service "$HERE"/systemd/*.timer; do put "$u" "/etc/systemd/system/$(basename "$u")" 644; done
for d in "$HERE"/systemd/*.service.d; do put "$d/10-robust.conf" "/etc/systemd/system/$(basename "$d")/10-robust.conf" 644; done
put "$HERE/cron.d/sean0algo-backup" /etc/cron.d/sean0algo-backup 644
put "$HERE/logrotate.d/sean0algo" /etc/logrotate.d/sean0algo 644
put "$HERE/needrestart/sean0algo.conf" /etc/needrestart/conf.d/sean0algo.conf 644
put "$HERE/nginx/sean-algo" /etc/nginx/sites-available/sean-algo 644
# enable the site only if nothing in sites-enabled already points at it (a second link = duplicate default_server)
ls -l /etc/nginx/sites-enabled/ 2>/dev/null | grep -q "sites-available/sean-algo" || ln -s /etc/nginx/sites-available/sean-algo /etc/nginx/sites-enabled/sean-algo
for s in "$HERE"/ops/*.sh; do put "$s" "$APP/ops/$(basename "$s")" 755; done
systemctl daemon-reload
systemctl enable sean0algo sean-algo vwap-st btc-rsi-ema crypto-rsi-ema@eth crypto-rsi-ema@sol macd-gold signal-resolver sean-healthcheck.timer >/dev/null 2>&1 || true
systemctl start sean-healthcheck.timer >/dev/null 2>&1 || true
nginx -t && systemctl reload nginx
echo "install_ops done (changed=$changed)"
