#!/bin/sh
set -e
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PREFIX="${HOME}/.local"
APP_DIR="${PREFIX}/share/felisros/app"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "${PREFIX}/bin" "${PREFIX}/share/applications"
cp -R "$BASE/app/." "$APP_DIR/"
cp "$BASE/bin/felisros" "${PREFIX}/bin/felisros"
chmod 755 "${PREFIX}/bin/felisros"
sed "s|@PREFIX@|${PREFIX}|g" "$BASE/felisros.desktop" > "${PREFIX}/share/applications/felisros.desktop"
echo "felisros 已安装到 ${PREFIX}"
case ":${PATH}:" in
  *":${PREFIX}/bin:"*) ;;
  *) echo "请把 ${PREFIX}/bin 加入 PATH: export PATH=\"${PREFIX}/bin:\$PATH\"" ;;
esac
