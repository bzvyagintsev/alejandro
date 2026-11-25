"""Bash policy for the builder role. Pure functions, no LLM.

`verdict(cmd, ctx)` returns None (allowed) or the reason a command is denied. The hook then allows
everything that is not denied, so the builder no longer needs a list of allowed command strings.

How it works:
  1. flatten: join continuations, drop heredoc bodies, pull out $(...), `...` and <(...) bodies, tokenize
     with quote awareness, split into simple commands, unwrap env/xargs/timeout/rtk..., recurse into
     `sh -c`, `eval` and `find -exec`.
  2. check every simple command against small tables (git, gh, package managers, machine tools) and
     against the files agents must never write (keys, approvals, settings, the framework, .git/config).
  3. the legacy regex rules (ctx.backstop) run last on a skeleton of each command in which prose in
     quotes is blanked out, so `--body "uses env; printenv"` is not a false positive.

This prevents accidents. It is not a sandbox: `python3 -c` can still do anything the user can.
Anything the parser cannot read and that mentions a risky program is denied (fail closed).
"""
import os
import re
import subprocess

SUB = "\x00SUB\x00"  # stands for a removed $(...) body; as a command word it cannot be checked
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RISKY = re.compile(r"\b(git|gh|rm|open|npm|yarn|pnpm|npx|sudo|ssh)\b")


class Deny(Exception):
    pass


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- parsing

HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_]\w*)\1")


def strip_heredocs(s):
    """Drop heredoc bodies (text, not commands). Returns (script, shell_reads_heredoc)."""
    out, pending, shell = [], [], False
    for line in s.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        for m in HEREDOC.finditer(line):
            pending.append(m.group(2))
            before = re.split(r"[;&|]", line[:m.start()])[-1]
            if re.search(r"\b(ba|z|da)?sh\b", before) or re.search(r"\|\s*(env\s+)?(ba|z|da)?sh\b", line[m.end():]):
                shell = True
    return "\n".join(out), shell


def _match_paren(s, i):
    """Index of the `)` that closes a `(` opened just before s[i]."""
    depth, n = 1, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            j = s.find("'", i + 1)
            if j < 0:
                break
            i = j + 1
            continue
        if ch == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ParseError("unbalanced parenthesis")


def extract_subs(s):
    """Replace $(...), `...`, <(...) and >(...) bodies by SUB; return (script, [bodies]). Single quotes stay opaque."""
    out, subs, i, n, sq, dq = [], [], 0, len(s), False, False
    while i < n:
        ch = s[i]
        if sq:
            out.append(ch)
            sq = ch != "'"
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(s[i:i + 2])
            i += 2
            continue
        if ch == "'" and not dq:
            sq = True
        elif ch == '"':
            dq = not dq
        elif (ch == "$" and s.startswith("$(", i)) or (not dq and ch in "<>" and s.startswith("(", i + 1)):
            end = _match_paren(s, i + 2)
            subs.append(s[i + 2:end])
            out.append(SUB)
            i = end + 1
            continue
        elif ch == "`":
            j = i + 1
            while j < n and s[j] != "`":
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                raise ParseError("unterminated backtick")
            subs.append(s[i + 1:j])
            out.append(SUB)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), subs


REDIR = re.compile(r"(&>>|&>|>>|>\||>&|>|<<<|<<-|<<|<&|<)")


