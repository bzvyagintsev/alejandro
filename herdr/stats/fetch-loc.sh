#!/bin/sh
# Lines added/deleted per month (last 12 months) across my merged PRs, all repos.
# Saves ~/.cache/herdr-stats/loc.json (atomic, mode 600) and prints it.
# The token is never stored: a login shell is asked for it.
export GH_TOKEN="${GH_TOKEN:-$(zsh -lic 'printf "%s" "$GH_TOKEN"' 2>/dev/null | tail -n 1)}"
[ -n "$GH_TOKEN" ] || { echo "GH_TOKEN not found in your login shell" >&2; exit 1; }
GH=$(command -v gh || ls /opt/homebrew/bin/gh /usr/local/bin/gh 2>/dev/null | head -n 1)
[ -n "$GH" ] || { echo "gh not found" >&2; exit 1; }
export GH_BIN="$GH"
DIR="$HOME/.cache/herdr-stats"
mkdir -p "$DIR" && chmod 700 "$DIR"
TMP=$(mktemp "$DIR/out.XXXXXX") || exit 1
trap 'rm -f "$TMP"' EXIT
python3 -I - >"$TMP" <<'PY' || exit 1
import json, os, subprocess, sys, datetime as dt
gh = os.environ["GH_BIN"]
def run(*a):
    r = subprocess.run([gh, *a], capture_output=True, text=True, timeout=60)
    if r.returncode: sys.exit(r.stderr.strip()[:300] or "gh failed")
    return r.stdout
login = run("api", "user", "-q", ".login").strip()
now = dt.datetime.now().astimezone()
# first day of the month 11 months back, minus one day of slack for UTC vs local
y, m = now.year, now.month - 11
while m < 1: m += 12; y -= 1
since = (dt.date(y, m, 1) - dt.timedelta(days=1)).isoformat()
months = []
for _ in range(12):
    months.append((y, m)); m += 1
    if m > 12: m = 1; y += 1
agg = {k: {"additions": 0, "deletions": 0, "prs": 0} for k in months}
Q = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{... on PullRequest{mergedAt additions deletions}}}}"""
after = None
for _ in range(20):  # 2000 PRs max
    args = ["api", "graphql", "-f", "query=" + Q, "-f", f"q=author:{login} is:pr is:merged merged:>={since}"]
    if after: args += ["-f", "after=" + after]
    d = json.loads(run(*args))
    if "errors" in d: sys.exit(str(d["errors"])[:300])
    s = d["data"]["search"]
    for n in s["nodes"]:
        if not n or not n.get("mergedAt"): continue
        t = dt.datetime.fromisoformat(n["mergedAt"].replace("Z", "+00:00")).astimezone()
        k = (t.year, t.month)
        if k in agg:
            agg[k]["additions"] += n["additions"]; agg[k]["deletions"] += n["deletions"]; agg[k]["prs"] += 1
    if not s["pageInfo"]["hasNextPage"]: break
    after = s["pageInfo"]["endCursor"]
out = [{"year": k[0], "month": k[1], **v} for k, v in agg.items()]
print(json.dumps({"fetched_at": now.isoformat(), "months": out,
    "total_additions": sum(v["additions"] for v in agg.values()),
    "total_prs": sum(v["prs"] for v in agg.values())}))
PY
chmod 600 "$TMP" && mv "$TMP" "$DIR/loc.json" && cat "$DIR/loc.json"
