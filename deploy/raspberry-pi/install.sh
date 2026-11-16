#!/usr/bin/env bash
# Install or upgrade Fantasy MTG as a systemd service on Raspberry Pi OS (or any
# systemd-based Debian). Safe to re-run: run it again after `git pull` to upgrade.
#
#   sudo ./deploy/raspberry-pi/install.sh
set -euo pipefail

APP_DIR=/opt/fantasymtg
STATE_DIR=/var/lib/fantasymtg
ENV_DIR=/etc/fantasymtg
ENV_FILE="$ENV_DIR/fantasymtg.env"
SERVICE_USER=fantasymtg
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UNIT_SRC="$REPO_DIR/deploy/raspberry-pi"

if [[ $EUID -ne 0 ]]; then
    echo "Please run with sudo: sudo $0" >&2
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python 3.10 or newer is required (found $(python3 --version 2>&1))." >&2
    echo "Raspberry Pi OS Bookworm or newer ships a suitable version." >&2
    exit 1
fi

if ! python3 -c 'import ensurepip' 2>/dev/null; then
    echo "==> Installing python3-venv"
    apt-get update
    apt-get install -y python3-venv
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "==> Creating system user $SERVICE_USER"
    useradd --system --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Copying application to $APP_DIR"
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/src" "$APP_DIR/build"
cp -r "$REPO_DIR/src" "$REPO_DIR/pyproject.toml" "$REPO_DIR/README.md" "$APP_DIR/"

echo "==> Installing Python packages (first run can take a few minutes on a Pi 2)"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet --upgrade "$APP_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "==> Creating $ENV_FILE"
    mkdir -p "$ENV_DIR"
    sed "s|^FANTASYMTG_DB_PATH=.*|FANTASYMTG_DB_PATH=$STATE_DIR/fantasymtg.db|" \
        "$REPO_DIR/.env.example" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

echo "==> Installing systemd units"
install -m 644 "$UNIT_SRC/fantasymtg.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/fantasymtg-backup.service" /etc/systemd/system/
install -m 644 "$UNIT_SRC/fantasymtg-backup.timer" /etc/systemd/system/
systemctl daemon-reload

# Make time-sync.target mean "the clock really is synced", so the bot waits for it.
if systemctl cat systemd-time-wait-sync.service >/dev/null 2>&1; then
    systemctl enable systemd-time-wait-sync.service
else
    echo "WARNING: systemd-time-wait-sync.service not found. The bot may start before the" >&2
    echo "         clock is synced after a reboot. Is systemd-timesyncd installed?" >&2
fi

systemctl enable --now fantasymtg-backup.timer

if grep -qE '^DISCORD_TOKEN=.+' "$ENV_FILE"; then
    systemctl enable fantasymtg.service
    systemctl restart fantasymtg.service
    echo
    echo "Fantasy MTG is running. Check it with:  systemctl status fantasymtg"
    echo "Follow its logs with:                  journalctl -u fantasymtg -f"
else
    systemctl enable fantasymtg.service
    echo
    echo "Almost done! Add your bot token to $ENV_FILE:"
    echo "    sudo nano $ENV_FILE"
    echo "then start the bot with:"
    echo "    sudo systemctl start fantasymtg"
fi