def tokenize(s):
    """Tokens: ("w", text, was_quoted), ("op", text), ("redir", op)."""
    toks, i, n, cur, inw, quoted = [], 0, len(s), [], False, False

    def end():
        nonlocal cur, inw, quoted
        if inw:
            toks.append(("w", "".join(cur), quoted))
        cur, inw, quoted = [], False, False

    while i < n:
        ch = s[i]
        if ch in " \t":
            end()
            i += 1
        elif ch == "\n":
            end()
            toks.append(("op", "\n"))
            i += 1
        elif ch == "#" and not inw:
            while i < n and s[i] != "\n":
                i += 1
        elif ch == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise ParseError("unterminated quote")
            cur.append(s[i + 1:j])
            inw = quoted = True
            i = j + 1
        elif ch == '"':
            j, buf = i + 1, []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n and s[j + 1] in '"\\$`':
                    buf.append(s[j + 1])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            if j >= n:
                raise ParseError("unterminated quote")
            cur.append("".join(buf))
            inw = quoted = True
            i = j + 1
        elif ch == "$" and s.startswith("$'", i):
            j = i + 2
            while j < n and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                raise ParseError("unterminated quote")
            try:
                cur.append(s[i + 2:j].encode("latin-1", "backslashreplace").decode("unicode_escape"))
            except Exception:
                raise ParseError("bad $'...' string")
            inw = quoted = True
            i = j + 1
        elif ch == "\\":
            if i + 1 < n:
                cur.append(s[i + 1])
                inw = True
            i += 2
        elif ch in "<>" or s.startswith("&>", i):
            if inw and not quoted and "".join(cur).isdigit():
                cur, inw = [], False  # a file descriptor number, e.g. 2>&1
            end()
            m = REDIR.match(s, i)
            toks.append(("redir", m.group(1)))
            i = m.end()
        elif ch in ";|&()":
            end()
            two = s[i:i + 2]
            if two in ("&&", "||", "|&"):
                toks.append(("op", two))
                i += 2
            else:
                toks.append(("op", ch))
                i += 1
        else:
            cur.append(ch)
            inw = True
            i += 1
    end()
    return toks


def split_cmds(toks):
    """Simple commands: {"words", "q" (was quoted), "env" [(k, v)], "redirs" [write targets]}."""
    cmds, cur, pending = [], None, None

    def new():
        return {"words": [], "q": [], "env": [], "redirs": [], "touched": False}

    cur = new()
    for t in toks:
        if t[0] == "op":
            if cur["words"] or cur["env"] or cur["redirs"]:
                cmds.append(cur)
            cur, pending = new(), None
            continue
        if t[0] == "redir":
            pending = t[1]
            continue
        text, quoted = t[1], t[2]
        if pending is not None:
            op, pending = pending, None
            if (op in (">", ">>", ">|", "&>", "&>>") or (op == ">&" and not (text.isdigit() or text == "-"))) and text != "/dev/null":
                cur["redirs"].append(text)
            continue
        if not cur["words"] and ASSIGN.match(text):
            cur["env"].append(tuple(text.split("=", 1)))
            continue
        if not quoted and text in ("{", "}") and not cur["words"]:
            continue
        cur["words"].append(text)
        cur["q"].append(quoted)
    if cur["words"] or cur["env"] or cur["redirs"]:
        cmds.append(cur)
    return cmds


def skeleton(c):
    """The command as plain text with prose in quotes blanked out: what the legacy regex rules look at."""
    parts = ["%s=%s" % (k, "Q" if re.search(r"\s", v) else v) for k, v in c["env"]]
    for w, q in zip(c["words"], c["q"]):
        parts.append("Q" if q and re.search(r"\s", w) else w)
    return " ".join(parts)


# wrapper -> options that take a value as the next word
WRAP_VALUE = {
    "env": {"-u", "-C", "-S", "--unset", "--chdir"}, "nice": {"-n"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {"-I", "-J", "-n", "-P", "-d", "-L", "-s", "-E", "-a", "-R", "-S"}, "ionice": {"-c", "-n", "-p", "-P", "-u"},
    "stdbuf": {"-i", "-o", "-e"}, "command": set(), "builtin": set(), "time": set(), "nohup": set(), "exec": set(),
    "setsid": set(), "rtk": set(),
}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}


