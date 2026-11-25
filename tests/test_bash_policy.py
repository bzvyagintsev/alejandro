"""The builder's Bash policy (lib/ale_bash.py through hooks/ale_hook.py). Run: sh tests/run.sh"""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from test_ale import Env, FW, HOOK, ALE, sh

sys.path.insert(0, str(FW / "lib"))
import ale_core  # noqa: E402


def payload(e, cmd, cwd=None):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd or e.wt)})


class BuilderPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Env()
        (cls.e.wt / "node_modules" / ".bin").mkdir(parents=True)
        (cls.e.wt / "node_modules" / ".bin" / "vitest").write_text("#!/bin/sh\n")
        (cls.e.wt / "pkg").mkdir()

    def b(self, cmd, role="builder", cwd=None):
        e = self.e
        r = sh("python3", str(HOOK), "pre-bash", cwd=cwd or e.wt, env=e.env(role), inp=payload(e, cmd, cwd))
        out = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
        return out.get("permissionDecision", "none"), out.get("permissionDecisionReason", ""), r.returncode

    def denied(self, cmds):
        for c in cmds:
            d, why, _ = self.b(c)
            self.assertEqual(d, "deny", "%r should be denied (%s)" % (c, why))

    def allowed(self, cmds):
        for c in cmds:
            d, why, _ = self.b(c)
            self.assertEqual(d, "allow", "%r should be allowed (%s)" % (c, why))

    def test_push_is_pinned_to_the_task_branch(self):
        self.denied(["git  push origin main", "git --no-pager push origin main", 'git -C "my dir" push origin main', "git push --mirror",
                     "git push origin --delete foo", "git push origin +HEAD:x", "git push origin HEAD:release/x", "git push", "git push origin",
                     "git push origin --tags", "git push upstream HEAD:NOJIRA/x", "git -c remote.origin.pushurl=x push origin HEAD:NOJIRA/x",
                     "git push --force origin HEAD:NOJIRA/x", "git push origin :NOJIRA/x"])
        self.allowed(["git push -u origin HEAD:NOJIRA/x", "git push origin NOJIRA/x", "git push origin NOJIRA/x:NOJIRA/x"])

    def test_branch_named_like_main_is_not_a_false_positive(self):
        e = Env()
        t = (e.td / "TASK.md").read_text().replace("branch: NOJIRA/x", "branch: feature/main")
        (e.td / "TASK.md").write_text(t)
        r = sh("python3", str(HOOK), "pre-bash", cwd=e.wt, env=e.env("builder"), inp=payload(e, "git push -u origin HEAD:feature/main"))
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "allow", r.stdout)

    def test_wrappers_and_substitutions_are_seen(self):
        self.denied(["env A=1 git push origin main", "echo a | xargs git push origin main", "sh -c 'git push origin main'",
                     'bash -lc "git push origin main"', "eval git push origin main", 'echo "$(git push --mirror)"', "x=`git push --mirror`",
                     "FOO=1 git push origin main", "rtk git push origin main", "git $'\\x70ush' origin main",
                     "bash <<EOF\ngit push origin main\nEOF", "echo hi | bash", "$CMD arg", "find . -exec git push origin main \;"])

    def test_prose_and_heredocs_are_not_commands(self):
        self.allowed(['gh pr create --draft --body "uses env; printenv"', 'gh pr create --draft --title "--draft x"',
                      "cat <<'EOF' > f.txt\nrun git push origin main now\nEOF",
                      "git commit -m \"$(cat <<'EOF'\nenv var fix; git push origin main\nEOF\n)\"",
                      'rg "a|kill -9" src', 'git commit -m "security fix"', 'echo "git push origin main"'])

    def test_gh_is_default_deny(self):
        self.denied(["gh -R o/r pr merge 1", "gh pr --repo o/r merge 1", "gh api repos/a/b/issues/1/comments -fbody=x",
                     "gh api --method=POST repos/a/b", "gh api -XPOST repos/a/b", "gh api graphql -f query=x", "gh gist create f", "gh pr checkout 3",
                     "gh alias set a b", "gh pr comment 3 -b x", "gh pr ready", "gh pr edit 3 --body x", "gh pr edit 3 --base main",
                     "gh pr create --title x", "gh pr close 3", "gh pr review 3 --approve", "gh issue create", "gh pr update-branch --rebase"])
        self.allowed(["gh pr view 3 --json state", "gh pr list", "gh pr diff 3", "gh pr checks 3", "gh run rerun 5 --failed", "gh run view 5 --log-failed",
                      "gh api repos/a/b/pulls/3 --jq .state", "gh api --method=GET repos/a/b", "gh pr edit 3 --title x --add-label y",
                      "gh pr update-branch", "gh auth status", "gh pr create --draft --title x"])

    def test_hook_skipping_and_git_overrides(self):
        self.denied(["HUSKY=0 git commit -m x", "git commit -n -m x", "git -c core.hooksPath=/dev/null commit -m x", "git commit --no-verify -m x",
                     "GIT_SSH_COMMAND=x git fetch", "git clean -fd", "git filter-branch x", "git reflog expire --all", "git update-ref -d refs/x",
                     "git branch -D other", "git worktree remove ../x"])
        self.allowed(["git commit -m x", "GIT_EDITOR=true git rebase --continue", "git fetch origin", "git branch --unset-upstream", "git branch -d NOJIRA/x"])

    def test_git_config_reads_only(self):
        self.denied(["git config user.email x", "git config --unset x", "git config alias.x push --get", "git config --global --add a b", "git remote add x y",
                     "git remote set-url origin y"])
        self.allowed(["git config --get user.name", "git config --list", "git config get core.editor", "git config user.name", "git remote -v", "git remote get-url origin"])

    def test_rebase_pull_reset(self):
        self.allowed(["git rebase origin/main", "git rebase --onto origin/main origin/main HEAD", "git rebase --onto origin/main origin/main NOJIRA/x", "git rebase --continue", "git rebase --abort", "git rebase --skip", "git pull --ff-only",
                      "git pull --ff-only origin NOJIRA/x", "git reset --soft HEAD~1", "git reset --mixed HEAD~1", "git reset --hard origin/NOJIRA/x"])
        self.denied(["git rebase -i origin/main", "git rebase --onto a b", "git rebase --onto origin/main deadbeef HEAD", "git rebase --onto origin/main origin/main other-branch", "git rebase --onto origin/other origin/main HEAD", "git rebase origin/other", "git rebase", "git rebase --exec x origin/main",
                     "git pull", "git pull --rebase", "git pull origin other", "git reset --hard HEAD~1", "git reset --hard", "git reset --merge x"])
        (self.e.wt / "README.md").write_text("changed")
        try:
            self.assertEqual(self.b("git rebase origin/main")[0], "deny")  # dirty tree
        finally:
            sh("git", "checkout", "--", "README.md", cwd=self.e.wt)
        sh("git", "checkout", "-q", "-b", "other", cwd=self.e.wt)
        try:
            self.assertEqual(self.b("git rebase origin/main")[0], "deny")  # not on the task branch
        finally:
            sh("git", "checkout", "-q", "NOJIRA/x", cwd=self.e.wt)

    def test_package_managers(self):
        self.denied(["npm publish", "npx -p x y", "npx -y x", "npx --package x y", "npx -c 'x'", "npx random-pkg", "pnpm dlx x", "yarn dlx x", "yarn global add x", "npm install -g x",
                     "npm config set registry x", "npm exec x", "pip install x", "python3 -m pip install x", "brew install x"])
        self.allowed(["npx prettier --check src/a.ts", "npx vitest run --reporter=dot x", "npx --no-install vitest run x", "cd pkg && npx vitest run y", "pnpm --dir pkg exec vitest run y", "yarn test src/a.spec.ts", "npx jest src/a.spec.ts",
                      "yarn install", "pnpm install", "npm run build"])

    def test_machine_and_terminal_tools(self):
        self.denied(["sudo ls", "ssh x", "scp a b:c", "pkill node", "killall node", "docker ps", "osascript -e x", "security find-generic-password", "printenv", "env",
                     "hc kill t1", "hc run t1 x", "herdr-app ls"])
        self.allowed(["kill 123", "curl -s http://localhost:3000", "hc send t1 hi", "hc wait t1", "hc read t1", "lsof -ti:3000", "chmod +x s.sh"])

    def test_ordinary_shapes_just_work(self):
        self.allowed(["ls | head", "cd pkg && git status", "git status && git diff | head -5", "node -e 'console.log(1)'", "python3 -c 'print(1)'",
                      "FOO=1 yarn test", "git add src/a.ts", "echo hi > /dev/null", "echo x > .ale/scratch/notes.md", "ale gate", "ale set phase=building",
                      "for f in a b; do echo $f; done", "ls $(pwd)"])

    def test_protected_files_cannot_be_written(self):
        fw = str(self.e.fw)
        self.denied(["cd pkg && echo x > ../.ale/approval.json", "echo x > .ale/gates.json", "echo x >> .ale/counters.json", "tee .ale/approval.json",
                     "sed -i '' x %s/hooks/ale_hook.py" % fw, "cp f .claude/settings.json", "echo x > .git/hooks/pre-commit", "cp f .git/config",
                     "mv f ~/.claude/settings.json", "echo x > ~/.ssh/config", "dd if=f of=.ale/gates.json", "echo x > %s/key" % self.e.home,
                     "cat ~/.ale/key", "rm .ale/gates.json"])

    def test_git_config_is_protected_in_a_linked_worktree(self):
        wt2 = self.e.d / "wt2"
        sh("git", "worktree", "add", "-q", "-b", "NOJIRA/y", str(wt2), cwd=self.e.wt)
        common = self.e.wt / ".git"
        for c in ["echo x > %s/config" % common, "echo x > %s/hooks/pre-commit" % common, "echo x > %s/worktrees/wt2/config.worktree" % common]:
            d, why, _ = self.b(c, cwd=wt2)
            self.assertEqual(d, "deny", "%r: %s" % (c, why))

    def test_other_roles_are_untouched(self):
        for role in ("reviewer", "lead", "alejandro"):
            d, _, rc = self.b("touch x", role=role)
            self.assertEqual((d, rc), ("none", 0), role)  # no opinion: their own allow rules decide
        self.assertEqual(self.b("git status", role="reviewer")[0], "none")

    def test_check_rm_and_open_still_apply(self):
        self.denied(["rm -rf src", "rm -f x", "rm README.md", "open https://example.com", "open -a Safari"])

    def test_unparseable_risky_command_is_denied_plain_is_not(self):
        self.assertEqual(self.b("git push origin 'unterminated")[0], "deny")
        self.assertEqual(self.b("echo 'unterminated")[0], "allow")


