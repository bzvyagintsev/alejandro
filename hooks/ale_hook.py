#!/usr/bin/env python3
"""Claude Code hooks for ale roles. Pure script, no LLM.

Usage in a role's settings.json:  python3 "$ALE_FRAMEWORK/hooks/ale_hook.py" <event>
Each hook exits immediately (allow) when ALE_ROLE is not set, so it never affects ordinary sessions.
Reads the hook JSON on stdin. Prints a decision JSON on stdout when it needs to block or ask.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get("ALE_FRAMEWORK") or Path(__file__).resolve().parent.parent) / "lib"))
import ale_core as core  # noqa: E402
import ale_bash  # noqa: E402

ROLE = os.environ.get("ALE_ROLE", "")


def decide(kind, reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind, "permissionDecisionReason": reason}}))
    sys.exit(0)


def gate_or_deny(cwd, what):
    td = core.task_dir(cwd)
    ok, why = core.gate_valid(td, cwd)
    if not ok:
        decide("deny", "%s is blocked until the gate is green: %s" % (what, why))


# ---------------------------------------------------------------- bash

DENY = [
    (r"(^|[;&|(]\s*)(env|printenv)(\s|$)", "printing the environment can leak tokens"),
    (r"\bpgrep\s+-\w*f\w*l|\bps\b.*\bauxe?\b", "process listings can leak tokens from other sessions' environments"),
    (r"\byarn lint(\s|$)", "`yarn lint` runs eslint --fix and rewrites unrelated files; use `npx eslint <changed files>`"),
    (r"\bgit add\s+(-A|--all|\.)(\s|$)", "stage explicit paths; never `git add -A` or `.`"),
    (r"--no-verify\b", "do not skip git hooks"),
    (r"\bgh pr merge\b", "merging is not allowed for agents"),
    (r"\bgit push\b.*\b(main|master)\b(\s|$)", "never push to main or master"),
    (r"\bgit\s+(-C\s+\S+\s+)?(config|remote)\b.*(--global|set-url)", "do not change git config or remotes"),
    (r"--admin\b", "--admin bypasses branch protection"),
    (r"\bsecurity\s+find-generic-password\b.*\s-w\b", "do not read secrets from the Keychain"),
    (r"\.ale/(key|gates\.json)|/\.ale/key", "that file is written only by `ale`"),
    (r"jira-approval|pending-jira", "Jira approvals are written only by the user's typed message"),
    (r"\bALE_(ROLE|TASK|HOME|FRAMEWORK)=", "do not change the ale environment"),
]
ASK = [
    (r"\bgit push\b.*(--force\b|--force-with-lease|\s-f\b)", "force-push is allowed only as `git push --force-with-lease origin HEAD:<your task branch>` with a green gate on HEAD; anything else needs the user"),
    (r"\bgit reset\s+--hard\b", "destructive reset needs confirmation"),
    (r"\bgit rebase\b", "rebase rewrites history: confirm the exact command"),
    (r"\bgh pr edit\b.*--body", "PR body edits must preserve the user's changes: read the current body and show the exact diff first"),
]


def normalize(cmd):
    """Strip `git -C <dir>` and `git -c k=v` so rules match `git push` however it is written."""
    return re.sub(r"\bgit\s+(?:(?:-C\s+\S+|-c\s+\S+|--git-dir=\S+|--work-tree=\S+)\s+)+", "git ", cmd)


def segments(cmd):
    """The whole command plus each part of a chain (&&, ||, ;, |, newline, $(...), backticks)."""
    parts = re.split(r"&&|\|\||;|\||\n|\$\(|\)|`", cmd)
    return [cmd] + [p.strip() for p in parts if p.strip()]


def check_rm(seg, cwd):
    """`rm` is allowed only for single, untracked files inside the worktree (or the task's scratch folder). No -r, -f, globs or tracked files."""
    import shlex
    try:
        toks = shlex.split(seg)
    except ValueError:
        return "unparseable rm command"
    args = toks[1:]
    flags = [t for t in args if t.startswith("-")]
    paths = [t for t in args if not t.startswith("-")]
    if flags:
        return "rm flags are not allowed (no -r, -f); remove single files only"
    if not paths:
        return "rm needs a file"
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True).stdout.strip()
    top = os.path.realpath(top) if top else ""
    for pth in paths:
        if re.search(r"[*?\[{$`~]", pth):
            return "no globs or expansions in rm: name each file"
        real = os.path.realpath(os.path.join(cwd, pth))
        if os.path.isdir(real):
            return "rm cannot remove directories"
        if real.startswith(ALE_REAL + os.sep + "tasks" + os.sep) and os.sep + "scratch" + os.sep in real:
            continue
        if not top or not real.startswith(top + os.sep) or real.startswith(top + os.sep + ".git" + os.sep):
            return "rm is limited to files inside this worktree or .ale/scratch"
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", real], cwd=top, capture_output=True, text=True).returncode == 0
        if tracked:
            return "%s is tracked by git; use `git rm` through a commit, or ask the user" % pth
    return None


