"""Shared helpers for `ale` and the hooks. Pure Python, no LLM calls."""
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # a clear message instead of a traceback
    raise SystemExit("ale needs PyYAML: run `python3 -m pip install --user pyyaml`")

HOME = Path.home()
FW = Path(os.environ.get("ALE_FRAMEWORK") or Path(__file__).resolve().parent.parent)
ALE = Path(os.environ.get("ALE_HOME") or HOME / ".ale")
TASKS = ALE / "tasks"


# ---------- files ----------

def read_frontmatter(path):
    p = Path(path)
    if not p.exists():
        return {}, ""
    text = p.read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return (yaml.safe_load(text[4:end]) or {}), text[end + 5:]
    return {}, text


def write_frontmatter(path, meta, body):
    Path(path).write_text("---\n" + yaml.safe_dump(meta, sort_keys=False).strip() + "\n---\n" + body)


def load_yaml(path):
    p = Path(path)
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def _merge(a, b):
    """Deep-merge dict b over a (lists and scalars in b replace those in a)."""
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config():
    """The user's settings: policy/config.defaults.yaml (generic) under ~/.ale/config.yaml (local, never committed)."""
    return _merge(load_yaml(FW / "policy" / "config.defaults.yaml"), load_yaml(ALE / "config.yaml"))


def ticket_pat():
    """Regex source for a ticket key, from jira.ticket_pattern (default: any PROJECT-123)."""
    return "(?:%s)" % ((config().get("jira") or {}).get("ticket_pattern") or r"[A-Z][A-Z0-9]+-\d+")


def is_ticket(key):
    return bool(re.fullmatch(ticket_pat(), key or "")) and not (key or "").startswith("NOJIRA")


def limits():
    base = {"review_rounds": 3, "ci_fixes": 2, "stop_blocks": 3, "rereview_loc": 30, "pr_max_loc": 500}
    base.update(load_yaml(FW / "policy" / "limits.yaml"))
    return base


def log_event(task_dir, kind, **kw):
    if not task_dir:
        return
    rec = {"ts": int(time.time()), "kind": kind, **kw}
    with open(Path(task_dir) / "events.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")


def add_decision(td, text, source):
    """Append a scope decision to the task's DECISIONS.md (written only by the user's typed message or `ale decide`, never by the builder)."""
    p = Path(td) / "DECISIONS.md"
    if not p.exists():
        p.write_text("# User decisions for this task\n")
    with open(p, "a") as f:
        f.write("- [%s] %s (source: %s)\n" % (time.strftime("%Y-%m-%d %H:%M"), " ".join(text.split()), source))
    log_event(td, "decision", text=text[:300], source=source)


_SECRET = re.compile(r"(\w*(?:TOKEN|KEY|SECRET|PASSWORD)\w*=)\S+|(--token[ =])\S+|(Authorization:\s*)\S+(?:\s+\S+)?|(://[^/\s:@]+:)[^@\s]+@", re.I)


def redact(text, limit=300):
    """Hide secrets in a command before it is logged, and cap its length."""
    t = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or m.group(3) or m.group(4)) + ("***@" if m.group(4) else "***"), text or "")
    return t if len(t) <= limit else t[:limit] + "..."


def log_hook(kind, role, task_dir=None, **kw):
    """Record a hook denial or error in the task's events.jsonl and in one global log (~/.ale/logs/hook.jsonl), so friction can be counted across tasks."""
    try:
        log_event(task_dir, kind, **kw)
        rec = {"ts": int(time.time()), "kind": kind, "role": role, "task": Path(task_dir).name if task_dir else None, **kw}
        d = ALE / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hook.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass  # logging must never break the hook


# ---------- git ----------

def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def head(cwd=None):
    return git("rev-parse", "HEAD", cwd=cwd)


def tree(cwd=None):
    return git("rev-parse", "HEAD^{tree}", cwd=cwd)


def is_clean(cwd=None):
    return git("status", "--porcelain", cwd=cwd) == ""


def is_ancestor(a, b, cwd=None):
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=cwd).returncode == 0


