"""Spend per task, from Claude Code's own session transcripts and `ccusage` prices. Pure script, no LLM.

A task's sessions live in ~/.claude/projects/<worktree path with / and . as ->/<sessionId>.jsonl. Each role pane is started with
`--name <task>-<role>`, which Claude Code writes into the transcript as a `custom-title`, so a session is mapped to a role without
any recording step. Prices come from `ccusage session --json` (the same source as the herdr stats page); the transcript gives the
shape of the session (turns, context size, cache rebuilds, biggest tool outputs) that the hints are built from.
"""
import json
import os
import re
import datetime as dt
import shutil
import subprocess
import time
from pathlib import Path

import ale_core as core
import ale_gate

COUNTED = ("builder", "reviewer", "lead")  # roles a task is charged for; other sessions in the worktree folder (probes, the user's own) are listed but not counted
REBUILD_TOKENS = 100_000  # a turn that writes this much cache after the first turn rebuilt the context (idle gap, clear, resume)
BIG_RESULT_CHARS = 20_000


def projects_dir():
    return Path(os.environ.get("ALE_CLAUDE_PROJECTS") or core.HOME / ".claude" / "projects")


def transcript_dir(worktree):
    """Claude Code names the folder after the path the session started in; try the path as given, resolved, and without macOS's /private prefix."""
    w = str(worktree)
    cands = [w, os.path.realpath(w)] + [x[len("/private"):] for x in (w, os.path.realpath(w)) if x.startswith("/private/")]
    dirs = [projects_dir() / re.sub(r"[/.]", "-", c) for c in cands]
    return next((d for d in dirs if d.exists()), dirs[0])


def worktree_for(td, cwd=None):
    """The task's worktree: the folder whose .ale link points at the task, else ~/projects/<repo>-<id>."""
    if cwd:
        p = Path(cwd).resolve()
        for d in [p, *p.parents]:
            link = d / ".ale"
            if link.exists() and link.resolve() == Path(td).resolve():
                return d
    meta = core.task_meta(td)
    return core.HOME / "projects" / ("%s-%s" % (meta.get("repo"), meta.get("id")))


def session_context(worktree, title):
    """Tokens in the context of the newest real assistant turn of the session named `title` (e.g. PROJ-1-reviewer) in this worktree's transcripts, or None.
    Reads only the head (for the title) and the tail of each file. Skips synthetic API-error turns (zero usage), sidechains and unreadable files."""
    d = transcript_dir(worktree)
    best = None
    for p in d.glob("*.jsonl") if d.exists() else []:
        try:
            with open(p, "rb") as f:
                head = f.read(60_000).decode("utf-8", "replace")
                if '"customTitle":"%s"' % title not in head.replace('": "', '":"'):
                    continue
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 600_000))
                tail = f.read().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            try:
                x = json.loads(line)
            except Exception:
                continue
            m = x.get("message") or {}
            u = m.get("usage")
            if x.get("type") != "assistant" or x.get("isSidechain") or x.get("isApiErrorMessage") or m.get("model") == "<synthetic>" or not u:
                continue
            tokens = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            if tokens:
                try:
                    when = dt.datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00")).timestamp()
                except Exception:
                    when = p.stat().st_mtime
                if best is None or when > best[0]:
                    best = (when, tokens)
                break
    return best[1] if best else None


def units(u):
    """Tokens weighted by what they cost relative to an input token (output 5x, cache write 1.25x, cache read 0.1x): used to split a session's price."""
    return ((u.get("input_tokens") or 0) + 1.25 * (u.get("cache_creation_input_tokens") or 0)
            + 0.1 * (u.get("cache_read_input_tokens") or 0) + 5 * (u.get("output_tokens") or 0))


