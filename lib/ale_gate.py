"""`ale gate`: one definition of "ready". Deterministic, no LLM."""
import glob
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path

import ale_core as core

TEST_RE = re.compile(r"(\.spec\.|\.test\.|(^|/)e2e/|(^|/)__tests__/|(^|/)testing/|(^|/)mocks/)")
CONFIG_RE = re.compile(r"(^|/)(jest\.config[^/]*|playwright\.config[^/]*|\.eslintrc[^/]*|eslint\.config[^/]*|tsconfig[^/]*\.json|\.prettierrc[^/]*)$")


def free_port():
    for _ in range(50):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if 20000 <= port <= 60000:
            return port
    return 38417


def _res(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def changed_files(cwd, base):
    out = core.git("diff", "--name-only", "--diff-filter=d", "origin/%s...HEAD" % base, cwd=cwd, check=False)
    return [l for l in out.splitlines() if l]


def _run_check(name, spec, cwd, env, files, logdir, timeout):
    if isinstance(spec, str):
        spec = {"cmd": spec}
    cmd = spec["cmd"]
    argv = None
    if spec.get("files"):
        rx = re.compile(spec["files"])
        sel = [f for f in files if rx.search(f)]
        if not sel:
            return _res(name, True, "no matching changed files")
        argv = cmd + " " + " ".join("'%s'" % f for f in sel)
    logf = Path(logdir) / ("%s.log" % name)
    t0 = time.time()
    try:
        r = subprocess.run(argv or cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=spec.get("timeout", timeout))
        out = (r.stdout or "") + (r.stderr or "")
        logf.write_text(out)
        ok = r.returncode == 0
        tail = "" if ok else "\n".join(out.strip().splitlines()[-8:])
        return _res(name, ok, ("%.0fs" % (time.time() - t0)) if ok else tail)
    except subprocess.TimeoutExpired:
        return _res(name, False, "timeout")


def check_pr_size(cwd, base, cfg, lim):
    out = core.git("diff", "--numstat", "origin/%s...HEAD" % base, cwd=cwd, check=False)
    excl = cfg.get("size_exclude", [])
    src = tests = 0
    for line in out.splitlines():
        a, d, f = (line.split("\t") + ["", "", ""])[:3]
        if not a.isdigit() or core.match_any(f, excl):
            continue
        n = int(a) + int(d)
        if TEST_RE.search(f):
            tests += n
        else:
            src += n
    mx = cfg.get("pr_max_loc", lim["pr_max_loc"])
    return _res("pr-size", src <= mx, "%d source LOC (limit %d), %d test LOC. Over the limit: write needs-user with a split proposal." % (src, mx, tests))


def parse_header(path):
    meta = {}
    for line in Path(path).read_text().splitlines()[:14]:
        m = re.match(r"^\s*(VERDICT|sha|CRITICAL|MAJOR|BLOCKERS|plan_sha|RISK|RISK_WHY)\s*:\s*(.+?)\s*$", line, re.I)
        if m:
            meta[m.group(1).lower()] = m.group(2)
    return meta


def review_reports(td):
    """The reviewer's reports, round-N.md in order. Requests (round-N-request.md) and the builder's answers (round-N-response.md) are not reports."""
    d = Path(td, "reviews")
    files = [f for f in d.iterdir() if re.fullmatch(r"round-\d+\.md", f.name)] if d.exists() else []
    return sorted(files, key=lambda p: int(re.search(r"\d+", p.name).group()))


def latest_review(td):
    files = review_reports(td)
    return files[-1] if files else None


def reviewed_base(td, cwd, sha, base):
    """The commit the reviewed sha was based on, so the PR as the reviewer saw it is `old_base..sha`.
    Order: the base_sha recorded when the review was requested; the base branch named in that round's request file (a stacked PR whose
    base has since merged); the merge-base with the current base."""
    if td:
        ev = Path(td, "events.jsonl")
        for line in (ev.read_text().splitlines() if ev.exists() else [])[::-1]:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("kind") == "review_request" and str(e.get("sha", "")).startswith(sha[:7]) and e.get("base_sha"):
                return e["base_sha"]
        for rq in sorted(Path(td, "reviews").glob("round-*-request.md"), reverse=True) if Path(td, "reviews").exists() else []:
            t = rq.read_text()
            if re.search(r"^sha:\s*%s" % re.escape(sha[:7]), t, re.M):
                m = re.search(r"^base_sha:\s*([0-9a-f]{7,40})", t, re.M) or None
                if m:
                    return m.group(1)
                b = re.search(r"^base:\s*(\S+)", t, re.M)
                mb = core.git("merge-base", sha, b.group(1), cwd=cwd, check=False) if b else ""
                if mb:
                    return mb
                break
    return core.git("merge-base", sha, "origin/%s" % base, cwd=cwd, check=False)


def interdiff_loc(cwd, base, sha, paths=None, old_base=None):
    """How many changed lines differ between the PR as the reviewer saw it (sha) and the PR now (HEAD).
    Works across rebases, base updates, squashes and a base that changed (a stacked PR retargeted to main): it compares patches, not commit
    ancestry. None if sha is unknown."""
    from collections import Counter
    if subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"], cwd=cwd, capture_output=True).returncode != 0:
        return None
    old_base = old_base or core.git("merge-base", sha, "origin/%s" % base, cwd=cwd, check=False)
    if not old_base:
        return None

    def lines(patch):
        return [l for l in patch.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    spec = ["--"] + list(paths) if paths else []
    pa = core.git("diff", "-U0", old_base, sha, *spec, cwd=cwd, check=False)
    pb = core.git("diff", "-U0", "origin/%s...HEAD" % base, *spec, cwd=cwd, check=False)
    ca, cb = Counter(lines(pa)), Counter(lines(pb))
    return sum(((ca - cb) + (cb - ca)).values())


def check_review(td, cwd, lim, base="main"):
    rf = latest_review(td)
    if not rf:
        return _res("review", False, "no reviews/round-N.md yet")
    h = parse_header(rf)
    sha = h.get("sha", "")
    if not sha:
        return _res("review", False, "%s has no sha: header" % rf.name)
    head = core.head(cwd)
    if not (head.startswith(sha) or sha.startswith(head)):
        moved = interdiff_loc(cwd, base, sha, old_base=reviewed_base(td, cwd, sha, base))
        if moved is None:
            return _res("review", False, "review sha %s is not in this repository" % sha)
        if moved > lim["rereview_loc"]:
            return _res("review", False, "the PR differs by %d changed lines from what was reviewed (limit %d): request another round" % (moved, lim["rereview_loc"]))
    crit, maj = int(h.get("critical", "99")), int(h.get("major", "99"))
    verdict = h.get("verdict", "").upper()
    ok = crit == 0 and maj == 0 and verdict.startswith("GO")
    return _res("review", ok, "%s: %s CRITICAL=%s MAJOR=%s" % (rf.name, verdict, crit, maj))


def check_rounds(td, lim):
    lim = dict(lim, review_rounds=lim["review_rounds"] + core.counters(td).get("review_extra", 0))
    n = len(review_reports(td))
    return _res("review-rounds", n <= lim["review_rounds"], "%d of max %d" % (n, lim["review_rounds"]))


def check_tests_first(cwd, base, meta, td):
    typ = str(meta.get("type", "task"))
    if typ in ("chore", "docs", "tooling"):
        return _res("tests-first", True, "n/a for %s" % typ)
    if (Path(td) / "artifacts" / "red-run.log").exists():
        return _res("tests-first", True, "red-run.log present")
    commits = core.git("log", "--reverse", "--format=%H %s", "origin/%s..HEAD" % base, cwd=cwd, check=False).splitlines()
    if typ == "refactor":
        ok = any(c.split(" ", 1)[1].startswith("test") for c in commits if " " in c)
        return _res("tests-first", ok, "refactor needs a separate test(...) commit before the refactor commit" if not ok else "test commit found")
    for c in commits:
        sha = c.split(" ", 1)[0]
        files = core.git("show", "--name-only", "--format=", sha, cwd=cwd, check=False).splitlines()
        if any(TEST_RE.search(f) for f in files):
            return _res("tests-first", True, "test commit %s precedes the end of the branch" % sha[:8])
        if files:
            break
    return _res("tests-first", False, "first commit touched no test file and artifacts/red-run.log is missing")


def check_test_weakening(cwd, base, meta):
    diff = core.git("diff", "-U0", "origin/%s...HEAD" % base, cwd=cwd, check=False)
    cur, removed, added, bad = None, 0, 0, []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and cur and TEST_RE.search(cur):
            if re.search(r"\b(xit|xdescribe|fit|fdescribe)\(|\.(skip|only)\(", line):
                bad.append("%s: %s" % (cur, line[1:].strip()[:60]))
            if re.match(r"^\+\s*(it|test)\(", line):
                added += 1
        elif line.startswith("-") and not line.startswith("---") and cur and TEST_RE.search(cur):
            if re.match(r"^-\s*(it|test)\(", line):
                removed += 1
    cfg = [f for f in changed_files(cwd, base) if CONFIG_RE.search(f)]
    if cfg and not meta.get("allow_config_change"):
        bad.append("config changed: " + ", ".join(cfg))
    if removed > added:
        bad.append("%d tests removed, %d added" % (removed, added))
    return _res("test-weakening", not bad, "; ".join(bad[:4]) or "none")


def check_names(cwd, base, meta):
    tid = str(meta.get("id", ""))
    branch = core.git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    bad = []
    if not re.match(r"^(%s|NOJIRA)/[a-z0-9._-]+" % core.ticket_pat(), branch):
        bad.append("branch %r should be TICKET/desc or NOJIRA/desc" % branch)
    subj = core.git("log", "--no-merges", "--format=%s", "origin/%s..HEAD" % base, cwd=cwd, check=False).splitlines()
    for s in subj:
        if not re.match(r"^[a-z]+\((%s|NOJIRA)\): .+" % core.ticket_pat(), s):
            bad.append("commit %r should be type(TICKET): title" % s[:50])
    return _res("names", not bad, "; ".join(bad[:3]) or "ok")


def check_locales(cwd, base):
    bad = [f for f in changed_files(cwd, base) if re.search(r"(^|/)translations/", f) and not f.endswith("en.json")]
    return _res("locales", not bad, "only translations/en.json may change: " + ", ".join(bad) if bad else "ok")


def check_ui(cwd, base, cfg, td):
    ui = cfg.get("ui")
    if not ui:
        return None
    files = [f for f in changed_files(cwd, base) if core.match_any(f, ui.get("paths", [])) and not core.match_any(f, ui.get("exclude", []))]
    if not files:
        return _res("ui-check", True, "no UI files changed")
    p = Path(td) / "artifacts" / "ui-check.md"
    if not p.exists():
        return _res("ui-check", False, "UI files changed but artifacts/ui-check.md is missing: run the verify-ui skill")
    text = p.read_text()
    m = re.search(r"^sha:\s*([0-9a-f]{7,40})", text, re.M)
    if not m:
        return _res("ui-check", False, "ui-check.md has no sha: line")
    sha = m.group(1)
    head = core.head(cwd)
    if not (head.startswith(sha) or sha.startswith(head)):
        rbase = reviewed_base(td, cwd, sha, base)
        moved = interdiff_loc(cwd, base, sha, old_base=rbase)
        if moved is None:
            return _res("ui-check", False, "ui-check.md sha %s is not in this repository: re-run verify-ui" % sha)
        is_ui = lambda f: core.match_any(f, ui.get("paths", [])) and not core.match_any(f, ui.get("exclude", []))
        old_base = rbase
        mine = {f for f in core.git("diff", "--name-only", old_base, sha, cwd=cwd, check=False).splitlines() if is_ui(f)}
        mine |= {f for f in core.git("diff", "--name-only", "origin/%s...HEAD" % base, cwd=cwd, check=False).splitlines() if is_ui(f)}
        if mine:
            own = interdiff_loc(cwd, base, sha, sorted(mine), old_base=rbase)
            if own:
                return _res("ui-check", False, "the PR's own UI changes differ by %d lines since the check: re-run verify-ui" % own)
            upstream = sorted(mine & set(core.git("diff", "--name-only", old_base, "origin/%s" % base, cwd=cwd, check=False).splitlines()))
            if upstream:
                return _res("ui-check", False, "upstream changed files this PR also touches since the check (%s): re-verify those screens, then update ui-check.md (sha and notes)" % ", ".join(upstream[:4]))
    items = re.findall(r"^\s*-\s*\[(PASS|FAIL|N-A)\]", text, re.M)
    if not items:
        return _res("ui-check", False, "no `- [PASS|FAIL|N-A] item` lines in ui-check.md")
    if "FAIL" in items:
        return _res("ui-check", False, "%d FAIL item(s) in ui-check.md" % items.count("FAIL"))
    missing = [a for a in set(re.findall(r"artifacts/[\w./-]+\.(?:png|webm|mp4|md|log)", text)) if not (Path(td) / a).exists()]
    if missing:
        return _res("ui-check", False, "referenced evidence missing: " + ", ".join(sorted(missing)[:3]))
    return _res("ui-check", True, "%d items, 0 FAIL" % len(items))


def run_gate(cwd, only=None):
    cwd = str(cwd)
    td = core.task_dir(cwd)
    if not td:
        raise SystemExit("ale gate: no task found (no .ale link and no ALE_TASK)")
    meta = core.task_meta(td)
    repo = meta.get("repo")
    cfg = core.load_yaml(core.FW / "repos" / ("%s.yaml" % repo)) if repo else {}
    if not cfg:
        raise SystemExit("ale gate: no gate configured for repo %r. Ask the user or Alejandro to run `ale repo-init %s`." % (repo, repo))
    lim = core.limits()
    base = meta.get("base", "main")
    core.git("fetch", "origin", base, cwd=cwd, check=False)
    logdir = Path(td) / "artifacts" / "gate"
    logdir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    nb = core.node_bin(cfg)
    if cfg.get("node_glob") and not nb:
        raise SystemExit("ale gate: Node %s is not installed. Run `ale bootstrap` (it installs it with nvm)." % core.node_major_wanted(cfg))
    if nb:
        env["PATH"] = nb + os.pathsep + env["PATH"]
    port = free_port()
    if cfg.get("port_env"):
        env[cfg["port_env"]] = str(port)
    env["ALE_GATE"] = "1"

    results = []
    results.append(_res("clean-tree", core.is_clean(cwd), "commit or stash everything first" if not core.is_clean(cwd) else "ok"))
    files = changed_files(cwd, base)
    pre = cfg.get("pre")
    if pre:
        r = _run_check("pre", pre, cwd, env, files, logdir, 600)
        results.append(r)
    for name, spec in (cfg.get("checks") or {}).items():
        if only and name not in only:
            continue
        r = _run_check(name, spec, cwd, env, files, logdir, 1800)
        results.append(r)
        if not r["ok"] and cfg.get("fail_fast", True):
            break
    for fn in (
        lambda: check_pr_size(cwd, base, cfg, lim),
        lambda: check_names(cwd, base, meta),
        lambda: check_locales(cwd, base),
        lambda: check_tests_first(cwd, base, meta, td),
        lambda: check_test_weakening(cwd, base, meta),
        lambda: check_rounds(td, lim),
        lambda: check_review(td, cwd, lim, base),
        lambda: check_ui(cwd, base, cfg, td),
    ):
        r = fn()
        if r:
            results.append(r)

    ok = all(r["ok"] for r in results)
    result = {"task": meta.get("id", ""), "sha": core.head(cwd), "tree": core.tree(cwd), "ok": ok, "ts": int(time.time()), "results": results}
    core.sign_gate(result)
    Path(td, "gates.json").write_text(json.dumps(result, indent=2))
    core.log_event(td, "gate", ok=ok, failed=[r["name"] for r in results if not r["ok"]])
    return result
