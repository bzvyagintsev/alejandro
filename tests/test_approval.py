"""Who approves a plan: the reviewer's risk, the size and the rules in policy/approval.yaml."""
import hashlib
import json
import shutil
import unittest

from test_ale import Env, FW, HOOK, ALE, sh
from test_decisions import load_ale


def plan_hash(e):
    return hashlib.sha256((e.td / "PLAN.md").read_bytes()).hexdigest()[:12]


class TierRules(unittest.TestCase):
    def test_matrix(self):
        ale = load_ale()
        ale.core.FW = FW  # other tests leave ALE_FRAMEWORK pointing at a throwaway dir
        tier_for = ale.tier_for
        cases = [("S", "green", "auto"), ("M", "green", "auto"), ("S", "yellow", "auto"), ("M", "yellow", "auto"),
                 ("L", "green", "user"), ("L", "yellow", "user"), ("S", "red", "user"), ("M", "red", "user"), ("L", "red", "user"),
                 ("S", None, "user"), ("M", "purple", "user")]
        for size, risk, want in cases:
            self.assertEqual(tier_for({"size": size, "type": "task", "flag": False}, risk), want, (size, risk))
        self.assertEqual(tier_for({"size": "S", "type": "task", "flag": True}, "green"), "auto")  # a feature flag does not change the tier

    def test_main_is_kept_for_a_rule_that_names_it(self):
        from pathlib import Path
        import os
        rules = Path(FW / "policy" / "approval.yaml")
        self.assertIn("main:", rules.read_text())  # documented in the file, usable by adding a rule


class PlanGateAndApprove(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        shutil.copy(FW / "policy" / "approval.yaml", self.e.fw / "policy" / "approval.yaml")
        (self.e.td / "PLAN.md").write_text("plan")

    def review(self, verdict="APPROVE", blockers=0, risk="green", why="isolated, covered", sha=None):
        lines = ["VERDICT: %s" % verdict, "BLOCKERS: %d" % blockers, "plan_sha: %s" % (sha or plan_hash(self.e))]
        if risk:
            lines += ["RISK: %s" % risk, "RISK_WHY: %s" % why]
        (self.e.td / "reviews" / "plan.md").write_text("\n".join(lines) + "\n")

    def gate(self):
        return sh("python3", str(ALE), "plan-gate", cwd=self.e.wt, env=self.e.env()).stdout

    def approve(self, role="builder"):
        return sh("python3", str(ALE), "approve", cwd=self.e.wt, env=self.e.env(role=role))

    def test_green_and_yellow_s_m_are_auto(self):
        self.review(risk="green")
        self.assertIn("AUTO-APPROVE (risk green: isolated, covered, size S)", self.gate())
        self.review(risk="yellow", why="no spec next to the modal")
        out = self.gate()
        self.assertIn("AUTO-APPROVE (risk yellow", out)
        self.assertIn("pins today's behaviour", out)
        self.review(risk="green")
        self.assertNotIn("pins", self.gate())

    def test_red_large_or_unlabelled_go_to_the_user(self):
        self.review(risk="red", why="touches billing")
        self.assertIn("NEEDS-USER (risk red: touches billing", self.gate())
        self.review(risk=None)
        self.assertIn("NEEDS-USER (risk unknown", self.gate())
        t = self.e.td / "TASK.md"
        t.write_text(t.read_text().replace("size: S", "size: L"))
        self.review(risk="green")
        self.assertIn("NEEDS-USER (risk green", self.gate())

    def test_a_review_that_is_not_clean_or_not_for_this_plan_waits(self):
        self.review(verdict="REVISE", blockers=1)
        self.assertIn("WAIT-REVIEW", self.gate())
        self.review(sha="deadbeef0000")
        self.assertIn("WAIT-REVIEW", self.gate())
        (self.e.td / "reviews" / "plan.md").unlink()
        self.assertIn("WAIT-REVIEW", self.gate())

    def test_builder_approves_itself_only_under_auto(self):
        self.review(risk="red")
        r = self.approve("builder")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.e.td / "approval.json").exists())
        self.review(risk="green")
        r = self.approve("builder")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rec = json.loads((self.e.td / "approval.json").read_text())
        self.assertEqual((rec["by"], rec["tier"], rec["risk"]), ("auto", "auto", "green"))
        self.assertEqual(self.e.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": str(self.e.wt / "src" / "a.ts")}})[0], "allow")
        self.assertIn('"by": "auto"', (self.e.td / "events.jsonl").read_text())

    def test_changing_the_plan_voids_an_auto_approval(self):
        self.review(risk="green")
        self.approve("builder")
        (self.e.td / "PLAN.md").write_text("a bigger plan")
        self.assertEqual(self.e.hook("pre-edit", {"tool_name": "Edit", "tool_input": {"file_path": str(self.e.wt / "src" / "a.ts")}})[0], "deny")
        self.assertIn("WAIT-REVIEW", self.gate())  # the review no longer matches the plan

    def test_reviewer_cannot_approve_and_a_stale_tier_line_is_ignored(self):
        self.review(risk="green")
        self.assertNotEqual(self.approve("reviewer").returncode, 0)
        t = self.e.td / "TASK.md"
        t.write_text(t.read_text().replace("slack_channel_id", "tier: user\nslack_channel_id", 1))  # what old tasks have
        self.assertIn("AUTO-APPROVE", self.gate())

    def test_the_risk_is_shown_to_agents(self):
        self.review(risk="yellow", why="weak tests")
        ctx = sh("python3", str(ALE), "context", cwd=self.e.wt, env=self.e.env()).stdout
        self.assertIn('"risk": "yellow"', ctx)
        self.assertIn('"tier": "auto"', ctx)


if __name__ == "__main__":
    unittest.main()
