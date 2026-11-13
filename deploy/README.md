# deploy/ — VPS ops config (source of truth)

Everything the production VPS (`/opt/sean0algo` on 179.66.144.239) needs besides the
Python code and the frontend bundle. Until 2026-10-05 these files existed only on the
VPS and were not in the backups; a VPS loss would have meant rebuilding them from memory.

| repo path | installed at | purpose |
|---|---|---|
| `systemd/<unit>.service` | `/etc/systemd/system/` | the 6 app units + `alert-telegram@` + `sean-healthcheck` service/timer |
| `systemd/<unit>.service.d/10-robust.conf` | `/etc/systemd/system/<unit>.service.d/` | Restart=always, OnFailure Telegram alert, stop timeouts |
| `cron.d/sean0algo-backup` | `/etc/cron.d/` | runs `ops/backup.sh` 4x/day (03/09/15/21:15 UTC) |
| `logrotate.d/sean0algo` | `/etc/logrotate.d/` | daily rotation of the bot logs |
| `needrestart/sean0algo.conf` | `/etc/needrestart/conf.d/` | stops apt from auto-restarting the trading units |
| `nginx/sean-algo` | `/etc/nginx/sites-available/` (+ symlink in sites-enabled) | :8080 -> 127.0.0.1:8000, SSE-safe (no buffering, long timeouts); :80 belongs to the co-tenant SEANBOOST site |
| `ops/backup.sh` | `/opt/sean0algo/ops/` | mongodump + config tar into `/var/backups/sean0algo` (14-day retention) |
| `ops/healthcheck.sh` | `/opt/sean0algo/ops/` | every 5 min: units, `/api/health`, price freshness, disk; Telegram alert |

Apply with `deploy/install_ops.sh` (as root on the VPS). It copies only files that differ,
reloads systemd and nginx, and never restarts the trading units.

New box from scratch: `deploy/bootstrap_new_vps.sh <release.tar.gz> <env file>` (packages, Python 3.12 venv,
app tree, this ops config, start everything). Used for the 2026-11-14 move after the old VPS was lost.

Still TODO: an off-box copy of `/var/backups/sean0algo` (see the last line of `ops/backup.sh`).
