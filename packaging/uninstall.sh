#!/bin/sh
set -e
PREFIX="${HOME}/.local"
APP_DIR="${PREFIX}/share/felisros"
pkill -f '[a]pp.py --no-browser' 2>/dev/null || true
if [ -d "$APP_DIR" ]; then
  rm -r "$APP_DIR"
fi
rm -f "${PREFIX}/bin/felisros" "${PREFIX}/share/applications/felisros.desktop"
rm -f "${HOME}/.config/systemd/user/felisros.service"
crontab -l 2>/dev/null | grep -v 'felisros --no-browser' | crontab - 2>/dev/null || true
RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -d "$RUNTIME" ]; then
  XDG_RUNTIME_DIR="$RUNTIME" systemctl --user daemon-reload >/dev/null 2>&1 || true
fi
echo "felisros 已卸载"
echo "导入的版本清单保留在 ${XDG_CONFIG_HOME:-$HOME/.config}/felisros/versions.json，需要时请手动删除"
