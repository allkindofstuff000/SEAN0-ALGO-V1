"""Deploy SEAN0-ALGO bot to VPS via SSH/SFTP using paramiko."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    os.system(f"{sys.executable} -m pip install paramiko -q")
    import paramiko

# ── VPS config ────────────────────────────────────────────────────────────────
VPS_HOST = "187.77.191.182"
VPS_USER = "root"
VPS_PASS = "Megaboostadmin1@"
VPS_PORT = 22
REMOTE_DIR = "/opt/quotex_bot"
VENV_PY = f"{REMOTE_DIR}/.venv/bin/python"
VENV_PIP = f"{REMOTE_DIR}/.venv/bin/pip"

LOCAL_ROOT = Path(__file__).resolve().parent

# (local_relative_path, remote_relative_path)
DEPLOY_FILES: list[tuple[str, str]] = [
    # core package
    ("core/__init__.py",               "core/__init__.py"),
    ("core/signal_logic.py",           "core/signal_logic.py"),
    ("core/indicator_engine.py",       "core/indicator_engine.py"),
    ("core/market_regime_engine.py",   "core/market_regime_engine.py"),
    ("core/trade_filters.py",          "core/trade_filters.py"),
    ("core/risk_manager.py",           "core/risk_manager.py"),
    ("core/decision_logger.py",        "core/decision_logger.py"),
    ("core/data_fetcher.py",           "core/data_fetcher.py"),
    ("core/telegram_bot.py",           "core/telegram_bot.py"),
    ("core/telegram_interface.py",     "core/telegram_interface.py"),
    ("core/mongo_store.py",            "core/mongo_store.py"),
    ("core/candle_engine.py",          "core/candle_engine.py"),
    # backtests package
    ("backtests/__init__.py",          "backtests/__init__.py"),
    ("backtests/backtest_forex_engine.py", "backtests/backtest_forex_engine.py"),
    ("backtests/backtest_dashboard.py",    "backtests/backtest_dashboard.py"),
    # web package
    ("web/__init__.py",                "web/__init__.py"),
    ("web/web_server.py",              "web/web_server.py"),
    ("web/dashboard.py",               "web/dashboard.py"),
    ("web/static/index.html",          "web/static/index.html"),
    # market_regime package
    ("market_regime/__init__.py",      "market_regime/__init__.py"),
    ("market_regime/regime_detector.py", "market_regime/regime_detector.py"),
    # root entry points & config
    ("main.py",                        "main.py"),
    ("requirements.txt",               "requirements.txt"),
]


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=VPS_HOST,
        port=VPS_PORT,
        username=VPS_USER,
        password=VPS_PASS,
        timeout=30,
    )
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 30) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(f"  >> {out}")
    if err and "WARNING" not in err and "notice" not in err.lower():
        print(f"  !! {err[:300]}")
    return out


def upload_files(client: paramiko.SSHClient) -> None:
    sftp = client.open_sftp()
    # Ensure all remote dirs exist
    for d in ["core", "backtests", "web", "web/static", "market_regime", "logs", "research_engine"]:
        run(client, f"mkdir -p {REMOTE_DIR}/{d}")

    uploaded = skipped = 0
    for local_rel, remote_rel in DEPLOY_FILES:
        local_path = LOCAL_ROOT / local_rel
        if not local_path.exists():
            print(f"  [SKIP] {local_rel}")
            skipped += 1
            continue
        remote_path = f"{REMOTE_DIR}/{remote_rel}"
        sftp.put(str(local_path), remote_path)
        print(f"  [OK]   {local_rel}")
        uploaded += 1

    sftp.close()
    print(f"\n  Uploaded: {uploaded}  Skipped: {skipped}")


def main() -> None:
    print(f"Connecting to {VPS_USER}@{VPS_HOST}:{VPS_PORT} ...")
    client = connect()
    print("Connected.\n")

    print("=== Uploading files ===")
    upload_files(client)

    print("\n=== Restarting services via systemctl ===")
    run(client, "systemctl restart quotex-dashboard", timeout=20)
    time.sleep(3)
    run(client, "systemctl status quotex-dashboard --no-pager -l | tail -15", timeout=10)

    print("\n=== Verify web server ===")
    run(client, "curl -s -o /dev/null -w '%{http_code}' http://localhost:8010/ || true", timeout=10)

    print("\n=== Deploy complete ===")
    client.close()


if __name__ == "__main__":
    main()
