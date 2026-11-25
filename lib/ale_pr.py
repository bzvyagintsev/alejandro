"""PR watching logic: decide what a PR needs next. Pure functions, no LLM, no network."""

FAIL = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
EXIT = {"ready": 0, "feedback": 10, "ci_failed": 11, "base": 12, "merged": 20, "closed": 21}


def failed_checks(rollup):
    out = []
    for c in rollup or []:
        res = (c.get("conclusion") or c.get("state") or "").upper()
        if res in FAIL:
            out.append(c.get("name") or c.get("context") or "?")
    return out


# Machine accounts whose comments are not review feedback. gh reports a bot's login without the "[bot]" suffix, so add the ones you see to
# `ignore_pr_authors` in ~/.ale/config.yaml (fnmatch patterns).
# In fnmatch "[bot]" is a character class (it would match any name ending in b, o or t), so the brackets are escaped.
BOT_AUTHORS = ["*[[]bot[]]", "*-bot", "github-actions", "dependabot*", "renovate*", "codecov*", "sonar*"]


def is_bot(author, extra=()):
    from fnmatch import fnmatchcase
    return any(fnmatchcase(author.lower(), p.lower()) for p in list(BOT_AUTHORS) + list(extra or []))


def new_feedback(items, seen, me, ignore=()):
    out = []
    for it in items:
        author = (it.get("author") or "")
        if str(it.get("id")) in seen or author == me or is_bot(author, ignore):
            continue
        body = (it.get("body") or "").strip()
        if not body and it.get("state") not in ("CHANGES_REQUESTED",):
            continue  # an approval with no text is not feedback
        out.append(it)
    return out


def classify(pr, items, seen, me, ignore=()):
    """Return (kind, detail). kind is None while we should keep waiting."""
    state = (pr.get("state") or "").upper()
    if state == "MERGED":
        return "merged", "the PR was merged"
    if state == "CLOSED":
        return "closed", "the PR was closed without merging"
    fb = new_feedback(items, seen, me, ignore)
    if fb:
        return "feedback", "%d new review item(s)" % len(fb)
    bad = failed_checks(pr.get("statusCheckRollup"))
    if bad:
        return "ci_failed", "failed checks: " + ", ".join(sorted(set(bad)))
    ms = (pr.get("mergeStateStatus") or "").upper()
    if ms in ("DIRTY", "BEHIND"):
        return "base", "the PR has conflicts with the base" if ms == "DIRTY" else "the PR is behind the base branch"
    if pr.get("isDraft"):
        return None, "still a draft"
    if (pr.get("reviewDecision") or "").upper() == "APPROVED" and ms in ("CLEAN", "HAS_HOOKS"):
        return "ready", "approved, checks green, no conflicts: ready to merge"
    return None, "waiting (review: %s, merge state: %s)" % (pr.get("reviewDecision") or "none", ms or "unknown")