# ---------- task resolution ----------

def task_dir(cwd=None):
    """Resolve the task dir: ALE_TASK env, else the .ale link in cwd or a parent."""
    t = os.environ.get("ALE_TASK")
    if t and (TASKS / t).is_dir():
        return TASKS / t
    p = Path(cwd or os.getcwd()).resolve()
    for d in [p, *p.parents]:
        link = d / ".ale"
        if link.exists():
            return link.resolve()
    return None


def task_meta(td):
    return read_frontmatter(Path(td) / "TASK.md")[0] if td else {}


# ---------- counters ----------

def counters(td):
    p = Path(td) / "counters.json"
    return json.loads(p.read_text()) if p.exists() else {}


def bump(td, key):
    c = counters(td)
    c[key] = c.get(key, 0) + 1
    (Path(td) / "counters.json").write_text(json.dumps(c))
    return c[key]


def reset_counter(td, key):
    c = counters(td)
    c[key] = 0
    (Path(td) / "counters.json").write_text(json.dumps(c))


# ---------- signed gate result ----------

def _key():
    kp = ALE / "key"
    return kp.read_bytes() if kp.exists() else b""


def _canon(d):
    return json.dumps({k: d[k] for k in ("task", "sha", "tree", "ok", "ts")}, sort_keys=True).encode()


def sign_gate(result):
    result["sig"] = hmac.new(_key(), _canon(result), hashlib.sha256).hexdigest()
    return result


def gate_valid(td, cwd=None):
    """True only if gates.json is signed, green, and matches the current commit and tree, with a clean tree."""
    gp = Path(td) / "gates.json" if td else None
    if not gp or not gp.exists():
        return False, "no gates.json: run `ale gate`"
    try:
        g = json.loads(gp.read_text())
    except Exception:
        return False, "gates.json unreadable"
    if not _key():
        return False, "no signing key: run `ale install`"
    expect = hmac.new(_key(), _canon(g), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, g.get("sig", "")):
        return False, "gates.json signature invalid"
    if not g.get("ok"):
        return False, "gate was red: run `ale gate` and fix the failures"
    if g.get("sha") != head(cwd) or g.get("tree") != tree(cwd):
        return False, "gate result is for a different commit: run `ale gate` again"
    if not is_clean(cwd):
        return False, "working tree is dirty: commit or stash, then run `ale gate`"
    return True, "ok"


# ---------- plan approval ----------

def plan_hash(td):
    p = Path(td) / "PLAN.md"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def approval_valid(td):
    ap = Path(td) / "approval.json"
    ph = plan_hash(td)
    if not ph:
        return False, "no PLAN.md yet"
    if not ap.exists():
        return False, "plan not approved yet"
    try:
        a = json.loads(ap.read_text())
    except Exception:
        return False, "approval.json unreadable"
    if a.get("plan_hash") != ph:
        return False, "PLAN.md changed after approval: ask for approval again"
    return True, "approved by %s" % a.get("by")


# ---------- misc ----------

