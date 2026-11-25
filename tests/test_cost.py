"""ale cost: sessions mapped to roles from transcripts, priced by ccusage."""
import json
import os
import stat
import unittest

from test_ale import Env, ALE, sh


def transcript(path, title, turns, rebuild=False, tool_chars=0, t0=1_000_000, step=10):
    import datetime as dt
    rows = [{"type": "custom-title", "customTitle": title}]
    for i in range(turns):
        stamp = dt.datetime.fromtimestamp(t0 + i * step, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        cc = 130_000 if i == 0 or (rebuild and i >= turns - 2) else 500
        rows.append({"type": "assistant", "timestamp": stamp, "message": {"role": "assistant", "usage": {"input_tokens": 10, "cache_creation_input_tokens": cc, "cache_read_input_tokens": 1000, "output_tokens": 50},
                                                       "content": [{"type": "tool_use", "id": "t%d" % i, "name": "Bash"}]}})
        rows.append({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t%d" % i, "content": [{"type": "text", "text": "x" * (tool_chars if i == 1 else 10)}]}]}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


class Cost(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.proj = self.e.d / "claude-projects"
        import re
        d = self.proj / re.sub(r"[/.]", "-", str(self.e.wt))
        d.mkdir(parents=True)
        transcript(d / "aaaa1111-0000-0000-0000-000000000000.jsonl", "NOJIRA-x-builder", 300, rebuild=True, tool_chars=30000)
        transcript(d / "bbbb2222-0000-0000-0000-000000000000.jsonl", "NOJIRA-x-reviewer", 5)
        transcript(d / "cccc3333-0000-0000-0000-000000000000.jsonl", "probe", 5)
        fake = self.e.d / "bin"
        fake.mkdir()
        prices = {"session": [{"period": "aaaa1111-0000-0000-0000-000000000000", "totalCost": 7.5}, {"period": "bbbb2222-0000-0000-0000-000000000000", "totalCost": 5.0},
                              {"period": "cccc3333-0000-0000-0000-000000000000", "totalCost": 99}]}
        (fake / "ccusage").write_text("#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % json.dumps(prices))
        (fake / "ccusage").chmod(0o755)
        self.env = self.e.env("alejandro", ALE_CLAUDE_PROJECTS=str(self.proj), PATH=str(fake) + os.pathsep + os.environ["PATH"])

    def run_cost(self, *args):
        return sh("python3", str(ALE), "cost", *args, cwd=self.e.wt, env=self.env)

    def test_counts_builder_and_reviewer_only(self):
        r = self.run_cost()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("$12.50 (2 sessions", r.stdout)
        self.assertIn("builder: $7.50", r.stdout)
        self.assertIn("reviewer: $5.00", r.stdout)
        self.assertIn("other: $99.00", r.stdout)
        self.assertIn("not counted", r.stdout)

    def test_suggestions_come_from_rules(self):
        out = self.run_cost().stdout
        self.assertIn("builder starts at 131k tokens", out)
        self.assertIn("builder rebuilt its context 2 times", out)
        self.assertNotIn("reviewer rebuilt", out)
        self.assertIn("ran 300 turns", out)
        self.assertIn("30k-character Bash result", out)

    def test_write_saves_artifact_and_logs(self):
        self.run_cost("--write")
        self.assertIn("Cost of NOJIRA-x", (self.e.td / "artifacts" / "cost.md").read_text())
        self.assertIn('"kind": "cost"', (self.e.td / "events.jsonl").read_text())

    def test_without_ccusage_shows_tokens_only(self):
        env = dict(self.env, PATH="/usr/bin:/bin")
        r = sh("/usr/bin/python3", str(ALE), "cost", cwd=self.e.wt, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("not installed", r.stdout)
        self.assertIn("n/a", r.stdout)

    def test_reviewer_cost_is_split_by_review(self):
        # reviewer turns are at t = 1_000_000 + 10*i (i = 0..4). Plan review before turn 0, round 1 at turn 2 (the round's first turn is the big one)
        ev = [{"ts": 1_000_000, "kind": "plan_review_request", "n": 1}, {"ts": 1_000_001, "kind": "plan_review_done", "n": 1, "header": {"verdict": "APPROVE"}},
              {"ts": 1_000_020, "kind": "review_request", "round": 1, "sha": "abc"}, {"ts": 1_000_040, "kind": "review_done", "round": 1, "header": {"verdict": "GO"}}]
        (self.e.td / "events.jsonl").write_text("\n".join(json.dumps(x) for x in ev) + "\n")
        d = json.loads(self.run_cost("--json").stdout)[0]
        rc = {r["label"]: r for r in d["review_costs"]}
        self.assertEqual(rc["plan 1"]["turns"], 2)
        self.assertEqual(rc["round 1"]["turns"], 3)
        self.assertEqual(rc["plan 1"]["verdict"], "APPROVE")
        self.assertEqual(rc["round 1"]["verdict"], "GO")
        self.assertAlmostEqual(sum(r["cost"] for r in d["review_costs"]), 5.0, places=2)  # the reviewer's $5.00, shared out
        self.assertGreater(rc["plan 1"]["cost"], rc["round 1"]["cost"])  # the plan review carried the cold 130k-token write
        out = self.run_cost().stdout
        self.assertIn("Reviewer cost by review", out)
        self.assertIn("round 1", out)

    def test_code_changes_counted_like_the_gate_and_kept_after_cleanup(self):
        self.e.commit("src/a.ts", "a\nb\nc\n", "feat(NOJIRA): a")
        self.e.commit("src/a.spec.ts", "t1\nt2\n", "test(NOJIRA): a")
        d = json.loads(self.run_cost("--json").stdout)[0]["code"]
        self.assertEqual((d["added"], d["deleted"], d["files"], d["source_loc"], d["test_loc"]), (5, 0, 2, 3, 2))
        self.assertEqual(json.loads((self.e.td / "artifacts" / "diffstat.json").read_text())["added"], 5)
        # the worktree is gone (ale cleanup): the saved numbers are used
        import shutil
        shutil.rmtree(self.e.wt / ".git")
        again = json.loads(self.run_cost("--json").stdout)[0]["code"]
        self.assertEqual(again["added"], 5)

    def test_stats_record_for_dashboards(self):
        d = json.loads(self.run_cost("--stats").stdout)
        self.assertEqual((d["n"], d["total"]), (1, 12.5))
        r = d["rows"][0]
        self.assertEqual((r["task"], r["builder"]["cost"], r["reviewer"]["cost"]), ("NOJIRA-x", 7.5, 5.0))
        self.assertEqual(r["builder"]["turns"], 300)
        self.assertNotIn("other", json.dumps(r))  # probe sessions are not counted

    def test_json_output(self):
        d = json.loads(self.run_cost("--json").stdout)
        self.assertEqual(round(d[0]["total"], 2), 12.5)


class HerdrMod(unittest.TestCase):
    def test_shipped_files_are_sound(self):
        from test_ale import FW
        import glob
        for f in ["install.sh"] + sorted(os.path.relpath(x, FW / "herdr") for x in glob.glob(str(FW / "herdr" / "stats" / "fetch-*.sh"))):
            r = sh("sh", "-n", str(FW / "herdr" / f))
            self.assertEqual(r.returncode, 0, f + r.stderr)
        mod = json.loads((FW / "herdr" / "stats" / "mod.json").read_text())
        self.assertEqual(mod["place"], "none")
        js = (FW / "herdr" / "stats" / "main.js").read_text()
        self.assertEqual(js.count("("), js.count(")"))  # a missing bracket broke the first draft
        doc = (FW / "docs" / "herdr-stats.md").read_text()
        for needle in ("install.sh", "mod.open --name stats", "ale cost --all --stats"):
            self.assertIn(needle, doc)


if __name__ == "__main__":
    unittest.main()