OPEN_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".webm", ".mp4", ".mov", ".pdf", ".md", ".txt", ".log"}


def check_open(seg, cwd):
    """`open` is allowed only to reveal or view media and docs inside the task folder or the worktree. No apps, URLs or executables."""
    import shlex
    try:
        toks = shlex.split(seg)
    except ValueError:
        return "unparseable open command"
    args = toks[1:]
    flags = [t for t in args if t.startswith("-")]
    paths = [t for t in args if not t.startswith("-")]
    if any(f != "-R" for f in flags):
        return "only `open -R <file>` (reveal in Finder) or `open <media file>` is allowed"
    if not paths:
        return "open needs a file"
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True).stdout.strip()
    top = os.path.realpath(top) if top else ""
    for pth in paths:
        if re.match(r"^[a-z][a-z0-9+.-]*://", pth, re.I) or re.search(r"[*?\[{$`~]", pth):
            return "no URLs, globs or expansions in open: name a file"
        real = os.path.realpath(os.path.join(cwd, pth))
        inside = real.startswith(ALE_REAL + os.sep + "tasks" + os.sep) or (top and real.startswith(top + os.sep))
        if not inside:
            return "open is limited to files in the task folder or this worktree"
        if "-R" in flags and os.path.exists(real):
            continue  # revealing never executes anything
        if os.path.splitext(real)[1].lower() not in OPEN_EXT:
            return "open can view only media and docs (%s)" % ", ".join(sorted(OPEN_EXT))
    return None


def force_push_ok(cmd, cwd):
    """`git push --force-with-lease` is allowed for the builder only to its own task branch, and only with a green gate on HEAD.
    Plain --force is never allowed here."""
    import shlex
    if ROLE != "builder" or re.search(r"--force(?!-with-lease)|(^|\s)-f(\s|$)", cmd):
        return False
    td = core.task_dir(cwd)
    meta = core.task_meta(td) if td else {}
    branch = meta.get("branch")
    m = re.search(r"\bgit push\b(.*)", cmd)
    if not branch or not m:
        return False
    try:
        toks = shlex.split(m.group(1))
    except ValueError:
        return False
    args = [t for t in toks if not t.startswith("-")]
    refs = args[1:]
    if refs:
        if any(r not in (branch, "HEAD:" + branch, "%s:%s" % (branch, branch)) for r in refs):
            return False
    else:
        cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True).stdout.strip()
        if cur != branch:
            return False
    return core.gate_valid(td, cwd)[0]


# The builder's backstop: the regex rules above, minus the two that need the parser (bare `env` is a wrapper
# there, and `git push ... main` would false-positive on a branch like feature/main; the push pin covers it).
BACKSTOP = [(rx, why) for rx, why in DENY if not rx.startswith(("(^|[;&|(]", r"\bgit push\b"))]


def pre_bash_builder(inp, raw, cwd):
    """Builder Bash is policed by lib/ale_bash.py: deny what is dangerous, allow everything else.
    The builder template still lists Bash allow rules as a FALLBACK (added 2026-11-20); they are safe to remove after 2026-12-04."""
    td = core.task_dir(cwd)
    meta = core.task_meta(td) if td else {}
    ctx = ale_bash.Ctx(cwd=cwd, branch=meta.get("branch"), base=meta.get("base"),
                       gate=lambda: core.gate_valid(td, cwd) if td else (False, "no task found"),
                       ale_real=ALE_REAL, fw=os.path.realpath(str(core.FW)), home=os.path.realpath(str(core.HOME)),
                       check_rm=check_rm, check_open=check_open, backstop=BACKSTOP, normalize=normalize)
    why = ale_bash.verdict(raw, ctx)
    if why:
        core.log_hook("bash_denied", ROLE, td, cmd=core.redact(raw), why=why)
        decide("deny", "ale guard: " + why)
    decide("allow", "ale policy: builder Bash is checked by the ale hook")