class HookFailure(unittest.TestCase):
    def run_event(self, event, role="builder"):
        e = Env()
        code = ("import sys, importlib.util\nspec = importlib.util.spec_from_file_location('h', %r)\nm = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(m)\ndef boom(i):\n    raise RuntimeError('boom')\n"
                "m.pre_bash = m.pre_edit = m.pre_mcp = m.stop = m.user_prompt = m.session = boom\nsys.argv = ['h', %r]\nm.main()\n") % (str(HOOK), event)
        r = sh("python3", "-c", code, cwd=e.wt, env=e.env(role), inp=json.dumps({"cwd": str(e.wt)}))
        return e, r

    def test_blocking_events_exit_2(self):
        for ev in ("pre-bash", "pre-edit", "pre-mcp"):
            e, r = self.run_event(ev)
            self.assertEqual(r.returncode, 2, ev)
            self.assertIn("blocked", r.stderr)

    def test_other_events_never_trap_the_agent(self):
        for ev in ("stop", "user-prompt", "session"):
            e, r = self.run_event(ev)
            self.assertEqual(r.returncode, 0, ev)

    def test_errors_and_denials_are_logged(self):
        e, r = self.run_event("pre-bash")
        log = e.home / "logs" / "hook.jsonl"
        self.assertIn("hook_error", log.read_text())
        self.assertIn("hook_error", (e.td / "events.jsonl").read_text())
        d = sh("python3", str(HOOK), "pre-bash", cwd=e.wt, env=e.env("builder"), inp=payload(e, "GH_TOKEN=abc123 git push origin main"))
        self.assertIn("deny", d.stdout)
        text = log.read_text()
        self.assertIn("bash_denied", text)
        self.assertNotIn("abc123", text)

    def test_hook_log_groups_by_reason(self):
        e = Env()
        (e.home / "logs").mkdir()
        rows = [{"ts": 4102444800, "kind": "bash_denied", "role": "builder", "task": "T-1", "cmd": "git push origin x", "why": "push only your branch"}] * 3
        (e.home / "logs" / "hook.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        r = sh("python3", str(ALE), "hook-log", env=e.env("builder"))
        self.assertRegex(r.stdout, r"3\s+bash_denied\s+push only your branch", r.stdout + r.stderr)


class Redaction(unittest.TestCase):
    def test_secrets_are_hidden_and_long_text_cut(self):
        t = ale_core.redact("GH_TOKEN=abc curl -H 'Authorization: Bearer xyz' --token qrs https://u:pw@host/x " + "a" * 400)
        for secret in ("abc", "xyz", "qrs", ":pw@"):
            self.assertNotIn(secret, t)
        self.assertLessEqual(len(t), 305)


if __name__ == "__main__":
    unittest.main()


class StackedRetarget(unittest.TestCase):
    """The base PR was squash-merged: move the branch onto main, dropping the base's old commits."""

    def test_onto_main_after_the_base_merged(self):
        e = Env()
        t = (e.td / "TASK.md").read_text().replace("base: main", "base: NOJIRA/base")
        (e.td / "TASK.md").write_text(t)
        sh("git", "checkout", "-q", "-b", "NOJIRA/base", cwd=e.wt)
        e.commit("b.txt", "b", "feat(NOJIRA): base work")
        base_sha = sh("git", "rev-parse", "HEAD", cwd=e.wt).stdout.strip()
        sh("git", "checkout", "-q", "NOJIRA/x", cwd=e.wt)
        sh("git", "reset", "-q", "--hard", base_sha, cwd=e.wt)
        e.commit("c.txt", "c", "feat(NOJIRA): my work")

        def go(cmd):
            r = sh("python3", str(HOOK), "pre-bash", cwd=e.wt, env=e.env("builder"), inp=payload(e, cmd))
            return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]
        self.assertEqual(go("git rebase --onto origin/main %s HEAD" % base_sha), "allow")
        self.assertEqual(go("git rebase origin/main"), "allow")
        self.assertEqual(go("git rebase --onto origin/main %s HEAD" % ("0" * 40)), "deny")
        self.assertEqual(go("git rebase --onto origin/NOJIRA/x %s HEAD" % base_sha), "deny")
