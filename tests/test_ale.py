"""Tests for ale hooks and gate. Run: python3 -m unittest discover -s tests"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
HOOK = FW / "hooks" / "ale_hook.py"
ALE = FW / "bin" / "ale"


def sh(*a, cwd=None, env=None, inp=None):
    return subprocess.run(a, cwd=cwd, env=env, input=inp, capture_output=True, text=True)


class Env:
    """A throwaway framework dir, ALE_HOME, origin repo, worktree and task."""

    def __init__(self):
        self.d = Path(tempfile.mkdtemp())
        self.fw = self.d / "fw"
        (self.fw / "repos").mkdir(parents=True)
        (self.fw / "policy").mkdir()
        shutil.copytree(FW / "lib", self.fw / "lib")
        shutil.copy(FW / "policy" / "jira.yaml", self.fw / "policy" / "jira.yaml")
        (self.fw / "repos" / "t.yaml").write_text('checks:\n  unit: "true"\nui:\n  paths: ["src/**/*.tsx"]\n  exclude: ["**/*.spec.tsx"]\npr_max_loc: 50\n')
        self.home = self.d / "ale"
        (self.home / "tasks").mkdir(parents=True)
        shutil.copy(FW / "tests" / "fixtures" / "config.yaml", self.home / "config.yaml")
        (self.home / "key").write_bytes(b"k" * 32)
        self.origin = self.d / "origin.git"
        sh("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = self.d / "seed"
        sh("git", "clone", "-q", str(self.origin), str(seed))
        for c in (["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            sh("git", *c, cwd=seed)
        (seed / "README.md").write_text("x")
        sh("git", "add", "README.md", cwd=seed)
        sh("git", "commit", "-q", "-m", "chore(NOJIRA): init", cwd=seed)
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=seed)
        self.wt = self.d / "wt"
        sh("git", "clone", "-q", "-b", "main", str(self.origin), str(self.wt))
        for c in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["checkout", "-q", "-b", "NOJIRA/x"]):
            sh("git", *c, cwd=self.wt)
        self.td = self.home / "tasks" / "NOJIRA-x"
        (self.td / "reviews").mkdir(parents=True)
        (self.td / "artifacts").mkdir()
        (self.td / "TASK.md").write_text("---\nid: NOJIRA-x\nrepo: t\nbranch: NOJIRA/x\nbase: main\ntype: task\nsize: S\nslack_channel_id: C123\n---\nbody\n")
        (self.td / "STATUS.md").write_text("---\nphase: building\n---\n")
        (self.wt / ".ale").symlink_to(self.td)
        with open(self.wt / ".git" / "info" / "exclude", "a") as f:
            f.write(".ale\n")

    def env(self, role="builder", **kw):
        e = dict(os.environ, ALE_FRAMEWORK=str(self.fw), ALE_HOME=str(self.home), ALE_TASK="NOJIRA-x", PATH=os.environ["PATH"])
        if role:
            e["ALE_ROLE"] = role
        else:
            e.pop("ALE_ROLE", None)
        e.update(kw)
        return e

    def hook(self, event, payload, role="builder", extra=None):
        payload = dict(payload, cwd=str(self.wt))
        r = sh("python3", str(FW / "hooks" / "ale_hook.py"), event, cwd=self.wt, env=self.env(role, **(extra or {})), inp=json.dumps(payload))
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        d = out.get("hookSpecificOutput", {})
        return d.get("permissionDecision", out.get("decision", "allow")), d.get("permissionDecisionReason", out.get("reason", ""))

    def commit(self, path, text, msg):
        p = self.wt / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        sh("git", "add", path, cwd=self.wt)
        sh("git", "commit", "-q", "-m", msg, cwd=self.wt)

    def gate(self):
        return sh("python3", str(ALE), "gate", cwd=self.wt, env=self.env())


class HookBash(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def bash(self, cmd, role="builder"):
        return self.e.hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": cmd}}, role)[0]

    def test_no_role_never_blocks(self):
        self.assertEqual(self.bash("git add -A", role=None), "allow")

    def test_denies(self):
        for c in ["git add -A", "git add .", "yarn lint", "env", "printenv", "pgrep -fl node", "git commit --no-verify -m x",
                  "gh pr merge 12", "gh api -X POST repos/a/b/issues", "gh api graphql -f query=x", "git push origin main", "cat ~/.ale/key",
                  "git -C /x push origin main", "git status && git -C /x push origin HEAD:main", "git log -1; git push origin main"]:
            self.assertEqual(self.bash(c), "deny", c)

    def test_only_builder_pushes(self):
        self.assertEqual(self.bash("git -C /x push origin HEAD:feat", role="alejandro"), "deny")
        self.assertEqual(self.bash("git push origin HEAD:NOJIRA/x", role="reviewer"), "deny")
        self.assertEqual(self.bash("git push origin HEAD:NOJIRA/x", role="builder"), "allow")

    def test_allows(self):
        for c in ["git status", "yarn lint:ts", "git add src/a.ts", "gh pr view 3", "gh api repos/a/b/pulls/3", "npx eslint src/a.ts", "yarn test"]:
            self.assertEqual(self.bash(c), "allow", c)

    def test_builder_never_gets_ask(self):
        # `ask` is a hard deny in dontAsk mode, so the builder gets a deny with a reason or an allow
        for c in ["git push --force-with-lease origin HEAD:NOJIRA/x", "git reset --hard HEAD~1", "gh pr edit 3 --body-file b.md"]:
            self.assertEqual(self.bash(c), "deny", c)
        self.assertEqual(self.bash("git rebase origin/main"), "allow")

    def test_other_roles_still_ask(self):
        for c in ["git reset --hard HEAD~1", "git rebase origin/main"]:
            self.assertEqual(self.bash(c, role="reviewer"), "ask", c)

    def test_pr_create_needs_gate_but_draft_does_not(self):
        self.assertEqual(self.bash("gh pr create --draft --title x"), "allow")
        self.assertEqual(self.bash("gh pr create --title x"), "deny")
        self.assertEqual(self.bash("gh pr ready"), "deny")


class HookRm(unittest.TestCase):
    def test_rm_rules(self):
        e = Env()
        (e.wt / "scratch.ts").write_text("x")
        e.commit("src/keep.ts", "x", "feat(NOJIRA): keep")
        (e.td / "scratch").mkdir(exist_ok=True)
        (e.td / "scratch" / "m.ts").write_text("x")
        bash = lambda c: e.hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": c}})[0]
        self.assertEqual(bash("rm scratch.ts"), "allow")
        self.assertEqual(bash("rm %s" % (e.td / "scratch" / "m.ts")), "allow")
        self.assertEqual(bash("rm src/keep.ts"), "deny")
        self.assertEqual(bash("rm -rf src"), "deny")
        self.assertEqual(bash("rm -f scratch.ts"), "deny")
        self.assertEqual(bash("rm *.ts"), "deny")
        self.assertEqual(bash("rm ../outside.txt"), "deny")
        self.assertEqual(bash("rm src"), "deny")
        self.assertEqual(bash("ls && rm scratch.ts"), "allow")


class PrWatch(unittest.TestCase):
    def test_classify(self):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_pr
        base = {"state": "OPEN", "isDraft": False, "reviewDecision": "REVIEW_REQUIRED", "mergeStateStatus": "BLOCKED", "statusCheckRollup": []}
        c = lambda pr=None, items=(), seen=(): ale_pr.classify(dict(base, **(pr or {})), list(items), set(seen), "me")[0]
        self.assertIsNone(c())
        self.assertEqual(c({"state": "MERGED"}), "merged")
        self.assertEqual(c({"state": "CLOSED"}), "closed")
        self.assertEqual(c(items=[{"id": 1, "author": "bob", "body": "rename this"}]), "feedback")
        self.assertIsNone(c(items=[{"id": 1, "author": "bob", "body": "rename this"}], seen=["1"]))
        self.assertIsNone(c(items=[{"id": 2, "author": "me", "body": "done"}]))
        self.assertIsNone(c(items=[{"id": 3, "author": "dependabot[bot]", "body": "x"}]))
        for human in ("scott", "bob", "robert", "bot"):
            self.assertEqual(c(items=[{"id": 30, "author": human, "body": "rename this"}]), "feedback", human)  # names ending in b, o or t are people
        self.assertIsNone(c(items=[{"id": 4, "author": "bob", "body": "", "state": "APPROVED"}]))
        self.assertEqual(c({"statusCheckRollup": [{"name": "e2e", "conclusion": "FAILURE"}]}), "ci_failed")
        self.assertEqual(c({"mergeStateStatus": "DIRTY"}), "base")
        self.assertEqual(c({"mergeStateStatus": "BEHIND"}), "base")
        self.assertEqual(c({"reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN"}), "ready")
        self.assertIsNone(c({"reviewDecision": "APPROVED", "mergeStateStatus": "BLOCKED"}))
        self.assertIsNone(c({"isDraft": True, "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN"}))


class HookPush(unittest.TestCase):
    def test_force_with_lease_and_update_branch(self):
        e = Env()
        e.commit("src/a.spec.ts", "it('a', () => {})\n", "test(NOJIRA): a")
        e.commit("src/a.ts", "export const a = 1\n", "feat(NOJIRA): a")
        sha = sh("git", "rev-parse", "HEAD", cwd=e.wt).stdout.strip()
        (e.td / "reviews" / "round-1.md").write_text("VERDICT: GO\nsha: %s\nCRITICAL: 0\nMAJOR: 0\n" % sha)
        bash = lambda c, role="builder": e.hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": c}}, role)[0]
        lease = "git push --force-with-lease origin HEAD:NOJIRA/x"
        self.assertEqual(bash(lease), "deny")          # no gate yet
        self.assertEqual(e.gate().returncode, 0)
        self.assertEqual(bash(lease), "allow")          # own branch, green gate
        self.assertEqual(bash("git push --force-with-lease origin HEAD:NOJIRA/other"), "deny")
        self.assertEqual(bash("git push --force origin HEAD:NOJIRA/x"), "deny")
        self.assertEqual(bash(lease, role="reviewer"), "deny")
        self.assertEqual(bash("gh pr update-branch"), "allow")
        self.assertEqual(bash("gh pr update-branch --rebase"), "deny")

    def test_review_survives_squash(self):
        e = Env()
        e.commit("src/a.spec.ts", "it('a', () => {})\n", "test(NOJIRA): a")
        e.commit("src/a.ts", "export const a = 1\n", "feat(NOJIRA): a")
        sha = sh("git", "rev-parse", "HEAD", cwd=e.wt).stdout.strip()
        (e.td / "reviews" / "round-1.md").write_text("VERDICT: GO\nsha: %s\nCRITICAL: 0\nMAJOR: 0\n" % sha)
        sh("git", "reset", "--soft", "origin/main", cwd=e.wt)
        sh("git", "commit", "-q", "-m", "feat(NOJIRA): a", cwd=e.wt)
        r = e.gate()
        self.assertIn("PASS review", r.stdout, r.stdout)


class RewrittenHistory(unittest.TestCase):
    def upstream(self, e, path, text):
        c2 = e.d / "clone2"
        if not c2.exists():
            sh("git", "clone", "-q", str(e.origin), str(c2))
            for c in (["config", "user.email", "t@t"], ["config", "user.name", "t"]):
                sh("git", *c, cwd=c2)
        (c2 / path).parent.mkdir(parents=True, exist_ok=True)
        (c2 / path).write_text(text)
        sh("git", "add", path, cwd=c2)
        sh("git", "commit", "-q", "-m", "feat(PROJ-1): upstream", cwd=c2)
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=c2)

    def setup_pr(self):
        e = Env()
        e.commit("src/c.spec.tsx", "it('c', () => {})\n", "test(NOJIRA): c")
        e.commit("src/c.tsx", "export const C = () => null\n", "feat(NOJIRA): c")
        sha = sh("git", "rev-parse", "HEAD", cwd=e.wt).stdout.strip()
        (e.td / "reviews" / "round-1.md").write_text("VERDICT: GO\nsha: %s\nCRITICAL: 0\nMAJOR: 0\n" % sha)
        (e.td / "artifacts" / "ui-check.md").write_text("sha: %s\n- [PASS] ok\n" % sha[:7])
        return e

    def test_merge_of_base_does_not_break_names_or_unrelated_ui_check(self):
        e = self.setup_pr()
        self.upstream(e, "src/other.tsx", "export const O = 1\n")   # upstream UI file the PR does not touch
        sh("git", "fetch", "-q", "origin", cwd=e.wt)
        sh("git", "merge", "-q", "--no-edit", "origin/main", cwd=e.wt)
        out = e.gate().stdout
        self.assertIn("PASS names", out)
        self.assertIn("PASS ui-check", out)
        self.assertIn("PASS review", out)

    def test_upstream_change_to_a_file_the_pr_touches_needs_a_recheck(self):
        e = Env()
        self.upstream(e, "src/shared.tsx", "A\nb\nb\nb\nb\nb\nb\nb\nb\nC\n")
        sh("git", "fetch", "-q", "origin", cwd=e.wt)
        sh("git", "merge", "-q", "--ff-only", "origin/main", cwd=e.wt)
        e.commit("src/shared.spec.tsx", "it('s', () => {})\n", "test(NOJIRA): s")
        e.commit("src/shared.tsx", "A-changed-by-pr\nb\nb\nb\nb\nb\nb\nb\nb\nC\n", "feat(NOJIRA): s")
        sha = sh("git", "rev-parse", "HEAD", cwd=e.wt).stdout.strip()
        (e.td / "reviews" / "round-1.md").write_text("VERDICT: GO\nsha: %s\nCRITICAL: 0\nMAJOR: 0\n" % sha)
        (e.td / "artifacts" / "ui-check.md").write_text("sha: %s\n- [PASS] ok\n" % sha[:7])
        self.upstream(e, "src/shared.tsx", "A\nb\nb\nb\nb\nb\nb\nb\nb\nC-changed-upstream\n")
        sh("git", "fetch", "-q", "origin", cwd=e.wt)
        r = sh("git", "merge", "-q", "--no-edit", "origin/main", cwd=e.wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = e.gate().stdout
        self.assertIn("FAIL ui-check", out)
        self.assertIn("upstream changed files", out)
        self.assertIn("PASS review", out)   # the review follows the PR's patch, which did not change

    def test_sync_branch_refuses_to_lose_local_commits(self):
        e = self.setup_pr()
        sh("git", "push", "-q", "origin", "HEAD:refs/heads/NOJIRA/x", cwd=e.wt)
        e.commit("src/local.ts", "x", "feat(NOJIRA): local only")
        r = sh("python3", str(ALE), "sync-branch", cwd=e.wt, env=e.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not on the remote", r.stdout + r.stderr)


class FakeJira:
    """A tiny Jira: one ticket whose transitions depend on its status (shapes taken from the real TU workflow)."""

    FLOW = {
        "Selected": [{"id": "121", "name": "In Progress", "to": {"name": "In Progress"}}, {"id": "11", "name": "Cancelled", "to": {"name": "Cancelled"}}],
        "In Progress": [{"id": "71", "name": "Validating", "to": {"name": "In Review"}}, {"id": "12", "name": "Cancelled", "to": {"name": "Cancelled"}}],
        "In Review": [{"id": "81", "name": "Done", "to": {"name": "Done"}}, {"id": "91", "name": "In Progress", "to": {"name": "In Progress"}}],
        "Done": [{"id": "5", "name": "Reopen issue", "to": {"name": "In Progress"}}],
    }

    def __init__(self, status="Selected", issuetype="Task", type_value=None):
        import http.server, threading
        outer = self
        self.status, self.issuetype, self.type_value = status, issuetype, type_value
        self.summary, self.description, self.labels = "Old summary", {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "old description"}]}]}, ["a"]
        self.comments, self.created, self.requests = [], [], []
        self.thread = []  # comments the ticket already has (what `ale jira get` shows)

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                if "/transitions" in self.path:
                    return self._send(200, {"transitions": outer.FLOW.get(outer.status, [])})
                if "/comment" in self.path:
                    return self._send(200, {"comments": outer.thread, "total": len(outer.thread)})
                return self._send(200, {"fields": {"status": {"name": outer.status}, "issuetype": {"name": outer.issuetype}, "customfield_10001": outer.type_value,
                                                   "summary": outer.summary, "description": outer.description, "labels": outer.labels}})

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def do_POST(self):
                body = self._body()
                outer.requests.append(("POST", self.path, body))
                if self.path.endswith("/comment"):
                    outer.comments.append(body)
                    return self._send(201, {"id": "1"})
                if self.path.endswith("/issue"):
                    outer.created.append(body)
                    return self._send(201, {"key": "PROJ-777"})
                tid = body["transition"]["id"]
                for t in outer.FLOW.get(outer.status, []):
                    if t["id"] == tid:
                        outer.status = t["to"]["name"]
                        break
                self._send(204, {})

            def do_PUT(self):
                body = self._body()
                outer.requests.append(("PUT", self.path, body))
                f = body["fields"]
                outer.type_value = f.get("customfield_10001", outer.type_value)
                outer.summary = f.get("summary", outer.summary)
                outer.description = f.get("description", outer.description)
                outer.labels = f.get("labels", outer.labels)
                self._send(204, {})

        self.server = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class JiraWorkflow(unittest.TestCase):
    def env(self, e, jira):
        return e.env(ALE_JIRA_BASE=jira.url, JIRA_API_TOKEN="x", ALE_JIRA_EMAIL="a@b")

    def test_next_step_pure(self):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_jira
        order = ["Todo", "Selected", "In Progress", "In Review", "Done"]
        F = FakeJira.FLOW
        self.assertEqual(ale_jira.next_step("Selected", F["Selected"], "In Review", order)[0]["id"], "121")   # steps via In Progress
        self.assertEqual(ale_jira.next_step("In Progress", F["In Progress"], "In Review", order)[0]["name"], "Validating")  # name differs from target
        self.assertEqual(ale_jira.next_step("In Review", F["In Review"], "Done", order)[1], "direct")
        self.assertEqual(ale_jira.next_step("In Review", F["In Review"], "In Review", order)[1], "already")
        self.assertEqual(ale_jira.next_step("Done", F["Done"], "In Review", order)[1], "already")                 # past the target: do nothing
        self.assertEqual(ale_jira.next_step("Weird", [], "In Review", order)[1], "unknown")
        self.assertEqual(ale_jira.next_step("Selected", [{"id": "1", "name": "Cancelled", "to": {"name": "Cancelled"}}], "In Review", order)[1], "none")

    def test_move_to_review_goes_through_in_progress(self):
        e = Env()
        j = FakeJira("Selected")
        r = sh("python3", str(ALE), "jira", "move", "PROJ-1", "review", cwd=e.wt, env=self.env(e, j))
        j.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(j.status, "In Review")
        self.assertEqual([q[2]["transition"]["id"] for q in j.requests if q[0] == "POST"], ["121", "71"])

    def test_already_there_does_nothing_and_no_path_does_not_guess(self):
        e = Env()
        j = FakeJira("In Review")
        r = sh("python3", str(ALE), "jira", "move", "PROJ-1", "review", cwd=e.wt, env=self.env(e, j))
        self.assertIn("already", r.stdout)
        self.assertEqual([q for q in j.requests if q[0] == "POST"], [])
        j.close()
        j2 = FakeJira("Cancelled")
        r = sh("python3", str(ALE), "jira", "move", "PROJ-1", "review", cwd=e.wt, env=self.env(e, j2))
        j2.close()
        self.assertIn("Do not guess", r.stdout)

    def test_done_is_manual_preview_first_and_never_for_builder(self):
        e = Env()
        j = FakeJira("In Review")
        preview = sh("python3", str(ALE), "jira", "done", "PROJ-1", cwd=e.wt, env={**self.env(e, j), "ALE_ROLE": ""})
        self.assertIn("would move", preview.stdout)
        self.assertEqual(j.status, "In Review")
        builder = sh("python3", str(ALE), "jira", "done", "PROJ-1", "--yes", cwd=e.wt, env=self.env(e, j))   # env() sets ALE_ROLE=builder
        self.assertNotEqual(builder.returncode, 0)
        self.assertEqual(j.status, "In Review")
        user = sh("python3", str(ALE), "jira", "done", "PROJ-1", "--yes", cwd=e.wt, env={**self.env(e, j), "ALE_ROLE": ""})
        j.close()
        self.assertEqual(j.status, "Done", user.stdout + user.stderr)

    def test_bug_workflow_targets_in_code_review(self):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_jira
        cfg = __import__("yaml").safe_load(open(FW / "tests" / "fixtures" / "config.yaml"))["jira"]
        wf = ale_jira.workflow_for(cfg, "Bug")
        self.assertEqual(ale_jira.target_status(wf, "review"), "Code Review")
        self.assertEqual(ale_jira.target_status(wf, "done"), "Shipped")
        self.assertEqual(ale_jira.target_status(ale_jira.workflow_for(cfg, "Task"), "review"), "In Review")
        self.assertEqual(ale_jira.type_default(cfg, "Bug"), "Maintenance")
        self.assertEqual(ale_jira.type_default(cfg, "Story"), "Feature")
        self.assertIsNone(ale_jira.type_default(cfg, "Epic"))

    def test_approve_moves_ticket_to_in_progress_and_failure_does_not_block(self):
        e = Env()
        (e.td / "TASK.md").write_text((e.td / "TASK.md").read_text().replace("id: NOJIRA-x", "id: PROJ-1"))
        (e.td / "PLAN.md").write_text("plan")
        j = FakeJira("Selected")
        r = sh("python3", str(ALE), "approve", cwd=e.wt, env={**self.env(e, j), "ALE_ROLE": ""})
        j.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(j.status, "In Progress")
        self.assertTrue((e.td / "approval.json").exists())
        # Jira down: approval still succeeds and the failure is recorded
        e2 = Env()
        (e2.td / "TASK.md").write_text((e2.td / "TASK.md").read_text().replace("id: NOJIRA-x", "id: PROJ-2"))
        (e2.td / "PLAN.md").write_text("plan")
        r = sh("python3", str(ALE), "approve", cwd=e2.wt, env={**e2.env(ALE_JIRA_BASE="http://127.0.0.1:9", JIRA_API_TOKEN="x", ALE_JIRA_EMAIL="a@b"), "ALE_ROLE": ""})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((e2.td / "approval.json").exists())
        self.assertIn("jira_failed", (e2.td / "events.jsonl").read_text())

    def test_spawn_dry_run_makes_no_jira_request(self):
        j = FakeJira("Selected")
        d = Path(tempfile.mkdtemp())
        home, repo = d / "home", d / "home" / "projects" / "demo"
        repo.mkdir(parents=True)
        sh("git", "init", "-q", ".", cwd=repo)
        fw = d / "fw"
        (fw / "repos").mkdir(parents=True)
        (fw / "policy").mkdir()
        shutil.copytree(FW / "lib", fw / "lib")
        shutil.copy(FW / "policy" / "jira.yaml", fw / "policy" / "jira.yaml")
        (fw / "repos" / "demo.yaml").write_text("checks:\n  unit: 'true'\n")
        (home / ".ale" / "policy").mkdir(parents=True)
        for role in ("builder", "reviewer"):
            (home / ".ale" / "policy" / ("%s.json" % role)).write_text("{}")
        env = {k: v for k, v in os.environ.items() if k not in ("ALE_ROLE", "ALE_TASK")}
        env.update(HOME=str(home), ALE_FRAMEWORK=str(fw), ALE_HOME=str(home / ".ale"), ALE_ROLE="", PYTHONUSERBASE=RepoInit.USER_BASE,
                   ALE_JIRA_BASE=j.url, JIRA_API_TOKEN="x", ALE_JIRA_EMAIL="a@b")
        r = sh("python3", str(ALE), "spawn", "demo", "PROJ-9", "--branch", "PROJ-9/probe", "--type", "chore", "--text", "probe", "--dry-run", env=env)
        j.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(j.requests, [])   # a dry run touches Jira not at all, not even to read


class JiraWrites(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.j = FakeJira("In Progress")
        self.addCleanup(self.j.close)
        self.base = {**self.e.env(ALE_JIRA_BASE=self.j.url, JIRA_API_TOKEN="x", ALE_JIRA_EMAIL="a@b"), "ALE_ROLE": ""}
        self.home = self.e.home

    def ale(self, *args, role=""):
        return sh("python3", str(ALE), "jira", *args, cwd=self.e.wt, env={**self.base, "ALE_ROLE": role})

    def writes(self):
        return [q for q in self.j.requests if q[0] in ("POST", "PUT")]

    def test_adf_helpers(self):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_jira
        adf = ale_jira.text_to_adf("first line\nsecond line\n\nnew paragraph")
        self.assertEqual([p["type"] for p in adf["content"]], ["paragraph", "paragraph"])
        self.assertTrue(ale_jira.same_text(ale_jira.adf_to_text(adf), "first line second line new paragraph"))
        self.assertIn("-old", ale_jira.diff_lines("old", "new").replace(" ", ""))

    def test_comment_previews_then_posts_only_with_yes(self):
        (self.e.d / "c.md").write_text("Fixed in the PR.\n\nPlease check.")
        r = self.ale("comment", "PROJ-1", "--file", str(self.e.d / "c.md"))
        self.assertIn("Nothing was changed", r.stdout)
        self.assertEqual(self.writes(), [])
        r = self.ale("comment", "PROJ-1", "--file", str(self.e.d / "c.md"), "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.j.comments), 1)

    def test_builder_can_preview_but_never_write(self):
        (self.e.d / "c.md").write_text("hello")
        self.assertEqual(self.ale("comment", "PROJ-1", "--file", str(self.e.d / "c.md"), role="builder").returncode, 0)
        r = self.ale("comment", "PROJ-1", "--file", str(self.e.d / "c.md"), "--yes", role="builder")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.j.comments, [])
        r = self.ale("create", "--summary", "x", "--yes", role="reviewer")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.j.created, [])

    def test_edit_shows_diff_backs_up_and_verifies(self):
        (self.e.d / "d.md").write_text("new description text")
        r = self.ale("edit", "PROJ-1", "--summary", "New summary", "--description-file", str(self.e.d / "d.md"), "--labels", "a,b")
        self.assertIn("-old description", r.stdout)
        self.assertIn("+new description text", r.stdout)
        self.assertEqual(self.writes(), [])
        r = self.ale("edit", "PROJ-1", "--summary", "New summary", "--description-file", str(self.e.d / "d.md"), "--labels", "a,b", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.j.summary, "New summary")
        self.assertEqual(self.j.labels, ["a", "b"])
        self.assertNotIn("WARNING", r.stdout)
        backups = list((self.home / "backups" / "jira").glob("PROJ-1-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("old description", backups[0].read_text())

    def test_edit_with_no_change_does_nothing(self):
        r = self.ale("edit", "PROJ-1", "--summary", "Old summary", "--yes")
        self.assertIn("Nothing to change", r.stdout)
        self.assertEqual(self.writes(), [])

    def test_create_defaults_to_task_with_type_default(self):
        r = self.ale("create", "--summary", "Do the thing")
        self.assertIn("type: Task", r.stdout)
        self.assertEqual(self.j.created, [])
        r = self.ale("create", "--summary", "Do the thing", "--parent", "PROJ-5", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        f = self.j.created[0]["fields"]
        self.assertEqual(f["issuetype"], {"name": "Task"})
        self.assertEqual(f["project"], {"key": "TU"})
        self.assertEqual(f["parent"], {"key": "PROJ-5"})
        self.assertEqual(f["customfield_10001"], {"value": "Feature"})
        self.assertIn("PROJ-777", r.stdout)

    def test_create_needs_a_summary_and_jira_down_is_loud(self):
        self.assertNotEqual(self.ale("create").returncode, 0)
        env = {**self.base, "ALE_JIRA_BASE": "http://127.0.0.1:9"}
        r = sh("python3", str(ALE), "jira", "create", "--summary", "x", "--yes", cwd=self.e.wt, env=env)
        self.assertNotEqual(r.returncode, 0)   # an unreachable Jira is an error, never a silent success


class RepoInit(unittest.TestCase):
    def make_repo(self, files):
        d = Path(tempfile.mkdtemp())
        home = d / "home"
        repo = home / "projects" / "demo"
        repo.mkdir(parents=True)
        sh("git", "init", "-q", ".", cwd=repo)
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        fw = d / "fw"
        (fw / "repos").mkdir(parents=True)
        (fw / "policy").mkdir()
        shutil.copytree(FW / "lib", fw / "lib")
        return home, fw

    USER_BASE = subprocess.run(["python3", "-m", "site", "--user-base"], capture_output=True, text=True).stdout.strip()

    def init(self, home, fw, *args, role=""):
        env = {k: v for k, v in os.environ.items() if k not in ("ALE_ROLE", "ALE_TASK")}
        env.update(HOME=str(home), ALE_FRAMEWORK=str(fw), ALE_HOME=str(home / ".ale"), ALE_ROLE=role, PYTHONUSERBASE=self.USER_BASE)
        return sh("python3", str(ALE), "repo-init", "demo", *args, env=env)

    def test_node_repo(self):
        pkg = {"scripts": {"test": "jest", "lint:ts": "tsc --noEmit", "build": "vite build", "generate": "codegen", "test:e2e": "playwright test",
                           "start": "run-p a b", "b:start": "serve --port ${DEMO_PORT:-3000}"},
               "devDependencies": {"prettier": "3", "eslint": "9", "@playwright/test": "1", "typescript": "5"}, "engines": {"node": "22"}}
        home, fw = self.make_repo({"package.json": json.dumps(pkg), "yarn.lock": "", "src/App.tsx": "x", ".nvmrc": "v22.1.0"})
        r = self.init(home, fw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = yaml_load(fw / "repos" / "demo.yaml")
        self.assertTrue(cfg["generated"])
        self.assertEqual(cfg["node_glob"], "~/.nvm/versions/node/v22*/bin")
        self.assertEqual(list(cfg["checks"]), ["prettier", "eslint", "tsc", "unit", "e2e", "build"])
        self.assertEqual(cfg["checks"]["unit"], "yarn test")
        self.assertEqual(cfg["pre"], "yarn generate")
        self.assertIn("npx playwright install chromium", cfg["setup"])
        self.assertEqual((cfg["dev"], cfg["port_env"]), ("yarn b:start", "DEMO_PORT"))   # the script that carries the port variable wins
        self.assertEqual(cfg["ui"]["paths"], ["src/**/*.tsx"])
        self.assertEqual(self.init(home, fw).returncode, 1)                            # never overwrites silently
        self.assertEqual(self.init(home, fw, "--force").returncode, 0)

    def test_node_version_from_tool_versions_and_pnpm(self):
        home, fw = self.make_repo({"package.json": json.dumps({"scripts": {"test": "vitest"}}), "pnpm-lock.yaml": "", ".tool-versions": "nodejs 24.14.1\npnpm 11.13.1\n"})
        self.assertEqual(self.init(home, fw).returncode, 0)
        cfg = yaml_load(fw / "repos" / "demo.yaml")
        self.assertEqual(cfg["node_glob"], "~/.nvm/versions/node/v24*/bin")
        self.assertEqual(cfg["setup"][0], "pnpm install")
        self.assertEqual(cfg["checks"]["unit"], "pnpm test")

    def test_roles_can_search_code_with_git_grep(self):
        for role in ("reviewer", "lead", "alejandro"):
            allow = json.loads((FW / "policy" / ("%s.json.tmpl" % role)).read_text())["permissions"]["allow"]
            self.assertIn("Bash(git grep*)", allow, role)
            self.assertFalse([a for a in allow if a.startswith("Bash(git -C")], role)   # no wildcard before the subcommand

    def test_a_test_runner_is_not_a_dev_server_and_env_templates_are_flagged(self):
        pkg = {"scripts": {"test:dev": "playwright test --config x.ts", "start": "vitest watch", "test:unit": "jest"}}
        home, fw = self.make_repo({"package.json": json.dumps(pkg), "yarn.lock": "", "playwright.config.ts": "", ".env.dist": "A=1\n"})
        r = self.init(home, fw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = yaml_load(fw / "repos" / "demo.yaml")
        self.assertNotIn("dev", cfg)
        self.assertIn("e2e", cfg["checks"])
        self.assertIn("may need secrets or a staging environment", r.stdout)

    def test_go_repo_and_unknown_repo(self):
        home, fw = self.make_repo({"go.mod": "module demo\n"})
        self.assertEqual(self.init(home, fw).returncode, 0)
        cfg = yaml_load(fw / "repos" / "demo.yaml")
        self.assertEqual(list(cfg["checks"]), ["vet", "unit", "build"])
        home, fw = self.make_repo({"README.md": "x"})
        r = self.init(home, fw)
        self.assertEqual(r.returncode, 0)
        self.assertIn("only the universal checks", r.stdout)
        self.assertEqual(yaml_load(fw / "repos" / "demo.yaml")["checks"], {})

    def test_agents_cannot_write_their_own_gate_config(self):
        home, fw = self.make_repo({"go.mod": "module demo\n"})
        for role in ("builder", "reviewer"):
            self.assertNotEqual(self.init(home, fw, role=role).returncode, 0)
        self.assertFalse((fw / "repos" / "demo.yaml").exists())

    def test_dry_run_writes_nothing(self):
        home, fw = self.make_repo({"go.mod": "module demo\n"})
        r = self.init(home, fw, "--dry-run")
        self.assertIn("would write", r.stdout)
        self.assertFalse((fw / "repos" / "demo.yaml").exists())

    def test_spawn_drafts_config_for_a_new_repo(self):
        home, fw = self.make_repo({"go.mod": "module demo\n"})
        (home / ".ale" / "policy").mkdir(parents=True)
        for role in ("builder", "reviewer"):
            (home / ".ale" / "policy" / ("%s.json" % role)).write_text("{}")
        sh("git", "-c", "user.email=a@b", "-c", "user.name=n", "commit", "-q", "--allow-empty", "-m", "init", cwd=home / "projects" / "demo")
        env = {k: v for k, v in os.environ.items() if k not in ("ALE_ROLE", "ALE_TASK")}
        env.update(HOME=str(home), ALE_FRAMEWORK=str(fw), ALE_HOME=str(home / ".ale"), ALE_ROLE="", PYTHONUSERBASE=self.USER_BASE)
        r = sh("python3", str(ALE), "spawn", "demo", "NOJIRA-new", "--branch", "NOJIRA/new", "--type", "chore", "--text", "t", "--dry-run", env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("drafting repos/demo.yaml", r.stdout)
        self.assertFalse((fw / "repos" / "demo.yaml").exists())   # a dry run still writes nothing


def yaml_load(path):
    import yaml
    return yaml.safe_load(Path(path).read_text())


class NodeResolution(unittest.TestCase):
    def cfg_env(self, yaml_text):
        e = Env()
        (e.fw / "repos" / "t.yaml").write_text(yaml_text)
        return e

    def test_bootstrap_installs_a_missing_node_and_package_manager(self):
        e = self.cfg_env('node_glob: "~/.nvm/versions/node/v99*/bin"\nsetup:\n  - "yarn install"\nchecks:\n  unit: "true"\n')
        r = sh("python3", str(ALE), "bootstrap", "--dry-run", cwd=e.wt, env=e.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Node 99 is not installed: installing it with nvm", r.stdout)

    def test_installed_node_is_not_reinstalled(self):
        e = self.cfg_env('node_glob: "~/.nvm/versions/node/v2*/bin"\nsetup:\n  - "true"\nchecks:\n  unit: "true"\n')
        r = sh("python3", str(ALE), "bootstrap", "--dry-run", cwd=e.wt, env=e.env())
        self.assertNotIn("installing it with nvm", r.stdout)

    def test_gate_says_what_to_do_when_node_is_missing(self):
        e = self.cfg_env('node_glob: "~/.nvm/versions/node/v99*/bin"\nchecks:\n  unit: "true"\n')
        r = e.gate()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ale bootstrap", r.stdout + r.stderr)

    def test_major_parsed_from_glob(self):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_core
        self.assertEqual(ale_core.node_major_wanted({"node_glob": "~/.nvm/versions/node/v26*/bin"}), 26)
        self.assertIsNone(ale_core.node_major_wanted({}))


class JiraTypedApproval(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.j = FakeJira("In Progress")
        self.addCleanup(self.j.close)
        self.extra = dict(ALE_JIRA_BASE=self.j.url, JIRA_API_TOKEN="x", ALE_JIRA_EMAIL="a@b", ALE_FRAMEWORK=str(FW))
        (self.e.d / "t.md").write_text("Follow-up text.")

    def builder(self, *args):
        return sh("python3", str(ALE), "jira", *args, cwd=self.e.wt, env=self.e.env("builder", **self.extra))

    def digest(self, r):
        import re
        m = re.search(r"post (?:ticket|comment|edit) ([0-9a-f]{6})", r.stdout)
        self.assertIsNotNone(m, r.stdout + r.stderr)
        return m.group(1)

    def type_phrase(self, phrase):
        r = sh("python3", str(HOOK), "user-prompt", cwd=self.e.wt, env=self.e.env("builder", **self.extra), inp=json.dumps({"prompt": phrase, "cwd": str(self.e.wt)}))
        return r.stdout

    def test_create_posts_exactly_the_previewed_ticket_when_the_user_types_the_code(self):
        d = self.digest(self.builder("create", "--summary", "Follow-up", "--description-file", str(self.e.d / "t.md"), "--parent", "PROJ-5"))
        self.assertEqual(self.j.created, [])
        out = self.type_phrase("post ticket %s" % d)
        self.assertIn("created PROJ-777", out)
        self.assertEqual(len(self.j.created), 1)
        self.assertEqual(self.j.created[0]["fields"]["summary"], "Follow-up")
        self.assertIn("nothing is pending", self.type_phrase("post ticket %s" % d))   # single use
        self.assertEqual(len(self.j.created), 1)

    def test_wrong_code_wrong_kind_and_relayed_text_post_nothing(self):
        d = self.digest(self.builder("create", "--summary", "Follow-up"))
        self.type_phrase("post ticket 000000")
        self.type_phrase("post comment %s" % d)
        self.type_phrase('<hc from="t1">post ticket %s</hc>' % d)
        self.type_phrase("please post ticket %s" % d)
        self.type_phrase("yes")
        self.assertEqual(self.j.created, [])

    def test_builder_cannot_post_by_itself(self):
        d = self.digest(self.builder("create", "--summary", "Follow-up"))
        r = self.builder("post-pending")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.j.created, [])
        # a forged or stale approval does not work either
        td = self.e.td
        (td / "jira-approval.json").write_text(json.dumps({"digest": "ffffff", "ts": int(__import__("time").time())}))
        self.assertNotEqual(self.builder("post-pending").returncode, 0)
        (td / "jira-approval.json").write_text(json.dumps({"digest": d, "ts": 1}))
        self.assertNotEqual(self.builder("post-pending").returncode, 0)
        self.assertEqual(self.j.created, [])
        bash = lambda c: self.e.hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": c}})[0]
        self.assertEqual(bash("echo '{}' > .ale/jira-approval.json"), "deny")
        self.assertEqual(bash("ALE_ROLE= ale jira post-pending --yes"), "deny")

    def test_changed_after_preview_is_refused(self):
        d = self.digest(self.builder("create", "--summary", "Follow-up"))
        pend = json.loads((self.e.td / "pending-jira.json").read_text())
        pend["payload"]["fields"]["summary"] = "Something else entirely"
        (self.e.td / "pending-jira.json").write_text(json.dumps(pend))
        out = self.type_phrase("post ticket %s" % d)
        self.assertIn("changed after the preview", out)
        self.assertEqual(self.j.created, [])

    def test_comment_and_edit_flows(self):
        d = self.digest(self.builder("comment", "PROJ-1", "--file", str(self.e.d / "t.md")))
        self.assertIn("posted the comment", self.type_phrase("post comment %s" % d))
        self.assertEqual(len(self.j.comments), 1)
        (self.e.d / "d.md").write_text("brand new description")
        d = self.digest(self.builder("edit", "PROJ-1", "--summary", "Renamed", "--description-file", str(self.e.d / "d.md")))
        out = self.type_phrase("post edit %s" % d)
        self.assertIn("applied the edit", out)
        self.assertEqual(self.j.summary, "Renamed")
        self.assertEqual(len(list((self.e.home / "backups" / "jira").glob("PROJ-1-*.json"))), 1)


class AutoClone(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.home = self.d / "home"
        (self.home / "projects").mkdir(parents=True)
        self.fw = self.d / "fw"
        (self.fw / "repos").mkdir(parents=True)
        (self.fw / "policy").mkdir()
        shutil.copytree(FW / "lib", self.fw / "lib")
        (self.home / ".ale").mkdir()
        (self.home / ".ale" / "config.yaml").write_text("github_owner: Acme\n")
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.log = self.d / "gh.log"
        (self.bin / "gh").write_text("#!/bin/sh\necho \"$@\" >> %s\n"
                                     "if [ \"$1 $2\" = \"repo view\" ]; then case \"$3\" in */missing) exit 1;; *) exit 0;; esac; fi\n"
                                     "if [ \"$1 $2\" = \"repo clone\" ]; then git init -q \"$4\"; echo '{\"scripts\":{\"test\":\"jest\"}}' > \"$4/package.json\"; exit 0; fi\n"
                                     "exit 0\n" % self.log)
        (self.bin / "gh").chmod(0o755)

    def ale(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("ALE_ROLE", "ALE_TASK")}
        env.update(HOME=str(self.home), ALE_FRAMEWORK=str(self.fw), ALE_HOME=str(self.home / ".ale"), ALE_ROLE="",
                   PYTHONUSERBASE=RepoInit.USER_BASE, PATH="%s:%s" % (self.bin, os.environ["PATH"]))
        return sh("python3", str(ALE), *args, env=env)

    def test_clones_a_missing_repo_with_the_default_owner_then_drafts_its_config(self):
        r = self.ale("repo-init", "demo")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("cloning Acme/demo", r.stdout)
        self.assertTrue((self.home / "projects" / "demo" / ".git").exists())
        self.assertIn("repo clone Acme/demo", self.log.read_text())
        self.assertTrue((self.fw / "repos" / "demo.yaml").exists())

    def test_owner_slash_name_and_existing_clone(self):
        self.assertEqual(self.ale("repo-info", "Other/thing").returncode, 0)
        self.assertIn("repo clone Other/thing", self.log.read_text())
        before = self.log.read_text()
        self.assertEqual(self.ale("repo-info", "Other/thing").returncode, 0)
        self.assertEqual(self.log.read_text(), before)   # already cloned: gh is not called again

    def test_not_found_bad_names_and_dry_run(self):
        r = self.ale("repo-init", "missing")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("was not found", r.stdout + r.stderr)
        self.assertFalse((self.home / "projects" / "missing").exists())
        for bad in ("../evil", "a b", "x/../y"):
            self.assertNotEqual(self.ale("repo-init", bad).returncode, 0)
        r = self.ale("repo-init", "fresh", "--dry-run")
        self.assertIn("would clone Acme/fresh", r.stdout)
        self.assertFalse((self.home / "projects" / "fresh").exists())
        self.assertNotIn("repo clone", self.log.read_text() if self.log.exists() else "")


class TrustFolder(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.home = self.d / "home"
        (self.home / "projects" / "demo-PROJ-1").mkdir(parents=True)
        self.cj = self.home / ".claude.json"
        self.cj.write_text(json.dumps({"oauthAccount": {"keep": "me"}, "projects": {"/x": {"hasTrustDialogAccepted": False}}}))
        os.chmod(self.cj, 0o644)

    def ale(self, path):
        env = {k: v for k, v in os.environ.items() if k not in ("ALE_ROLE", "ALE_TASK")}
        env.update(HOME=str(self.home), PYTHONUSERBASE=RepoInit.USER_BASE, ALE_HOME=str(self.home / ".ale"))
        return sh("python3", str(ALE), "trust", str(path), env=env)

    def test_trusts_a_worktree_and_keeps_everything_else(self):
        wt = self.home / "projects" / "demo-PROJ-1"
        r = self.ale(wt)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(self.cj.read_text())
        entry = data["projects"][os.path.realpath(str(wt))]
        self.assertIs(entry["hasTrustDialogAccepted"], True)
        self.assertEqual(entry["allowedTools"], [])
        self.assertEqual(data["oauthAccount"], {"keep": "me"})      # other keys survive
        self.assertIn("/x", data["projects"])
        self.assertEqual(oct(os.stat(self.cj).st_mode)[-3:], "600")
        self.assertIn("already trusted", self.ale(wt).stdout)         # idempotent

    def test_refuses_folders_outside_projects(self):
        r = self.ale(self.d)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only folders under ~/projects", r.stdout + r.stderr)
        r = self.ale(self.home / ".ale" / "tasks" / "PROJ-1")                       # a task folder is allowed
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(os.path.realpath(str(self.home / ".ale" / "tasks" / "PROJ-1")), json.loads(self.cj.read_text())["projects"])
        self.assertNotEqual(self.ale(self.home / ".ale" / "key").returncode, 0)   # other ale folders are not
        self.assertNotIn(os.path.realpath(str(self.d)), json.loads(self.cj.read_text())["projects"])


class PackageManager(unittest.TestCase):
    def run_bootstrap(self, setup, with_npm):
        e = Env()
        (e.fw / "repos" / "t.yaml").write_text("setup:\n  - '%s'\nchecks:\n  unit: 'true'\n" % setup)
        bindir = e.d / "bin"
        bindir.mkdir()
        if with_npm:   # a fake npm whose "global install" drops a working stub next to itself
            (bindir / "npm").write_text("#!/bin/sh\nif [ \"$1 $2\" = \"install -g\" ]; then printf '#!/bin/sh\\nexit 0\\n' > \"$(dirname \"$0\")/$3\"; chmod +x \"$(dirname \"$0\")/$3\"; fi\nexit 0\n")
            (bindir / "npm").chmod(0o755)
        env = e.env(None, PATH="%s:/usr/bin:/bin" % bindir)
        return sh("python3", str(ALE), "bootstrap", cwd=e.wt, env=env), bindir

    def test_missing_pnpm_is_installed_not_a_crash(self):
        r, bindir = self.run_bootstrap("pnpm install", with_npm=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pnpm is not available under this Node: installing it globally with npm", r.stdout)
        self.assertTrue((bindir / "pnpm").exists())
        self.assertIn("bootstrap ok", r.stdout)

    def test_no_npm_either_gives_a_clear_message_not_a_traceback(self):
        r, _ = self.run_bootstrap("pnpm install", with_npm=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        self.assertIn("npm is not on PATH", r.stdout + r.stderr)


class TaskPermissions(unittest.TestCase):
    CFG = {"setup": ["pnpm install", "pnpm generate"], "pre": "pnpm generate", "dev": "pnpm dev",
           "checks": {"tsc": "pnpm typecheck", "unit": {"cmd": "pnpm test"}, "prettier": {"files": "x", "cmd": "npx prettier --check"}, "weird": "make a && rm -rf x"}}

    def rules(self, role):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_repo
        return ale_repo.extra_permissions(self.CFG, role)

    def test_reviewer_can_run_the_checks_but_not_install_or_serve(self):
        r = self.rules("reviewer")
        self.assertIn("Bash(pnpm typecheck*)", r)
        self.assertIn("Bash(pnpm test*)", r)
        self.assertIn("Bash(npx vitest*)", r)
        self.assertIn("Bash(pnpm --dir * exec vitest*)", r)      # monorepo packages
        self.assertNotIn("Bash(pnpm install*)", r)
        self.assertNotIn("Bash(pnpm dev*)", r)

    def test_builder_gets_no_extra_bash_rules(self):
        self.assertEqual(self.rules("builder"), [])      # its Bash is checked by the hook, not by a list

    def test_chains_and_redirects_are_never_turned_into_rules(self):
        self.assertFalse([x for x in self.rules("reviewer") if "&&" in x or "rm -rf" in x])
        self.assertEqual(self.rules("alejandro"), [])      # roles without a repo job get nothing extra

    def test_rewritten_forms_are_allowed_too(self):
        import sys
        sys.path.insert(0, str(FW / "lib"))
        import ale_repo
        fake = {"npx vitest x": "rtk vitest x", "git status x": "rtk git status x", "pnpm test x": None}
        rules = ["Bash(npx vitest*)", "Bash(git status*)", "Bash(pnpm test*)", "Bash(rm -rf*)", "Read", "Bash(git add *)"]
        out = ale_repo.rtk_variants(rules, lambda c: fake.get(c))
        self.assertEqual(out, ["Bash(rtk vitest*)", "Bash(rtk git status*)"])   # only what actually gets rewritten; nothing for a rule that stays as is
        self.assertEqual(ale_repo.rtk_variants(rules, lambda c: None), [])       # no rtk, nothing added

    def test_the_task_settings_file_keeps_the_deny_rules(self):
        import subprocess as sp
        e = Env()
        home = e.d / "home"
        (home / ".ale" / "policy").mkdir(parents=True)
        (home / ".ale" / "policy" / "reviewer.json").write_text(json.dumps({"permissions": {"allow": ["Read"], "deny": ["Bash(git push*)"]}}))
        code = ("import sys, importlib.machinery as m, importlib.util as u\nsys.path.insert(0, %r)\n"
                "l = m.SourceFileLoader('ale', %r); s = u.spec_from_loader('ale', l); mod = u.module_from_spec(s); l.exec_module(mod)\n"
                "print(mod.task_settings('reviewer', %r, {'checks': {'unit': 'pnpm test'}}))") % (str(FW / "lib"), str(ALE), str(e.td))
        r = sp.run(["python3", "-c", code], capture_output=True, text=True, env={**os.environ, "ALE_HOME": str(home / ".ale")})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(Path(r.stdout.strip()).read_text())
        self.assertIn("Bash(pnpm test*)", d["permissions"]["allow"])
        self.assertIn("Read", d["permissions"]["allow"])
        self.assertEqual(d["permissions"]["deny"], ["Bash(git push*)"])


class NeedsUserEvents(unittest.TestCase):
    def events(self, e):
        p = e.td / "events.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_logged_once_on_change_not_on_clear_or_repeat(self):
        e = Env()
        run = lambda v: sh("python3", str(ALE), "set", "needs-user=%s" % v, cwd=e.wt, env=e.env(None))
        run("blocked on X")
        run("blocked on X")
        self.assertEqual(len([x for x in self.events(e) if x["kind"] == "needs_user"]), 1)
        run("")
        self.assertEqual(len([x for x in self.events(e) if x["kind"] == "needs_user"]), 1)
        run("blocked on Y")
        self.assertEqual(len([x for x in self.events(e) if x["kind"] == "needs_user"]), 2)

    def test_stop_hook_path_logs_too(self):
        e = Env()
        (e.td / "STATUS.md").write_text("---\nphase: done\n---\n")
        for _ in range(5):
            e.hook("stop", {"hook_event_name": "Stop"})
        self.assertEqual(len([x for x in self.events(e) if x["kind"] == "needs_user"]), 1)


class HookSlack(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        (self.e.td / "TASK.md").write_text((self.e.td / "TASK.md").read_text().replace("id: NOJIRA-x", "id: PROJ-9"))

    def send(self, role="builder", **kw):
        ti = {"channel_id": "C123", "message": "Hey team, [PROJ-9](https://example.atlassian.net/browse/PROJ-9): thing. Please review: https://github.com/acme/r/pull/7"}
        ti.update(kw)
        return self.e.hook("pre-mcp", {"tool_name": "mcp__plugin_slack_slack__slack_send_message", "tool_input": ti}, role)[0]

    def ready(self):
        (self.e.td / "events.jsonl").write_text(json.dumps({"kind": "pr_ready"}) + "\n")

    def test_denied_before_pr_ready(self):
        self.assertEqual(self.send(), "deny")

    def test_allowed_once_to_the_right_channel(self):
        self.ready()
        self.assertEqual(self.send(), "allow")
        self.assertEqual(self.send(), "deny")  # already sent

    def test_wrong_channel_thread_mentions_and_missing_links(self):
        self.ready()
        self.assertEqual(self.send(channel_id="C999"), "deny")
        self.assertEqual(self.send(thread_ts="1.2"), "deny")
        self.assertEqual(self.send(message="Hey <!here> PROJ-9 https://github.com/a/b/pull/1"), "deny")
        self.assertEqual(self.send(message="Hey team, review please https://github.com/a/b/pull/1"), "deny")
        self.assertEqual(self.send(message="Hey team, PROJ-9 please review"), "deny")

    def test_only_builder(self):
        self.ready()
        self.assertEqual(self.send(role="reviewer"), "deny")
        self.assertEqual(self.send(role="alejandro"), "deny")


class HookOpen(unittest.TestCase):
    def test_open_rules(self):
        e = Env()
        (e.td / "artifacts" / "video").mkdir(parents=True)
        vid = e.td / "artifacts" / "video" / "main.webm"
        vid.write_text("v")
        (e.wt / "run.sh").write_text("x")
        bash = lambda c: e.hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": c}})[0]
        self.assertEqual(bash("open -R %s" % vid), "allow")
        self.assertEqual(bash("open %s" % vid), "allow")
        self.assertEqual(bash("open -R run.sh"), "allow")
        self.assertEqual(bash("open run.sh"), "deny")
        self.assertEqual(bash("open https://example.com"), "deny")
        self.assertEqual(bash("open -a Terminal %s" % vid), "deny")
        self.assertEqual(bash("open /Applications/Calculator.app"), "deny")
        self.assertEqual(bash("open /etc/hosts"), "deny")


class HookEditMcp(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def edit(self, path, role="builder"):
        return self.e.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": path}}, role)[0]

    def test_builder_blocked_until_plan_approved(self):
        src = str(self.e.wt / "src" / "a.ts")
        self.assertEqual(self.edit(src), "deny")
        (self.e.td / "PLAN.md").write_text("plan")
        self.assertEqual(self.edit(src), "deny")
        self.assertEqual(sh("python3", str(ALE), "approve", cwd=self.e.wt, env=self.e.env(role=None)).returncode, 0)
        self.assertEqual(self.edit(src), "allow")
        (self.e.td / "PLAN.md").write_text("plan changed")
        self.assertEqual(self.edit(src), "deny")

    def test_protected_files(self):
        for p in [str(self.e.td / "gates.json"), str(self.e.td / "approval.json"), str(self.e.home / "key")]:
            self.assertEqual(self.edit(p), "deny", p)

    def test_reviewer_writes_only_reviews(self):
        self.assertEqual(self.edit(str(self.e.wt / "src" / "a.ts"), "reviewer"), "deny")
        self.assertEqual(self.edit(str(self.e.td / "reviews" / "round-1.md"), "reviewer"), "allow")

    def test_mcp(self):
        m = lambda n, **ti: self.e.hook("pre-mcp", {"tool_name": n, "tool_input": ti})[0]
        self.assertEqual(m("mcp__plugin_github_github__merge_pull_request"), "deny")
        self.assertEqual(m("mcp__plugin_slack_slack__slack_send_message"), "deny")
        self.assertEqual(m("mcp__plugin_slack_slack__slack_send_message_draft"), "allow")
        self.assertEqual(m("mcp__plugin_github_github__create_pull_request", draft=True), "allow")
        self.assertEqual(m("mcp__plugin_github_github__create_pull_request", draft=False), "deny")
        self.assertEqual(m("mcp__figma-remote-mcp__get_design_context"), "allow")


class ApprovalCli(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        (self.e.td / "PLAN.md").write_text("plan")

    def approve(self, role):
        return sh("python3", str(ALE), "approve", cwd=self.e.wt, env=self.e.env(role=role))

    def test_builder_cannot_approve(self):
        self.assertNotEqual(self.approve("builder").returncode, 0)

    def pin(self, tier):
        t = self.e.td / "TASK.md"
        t.write_text(t.read_text().replace("slack_channel_id", "tier_override: %s\nslack_channel_id" % tier, 1))

    def test_main_needs_clean_review(self):
        self.pin("main")
        self.assertNotEqual(self.approve("alejandro").returncode, 0)
        ph = sh("python3", "-c", "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:12])", str(self.e.td / "PLAN.md")).stdout.strip()
        (self.e.td / "reviews" / "plan.md").write_text("VERDICT: APPROVE\nBLOCKERS: 0\nplan_sha: %s\n" % ph)
        self.assertEqual(self.approve("alejandro").returncode, 0)

    def test_user_tier_refuses_main_agent(self):
        self.pin("user")
        self.assertNotEqual(self.approve("alejandro").returncode, 0)
        self.assertEqual(self.approve(None).returncode, 0)


class Gate(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def green_branch(self):
        e = self.e
        e.commit("src/a.spec.ts", "it('a', () => {})\n", "test(NOJIRA): a")
        e.commit("src/a.ts", "export const a = 1\n", "feat(NOJIRA): a")
        sha = sh("git", "rev-parse", "HEAD", cwd=e.wt).stdout.strip()
        (e.td / "reviews" / "round-1.md").write_text("VERDICT: GO\nsha: %s\nCRITICAL: 0\nMAJOR: 0\n" % sha)

    def test_green_then_stale_and_dirty(self):
        self.green_branch()
        r = self.e.gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        import sys
        sys.path.insert(0, str(self.e.fw / "lib"))
        os.environ["ALE_HOME"] = str(self.e.home)
        self.addCleanup(os.environ.pop, "ALE_HOME", None)
        import importlib, ale_core
        importlib.reload(ale_core)
        ok, why = ale_core.gate_valid(self.e.td, str(self.e.wt))
        self.assertTrue(ok, why)
        (self.e.wt / "dirty.txt").write_text("x")
        ok, why = ale_core.gate_valid(self.e.td, str(self.e.wt))
        self.assertFalse(ok)
        (self.e.wt / "dirty.txt").unlink()
        self.e.commit("src/b.ts", "export const b = 1\n", "feat(NOJIRA): b")
        ok, why = ale_core.gate_valid(self.e.td, str(self.e.wt))
        self.assertFalse(ok)

    def test_tampered_gate_rejected(self):
        self.green_branch()
        self.assertEqual(self.e.gate().returncode, 0)
        g = json.loads((self.e.td / "gates.json").read_text())
        g["ts"] += 1
        (self.e.td / "gates.json").write_text(json.dumps(g))
        r = sh("python3", "-c", "import sys;sys.path.insert(0,sys.argv[1]);import ale_core as c;print(c.gate_valid(sys.argv[2],sys.argv[3]))",
               str(self.e.fw / "lib"), str(self.e.td), str(self.e.wt), env=self.e.env())
        self.assertIn("False", r.stdout)

    def test_missing_review_fails(self):
        self.e.commit("src/a.spec.ts", "it('a', () => {})\n", "test(NOJIRA): a")
        r = self.e.gate()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("review", r.stdout)

    def test_weakening_and_names_and_size(self):
        e = self.e
        e.commit("src/a.spec.ts", "it('a', () => {})\nit('b', () => {})\n", "test(NOJIRA): a")
        e.commit("src/a.spec.ts", "it.skip('a', () => {})\n", "bad commit message")
        e.commit("src/big.ts", "x\n" * 80, "feat(NOJIRA): big")
        out = e.gate().stdout
        self.assertIn("FAIL test-weakening", out)
        self.assertIn("FAIL names", out)
        self.assertIn("FAIL pr-size", out)

    def test_ui_check_required(self):
        e = self.e
        e.commit("src/a.spec.tsx", "it('a', () => {})\n", "test(NOJIRA): a")
        e.commit("src/c.tsx", "export const C = () => null\n", "feat(NOJIRA): c")
        self.assertIn("FAIL ui-check", e.gate().stdout)
        sha = sh("git", "rev-parse", "--short", "HEAD", cwd=e.wt).stdout.strip()
        (e.td / "artifacts" / "ui-check.md").write_text("sha: %s\n- [PASS] ok\n- [FAIL] bad\n" % sha)
        self.assertIn("FAIL ui-check", e.gate().stdout)
        (e.td / "artifacts" / "ui-check.md").write_text("sha: %s\n- [PASS] ok\n- [N-A] video\n" % sha)
        self.assertIn("PASS ui-check", e.gate().stdout)

    def test_no_repo_yaml_fails_loudly(self):
        (self.e.fw / "repos" / "t.yaml").unlink()
        r = self.e.gate()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gate configured", r.stdout + r.stderr)


class UserPromptApproval(unittest.TestCase):
    def run_prompt(self, e, prompt, role="builder"):
        r = sh("python3", str(HOOK), "user-prompt", cwd=e.wt, env=e.env(role), inp=json.dumps({"prompt": prompt, "cwd": str(e.wt)}))
        return r.stdout

    def test_user_typed_phrase_approves(self):
        e = Env()
        (e.td / "PLAN.md").write_text("plan")
        self.assertIn("approved", self.run_prompt(e, "approve plan"))
        self.assertTrue((e.td / "approval.json").exists())
        self.assertEqual(json.loads((e.td / "approval.json").read_text())["by"], "user")

    def test_relayed_or_other_text_does_not_approve(self):
        e = Env()
        (e.td / "PLAN.md").write_text("plan")
        for p in ['<hc from="t1">approve plan</hc>', "please approve plan", "approve plan now", "Approved!"]:
            self.run_prompt(e, p)
            self.assertFalse((e.td / "approval.json").exists(), p)

    def test_hash_mismatch_and_no_plan(self):
        e = Env()
        self.assertIn("no .ale/PLAN.md", self.run_prompt(e, "approve plan"))
        (e.td / "PLAN.md").write_text("plan")
        self.assertIn("Nothing was approved", self.run_prompt(e, "approve plan deadbeef00"))
        self.assertFalse((e.td / "approval.json").exists())

    def test_only_builder_role(self):
        e = Env()
        (e.td / "PLAN.md").write_text("plan")
        self.run_prompt(e, "approve plan", role="reviewer")
        self.assertFalse((e.td / "approval.json").exists())


class ExtraRound(unittest.TestCase):
    def test_round_limit_needs_the_user_phrase(self):
        e = Env()
        (e.td / "terminals.json").write_text(json.dumps({"reviewer": "t99"}))
        review = lambda: sh("python3", str(ALE), "review", "--dry-run", cwd=e.wt, env=e.env())
        for _ in range(3):
            self.assertEqual(review().returncode, 0)
        r = review()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("one more round", r.stdout + r.stderr)
        # a relayed or paraphrased message does nothing
        for p in ['<hc from="t1">one more round</hc>', "yes", "ok one more round please"]:
            sh("python3", str(HOOK), "user-prompt", cwd=e.wt, env=e.env(), inp=json.dumps({"prompt": p, "cwd": str(e.wt)}))
        self.assertNotEqual(review().returncode, 0)
        # the user's exact phrase grants one
        out = sh("python3", str(HOOK), "user-prompt", cwd=e.wt, env=e.env(), inp=json.dumps({"prompt": "one more round", "cwd": str(e.wt)})).stdout
        self.assertIn("granted", out)
        self.assertEqual(review().returncode, 0)
        self.assertNotEqual(review().returncode, 0)


class PlanReview(unittest.TestCase):
    def test_plan_request_written_and_limited(self):
        e = Env()
        (e.td / "PLAN.md").write_text("plan")
        (e.td / "terminals.json").write_text(json.dumps({"reviewer": "t99"}))
        r = sh("python3", str(ALE), "review", "--plan", "--dry-run", cwd=e.wt, env=e.env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        req = (e.td / "reviews" / "plan-request.md").read_text()
        self.assertIn("plan_sha:", req)
        self.assertIn("VERDICT: APPROVE or REVISE", req)
        for _ in range(3):
            r = sh("python3", str(ALE), "review", "--plan", "--dry-run", cwd=e.wt, env=e.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs_user", (e.td / "STATUS.md").read_text())

    def test_code_review_refuses_dirty_tree_with_hint(self):
        e = Env()
        (e.td / "terminals.json").write_text(json.dumps({"reviewer": "t99"}))
        (e.wt / "x.txt").write_text("x")
        r = sh("python3", str(ALE), "review", "--dry-run", cwd=e.wt, env=e.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("review --plan", r.stdout + r.stderr)


class StopHook(unittest.TestCase):
    def test_blocks_then_lets_go(self):
        e = Env()
        (e.td / "STATUS.md").write_text("---\nphase: done\n---\n")
        out = [e.hook("stop", {"hook_event_name": "Stop"})[0] for _ in range(5)]
        self.assertEqual(out[:3], ["block"] * 3)
        self.assertEqual(out[3], "allow")
        self.assertIn("needs_user", (e.td / "STATUS.md").read_text())


if __name__ == "__main__":
    unittest.main()
