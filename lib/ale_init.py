"""`ale init`: build ~/.ale/config.yaml and a starter ~/.ale/rules.local.md from the user's answers."""
import os
from pathlib import Path

import yaml

import ale_core as core

FIELDS = [
    # (key, question, kind) kind: str | list
    ("github_owner", "GitHub org or user that owns your repos (so `ale spawn my-app ...` can clone it)", "str"),
    ("jira_host", "Jira host, like example.atlassian.net (empty = no Jira)", "str"),
    ("ticket_prefix", "Ticket prefix, like PROJ (empty = any PROJECT-123)", "str"),
    ("slack_channel", "Default Slack channel name for review requests (empty = none)", "str"),
    ("integrations", "Integrations to enable, comma separated (%s)", "list"),
    ("webfetch", "Doc sites agents may fetch, comma separated (like react.dev)", "list"),
    ("mcp", "Extra MCP tools every role may use, comma separated (like mcp__my-design__*)", "list"),
    ("env_vars", "Names of environment variables agents may rely on (names only, never values)", "list"),
    ("a11y_skill", "Name of your accessibility audit skill (empty = none; suggested: `accessibility` from github.com/addyosmani/web-quality-skills)", "str"),
]

STARTER_RULES = """# My local rules (imported by the ale global rules; never committed)
# Put what only applies to you or your company here: design system, naming habits, tools, variables.
# `ale init` and the `setup-ale` skill help write this file.
"""


def integrations_available():
    return sorted(p.stem for p in (core.FW / "policy" / "integrations").glob("*.yaml"))


def split(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()] if isinstance(v, str) else list(v or [])


def build_config(existing, ans):
    """Merge answers (a dict keyed like FIELDS; missing keys keep the existing value) into an existing config dict."""
    c = dict(existing or {})
    j = dict(c.get("jira") or {})
    sl = dict(c.get("slack") or {})
    if "github_owner" in ans:
        c["github_owner"] = ans["github_owner"] or ""
    if "jira_host" in ans:
        j["host"] = (ans["jira_host"] or "").replace("https://", "").rstrip("/")
    if "ticket_prefix" in ans:
        pre = (ans["ticket_prefix"] or "").strip().upper().rstrip("-")
        if pre and not pre.isalnum():
            raise SystemExit("ale init: the ticket prefix must be letters and digits (like PROJ), got %r" % pre)
        if pre or "ticket_pattern" not in j:   # an empty answer keeps a custom pattern already in the config
            j["ticket_pattern"] = (r"%s-\d+" % pre) if pre else r"[A-Z][A-Z0-9]+-\d+"
    if "slack_channel" in ans:
        sl["default_channel"] = (ans["slack_channel"] or "").lstrip("#")
    if j:
        c["jira"] = j
    if sl:
        c["slack"] = sl
    for key, dst in (("integrations", "integrations"), ("webfetch", "webfetch_domains"), ("mcp", "mcp_allow"), ("env_vars", "env_vars")):
        if key in ans:
            c[dst] = split(ans[key])
    if "a11y_skill" in ans:
        c["a11y_skill"] = ans["a11y_skill"] or ""
    bad = [i for i in c.get("integrations") or [] if i not in integrations_available()]
    if bad:
        raise SystemExit("ale init: unknown integration(s) %s; available: %s" % (", ".join(bad), ", ".join(integrations_available())))
    return c


def current_answers(cfg):
    j, sl = cfg.get("jira") or {}, cfg.get("slack") or {}
    pat = j.get("ticket_pattern") or ""
    pre = pat[:-4] if pat.endswith(r"-\d+") and pat[:-4].isalnum() else ""
    return {"github_owner": cfg.get("github_owner") or "", "jira_host": j.get("host") or "", "ticket_prefix": pre,
            "slack_channel": sl.get("default_channel") or "", "integrations": ",".join(cfg.get("integrations") or []),
            "webfetch": ",".join(cfg.get("webfetch_domains") or []), "mcp": ",".join(cfg.get("mcp_allow") or []),
            "env_vars": ",".join(cfg.get("env_vars") or []), "a11y_skill": cfg.get("a11y_skill") or ""}


def ask(cur, prompt_fn=input):
    out = {}
    for key, q, _ in FIELDS:
        q = q % ", ".join(integrations_available()) if "%s" in q else q
        d = cur.get(key, "")
        r = prompt_fn("%s%s: " % (q, " [%s]" % d if d else "")).strip()
        out[key] = r if r else d
        if r == "-":
            out[key] = ""
    return out


def write(cfg, rules_path=None):
    path = core.ALE / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text("# Local ale settings (never committed). Generic defaults: policy/config.defaults.yaml\n" + yaml.safe_dump(cfg, sort_keys=False))
    os.replace(tmp, path)
    rules = Path(rules_path or core.ALE / "rules.local.md")
    made = False
    if not rules.exists():
        body = STARTER_RULES
        if cfg.get("env_vars"):
            body += "\n## Variables\n- Known variables: %s. Names only; never print or store values.\n" % ", ".join("`%s`" % v for v in cfg["env_vars"])
        if cfg.get("a11y_skill"):
            body += "\n## UI work\n- Run the `%s` skill (accessibility audit) before the first PR.\n" % cfg["a11y_skill"]
        rules.write_text(body)
        made = True
    return path, rules, made
