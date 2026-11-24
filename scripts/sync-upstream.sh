#!/bin/sh
# Sync this fork with the original project (upstream = cibernicola/tdt-local) and push it.
#
#   scripts/sync-upstream.sh            merge upstream/main into plugins-navegador, test, push the branch
#   scripts/sync-upstream.sh main       also merge the result into this fork's main (where the releases live) and push it
#
# The merge is a merge, never a rebase: the branch is already on origin. If there are conflicts, they are
# expected only in README/docs (upstream rewrites them); desktop/ is not touched by this fork, so it must
# never conflict. Tests must be green before anything is pushed.
set -eu

cd "$(dirname "$0")/.."
BRANCH=plugins-navegador

git fetch upstream
git checkout "$BRANCH"
git merge --no-ff upstream/main -m "Merge upstream/main into $BRANCH (sync del proyecto original)"

python -m unittest discover -s tests -t .
node --test

git push origin "$BRANCH"

if [ "${1:-}" = "main" ]; then
  git checkout main
  git merge --no-ff "$BRANCH" -m "Release del fork navegador-only en main: $BRANCH"
  git push origin main
  echo "main actualizado en origin: las releases de este fork salen de ahí (tag sugerido: v$(python -c 'from tdtlocal import __version__; print(__version__)')-browser.1)"
fi

echo "OK: $BRANCH sincronizada con upstream y subida a origin."
