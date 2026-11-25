#!/bin/sh
# Review quality of my PRs merged this month (local time):
#   rejections per merged PR  = CHANGES_REQUESTED reviews by others / merged PRs
#   first-try merge rate      = merged PRs that never got a CHANGES_REQUESTED review / merged PRs
# Saves ~/.cache/herdr-stats/quality.json (atomic, mode 600) and prints it. Token never stored.
export GH_TOKEN="${GH_TOKEN:-$(zsh -lic 'printf "%s" "$GH_TOKEN"' 2>/dev/null | tail -n 1)}"
[ -n "$GH_TOKEN" ] || { echo "GH_TOKEN not found in your login shell" >&2; exit 1; }
export GH_BIN=$(command -v gh || ls /opt/homebrew/bin/gh /usr/local/bin/gh 2>/dev/null | head -n 1)
[ -n "$GH_BIN" ] || { echo "gh not found" >&2; exit 1; }
DIR="$HOME/.cache/herdr-stats"
mkdir -p "$DIR" && chmod 700 "$DIR"
TMP=$(mktemp "$DIR/out.XXXXXX") || exit 1
trap 'rm -f "$TMP"' EXIT
python3 -I - >"$TMP" <<'PY' || exit 1
import json, os, subprocess, sys, time, datetime as dt
GH = os.environ["GH_BIN"]
def gh(*a):
    for attempt in range(3):
        r = subprocess.run([GH, *a], capture_output=True, text=True, timeout=90)
        if not r.returncode: return r.stdout
        time.sleep(1 + attempt)
    raise RuntimeError((r.stderr.strip() or "gh failed")[:300])
me = gh("api", "user", "-q", ".login").strip()
now = dt.datetime.now().astimezone()
since = (now.date().replace(day=1) - dt.timedelta(days=1)).isoformat()
Q = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:25,after:$after){
pageInfo{hasNextPage endCursor} nodes{... on PullRequest{number title url mergedAt repository{nameWithOwner}
reviews(first:50){nodes{state author{login}}}}}}}"""
rows, after = [], None
for _ in range(20):
    a = ["api", "graphql", "-f", "query=" + Q, "-f", f"q=author:{me} is:pr is:merged merged:>={since}"]
    if after: a += ["-f", "after=" + after]
    d = json.loads(gh(*a))
    if "errors" in d: sys.exit(str(d["errors"])[:300])
    s = d["data"]["search"]
    for n in s["nodes"]:
        if not n or not n.get("mergedAt"): continue
        t = dt.datetime.fromisoformat(n["mergedAt"].replace("Z", "+00:00")).astimezone()
        if (t.year, t.month) != (now.year, now.month): continue
        rev = [r for r in n["reviews"]["nodes"] if r and (r.get("author") or {}).get("login") != me]
        rows.append({"repo": n["repository"]["nameWithOwner"].split("/")[1], "number": n["number"], "title": n["title"],
                     "rejections": sum(r["state"] == "CHANGES_REQUESTED" for r in rev),
                     "approvals": sum(r["state"] == "APPROVED" for r in rev)})
    if not s["pageInfo"]["hasNextPage"]: break
    after = s["pageInfo"]["endCursor"]
n = len(rows); rej = sum(r["rejections"] for r in rows); first = sum(r["rejections"] == 0 for r in rows)
print(json.dumps({"fetched_at": now.isoformat(), "year": now.year, "month": now.month, "prs": n, "rejections": rej,
    "rejections_per_pr": rej / n if n else None, "first_try": first, "first_try_rate": first / n if n else None, "rows": rows}))
PY
chmod 600 "$TMP" && mv "$TMP" "$DIR/quality.json" && cat "$DIR/quality.json"
