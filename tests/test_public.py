"""The repo must stay public-safe: no company, person or credential details in any tracked file.
Generic patterns always run; your own words (company, repos, people) go in ~/.ale/banned-terms.txt, one per line (never committed)."""
import os
import re
import subprocess
import unittest
from pathlib import Path

FW = Path(__file__).resolve().parent.parent

PATTERNS = {
    "a ticket key with 4+ digits": re.compile(r"\b(?!(?:ISO|UTF|RFC|CVE|SHA|TLS|WCAG|HTTP|PROJ)-)[A-Z][A-Z0-9]+-\d{4,}\b"),
    "a Jira custom field id": re.compile(r"\bcustomfield_(?!10001\b)\d+\b"),
    "a concrete Atlassian site": re.compile(r"\b(?!example\.|acme\.)[a-z0-9-]+\.atlassian\.net\b"),
    "a Slack channel or user id": re.compile(r"\b[CUWD](?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,11}\b"),
    "an email address": re.compile(r"[\w.+-]+@(?!example\.)[\w-]+\.[a-z]{2,}", re.I),
    "a token": re.compile(r"\b(?:ghp_|gho_|github_pat_|xox[abp]-|AKIA|sk-ant-)[A-Za-z0-9_-]{10,}"),
}


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=FW, capture_output=True, text=True, check=True).stdout
    for name in filter(None, out.split("\0")):
        p = FW / name
        if name == "tests/test_public.py":   # its own samples
            continue
        try:
            yield name, p.read_text()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def banned_terms():
    f = Path(os.environ.get("ALE_HOME") or Path.home() / ".ale") / "banned-terms.txt"
    return [t.strip().lower() for t in f.read_text().splitlines() if t.strip() and not t.startswith("#")] if f.exists() else []


class PublicSafe(unittest.TestCase):
    def test_no_generic_company_details(self):
        hits = []
        for name, text in tracked():
            for label, rx in PATTERNS.items():
                for m in rx.finditer(text):
                    hits.append("%s: %s (%s)" % (name, label, m.group(0)))
        self.assertEqual(hits, [], "company or personal details in tracked files:\n" + "\n".join(hits[:20]))

    def test_no_banned_terms(self):
        terms = banned_terms()
        hits = []
        for name, text in tracked():
            low = (name + "\n" + text).lower()
            hits += ["%s: %r" % (name, t) for t in terms if t in low]
        self.assertEqual(hits, [], "terms from ~/.ale/banned-terms.txt found:\n" + "\n".join(hits[:20]))

    def test_the_guard_catches_what_it_should(self):
        for label, sample in (("a ticket key with 4+ digits", "XY-12345"), ("a Jira custom field id", "customfield_10456"),
                              ("a concrete Atlassian site", "foo.atlassian.net"), ("a Slack channel or user id", "C09ABCD1234"),
                              ("an email address", "a.b@corp.com"), ("a token", "ghp_abcdefghijklmnop")):
            self.assertTrue(PATTERNS[label].search(sample), label)
        for ok in ("PROJ-123", "example.atlassian.net", "ISO-8601", "x@example.com", "CONTEXT", "CONFIGURATION"):
            self.assertFalse(any(rx.search(ok) for rx in PATTERNS.values()), ok)


if __name__ == "__main__":
    unittest.main()