def pre_bash(inp):
    raw = (inp.get("tool_input") or {}).get("command", "")
    cwd = inp.get("cwd") or os.getcwd()
    if ROLE == "builder":
        pre_bash_builder(inp, raw, cwd)
    cmds = []
    for c in segments(raw):
        cmds.append(c)
        n = normalize(c)
        if n != c:
            cmds.append(n)
    for seg in segments(raw)[1:]:
        if re.match(r"^rm(\s|$)", seg):
            why = check_rm(seg, cwd)
            if why:
                decide("deny", "ale guard: " + why)
        if re.match(r"^open(\s|$)", seg):
            why = check_open(seg, cwd)
            if why:
                decide("deny", "ale guard: " + why)
    for cmd in cmds:
        for rx, why in DENY:
            if re.search(rx, cmd):
                decide("deny", "ale guard: " + why)
        if ROLE != "builder" and re.search(r"\bgit push\b", cmd):
            decide("deny", "ale guard: only the builder pushes, and only its own branch")
        m = re.search(r"\bgh api\b(.*)", cmd, re.S)
        if m and re.search(r"(-X|--method)\s*(POST|PUT|PATCH|DELETE)|\s(-f|-F|--field|--raw-field|--input)\b|graphql", m.group(1), re.I):
            decide("deny", "ale guard: `gh api` writes and graphql are not allowed; use `gh pr ...` subcommands")
    for cmd in cmds:
        if re.search(r"\bgh pr create\b", cmd) and "--draft" not in cmd:
            gate_or_deny(cwd, "opening a ready PR")
        if re.search(r"\bgh pr ready\b", cmd):
            gate_or_deny(cwd, "marking a PR ready (use `ale pr-ready`)")
    for cmd in cmds:
        if re.search(r"\bgh pr update-branch\b", cmd) and "--rebase" in cmd:
            decide("deny", "ale guard: use `gh pr update-branch` without --rebase (it adds a merge commit, no force-push), or rebase locally")
    for cmd in cmds:
        for rx, why in ASK:
            if re.search(rx, cmd):
                if rx.startswith(r"\bgit push") and force_push_ok(cmd, cwd):
                    continue
                decide("ask", "ale guard: " + why)
    sys.exit(0)


# ---------------------------------------------------------------- edit / write

PROTECTED = [r"DECISIONS\.md$", r"gates\.json$", r"approval\.json$", r"jira-approval\.json$", r"pending-jira\.json$", r"/agentic-framework/", r"/\.claude/(settings|CLAUDE|agents|skills)", r"counters\.json$"]
ALE_REAL = os.path.realpath(str(core.ALE))


def pre_edit(inp):
    ti = inp.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    cwd = inp.get("cwd") or os.getcwd()
    if not path:
        sys.exit(0)
    real = os.path.realpath(path)
    if real in [os.path.join(ALE_REAL, n) for n in ("key", "config.yaml", "rules.local.md", "banned-terms.txt")] or any(re.search(rx, real) for rx in PROTECTED):
        decide("deny", "ale guard: %s is protected (written only by `ale` or by the user)" % os.path.basename(path))
    td = core.task_dir(cwd)
    if ROLE == "reviewer":
        if not re.match(re.escape(ALE_REAL) + r"/tasks/[^/]+/reviews/", real):
            decide("deny", "ale guard: the reviewer may write only under .ale/reviews/")
        sys.exit(0)
    if ROLE == "builder" and td:
        in_task = real.startswith(ALE_REAL + os.sep)
        if not in_task:
            ok, why = core.approval_valid(td)
            if not ok:
                decide("deny", "ale guard: code edits start only after plan approval (%s). Write .ale/PLAN.md, run `ale plan-gate`, and wait." % why)
    sys.exit(0)


# ---------------------------------------------------------------- mcp

MCP_DENY = [
    (r"github.*(merge_pull_request|push_files|delete_|create_or_update_file|create_repository|fork_repository)", "GitHub writes through MCP are not allowed; use gh"),
    (r"slack.*(schedule_message|update_canvas|create_canvas)", "Slack posts are drafts only"),
    (r"(LaunchDarkly|launchdarkly).*(toggle|update-|delete-|create-|archive)", "LaunchDarkly writes are not allowed for agents"),
    (r"Datadog.*(delete_|upsert_|update_|create_|execute_datadog_workflow)", "Datadog writes are not allowed for agents"),
    (r"atlassian.*(createJiraIssue|editJiraIssue|addCommentToJiraIssue|transitionJiraIssue|addOrEditJiraIssueComment)", "Jira writes go through `ale jira`, with the user's approval for comments and edits"),
]