def unwrap(c):
    """Strip env/command/time/nohup/timeout/nice/xargs/exec/rtk... so the real command word is first."""
    words, q, env = list(c["words"]), list(c["q"]), list(c["env"])
    prints_env = False
    while words and os.path.basename(words[0]) in WRAP_VALUE:
        prog = os.path.basename(words[0])
        del words[0], q[0]
        if prog == "rtk" and words and words[0] == "proxy":
            del words[0], q[0]
        while words and (words[0].startswith("-") or (prog == "env" and ASSIGN.match(words[0]))):
            w = words[0]
            del words[0], q[0]
            if ASSIGN.match(w) and not w.startswith("-"):
                env.append(tuple(w.split("=", 1)))
            elif w in WRAP_VALUE[prog] and words:
                del words[0], q[0]
        if prog == "timeout" and words:
            del words[0], q[0]  # the duration
        if prog == "env" and not words:
            prints_env = True
    d = dict(c, words=words, q=q, env=env)
    d["skel"] = c.get("skel") or skeleton(c)
    d["prints_env"] = prints_env
    return d


def flatten(script, depth=0):
    if depth > 6:
        raise Deny("commands are nested too deeply to check")
    script, shell_heredoc = strip_heredocs(script.replace("\\\n", ""))
    if shell_heredoc:
        raise Deny("a heredoc fed to a shell cannot be checked; write a script file and run it")
    script, subs = extract_subs(script)
    out = []
    for c in split_cmds(tokenize(script)):
        c["skel"] = skeleton(c)
        u = unwrap(c)
        words = u["words"]
        prog = os.path.basename(words[0]) if words else ""
        if prog in SHELLS:
            code = next((i for i, w in enumerate(words[1:], 1) if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", w)), None)
            if code is not None and code + 1 < len(words):
                out += flatten(words[code + 1], depth + 1)
                out.append(dict(u, words=[], q=[]))  # keep redirs and skeleton of the wrapper
                continue
            if not [w for w in words[1:] if not w.startswith("-")]:
                raise Deny("a shell without -c or a script reads commands from stdin; name the script file")
        elif prog == "eval":
            out += flatten(" ".join(words[1:]), depth + 1)
            continue
        elif prog in ("source", "."):
            raise Deny("do not source files; run the script directly")
        elif prog == "find" and any(w in ("-exec", "-execdir", "-ok", "-okdir") for w in words):
            i = next(i for i, w in enumerate(words) if w in ("-exec", "-execdir", "-ok", "-okdir"))
            inner = []
            for w in words[i + 1:]:
                if w in (";", "+"):
                    break
                inner.append(w)
            if inner:
                out += flatten(" ".join("'%s'" % w.replace("'", "'\\''") for w in inner), depth + 1)
        out.append(u)
    for sub in subs:
        out += flatten(sub, depth + 1)
    return out


# ---------------------------------------------------------------- state and helpers

class Ctx:
    """What the checks need to know. Built by the hook."""

    def __init__(self, cwd, branch, base, gate, ale_real, fw, home, check_rm, check_open, backstop, normalize):
        self.cwd, self.branch, self.base, self.gate = cwd, branch, base or "main", gate
        self.ale_real, self.fw, self.home = ale_real, fw, home
        self.check_rm, self.check_open, self.backstop, self.normalize = check_rm, check_open, backstop, normalize


def _git(args, cwd):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def positionals(words, value_flags=()):
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w.startswith("-"):
            i += 2 if w in value_flags else 1
        else:
            out.append(w)
            i += 1
    return out


def _real(path, st):
    p = path.replace("${HOME}", st.ctx.home).replace("$HOME", st.ctx.home)
    p = os.path.expanduser(p)
    if "$" in p or SUB in p:
        return None
    if not os.path.isabs(p):
        p = os.path.join(st.cwd, p)
    return os.path.realpath(p)


CONTROL_FILES = {"DECISIONS.md", "gates.json", "approval.json", "jira-approval.json", "pending-jira.json", "counters.json"}


def protected_reason(real, st):
    c = st.ctx
    base = os.path.basename(real)
    if real == c.ale_real + "/key":
        return "the signing key is written only by `ale install`"
    if real.startswith(c.ale_real + "/tasks/") and (base in CONTROL_FILES or re.match(r"^settings-.*\.json$", base)):
        return "%s is written only by `ale` or by the user's typed message" % base
    if real in (c.ale_real + "/config.yaml", c.ale_real + "/rules.local.md", c.ale_real + "/banned-terms.txt"):
        return "your settings and local rules (they steer every agent and where the Jira token goes) are edited only by the user or `ale init`"
    if real.startswith(c.ale_real + "/policy/"):
        return "rendered role policy is written only by `ale install`"
    if real == c.home + "/.claude.json" or real.startswith((c.home + "/.claude/", c.home + "/.ssh/")):
        return "that is the user's own configuration"
    if real == c.fw or real.startswith(c.fw + "/") or "/agentic-framework/" in real + "/":
        return "the framework is changed by the user, not by agents"
    if re.search(r"/\.claude/settings[^/]*\.json$", real):
        return "settings files can add permissions and hooks; only the user edits them"
    for gd in st.git_dirs():
        if real in (gd + "/config", gd + "/config.worktree", gd + "/info/attributes") or real.startswith(gd + "/hooks/") or real == gd + "/hooks":
            return "git config and hooks run as every agent's git; only the user edits them"
        if real.startswith(gd + "/worktrees/") and os.path.basename(real).startswith("config"):
            return "git config and hooks run as every agent's git; only the user edits them"
    return None


class State:
    def __init__(self, ctx):
        self.ctx, self.cwd, self._gd = ctx, ctx.cwd, {}

    def git_dirs(self):
        if self.cwd not in self._gd:
            out = set()
            for flag in ("--git-dir", "--git-common-dir"):
                p = _git(["rev-parse", flag], self.cwd)
                if p:
                    out.add(os.path.realpath(os.path.join(self.cwd, p)))
            self._gd[self.cwd] = out
        return self._gd[self.cwd]

    def check_write(self, path):
        real = _real(path, self)
        why = protected_reason(real, self) if real else None
        if why:
            raise Deny("writing %s is not allowed: %s" % (path, why))


# ---------------------------------------------------------------- git

GIT_ENV_OK = {"GIT_EDITOR", "GIT_SEQUENCE_EDITOR"}
GIT_DENY_SUBS = {
    "clean": "`git clean` deletes untracked files for good",
    "filter-branch": "history rewriting tools are not allowed", "filter-repo": "history rewriting tools are not allowed",
    "update-ref": "do not move refs by hand", "replace": "do not replace objects",
    "credential": "credentials are not for agents", "daemon": "no servers", "send-email": "no email",
    "prune": "do not prune objects",
}
CFG_FLAGS = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--show-scope", "--name-only", "--local",
             "--global", "--system", "--worktree", "--null", "-z", "--bool", "--int", "--path", "--includes", "--no-includes"}
CFG_ACTIONS = {"--get", "--get-all", "--get-regexp", "--list", "-l"}


def check_config(rest):
    """Allowed only when the command is proven to be a read; anything else is a write."""
    flags, pos = [], []
    for a in rest:
        if a.startswith("-"):
            if pos or a not in CFG_FLAGS:
                raise Deny("`git config` is allowed only for reads (--get, --list, get <key>); changing config is the user's job")
            flags.append(a)
        else:
            pos.append(a)
    acts = [f for f in flags if f in CFG_ACTIONS]
    ok = False
    if len(acts) > 1:
        ok = False
    elif acts and acts[0] in ("--list", "-l"):
        ok = not pos
    elif acts:
        ok = 1 <= len(pos) <= 2
    elif pos[:1] == ["get"]:
        ok = len(pos) in (2, 3)
    elif pos[:1] == ["list"]:
        ok = len(pos) == 1
    else:
        ok = len(pos) <= 1 and (not pos or pos[0] not in ("set", "unset", "edit", "rename-section", "remove-section"))
    if not ok:
        raise Deny("`git config` is allowed only for reads (--get, --list, get <key>); changing config is the user's job")


def check_push(rest, st):
    ctx, lease, pos = st.ctx, False, []
    ok_flags = {"-u", "--set-upstream", "-v", "--verbose", "-q", "--quiet", "--porcelain", "--progress", "--no-progress", "--dry-run", "-n", "--force-if-includes"}
    for a in rest:
        if a.startswith("-"):
            if a == "--force-with-lease" or a.startswith("--force-with-lease="):
                lease = True
            elif a not in ok_flags:
                raise Deny("git push option %s is not allowed (use -u or --force-with-lease only)" % a)
        else:
            pos.append(a)
    if not ctx.branch:
        raise Deny("no task branch is known, so no push is allowed")
    if not pos:
        raise Deny("name the remote and branch: `git push -u origin HEAD:%s`" % ctx.branch)
    if pos[0] != "origin":
        raise Deny("push only to `origin` (not %s)" % pos[0])
    refs = pos[1:]
    if not refs:
        raise Deny("name the branch: `git push origin HEAD:%s` (a bare push can go to main)" % ctx.branch)
    for r in refs:
        if r.startswith("+") or r.startswith(":"):
            raise Deny("force or delete refspecs are not allowed (%s)" % r)
        if r not in (ctx.branch, "HEAD:" + ctx.branch, "%s:%s" % (ctx.branch, ctx.branch)):
            raise Deny("you may push only your own branch %s (not %s)" % (ctx.branch, r))
    if lease:
        ok, why = ctx.gate()
        if not ok:
            raise Deny("--force-with-lease needs a green gate on HEAD: %s. Run `ale gate` first" % why)


def check_rebase(rest, st, cwd):
    """Rebase the task branch onto its base, its own remote branch, or the default branch (a stacked PR whose base was squash-merged)."""
    ctx = st.ctx
    if rest in (["--continue"], ["--abort"], ["--skip"], ["--quit"]):
        return
    trunk = ["origin/main", "origin/master"]
    onto = None
    if "--onto" in rest:
        i = rest.index("--onto")
        if i + 1 >= len(rest):
            raise Deny("`git rebase --onto` needs a target")
        onto, rest = rest[i + 1], rest[:i] + rest[i + 2:]
        if onto not in trunk:
            raise Deny("`--onto` may only move your branch onto %s (the base was merged)" % " or ".join(trunk))
    flags = [a for a in rest if a.startswith("-")]
    pos = [a for a in rest if not a.startswith("-")]
    if any(f not in ("-q", "--quiet", "-v", "--verbose", "--no-stat") for f in flags):
        raise Deny("rebase option %s is not allowed (no -i, --exec, --autostash)" % [f for f in flags if f not in ("-q", "--quiet")][0])
    if onto:
        # git rebase --onto origin/main <old base> [HEAD|<task branch>]
        if not 1 <= len(pos) <= 2 or (len(pos) == 2 and pos[1] not in ("HEAD", ctx.branch)):
            raise Deny("use `git rebase --onto origin/main <old base commit or origin/<base>> [HEAD]`")
        up = pos[0]
        if up != "origin/" + ctx.base and _git(["merge-base", "--is-ancestor", up, "HEAD"], cwd) is None:
            raise Deny("the old base %s must be an ancestor of HEAD or origin/%s" % (up, ctx.base))
    else:
        allowed = ["origin/" + ctx.base] + (["origin/" + ctx.branch] if ctx.branch else []) + trunk
        if len(pos) != 1 or pos[0] not in allowed:
            raise Deny("rebase only onto %s" % " or ".join(allowed))
    cur = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if cur != ctx.branch:
        raise Deny("rebase only while your task branch %s is checked out (now: %s)" % (ctx.branch, cur))
    if _git(["status", "--porcelain", "--untracked-files=no"], cwd):
        raise Deny("rebase needs a clean tree: commit or stash your changes first")


def check_git(c, st):
    ctx, words, cwd = st.ctx, c["words"][1:], st.cwd
    for k, v in c["env"]:
        if k.startswith("GIT_") and not (k in GIT_ENV_OK and v in ("true", ":")):
            raise Deny("git environment overrides (%s=) are not allowed" % k)
    i = 0
    while i < len(words) and words[i].startswith("-"):
        w = words[i]
        if w == "-c" or (w.startswith("-c") and not w.startswith("--")):
            raise Deny("`git -c` is not allowed: it can run programs (core.sshCommand, alias, hooksPath) or change remotes")
        if w == "-C" and i + 1 < len(words):
            cwd = os.path.join(cwd, os.path.expanduser(words[i + 1]))
            i += 2
            continue
        if w.startswith(("--git-dir", "--work-tree", "--exec-path", "--super-prefix", "--namespace")):
            raise Deny("do not point git at another repository with %s; use `git -C <dir>`" % w.split("=")[0])
        i += 1
    if i >= len(words):
        return
    sub, rest = words[i], words[i + 1:]
    if "--no-verify" in rest:
        raise Deny("do not skip git hooks")
    if sub in ("commit", "push", "merge", "rebase", "cherry-pick", "am", "revert"):
        for k, _ in c["env"]:
            if re.match(r"^(HUSKY|SKIP|NO_VERIFY)", k):
                raise Deny("do not skip git hooks (%s=)" % k)
    if sub in GIT_DENY_SUBS:
        raise Deny("`git %s` is not allowed: %s" % (sub, GIT_DENY_SUBS[sub]))
    if sub == "commit":
        skip = False
        for a in rest:
            if skip:
                skip = False
            elif a in ("-m", "-F", "-C", "-c", "--message", "--file", "--reuse-message", "--reedit-message"):
                skip = True
            elif re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", a):
                raise Deny("do not skip git hooks (commit -n)")
    elif sub == "add":
        if any(a in ("-A", "--all", ".") for a in rest):
            raise Deny("stage explicit paths; never `git add -A` or `.`")
    elif sub == "push":
        check_push(rest, st)
    elif sub == "rebase":
        check_rebase(rest, st, cwd)
    elif sub == "pull":
        flags = [a for a in rest if a.startswith("-")]
        pos = [a for a in rest if not a.startswith("-")]
        if "--ff-only" not in flags or any(f not in ("--ff-only", "-q", "--quiet") for f in flags):
            raise Deny("pull only with `git pull --ff-only` (no merge or rebase pulls)")
        if pos and (len(pos) != 2 or pos[0] != "origin" or pos[1] not in (ctx.branch, ctx.base)):
            raise Deny("pull only `origin %s`" % ctx.branch)
    elif sub == "reset":
        if "--hard" in rest:
            pos = [a for a in rest if not a.startswith("-")]
            if pos != ["origin/%s" % ctx.branch]:
                raise Deny("`git reset --hard` only to origin/%s (it discards work)" % ctx.branch)
        elif any(a in ("--merge", "--keep") for a in rest):
            raise Deny("use `git reset --soft` or `--mixed`")
    elif sub == "config":
        check_config(rest)
    elif sub == "remote":
        if rest and rest[0] not in ("-v", "--verbose", "show", "get-url"):
            raise Deny("do not change remotes")
    elif sub == "branch":
        if any(a in ("-d", "-D", "--delete") for a in rest):
            names = [a for a in rest if not a.startswith("-")]
            if not names or any(n != ctx.branch for n in names):
                raise Deny("delete only your own task branch with `git branch -d`")
    elif sub == "worktree":
        if rest and rest[0] in ("remove", "prune"):
            raise Deny("`ale cleanup` removes worktrees")
    elif sub == "reflog":
        if rest and rest[0] in ("expire", "delete"):
            raise Deny("do not expire the reflog")
    elif sub == "gc":
        if any(a.startswith("--prune") for a in rest):
            raise Deny("do not prune objects")


# ---------------------------------------------------------------- gh

GH_ALLOW = {
    "pr": {"view", "list", "diff", "checks", "status", "update-branch", "create", "edit"},
    "run": {"list", "view", "watch", "rerun", "download"},
    "repo": {"view"}, "auth": {"status"}, "api": None,
}
GH_WHY = {
    ("pr", "merge"): "merging is not allowed for agents",
    ("pr", "ready"): "mark a PR ready with `ale pr-ready` (it checks the gate)",
    ("pr", "comment"): "replies to people are drafts for the user: write the text to a file and tell the user",
    ("pr", "review"): "reviews on GitHub need the user: show the draft first",
    ("pr", "close"): "closing a PR is the user's decision",
}
GH_VALUE_FLAGS = {"-R", "--repo", "--hostname", "-q", "--jq", "-H", "--header", "-t", "--template", "--cache", "-X", "--method"}


def check_gh(c, st):
    ctx, words = st.ctx, c["words"][1:]
    pos = positionals(words, GH_VALUE_FLAGS)
    if not pos or pos[0] == "help":
        return
    group, action = pos[0], (pos[1] if len(pos) > 1 else "")
    if group not in GH_ALLOW:
        raise Deny("`gh %s` is not allowed for agents" % group)
    if group == "api":
        for i, w in enumerate(words):
            if re.match(r"^(-f|-F|--field|--raw-field|--input)", w):
                raise Deny("`gh api` writes are not allowed; use `gh pr ...` subcommands")
            m = re.match(r"^(-X|--method)(=|)(.*)$", w)
            if m:
                val = m.group(3) or (words[i + 1] if i + 1 < len(words) else "")
                if val.upper() != "GET":
                    raise Deny("`gh api` writes are not allowed; use `gh pr ...` subcommands")
        if any(w == "graphql" or w.endswith("/graphql") for w in pos[1:]):
            raise Deny("`gh api graphql` is not allowed")
        return
    if action not in GH_ALLOW[group]:
        raise Deny("`gh %s %s` is not allowed: %s" % (group, action, GH_WHY.get((group, action), "not on the allowed list for agents")))
    flags = [w for w in words if w.startswith("-")]
    if (group, action) == ("pr", "update-branch") and "--rebase" in flags:
        raise Deny("use `gh pr update-branch` without --rebase (it adds a merge commit, no force-push), or rebase locally")
    if (group, action) == ("pr", "create") and "--draft" not in flags and "-d" not in flags:
        ok, why = ctx.gate()
        if not ok:
            raise Deny("opening a ready PR is blocked until the gate is green: %s" % why)
    if (group, action) == ("pr", "edit"):
        for w in flags:
            name = w.split("=")[0]
            if name in ("--body", "-b", "--body-file", "-F"):
                raise Deny("PR body edits need the user: read the current body and show the exact old and new text first")
            if name not in ("--title", "-t", "--add-label", "--remove-label"):
                raise Deny("`gh pr edit` may change only the title and labels")


# ---------------------------------------------------------------- package managers and machine tools

NPX_KNOWN = {"jest", "tsc", "eslint", "prettier", "playwright", "vitest"}
PKG_VALUE = {"--dir", "-C", "--prefix", "--filter", "-F", "--cwd", "--workspace"}
PKG_DENY_SUB = {"publish", "unpublish", "deprecate", "login", "logout", "adduser", "owner", "token", "access", "dist-tag", "dlx", "create"}
MACHINE = {
    "sudo": "no privileged commands", "su": "no privileged commands", "doas": "no privileged commands",
    "ssh": "no remote shells", "scp": "no remote copies", "sftp": "no remote copies", "rsync": "no remote copies",
    "nc": "no raw network tools", "ncat": "no raw network tools", "netcat": "no raw network tools", "ftp": "no raw network tools", "telnet": "no raw network tools",
    "osascript": "no desktop scripting", "security": "do not read the Keychain", "brew": "do not install system software",
    "docker": "no containers from agents", "pkill": "stop only your own process with `kill <pid>`", "killall": "stop only your own process with `kill <pid>`",
    "herdr": "terminals are managed by `ale` and `hc`", "herdr-app": "terminals are managed by `ale` and `hc`",
}


def local_bin(name, cwd):
    d = cwd
    while True:
        if os.path.exists(os.path.join(d, "node_modules", ".bin", name)):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def check_npx(args, st):
    # options BEFORE the tool name are npx's own (-p, -y, -c, --package fetch or run arbitrary packages); options after it belong to the tool
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] not in ("--no-install", "--no"):
            raise Deny("npx options (-p, -y, -c, --package) fetch or run arbitrary packages; run a local tool by name")
        i += 1
    if i >= len(args):
        raise Deny("npx needs a tool name")
    name = args[i]
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", name) or not (name in NPX_KNOWN or local_bin(name, st.cwd)):
        raise Deny("npx %s is not installed in this repo (node_modules/.bin). Install it as a dependency, or ask the user" % name)


