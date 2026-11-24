#!/bin/sh
# Empaqueta una release de este fork como zip de FUENTE: a diferencia de la app de escritorio (PyInstaller +
# Electron, ver docs/DESKTOP.md), el modo navegador no necesita build — solo Python (biblioteca estándar) y un
# navegador. El zip sirve para distribuir el fork entero (con tests y docs) o para etiquetar una release.
#
#   scripts/pack-release.sh            -> dist/tdt-local-<versión>-browser.zip
#   scripts/pack-release.sh v1.5.0-browser.1   -> nombra el zip con esa etiqueta (si existe como tag)
set -eu

cd "$(dirname "$0")/.."
TAG="${1:-}"
VERSION=$(python -c 'from tdtlocal import __version__; print(__version__)')
if [ -n "$TAG" ]; then
  NAME="tdt-local-${TAG}"   # the tag already carries the suffix (v1.5.0-browser.1)
else
  if git describe --tags --match 'v*-browser*' >/dev/null 2>&1; then
    VERSION=$(git describe --tags --match 'v*-browser*')
  fi
  NAME="tdt-local-${VERSION}-browser"
fi
OUT="dist/${NAME}.zip"

mkdir -p dist
rm -f "$OUT"
# git archive only takes tracked files (no __pycache__, no data/, no .git): exactly what a release needs
git archive --format=zip --prefix="${NAME}/" -o "$OUT" HEAD
# git archive only takes tracked files (no __pycache__, no data/, no .git): exactly what a release needs
echo "Hecho: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Para usarlo: descomprime, entra en la carpeta y «python -m tdtlocal --open»."
