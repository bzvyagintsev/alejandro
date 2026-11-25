"""Renders the per-role Claude settings from policy/<role>.json.tmpl plus the user's config and integration presets."""
import json

import ale_core as core

ROLES = ("builder", "reviewer", "lead", "alejandro")


def preset(name):
    p = core.FW / "policy" / "integrations" / ("%s.yaml" % name)
    if not p.exists():
        raise SystemExit("ale: unknown integration %r (see policy/integrations/)" % name)
    return core.load_yaml(p)


def extra_allow(role, cfg):
    """MCP tools and WebFetch domains the user's config adds for a role, in order, without duplicates."""
    out = []
    for name in cfg.get("integrations") or []:
        pr = preset(name)
        if role == "alejandro":
            out += pr.get("alejandro_allow") or []
            continue
        if role in (pr.get("roles") or ("builder", "reviewer", "lead")):
            out += pr.get("allow") or []
        if role == "builder":
            out += pr.get("builder_allow") or []
    if role != "alejandro":
        out += cfg.get("mcp_allow") or []
        domains = list(cfg.get("webfetch_domains") or [])
        host = (cfg.get("jira") or {}).get("host") or ""
        if host:
            domains.append(host.replace("https://", "").rstrip("/"))
        out += ["WebFetch(domain:%s)" % d for d in domains]
    return list(dict.fromkeys(out))


def render(role, text, cfg, home, fw):
    """Return the settings JSON text for a role."""
    doc = json.loads(text.replace("__HOME__", str(home)).replace("__FRAMEWORK__", str(fw)))
    doc.pop("_comment", None)  # notes for people editing the template are not Claude settings
    allow = doc["permissions"]["allow"]
    doc["permissions"]["allow"] = list(dict.fromkeys(allow + extra_allow(role, cfg)))
    return json.dumps(doc, indent=2) + "\n"
