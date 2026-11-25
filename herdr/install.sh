#!/bin/sh
# Installs the Stats page (herdr/stats) into the Herdr desktop app as the mod `stats`, and records where the ale CLI is.
# It will not overwrite a `stats` mod that differs from this one: pass --force to replace it (the old one is kept in the app folder as stats.bak-<time>).
# Needs: the Herdr desktop app; gh with a GH_TOKEN in your login shell (the GitHub cards); ccusage (`npm i -g ccusage`) for prices.
set -e
FW=$(cd "$(dirname "$0")/.." && pwd)
APP="${HERDR_APP_HOME:-$HOME/.config/herdr-app}"
DEST="$APP/mods/stats"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
[ -d "$APP" ] || { echo "Herdr app folder not found: $APP (open the app once, or set HERDR_APP_HOME)" >&2; exit 1; }
command -v ccusage >/dev/null 2>&1 || zsh -lic 'command -v ccusage' >/dev/null 2>&1 || echo "warning: ccusage not found: costs will be missing (npm i -g ccusage)" >&2
if [ -d "$DEST" ]; then
  # compare only the files this repo ships (the app adds its own .d.ts and jsconfig.json)
  DIFF=0
  for f in "$FW"/herdr/stats/*; do
    cmp -s "$f" "$DEST/$(basename "$f")" || DIFF=1
  done
  if [ "$DIFF" = 1 ] && [ "$FORCE" = 0 ]; then
    echo "A different stats mod is already installed at $DEST." >&2
    echo "See what differs: diff -r $FW/herdr/stats $DEST     Replace it: sh $0 --force (your copy is kept next to mods/ as stats.bak-<time>)" >&2
    exit 1
  fi
  if [ "$DIFF" = 1 ]; then
    mv "$DEST" "$APP/stats.bak-$(date +%Y%m%d%H%M%S)"  # outside mods/, or Herdr would load it as a mod
  fi
fi
mkdir -p "$DEST"
cp "$FW"/herdr/stats/* "$DEST/"
chmod 755 "$DEST"/fetch-*.sh
printf '%s\n' "$FW/bin/ale" >"$DEST/ale-path"
herdr-app check "$DEST" 2>&1 | tail -2 || true
echo "installed: $DEST"
echo "open it:   herdr-app do mod.open --name stats   (run it again to close)"