def glob_to_re(g):
    out, i = "", 0
    while i < len(g):
        if g[i:i + 3] == "**/":
            out += "(?:.*/)?"
            i += 3
        elif g[i:i + 2] == "**":
            out += ".*"
            i += 2
        elif g[i] == "*":
            out += "[^/]*"
            i += 1
        elif g[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(g[i])
            i += 1
    return re.compile("^" + out + "$")


def match_any(path, globs):
    return any(glob_to_re(g).match(path) for g in globs or [])


def node_bin(cfg):
    """Newest node bin dir matching the repo's node_glob (e.g. Node 26 under nvm), or None."""
    import glob as _g
    hits = sorted(_g.glob(os.path.expanduser(cfg.get("node_glob", "")))) if cfg.get("node_glob") else []
    return hits[-1] if hits else None


def write_status(td, **kv):
    """Update STATUS.md frontmatter. The one place that writes status, so every needs-user is logged (only on change, only when set)."""
    import time as _t
    path = Path(td) / "STATUS.md"
    meta, body = read_frontmatter(path)
    old = str(meta.get("needs_user") or "")
    meta.update(kv)
    meta["updated"] = _t.strftime("%Y-%m-%dT%H:%M:%S")
    write_frontmatter(path, meta, body)
    new = str(meta.get("needs_user") or "")
    if "needs_user" in kv and new and new != old:
        log_event(td, "needs_user", msg=new[:200])


def node_major_wanted(cfg):
    m = re.match(r".*/v(\d+)", cfg.get("node_glob") or "")
    return int(m.group(1)) if m else None


def ensure_node(cfg, say=print, dry=False):
    """Make sure the Node version the repo needs is installed under nvm. Returns the bin dir (or None when the repo states no version)."""
    major = node_major_wanted(cfg)
    if not major:
        return None
    nb = node_bin(cfg)
    if nb:
        return nb
    nvm = HOME / ".nvm" / "nvm.sh"
    if not nvm.exists():
        raise SystemExit("Node %d is needed but nvm is not installed (%s). Install nvm, then run `ale bootstrap`." % (major, nvm))
    say("Node %d is not installed: installing it with nvm" % major)
    if dry:
        return None
    r = subprocess.run(["bash", "-c", 'export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm install %d' % major], capture_output=True, text=True)
    nb = node_bin(cfg)
    if r.returncode != 0 or not nb:
        raise SystemExit("nvm install %d failed:\n%s" % (major, "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])))
    return nb


def _try(argv, env):
    """Run a command; a program that is not on PATH is a failure, not a crash."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, env=env)
        return r.returncode, (r.stdout + r.stderr)
    except FileNotFoundError:
        return 127, "%s: command not found" % argv[0]


def ensure_pm(cfg, env, say=print, dry=False):
    """yarn and pnpm are global npm packages, so a freshly installed Node has neither. Install the one the repo uses."""
    first = (cfg.get("setup") or [""])[0].split(" ")[0]
    if first not in ("yarn", "pnpm"):
        return
    if _try([first, "--version"], env)[0] == 0:
        return
    say("%s is not available under this Node: installing it globally with npm" % first)
    if dry:
        return
    rc, out = _try(["npm", "install", "-g", first], env)
    if rc != 0:
        raise SystemExit("could not install %s: `npm install -g %s` failed (%s)\n%s" % (first, first, "npm is not on PATH" if rc == 127 else "exit %d" % rc, "\n".join(out.strip().splitlines()[-6:])))
    if _try([first, "--version"], env)[0] != 0:
        raise SystemExit("%s was installed but is still not on PATH (check the npm global prefix)" % first)


def trust_folder(path):
    """Pre-accept Claude Code's "do you trust this folder" prompt for a folder ale created (a task worktree or a task folder), so every new space does not ask.
    Only folders under ~/projects or ~/.ale/tasks. Atomic read-modify-write of ~/.claude.json, which keeps every other key. Returns True if it changed anything."""
    real = os.path.realpath(str(path))
    allowed = [os.path.realpath(str(HOME / "projects")), os.path.realpath(str(TASKS))]   # worktrees and task folders only
    if not any(real.startswith(root + os.sep) for root in allowed):
        raise SystemExit("ale trust: only folders under ~/projects or the ale tasks folder can be trusted (got %s)" % real)
    cj = HOME / ".claude.json"
    if not cj.exists():
        return False
    data = json.loads(cj.read_text())
    entry = data.setdefault("projects", {}).setdefault(real, {})
    for k, v in (("allowedTools", []), ("mcpContextUris", []), ("mcpServers", {}), ("enabledMcpjsonServers", []), ("disabledMcpjsonServers", []),
                 ("hasClaudeMdExternalIncludesApproved", False), ("hasClaudeMdExternalIncludesWarningShown", False)):
        entry.setdefault(k, v)
    if entry.get("hasTrustDialogAccepted") is True:
        return False
    entry["hasTrustDialogAccepted"] = True
    tmp = cj.with_name(cj.name + ".ale-tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, cj)
    return True