def slack_send_gate(inp):
    """The builder may post ONE review request, to the repo's configured channel, after the PR is marked ready. Everything else is denied."""
    ti = inp.get("tool_input") or {}
    cwd = inp.get("cwd") or os.getcwd()
    if ROLE != "builder":
        decide("deny", "ale guard: only the builder posts, and only the review request")
    td = core.task_dir(cwd)
    if not td:
        decide("deny", "ale guard: no task found; Slack posts need a task")
    meta = core.task_meta(td)
    events = []
    ep = Path(td) / "events.jsonl"
    if ep.exists():
        for line in ep.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    kinds = [e.get("kind") for e in events]
    if "pr_ready" not in kinds:
        decide("deny", "ale guard: post the review request only after `ale pr-ready` has marked the PR ready")
    if kinds.count("slack_sent") >= 1:
        decide("deny", "ale guard: the review request was already posted for this task. Tell the user if it needs a follow-up")
    want = meta.get("slack_channel_id")
    if not want or ti.get("channel_id") != want:
        decide("deny", "ale guard: this task may post only to its configured channel (%s)" % (meta.get("slack_channel") or "none set"))
    if ti.get("thread_ts") or ti.get("reply_broadcast"):
        decide("deny", "ale guard: post a new message, not a thread reply")
    msg = ti.get("message") or ""
    if len(msg) > 1500:
        decide("deny", "ale guard: keep the review request short (under 1500 characters)")
    if re.search(r"<!(here|channel|everyone)|<@|@here\b|@channel\b|@everyone\b", msg):
        decide("deny", "ale guard: no mentions (@here, @channel, @user) in the review request")
    key = str(meta.get("id", ""))
    if core.is_ticket(key) and key not in msg:
        decide("deny", "ale guard: the message must contain the ticket key %s" % key)
    if not re.search(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+", msg):
        decide("deny", "ale guard: the message must contain the PR link")
    core.log_event(td, "slack_sent", channel=want)
    sys.exit(0)


def pre_mcp(inp):
    name = inp.get("tool_name", "")
    ti = inp.get("tool_input") or {}
    cwd = inp.get("cwd") or os.getcwd()
    if re.search(r"slack.*slack_send_message$", name):
        slack_send_gate(inp)
    for rx, why in MCP_DENY:
        if re.search(rx, name):
            decide("deny", "ale guard: " + why)
    if re.search(r"github.*(create_pull_request|update_pull_request)", name):
        if not ti.get("draft", False):
            gate_or_deny(cwd, "opening or updating a ready PR")
    if re.search(r"pull_request_review_write", name):
        decide("ask", "ale guard: submitting a review needs the user's approval; show the draft first")
    sys.exit(0)


# ---------------------------------------------------------------- stop

def stop(inp):
    cwd = inp.get("cwd") or os.getcwd()
    td = core.task_dir(cwd)
    if ROLE != "builder" or not td:
        sys.exit(0)
    status, _ = core.read_frontmatter(Path(td) / "STATUS.md")
    phase = str(status.get("phase", ""))
    if phase not in ("done", "pr-ready", "shipped"):
        core.reset_counter(td, "stop_blocks")
        sys.exit(0)
    ok, why = core.gate_valid(td, cwd)
    if ok:
        core.reset_counter(td, "stop_blocks")
        sys.exit(0)
    lim = core.limits()["stop_blocks"]
    n = core.bump(td, "stop_blocks")
    if n > lim:
        core.write_status(td, needs_user="builder claims %s but the gate is not green (%s)" % (phase, why))
        core.reset_counter(td, "stop_blocks")
        sys.exit(0)  # let it stop; the user is told through needs_user
    print(json.dumps({"decision": "block", "reason": "STATUS says '%s' but the gate is not green: %s. Fix it, or set needs-user with `ale set needs-user=<why>`. (block %d of %d)" % (phase, why, n, lim)}))
    sys.exit(0)


def post_jira(inp, kind, code):
    """The user typed `post ticket|comment|edit <code>`: approve exactly the previewed Jira write with that code, then post it."""
    cwd = inp.get("cwd") or os.getcwd()
    td = core.task_dir(cwd)

    def say(msg):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}))
        sys.exit(0)
    pp = Path(td) / "pending-jira.json" if td else None
    if not pp or not pp.exists():
        say("The user typed 'post %s %s' but nothing is pending. Nothing was posted. Run the preview again." % (kind, code))
    pend = json.loads(pp.read_text())
    if pend.get("kind") != kind or pend.get("digest") != code:
        say("The user typed a code that does not match the pending preview (pending: %s %s). Nothing was posted." % (pend.get("kind"), pend.get("digest")))
    (Path(td) / "jira-approval.json").write_text(json.dumps({"digest": code, "ts": int(__import__("time").time())}))
    r = subprocess.run([sys.executable, str(core.FW / "bin" / "ale"), "jira", "post-pending"], capture_output=True, text=True, cwd=cwd, timeout=60)
    say("The user approved the previewed Jira %s (code %s). Result: %s" % (kind, code, ((r.stdout or "") + (r.stderr or "")).strip()[-400:]))


