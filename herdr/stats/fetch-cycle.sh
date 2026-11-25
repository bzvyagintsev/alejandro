#!/bin/sh
# Cycle-time stats over my PRs merged in the current month (local time):
#   1. spawn -> merge        : herdr terminal spawn time (from a persistent log) to PR merge
#   2. first commit -> merge : first commit in the PR to PR merge
# Spawn log: ~/.cache/herdr-stats/spawns.json (grows as the script runs; the core prunes
# finished terminals, this log keeps them). Output: ~/.cache/herdr-stats/cycle.json.
# A terminal matches a PR by ticket key (PROJ-123, NOJIRA-123, ...) in its tab name, title or
# branch vs the PR's branch/title, or by an identical branch name.
export GH_TOKEN="${GH_TOKEN:-$(zsh -lic 'printf "%s" "$GH_TOKEN"' 2>/dev/null | tail -n 1)}"
[ -n "$GH_TOKEN" ] || { echo "GH_TOKEN not found in your login shell" >&2; exit 1; }
export GH_BIN=$(command -v gh || ls /opt/homebrew/bin/gh /usr/local/bin/gh 2>/dev/null | head -n 1)
[ -n "$GH_BIN" ] || { echo "gh not found" >&2; exit 1; }
export HC_BIN=$(command -v hc || ls "$HOME/.local/bin/hc" 2>/dev/null | head -n 1)
DIR="$HOME/.cache/herdr-stats"
mkdir -p "$DIR" && chmod 700 "$DIR"
export STATS_DIR="$DIR"
TMP=$(mktemp "$DIR/out.XXXXXX") || exit 1
trap 'rm -f "$TMP"' EXIT
python3 -I - >"$TMP" <<'PY' || exit 1
import json, math, os, re, subprocess, sys, time, datetime as dt
DIR = os.environ["STATS_DIR"]; GH = os.environ["GH_BIN"]; HC = os.environ.get("HC_BIN")
KEY = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9}-\d+)\b")
def keys(*texts): return {m.upper() for t in texts for m in KEY.findall(t or "")}
def run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode: raise RuntimeError((r.stderr.strip() or "failed")[:300])
    return r.stdout
def write(path, obj):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f: json.dump(obj, f)
    os.replace(tmp, path)

# ---- 1. update the spawn log from herdr-core -------------------------------------------
log_path = os.path.join(DIR, "spawns.json")
try: spawns = json.load(open(log_path))
except Exception: spawns = {}
if HC:
    try:
        for t in json.loads(run([HC, "ls", "--all", "--json"]))["terminals"]:
            g = json.loads(run([HC, "get", t["address"], "--json"]))
            k = f'{g["address"]}:{g["created_ms"]}'
            s = spawns.setdefault(k, {"created_ms": g["created_ms"], "branches": []})
            s.update(finished_ms=g.get("finished_ms"), where=g.get("labels", {}).get("where", ""), title=g.get("title") or "")
            cwd = g.get("cwd") or g.get("last_cwd")
            if g.get("state") == "live" and cwd and os.path.isdir(cwd):
                try:
                    b = run(["git", "-C", cwd, "branch", "--show-current"], 5).strip()
                    if b and b not in s["branches"]: s["branches"].append(b)
                except Exception: pass
        write(log_path, spawns)
    except Exception as e:
        print("spawn log not updated:", e, file=sys.stderr)

# ---- 2. my merged PRs, with first commit ------------------------------------------------
login = run([GH, "api", "user", "-q", ".login"]).strip()
now = dt.datetime.now().astimezone()
# one day of slack before the 1st: the search takes UTC dates, the month filter below is local
since = (now.date().replace(day=1) - dt.timedelta(days=1)).isoformat()
Q = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){
pageInfo{hasNextPage endCursor} nodes{... on PullRequest{number title headRefName mergedAt
repository{nameWithOwner} commits(first:1){nodes{commit{authoredDate}}}}}}}"""
prs, after = [], None
for _ in range(10):
    a = [GH, "api", "graphql", "-f", "query=" + Q, "-f", f"q=author:{login} is:pr is:merged merged:>={since}"]
    if after: a += ["-f", "after=" + after]
    d = json.loads(run(a))
    if "errors" in d: raise SystemExit(str(d["errors"])[:300])
    s = d["data"]["search"]
    prs += [n for n in s["nodes"] if n and n.get("mergedAt")]
    if not s["pageInfo"]["hasNextPage"]: break
    after = s["pageInfo"]["endCursor"]
prs = [n for n in prs if (lambda t: (t.year, t.month) == (now.year, now.month))(dt.datetime.fromisoformat(n["mergedAt"].replace("Z", "+00:00")).astimezone())]
ts = lambda iso: dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
HOUR = 3600_000

def pct(sorted_vals, p): return sorted_vals[max(0, math.ceil(p * len(sorted_vals)) - 1)]
def stats(vals):
    v = sorted(vals)
    if not v: return {"n": 0}
    mid = len(v) // 2
    return {"n": len(v), "avg_h": sum(v) / len(v), "median_h": v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2, "p90_h": pct(v, 0.9)}

rows, first_c, spawn_d = [], [], []
for p in prs:
    merged = ts(p["mergedAt"])
    row = {"number": p["number"], "repo": p["repository"]["nameWithOwner"], "title": p["title"], "merged_at": p["mergedAt"]}
    c = (p["commits"]["nodes"] or [None])[0]
    if c:
        h = (merged - ts(c["commit"]["authoredDate"])) / HOUR
        if h >= 0: row["first_commit_h"] = h; first_c.append(h)
    pk = keys(p["headRefName"], p["title"])
    cands = [s["created_ms"] for s in spawns.values()
             if s["created_ms"] < merged and (p["headRefName"] in s["branches"] or pk & keys(s.get("where"), s.get("title"), *s["branches"]))]
    if cands:
        h = (merged - min(cands)) / HOUR; row["spawn_h"] = h; spawn_d.append(h)
    rows.append(row)
earliest = min((s["created_ms"] for s in spawns.values()), default=None)
print(json.dumps({
    "fetched_at": now.isoformat(), "year": now.year, "month": now.month, "prs": len(prs),
    "first_commit": stats(first_c),
    "spawn": {**stats(spawn_d), "terminals_logged": len(spawns),
              "tracking_since": dt.datetime.fromtimestamp(earliest / 1000).astimezone().isoformat() if earliest else None},
    "rows": rows,
}))
PY
chmod 600 "$TMP" && mv "$TMP" "$DIR/cycle.json" && cat "$DIR/cycle.json"
