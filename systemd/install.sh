#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

mkdir -p "$SYSTEMD_USER_DIR"

# Generate service file with absolute paths resolved from the project directory
cat > "$SYSTEMD_USER_DIR/docker-sentinel.service" << EOF
[Unit]
Description=docker-sentinel container health checker
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/.venv/bin/python -m checker.check
StandardOutput=journal
StandardError=journal
EOF

cp "${PROJECT_DIR}/systemd/docker-sentinel.timer" \
   "$SYSTEMD_USER_DIR/docker-sentinel.timer"

systemctl --user daemon-reload
systemctl --user enable --now docker-sentinel.timer

echo ""
echo "Installed to: ${SYSTEMD_USER_DIR}"
echo ""
systemctl --user status docker-sentinel.timer --no-pager
