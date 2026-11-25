"""Jira workflow logic: choose transitions by target status. Pure functions, no network, no LLM."""
import re


def norm(s):
    return (s or "").strip().lower()


def workflow_for(cfg, jira_type):
    wfs = cfg.get("workflows", {})
    return wfs.get("bug" if norm(jira_type) == "bug" else "default", {})


def target_status(wf, goal):
    """goal is start, review, done, or a literal status name."""
    return wf.get(goal) or goal


def next_step(current, offered, target, order):
    """Return (transition or None, why). Takes one step toward `target` using only the transitions Jira offers now.
    why: direct, step, already (at or past the target), unknown (status not in the workflow), none (no way forward offered)."""
    cur, tgt = norm(current), norm(target)
    if cur == tgt:
        return None, "already"
    for t in offered:
        if norm((t.get("to") or {}).get("name")) == tgt:
            return t, "direct"
    rank = {norm(s): i for i, s in enumerate(order)}
    if cur not in rank or tgt not in rank:
        return None, "unknown"
    if rank[cur] >= rank[tgt]:
        return None, "already"
    best, best_rank = None, -1
    for t in offered:
        r = rank.get(norm((t.get("to") or {}).get("name")))
        if r is not None and rank[cur] < r < rank[tgt] and r > best_rank:
            best, best_rank = t, r
    return (best, "step") if best else (None, "none")


def type_default(cfg, jira_type):
    return (cfg.get("type_defaults") or {}).get(norm(jira_type))


def text_to_adf(text):
    """Plain text to Jira's document format: blank lines split paragraphs, single newlines become line breaks. No formatting is invented."""
    content = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        nodes = []
        for i, line in enumerate(para.split("\n")):
            if i:
                nodes.append({"type": "hardBreak"})
            if line:
                nodes.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": nodes})
    return {"type": "doc", "version": 1, "content": content}


def adf_to_text(n):
    if isinstance(n, dict):
        t = n.get("type")
        if t == "text":
            return n.get("text", "")
        if t == "hardBreak":
            return "\n"
        inner = "".join(adf_to_text(c) for c in n.get("content", []))
        return inner + ("\n" if t in ("paragraph", "heading", "listItem", "codeBlock") else "")
    if isinstance(n, list):
        return "".join(adf_to_text(c) for c in n)
    return ""


def same_text(a, b):
    """Compare two texts ignoring whitespace differences (Jira reflows paragraphs)."""
    return " ".join((a or "").split()) == " ".join((b or "").split())


def diff_lines(old, new):
    import difflib
    return "\n".join(difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), "current", "new", lineterm="", n=1))


def digest(kind, payload):
    """Short code for a previewed Jira write. The user types it to post exactly what they saw; any change to the payload changes it."""
    import hashlib, json
    return hashlib.sha256(json.dumps({"kind": kind, "payload": payload}, sort_keys=True).encode()).hexdigest()[:6]