def user_prompt(inp):
    """A message the user types in the builder pane can approve the plan. The hook, not the agent, writes the approval.
    Only an exact `approve plan` typed by the user counts. Messages relayed by other agents arrive wrapped in <hc from=...> and never count."""
    prompt = (inp.get("prompt") or "").strip()
    pm = re.match(r"^post (ticket|comment|edit) ([0-9a-f]{6})$", prompt, re.I)
    if ROLE == "builder" and pm:
        post_jira(inp, {"ticket": "create", "comment": "comment", "edit": "edit"}[pm.group(1).lower()], pm.group(2).lower())
    if ROLE == "builder" and re.match(r"^one more round$", prompt, re.I):
        td = core.task_dir(inp.get("cwd") or os.getcwd())
        if td:
            n = core.bump(td, "review_extra")
            core.log_event(td, "round_granted", by="user", extra=n)
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "The user granted one more review round (%d extra so far). `ale review` will now accept it. Clear needs-user with `ale set needs-user=` and run it." % n}}))
        sys.exit(0)
    sm = re.match(r"^scope:\s*(\S.*)$", prompt, re.I | re.S)
    if ROLE == "builder" and sm:
        td = core.task_dir(inp.get("cwd") or os.getcwd())
        if td:
            core.add_decision(td, sm.group(1), "user, typed in the builder pane")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "The user recorded a scope decision in .ale/DECISIONS.md. It binds you and the reviewer, and it is pasted into every review request. Apply it, and update PLAN.md if it changes the plan."}}))
        sys.exit(0)
    m = re.match(r"^approve plan(?:\s+([0-9a-f]{6,12}))?$", prompt, re.I)
    if ROLE != "builder" or not m:
        sys.exit(0)
    td = core.task_dir(inp.get("cwd") or os.getcwd())
    ph = core.plan_hash(td) if td else None

    def say(msg):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}))
        sys.exit(0)
    if not ph:
        say("The user typed 'approve plan' but there is no .ale/PLAN.md yet. Nothing was approved. Write the plan first.")
    if m.group(1) and not ph.startswith(m.group(1).lower()):
        say("The user typed 'approve plan %s' but PLAN.md now has hash %s. Nothing was approved. Tell the user the plan changed." % (m.group(1), ph[:12]))
    (Path(td) / "approval.json").write_text(json.dumps({"by": "user", "plan_hash": ph, "tier": "user", "ts": int(__import__("time").time())}))
    core.log_event(td, "plan_approved", by="user", via="prompt")
    key = str(core.task_meta(td).get("id", ""))
    if core.is_ticket(key):
        try:
            subprocess.run([sys.executable, str(core.FW / "bin" / "ale"), "jira", "move", key, "start"], capture_output=True, timeout=30, cwd=inp.get("cwd") or os.getcwd())
        except Exception:
            pass  # a Jira failure never blocks the builder
    say("The user approved the plan (hash %s) by typing 'approve plan'. Code edits are now allowed. Set phase=building with `ale set` and start building." % ph[:12])


def session(inp):
    out = subprocess.run([sys.executable, str(core.FW / "bin" / "ale"), "context"], capture_output=True, text=True, cwd=inp.get("cwd") or os.getcwd())
    sys.stdout.write(out.stdout)
    sys.exit(0)


def main():
    if not ROLE:
        sys.exit(0)
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except Exception:
        inp = {}
    fn = {"pre-bash": pre_bash, "pre-edit": pre_edit, "pre-mcp": pre_mcp, "stop": stop, "session": session, "user-prompt": user_prompt}.get(event)
    if not fn:
        sys.exit(0)
    try:
        fn(inp)
    except SystemExit:
        raise
    except Exception as e:  # a crash would exit 1, which Claude Code treats as "no objection"
        err = "%s: %s" % (type(e).__name__, e)
        try:
            core.log_hook("hook_error", ROLE, core.task_dir(inp.get("cwd") or os.getcwd()), event=event, error=err)
        except Exception:
            pass
        if event in ("pre-bash", "pre-edit", "pre-mcp"):
            sys.stderr.write("ale hook error in %s (%s). The call was blocked. Tell the user; details are in ~/.ale/logs/hook.jsonl\n" % (event, err))
            sys.exit(2)
        sys.stderr.write("ale hook error in %s (%s); continuing without it.\n" % (event, err))
    sys.exit(0)


if __name__ == "__main__":
    main()
