import json
import os
import sys
import unittest
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FW / "lib"))
os.environ.pop("ALE_FRAMEWORK", None)
import ale_core as core  # noqa: E402
import ale_policy  # noqa: E402

CFG = {"integrations": ["slack", "atlassian", "chrome-devtools"], "mcp_allow": ["mcp__my-design__list"],
       "webfetch_domains": ["docs.example.org"], "jira": {"host": "https://acme.atlassian.net/"}}


def allow(role, cfg=CFG):
    core.FW = FW
    text = (FW / "policy" / ("%s.json.tmpl" % role)).read_text()
    return json.loads(ale_policy.render(role, text, cfg, "/h", "/fw"))["permissions"]["allow"]


class Render(unittest.TestCase):
    def test_presets_mcp_and_domains_are_added(self):
        a = allow("reviewer")
        for want in ("mcp__plugin_slack_slack__slack_read_channel", "mcp__atlassian__getJiraIssue", "mcp__my-design__list",
                     "WebFetch(domain:docs.example.org)", "WebFetch(domain:acme.atlassian.net)", "mcp__plugin_chrome-devtools-mcp_chrome-devtools"):
            self.assertIn(want, a)
        self.assertNotIn("mcp__plugin_slack_slack__slack_send_message", a)   # the builder alone may post

    def test_builder_may_post_and_the_lead_has_no_browser(self):
        self.assertIn("mcp__plugin_slack_slack__slack_send_message", allow("builder"))
        self.assertNotIn("mcp__plugin_chrome-devtools-mcp_chrome-devtools", allow("lead"))

    def test_nothing_configured_means_no_mcp_and_no_company_domains(self):
        for role in ("builder", "reviewer", "lead", "alejandro"):
            a = allow(role, {})
            self.assertFalse([x for x in a if x.startswith("mcp__")], role)
            self.assertFalse([x for x in a if x.startswith("WebFetch") and "github.com" not in x], role)

    def test_the_builder_has_no_bash_allow_rules(self):
        self.assertFalse([x for x in allow("builder") if x.startswith("Bash(")])

    def test_alejandro_gets_only_its_own_reads(self):
        a = allow("alejandro")
        self.assertIn("mcp__atlassian__getJiraIssue", a)
        self.assertNotIn("mcp__my-design__list", a)

    def test_an_unknown_integration_is_an_error(self):
        with self.assertRaises(SystemExit):
            allow("builder", {"integrations": ["nope"]})


class Init(unittest.TestCase):
    def test_answers_become_config_and_round_trip(self):
        import ale_init
        c = ale_init.build_config({}, {"github_owner": "acme", "jira_host": "https://acme.atlassian.net/", "ticket_prefix": "pj-",
                                       "slack_channel": "#eng", "integrations": "slack, figma", "webfetch": "react.dev", "env_vars": "A_TOKEN"})
        self.assertEqual(c["jira"]["host"], "acme.atlassian.net")
        self.assertEqual(c["jira"]["ticket_pattern"], r"PJ-\d+")
        self.assertEqual(c["slack"]["default_channel"], "eng")
        self.assertEqual(c["integrations"], ["slack", "figma"])
        self.assertEqual(ale_init.current_answers(c)["ticket_prefix"], "PJ")

    def test_keys_not_answered_are_kept(self):
        import ale_init
        old = {"jira": {"workflows": {"default": {"order": ["A"]}}, "host": "x"}, "github_owner": "o"}
        c = ale_init.build_config(old, {"slack_channel": "eng"})
        self.assertEqual(c["jira"]["workflows"], old["jira"]["workflows"])
        self.assertEqual(c["github_owner"], "o")

    def test_empty_prefix_keeps_a_custom_pattern_and_a_bad_prefix_is_refused(self):
        import ale_init
        old = {"jira": {"ticket_pattern": r"(?:AB|CD)-\d+"}}
        self.assertEqual(ale_init.build_config(old, {"ticket_prefix": ""})["jira"]["ticket_pattern"], r"(?:AB|CD)-\d+")
        with self.assertRaises(SystemExit):
            ale_init.build_config({}, {"ticket_prefix": "A("})

    def test_agents_cannot_write_the_local_settings_and_rules(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as d:
            for role in ("builder", "lead"):
                for f in ("config.yaml", "rules.local.md"):
                    inp = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "%s/%s" % (d, f)}, "cwd": "/tmp"})
                    r = subprocess.run([sys.executable, str(FW / "hooks" / "ale_hook.py"), "pre-edit"], input=inp, capture_output=True, text=True,
                                       env=dict(os.environ, ALE_ROLE=role, ALE_HOME=d, ALE_FRAMEWORK=str(FW)))
                    self.assertIn("deny", r.stdout, (role, f, r.stderr))
            inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo x >> %s/rules.local.md" % d}, "cwd": "/tmp"})
            r = subprocess.run([sys.executable, str(FW / "hooks" / "ale_hook.py"), "pre-bash"], input=inp, capture_output=True, text=True,
                               env=dict(os.environ, ALE_ROLE="builder", ALE_HOME=d, ALE_FRAMEWORK=str(FW), ALE_TASK="NOJIRA-x"))
            self.assertIn("deny", r.stdout, r.stderr)

    def test_unknown_integration_is_refused(self):
        import ale_init
        with self.assertRaises(SystemExit):
            ale_init.build_config({}, {"integrations": "nope"})

    def test_non_interactive_command_writes_the_files(self):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, ALE_HOME=d)
            r = subprocess.run([sys.executable, str(FW / "bin" / "ale"), "init", "--non-interactive", "--github-owner", "acme", "--ticket-prefix", "PJ", "--integrations", "slack", "--env-vars", "X_TOKEN"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("github_owner: acme", (Path(d) / "config.yaml").read_text())
            self.assertIn("X_TOKEN", (Path(d) / "rules.local.md").read_text())


if __name__ == "__main__":
    unittest.main()
