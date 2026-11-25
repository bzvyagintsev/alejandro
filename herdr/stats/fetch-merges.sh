#!/bin/sh
# My merged PRs per day for the current month. Saves ~/.cache/herdr-stats/merges.json
# (atomic, mode 600) and prints it. The token is never stored: the GUI app doesn't
# inherit shell env, so a login shell is asked for it.
export GH_TOKEN="${GH_TOKEN:-$(zsh -lic 'printf "%s" "$GH_TOKEN"' 2>/dev/null | tail -n 1)}"
[ -n "$GH_TOKEN" ] || { echo "GH_TOKEN not found in your login shell" >&2; exit 1; }
GH=$(command -v gh || ls /opt/homebrew/bin/gh /usr/local/bin/gh 2>/dev/null | head -n 1)
[ -n "$GH" ] || { echo "gh not found" >&2; exit 1; }
DIR="$HOME/.cache/herdr-stats"
mkdir -p "$DIR" && chmod 700 "$DIR"
RAW=$(mktemp "$DIR/raw.XXXXXX") && TMP=$(mktemp "$DIR/out.XXXXXX") || exit 1
trap 'rm -f "$RAW" "$TMP"' EXIT
# One day of slack before the 1st: dates are UTC in the search, local below.
SINCE=$(python3 -I -c "import datetime as d;t=d.date.today().replace(day=1)-d.timedelta(days=1);print(t)")
"$GH" search prs --author @me --merged --merged-at ">=$SINCE" --limit 500 --json closedAt,repository,number >"$RAW" || exit 1
python3 -I - "$RAW" >"$TMP" <<'PY' || exit 1
import json, sys, calendar, datetime as dt
prs = json.load(open(sys.argv[1]))
now = dt.datetime.now().astimezone()
days = [0] * calendar.monthrange(now.year, now.month)[1]
for p in prs:
    t = dt.datetime.fromisoformat(p["closedAt"].replace("Z", "+00:00")).astimezone()
    if (t.year, t.month) == (now.year, now.month):
        days[t.day - 1] += 1
print(json.dumps({
    "fetched_at": now.isoformat(), "year": now.year, "month": now.month,
    "today": now.day, "days": days, "total": sum(days),
}))
PY
chmod 600 "$TMP" && mv "$TMP" "$DIR/merges.json" && cat "$DIR/merges.json"
