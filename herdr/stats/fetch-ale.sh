#!/bin/sh
# Prints what each ale task cost, as JSON, for the Stats page's Ale card: `ale cost --all --stats`.
# Cached in ~/.cache/herdr-stats/ale.json (mode 600). install.sh writes the path of the ale CLI into the file `ale-path` next to this script.
DIR="$HOME/.cache/herdr-stats"
mkdir -p "$DIR" && chmod 700 "$DIR"
HERE=$(cd "$(dirname "$0")" && pwd)
ALE=$(cat "$HERE/ale-path" 2>/dev/null)
[ -n "$ALE" ] || ALE="$HOME/projects/agentic-framework/bin/ale"
[ -f "$ALE" ] || { echo "ale not found at $ALE: run herdr/install.sh again" >&2; exit 1; }
# ale prices tasks with ccusage (a node script): a login shell has node on PATH, the desktop app does not.
OUT=$(zsh -lic "python3 '$ALE' cost --all --stats 2>/dev/null" 2>/dev/null)
# Validate, then save atomically (mode 600) and print.
printf '%s' "$OUT" | python3 -I -c '
import json, os, sys
raw = sys.stdin.read()
d = json.loads(raw[raw.index("{"):])
path = os.path.join(sys.argv[1], "ale.json")
tmp = path + ".tmp.%d" % os.getpid()
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(d, f)
os.replace(tmp, path)
print(json.dumps(d))
' "$DIR"
