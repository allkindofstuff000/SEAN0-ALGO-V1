#!/bin/bash
# SEAN-ALGO backup — Mongo (live_signals = the entire live track record, backtest_reports,
# candles, bot_state) + secrets/state/unit files. On-box, 14-day retention. 4x daily via cron.
set -uo pipefail; umask 077
TS=$(date -u +%Y%m%dT%H%M%SZ); DIR=/var/backups/sean0algo; mkdir -p "$DIR"
if mongodump --quiet --host 127.0.0.1 --db sean0_algo --gzip --archive="$DIR/mongo_$TS.archive.gz"; then
  m="mongo ok"; else m="MONGODUMP FAILED"; fi
tar --ignore-failed-read -czf "$DIR/config_$TS.tgz" -C / \
  opt/sean0algo/.env opt/sean0algo/state.json opt/sean0algo/risk_state.json \
  opt/sean0algo/state_btc_rsi_ema.txt opt/sean0algo/state_macd_gold.txt opt/sean0algo/state_vwap_st.txt \
  etc/systemd/system/sean0algo.service etc/systemd/system/sean-algo.service etc/systemd/system/vwap-st.service \
  etc/systemd/system/btc-rsi-ema.service etc/systemd/system/macd-gold.service etc/systemd/system/signal-resolver.service \
  etc/systemd/system/sean0algo.service.d etc/systemd/system/sean-algo.service.d etc/systemd/system/vwap-st.service.d \
  etc/systemd/system/btc-rsi-ema.service.d etc/systemd/system/macd-gold.service.d etc/systemd/system/signal-resolver.service.d \
  etc/systemd/system/alert-telegram@.service etc/systemd/system/sean-healthcheck.service etc/systemd/system/sean-healthcheck.timer \
  etc/systemd/system/crypto-rsi-ema@.service etc/systemd/system/crypto-rsi-ema@.service.d \
  opt/sean0algo/state_eth_rsi_ema.txt opt/sean0algo/state_sol_rsi_ema.txt \
  etc/cron.d/sean0algo-backup etc/needrestart/conf.d/sean0algo.conf opt/sean0algo/ops \
  etc/nginx/sites-available/sean-algo etc/logrotate.d/sean0algo 2>/dev/null
find "$DIR" -type f -mtime +14 -delete
echo "$(date -u +%FT%TZ) backup: $m; $(ls -1 "$DIR" | wc -l) files, $(du -sh "$DIR" | cut -f1) on-box"
# OFF-BOX COPY: no target configured yet — add e.g.:  rclone copy "$DIR" <remote>:sean0algo-backups --min-age 1m