def check_pkg(prog, args):
    pos = positionals(args, PKG_VALUE)
    sub = pos[0] if pos else ""
    if any(a in ("-g", "--global") for a in args) or sub == "global":
        raise Deny("global installs change the machine; install into the repo")
    if sub in PKG_DENY_SUB:
        raise Deny("`%s %s` is not allowed for agents" % (prog, sub))
    if sub == "npm" and len(pos) > 1 and pos[1] in ("publish", "login", "logout", "tag"):
        raise Deny("publishing and registry logins are not allowed for agents")
    if sub == "config" and len(pos) > 1 and pos[1] in ("set", "delete", "unset"):
        raise Deny("do not change package manager config")
    if prog == "npm" and sub in ("exec", "x"):
        raise Deny("`npm exec` can fetch packages; run the local tool by name")
    if prog == "npm" and sub == "init" and len(pos) > 1:
        raise Deny("`npm init <pkg>` runs a remote package")


WRITERS = {"tee", "cp", "mv", "ln", "install", "touch", "truncate", "rm", "unlink", "shred", "chmod", "chown", "chgrp", "mkdir", "rmdir"}


def check_cmd(c, st):
    ctx = st.ctx
    for t in c["redirs"]:
        st.check_write(t)
    if c.get("prints_env"):
        raise Deny("printing the environment can leak tokens")
    words = c["words"]
    if not words:
        return
    w0 = words[0]
    if w0.startswith("$") or SUB in w0:
        raise Deny("name the command: a variable or substitution as the command word cannot be checked")
    prog, args = os.path.basename(w0), words[1:]
    if prog in MACHINE:
        raise Deny("`%s` is not allowed: %s" % (prog, MACHINE[prog]))
    if prog == "cd":
        target = os.path.expanduser(args[0]) if args else ctx.home
        if "$" not in target and SUB not in target and target != "-":
            new = os.path.realpath(os.path.join(st.cwd, target))
            if os.path.isdir(new):
                st.cwd = new
        return
    if prog == "printenv":
        raise Deny("printing the environment can leak tokens")
    if prog == "git":
        check_git(c, st)
    elif prog == "gh":
        check_gh(c, st)
    elif prog in ("npm", "yarn", "pnpm"):
        check_pkg(prog, args)
    elif prog in ("npx", "pnpx", "bunx"):
        check_npx(args, st)
    elif prog in ("pip", "pip3", "pipx") or (prog.startswith("python") and args[:2] == ["-m", "pip"]):
        raise Deny("do not install Python packages from an agent")
    elif prog == "hc":
        if not args or args[0] not in ("send", "wait", "get", "read"):
            raise Deny("`hc` may only send, wait, get or read")
    elif prog == "rm":
        why = ctx.check_rm(" ".join("'%s'" % w.replace("'", "'\\''") for w in words), st.cwd)
        if why:
            raise Deny(why)
    elif prog == "open":
        why = ctx.check_open(" ".join("'%s'" % w.replace("'", "'\\''") for w in words), st.cwd)
        if why:
            raise Deny(why)
    if prog in WRITERS:
        for a in args:
            if not a.startswith("-"):
                st.check_write(a)
    elif prog == "sed" and any(re.match(r"^(-[a-zA-Z]*i|--in-place)", a) for a in args):
        for a in args:
            if not a.startswith("-"):
                st.check_write(a)
    elif prog == "dd":
        for a in args:
            if a.startswith("of="):
                st.check_write(a[3:])


def verdict(cmd, ctx):
    """None when the builder may run `cmd`; otherwise the reason it may not."""
    try:
        st = State(ctx)
        for c in flatten(cmd):
            check_cmd(c, st)
            for text in {c["skel"], ctx.normalize(c["skel"])}:
                for rx, why in ctx.backstop:
                    if re.search(rx, text):
                        raise Deny(why)
        return None
    except Deny as d:
        return str(d)
    except ParseError as e:
        if RISKY.search(cmd):
            return "could not read this command (%s); use plain quoting and one command per call" % e
        return None