def read_session(path):
    """Role, name and shape of one transcript."""
    title, setting, turns, tool_names, big, stamped = None, None, [], {}, [], []
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        if t == "custom-title" and not title:
            title = d.get("customTitle")
        elif t == "agent-setting" and not setting:
            setting = d.get("agentSetting")
        m = d.get("message") or {}
        u = m.get("usage")
        if u and m.get("role") == "assistant":
            turns.append(u)
            try:
                when = dt.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
            except Exception:
                when = None
            stamped.append((when, units(u), (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)))
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if b.get("type") == "tool_use":
                    tool_names[b.get("id")] = b.get("name")
                elif b.get("type") == "tool_result":
                    x = b.get("content")
                    n = sum(len(i.get("text", "")) for i in x if isinstance(i, dict)) if isinstance(x, list) else len(str(x or ""))
                    big.append((n, tool_names.get(b.get("tool_use_id"), "?")))
    ctx = lambda u: (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    rebuilds = [u.get("cache_creation_input_tokens") or 0 for u in turns[1:] if (u.get("cache_creation_input_tokens") or 0) >= REBUILD_TOKENS]
    role = (title or "").rsplit("-", 1)[-1] if title and re.search(r"-(builder|reviewer|lead|alejandro)$", title) else (setting or "other")
    return {
        "session": Path(path).stem, "title": title, "role": role, "turns": len(turns),
        "start_ctx": ctx(turns[0]) if turns else 0, "max_ctx": max((ctx(u) for u in turns), default=0),
        "rebuilds": len(rebuilds), "rebuilt_tokens": sum(rebuilds),
        "output_tokens": sum(u.get("output_tokens") or 0 for u in turns),
        "biggest_results": sorted(big, reverse=True)[:3], "mtime": Path(path).stat().st_mtime, "stamped": stamped,
    }


def ccusage_prices(since=None):
    """{sessionId: dollars} from ccusage, or (None, reason). Online prices: the cached offline table lacks new models and prices them at $0."""
    exe = shutil.which("ccusage")
    if not exe:
        return None, "ccusage is not installed (npm i -g ccusage): showing tokens only"
    cmd = [exe, "session", "--json"] + (["--since", since] if since else [])
    for extra, note in (([], ""), (["--offline"], "ccusage could not fetch prices; offline prices may show $0 for new models")):
        try:
            r = subprocess.run(cmd + extra, capture_output=True, text=True, timeout=180)
            rows = json.loads(r.stdout)["session"]
            return {x["period"]: x.get("totalCost") for x in rows}, note
        except Exception:
            continue
    return None, "ccusage failed: showing tokens only"


def code_changes(td, wt):
    """Lines the task changed against its base, counted like the gate counts them (generated files, lockfiles and snapshots left out; tests apart).
    Read from the worktree; saved to artifacts/diffstat.json so it survives `ale cleanup`, and read back from there when the worktree is gone."""
    cache = Path(td) / "artifacts" / "diffstat.json"
    meta = core.task_meta(td)
    if Path(wt).is_dir() and (Path(wt) / ".git").exists():
        base = next((b for b in (meta.get("base") or "main", "main") if core.git("rev-parse", "-q", "--verify", "origin/%s" % b, cwd=wt, check=False)), None)
        if base:
            cfg = core.load_yaml(core.FW / "repos" / ("%s.yaml" % meta.get("repo")))
            excl = cfg.get("size_exclude", [])
            add = dele = files = src = tests = 0
            for line in core.git("diff", "--numstat", "origin/%s...HEAD" % base, cwd=wt, check=False).splitlines():
                a, d, f = (line.split("\t") + ["", "", ""])[:3]
                if not a.isdigit() or core.match_any(f, excl):
                    continue
                add, dele, files = add + int(a), dele + int(d), files + 1
                if ale_gate.TEST_RE.search(f):
                    tests += int(a) + int(d)
                else:
                    src += int(a) + int(d)
            if files:
                res = {"added": add, "deleted": dele, "files": files, "source_loc": src, "test_loc": tests, "base": base}
                try:
                    cache.parent.mkdir(exist_ok=True)
                    cache.write_text(json.dumps(res))
                except OSError:
                    pass
                return res
    try:
        return json.loads(cache.read_text())
    except Exception:
        return None


def task_cost(td, cwd=None, prices=None):
    wt = worktree_for(td, cwd)
    d = transcript_dir(wt)
    sessions = [read_session(p) for p in sorted(d.glob("*.jsonl"))] if d.exists() else []
    sessions = [s for s in sessions if s["turns"]]
    for s in sessions:
        s["cost"] = (prices or {}).get(s["session"]) if prices is not None else None
    roles = {}
    for s in sessions:
        r = roles.setdefault(s["role"], {"cost": 0.0, "sessions": 0, "turns": 0, "priced": True})
        r["sessions"] += 1
        r["turns"] += s["turns"]
        if s["cost"] is None:
            r["priced"] = False
        else:
            r["cost"] += s["cost"]
    events = []
    ep = Path(td) / "events.jsonl"
    for line in (ep.read_text().splitlines() if ep.exists() else []):
        try:
            events.append(json.loads(line))
        except Exception:
            pass
    rounds = len([e for e in events if e.get("kind") == "review_request"])
    review_costs = split_by_review(sessions, events)
    mine = [s for s in sessions if s["role"] in COUNTED]
    for s in sessions:
        s.pop("stamped", None)
    total = sum(s["cost"] for s in mine if s["cost"] is not None)
    return {"task": core.task_meta(td).get("id"), "worktree": str(wt), "total": total, "sessions": sessions, "roles": roles, "review_rounds": rounds,
            "review_costs": review_costs, "code": code_changes(td, wt), "priced": all(s["cost"] is not None for s in mine)}


def split_by_review(sessions, events):
    """The reviewer's price split by review: plan reviews and code rounds, each from its request to the next one. A session's price is shared
    out by weighted tokens (units), so a round that rebuilt a cold context shows its real share. An estimate, exact only per session."""
    marks = []
    for e in events:
        if e.get("kind") == "plan_review_request":
            marks.append((e["ts"], "plan %s" % e.get("n", "")))
        elif e.get("kind") == "review_request":
            marks.append((e["ts"], "round %s" % e.get("round", "")))
    marks.sort()
    verdict = {}
    for e in events:
        h = e.get("header") or {}
        if e.get("kind") == "plan_review_done":
            verdict["plan %s" % e.get("n", "")] = h.get("verdict")
        elif e.get("kind") == "review_done":
            verdict["round %s" % e.get("round", "")] = h.get("verdict")
    cleared = {}
    for e in events:
        if e.get("kind") in ("plan_review_request", "review_request") and "cleared" in e:
            cleared["%s %s" % ("plan" if e["kind"] == "plan_review_request" else "round", e.get("n") if e["kind"] == "plan_review_request" else e.get("round"))] = e["cleared"]
    out = {label: {"label": label, "cost": 0.0, "turns": 0, "verdict": verdict.get(label), "ts": ts, "cleared": cleared.get(label)} for ts, label in marks}
    for s in sessions:
        if s["role"] != "reviewer" or s.get("cost") is None:
            continue
        total = sum(x[1] for x in s["stamped"]) or 1
        for when, u, ctx in s["stamped"]:
            label = None
            for ts, name in marks:
                if when is not None and when >= ts - 5:
                    label = name
            if label:
                out[label]["cost"] += s["cost"] * u / total
                out[label]["turns"] += 1
                out[label].setdefault("start_ctx", ctx)  # the context the review began with: the first turn after its request
    rows = sorted(out.values(), key=lambda r: r["ts"])
    paid = sum(s["cost"] for s in sessions if s["role"] == "reviewer" and s.get("cost") is not None)
    rest = paid - sum(r["cost"] for r in rows)
    if rest > 0.005:
        rows.insert(0, {"label": "other", "cost": rest, "turns": 0, "verdict": "before the first request", "ts": 0})
    return rows


def hints(c):
    """Rule-based suggestions for the learning step. Numbers, not opinions."""
    out = []
    k = lambda n: "%dk" % round(n / 1000)
    for s in c["sessions"]:
        if s["role"] not in COUNTED:
            continue
        who = s["role"]
        if s["start_ctx"] > 100_000:
            out.append("%s starts at %s tokens before any work: trim per-role plugins and MCP servers (docs/cost.md)." % (who, k(s["start_ctx"])))
        if s["rebuilds"] >= 2:
            out.append("%s rebuilt its context %d times (%s tokens): idle gaps over 5 minutes (the cache expires), a clear or a resume. Fewer waits, or review sooner after the builder commits." % (who, s["rebuilds"], k(s["rebuilt_tokens"])))
        if s["turns"] > 250:
            out.append("%s ran %d turns with context up to %s tokens: restart from files at a phase boundary (after plan approval, after each review round)." % (who, s["turns"], k(s["max_ctx"])))
        if s["biggest_results"] and s["biggest_results"][0][0] > BIG_RESULT_CHARS:
            n, tool = s["biggest_results"][0]
            out.append("%s got a %s-character %s result: narrow that command (rg flags, --max-count, a path)." % (who, k(n), tool))
    r = c["roles"]
    if "builder" in r and "reviewer" in r and r["reviewer"]["cost"] > r["builder"]["cost"] and r["builder"]["priced"] and r["reviewer"]["priced"]:
        out.append("the reviewer cost more than the builder: check review rounds (%d) and the reviewer's starting context." % c["review_rounds"])
    if c["review_rounds"] > 3:
        out.append("%d review rounds: record scope decisions early (`scope: ...`) and check the plan review caught them." % c["review_rounds"])
    return out


def render(c, note=""):
    money = lambda x: "$%.2f" % x if x is not None else "n/a"
    lines = ["# Cost of %s: %s%s (%d sessions, %d review rounds)" % (c["task"], money(c["total"]), "" if c["priced"] else " (some sessions unpriced)", len([x for x in c["sessions"] if x["role"] in COUNTED]), c["review_rounds"])]
    for name, r in sorted(c["roles"].items()):
        lines.append("- %s: %s%s over %d session(s), %d turns%s" % (name, money(r["cost"]), "" if r["priced"] else "+", r["sessions"], r["turns"], "" if name in COUNTED else " (not counted: not a task role)"))
    if c.get("code"):
        k = c["code"]
        lines.append("- code: +%d / -%d lines in %d files against %s (%d source, %d test)" % (k["added"], k["deleted"], k["files"], k["base"], k["source_loc"], k["test_loc"]))
    lines.append("")
    for s in c["sessions"]:
        lines.append("  %-9s %s  %3d turns  start %dk  max %dk  rebuilds %d  out %dk  (%s)" % (
            s["role"], money(s["cost"]).rjust(7), s["turns"], s["start_ctx"] // 1000, s["max_ctx"] // 1000, s["rebuilds"], s["output_tokens"] // 1000, s["session"][:8]))
    if any(r["turns"] for r in c.get("review_costs", [])):
        lines += ["", "Reviewer cost by review (estimate: the session price shared by weighted tokens):"]
        for r in c["review_costs"]:
            if r["turns"] or r["label"] == "other":
                lines.append("  %-8s %7s  %3d turns  start %4s  %-7s %s" % (r["label"], money(r["cost"]), r["turns"],
                             ("%dk" % (r["start_ctx"] // 1000)) if r.get("start_ctx") else "-", {True: "cleared", False: "kept"}.get(r.get("cleared"), ""), r["verdict"] or "no report"))
    h = hints(c)
    if h:
        lines += ["", "## Suggestions"] + ["- " + x for x in h]
    if note:
        lines += ["", "(" + note + ")"]
    return "\n".join(lines)


def stats(results):
    """One compact record for dashboards (the herdr Stats mod, or anything that reads JSON): a row per task and the totals."""
    import math
    rows = []
    for c in results:
        mine = [s for s in c["sessions"] if s["role"] in COUNTED]
        if not mine:
            continue

        def side(role):
            ss = [s for s in mine if s["role"] == role]
            r = c["roles"].get(role)
            if not ss or not r:
                return None
            return {"cost": round(r["cost"], 2), "turns": r["turns"], "sessions": r["sessions"], "start_k": round(max(s["start_ctx"] for s in ss) / 1000),
                    "max_k": round(max(s["max_ctx"] for s in ss) / 1000), "rebuilds": sum(s["rebuilds"] for s in ss), "out_k": round(sum(s["output_tokens"] for s in ss) / 1000)}
        rows.append({"task": c["task"], "total": round(c["total"], 2), "priced": c["priced"], "rounds": c["review_rounds"], "builder": side("builder"),
                     "reviewer": side("reviewer"), "lead": side("lead"), "code": c.get("code"), "last": max(s["mtime"] for s in mine)})
    rows.sort(key=lambda r: -r["last"])
    cost = sorted(r["total"] for r in rows)
    n = len(rows)
    mid = (cost[n // 2] if n % 2 else (cost[n // 2 - 1] + cost[n // 2]) / 2) if n else None
    return {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "n": n, "total": round(sum(cost), 2), "avg": round(sum(cost) / n, 2) if n else None,
            "median": mid, "p90": cost[max(0, math.ceil(0.9 * n) - 1)] if n else None, "rows": rows}


def since_for(oldest_mtime):
    return time.strftime("%Y%m%d", time.localtime(oldest_mtime - 86400))
