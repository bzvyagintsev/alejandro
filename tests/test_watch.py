"""ale watch-pr with a fake gh: machine accounts are not feedback, and the watcher does not crash when it logs."""
import json
import os
import shutil
import unittest

from test_ale import Env, ALE, FW, sh


def fake_gh(e, pr):
    d = e.d / "bin"
    d.mkdir(exist_ok=True)
    (d / "gh").write_text('''#!/bin/sh
case "$*" in
  "api user --jq .login") echo me ;;
  "pr view --json"*) cat <<'JSON'
%s
JSON
  ;;
  "repo view"*) echo o/r ;;
  "api repos/o/r/pulls/1/comments"*) echo "[]" ;;
  *) echo "[]" ;;
esac
''' % json.dumps(pr))
    (d / "gh").chmod(0o755)
    return str(d) + os.pathsep + os.environ["PATH"]


BASE = {"number": 1, "url": "https://github.com/o/r/pull/1", "state": "OPEN", "isDraft": False, "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED", "statusCheckRollup": []}


class WatchPr(unittest.TestCase):
    def run_watch(self, pr):
        e = Env()
        pass
        env = e.env("builder", PATH=fake_gh(e, pr))
        r = sh("python3", str(ALE), "watch-pr", "--max-wait", "3", "--interval", "1", cwd=e.wt, env=env)
        return e, r

    def test_bot_comments_are_not_feedback_and_logging_does_not_crash(self):
        comments = [{"id": i, "author": {"login": who}, "body": "preview / audit / scan result"} for i, who in enumerate(["ci-bot-account", "github-actions", "audit-account"])]
        e, r = self.run_watch(dict(BASE, comments=comments, reviews=[{"id": 9, "author": {"login": "alice"}, "state": "APPROVED", "body": ""}]))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # 0 = ready; the old code crashed here with exit 1
        self.assertIn("WATCH: ready", r.stdout)
        self.assertIn('"outcome": "ready"', (e.td / "events.jsonl").read_text())

    def test_a_human_comment_is_feedback(self):
        comments = [{"id": 1, "author": {"login": "ci-bot-account"}, "body": "build ok"}, {"id": 2, "author": {"login": "alice"}, "body": "please rename this"}]
        e, r = self.run_watch(dict(BASE, reviewDecision="", comments=comments, reviews=[]))
        self.assertEqual(r.returncode, 10, r.stdout + r.stderr)
        fb = (e.td / "reviews" / "pr-feedback.md").read_text()
        self.assertIn("please rename this", fb)
        self.assertNotIn("build ok", fb)


if __name__ == "__main__":
    unittest.main()
