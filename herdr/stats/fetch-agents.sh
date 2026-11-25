#!/bin/sh
# Agent tasks this month, from Claude Code session transcripts + ccusage cost.
#   task            = a session with >=1 human prompt and >=3 tool calls, active this month
#   intervention    = a human prompt after the first one, or an "interrupted by user" stop
#   cost per task   = ccusage session cost (estimated, usage since the 1st of the month)
# Saves ~/.cache/herdr-stats/agents.json (atomic, mode 600) and prints it. No prompt text is stored.
DIR="$HOME/.cache/herdr-stats"
mkdir -p "$DIR" && chmod 700 "$DIR"
export OUT_DIR="$DIR"
SINCE=$(date +%Y%m)01
RAW=$(mktemp "$DIR/cc.XXXXXX") && TMP=$(mktemp "$DIR/out.XXXXXX") || exit 1
trap 'rm -f "$RAW" "$TMP"' EXIT
# ccusage is a node script; a login shell has node on PATH (the GUI app does not).
CC=$(command -v ccusage || zsh -lic 'command -v ccusage' 2>/dev/null | tail -n 1)
[ -n "$CC" ] || { echo "ccusage not found" >&2; exit 1; }
if command -v node >/dev/null 2>&1; then "$CC" session --since "$SINCE" --json >"$RAW" 2>/dev/null
else zsh -lic "ccusage session --since $SINCE --json 2>/dev/null" >"$RAW" 2>/dev/null; fi
python3 -I - "$RAW" >"$TMP" <<'PY' || exit 1
import glob, json, math, os, sys, datetime as dt
raw = open(sys.argv[1], errors="replace").read()
cc = json.loads(raw[raw.index("{"):])
sessions = cc.get("session") or []
now = dt.datetime.now().astimezone()
files = {os.path.basename(f)[:-6]: f for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))}
def text_of(c):
    if isinstance(c, str): return [c]
    return [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"] if isinstance(c, list) else []
def scan(path):
    prompts = interrupts = tools = 0
    for line in open(path, errors="replace"):
        try: o = json.loads(line)
        except Exception: continue
        if o.get("isSidechain"): continue
        m = o.get("message") or {}; c = m.get("content")
        if o.get("type") == "assistant" and isinstance(c, list):
            tools += sum(1 for b in c if isinstance(b, dict) and b.get("type") == "tool_use")
        elif o.get("type") == "user" and not o.get("isMeta"):
            texts = [t.strip() for t in text_of(c) if t.strip()]
            if any("[Request interrupted by user" in t for t in texts): interrupts += 1; continue
            real = [t for t in texts if not t.startswith("<")]
            if real: prompts += 1
    return prompts, interrupts, tools
tasks = []
for s in sessions:
    sid = s["period"]
    if sid not in files: continue
    prompts, interrupts, tools = scan(files[sid])
    if prompts < 1 or tools < 3: continue
    tasks.append({"session": sid[:8], "project": os.path.basename(os.path.dirname(files[sid])).replace(os.path.expanduser("~").replace("/", "-").replace(".", "-"), "~").strip("-"),
        "prompts": prompts, "interrupts": interrupts, "interventions": (prompts - 1) + interrupts, "tools": tools,
        "cost": round(s["totalCost"], 2), "tokens": s["totalTokens"], "last": s.get("metadata", {}).get("lastActivity")})
def pct(v, p): v = sorted(v); return v[max(0, math.ceil(p * len(v)) - 1)] if v else None
cost = [t["cost"] for t in tasks]; n = len(tasks)
clean = sum(t["interventions"] == 0 for t in tasks)
mid = sorted(cost)[n // 2] if n % 2 else (sorted(cost)[n // 2 - 1] + sorted(cost)[n // 2]) / 2 if n else None
print(json.dumps({"fetched_at": now.isoformat(), "year": now.year, "month": now.month, "tasks": n,
    "cost_total": round(sum(cost), 2), "cost_avg": round(sum(cost) / n, 2) if n else None, "cost_median": mid, "cost_p90": pct(cost, 0.9),
    "tokens_avg": round(sum(t["tokens"] for t in tasks) / n) if n else None,
    "no_intervention": clean, "no_intervention_rate": clean / n if n else None,
    "ccusage_sessions": len(sessions), "rows": tasks}))
PY
chmod 600 "$TMP" && mv "$TMP" "$DIR/agents.json" && cat "$DIR/agents.json"
