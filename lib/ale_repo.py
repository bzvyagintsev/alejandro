"""Draft a gate config (repos/<repo>.yaml) by reading a repo. Pure file reading, no network, no LLM."""
import json
import re
from pathlib import Path

LOCKS = [("yarn.lock", "yarn"), ("pnpm-lock.yaml", "pnpm"), ("package-lock.json", "npm")]
SIZE_EXCLUDE = ["yarn.lock", "package-lock.json", "pnpm-lock.yaml", "go.sum", "**/*.snap", "src/generated/**", "translations/**"]


def _read(p):
    try:
        return Path(p).read_text()
    except Exception:
        return ""


def node_major(repo):
    """Node major version if the repo states one exactly (.nvmrc, .node-version, engines like 22 or ^22 or 22.x). None if unclear."""
    for f in (".nvmrc", ".node-version"):
        m = re.match(r"^v?(\d+)", _read(Path(repo) / f).strip())
        if m:
            return int(m.group(1)), f
    m = re.search(r"^\s*(?:nodejs|node)\s+v?(\d+)", _read(Path(repo) / ".tool-versions"), re.M)
    if m:
        return int(m.group(1)), ".tool-versions"
    pkg = _json(Path(repo) / "package.json")
    eng = ((pkg.get("engines") or {}).get("node") or "").strip()
    m = re.match(r"^[\^~]?v?(\d+)(\.\d+|\.x|\.\*)*$", eng)
    if m:
        return int(m.group(1)), "package.json engines"
    return None, None


def _json(p):
    try:
        return json.loads(_read(p) or "{}")
    except Exception:
        return {}


def _run(pm, name):
    return {"yarn": "yarn %s", "pnpm": "pnpm %s", "npm": "npm run %s"}[pm] % name


def detect(repo):
    """Return (config dict, notes list). notes say what was found and what could not be detected."""
    repo = Path(repo)
    cfg, notes = {}, []
    if (repo / "package.json").exists():
        _detect_node(repo, cfg, notes)
    elif (repo / "go.mod").exists():
        _detect_go(repo, cfg, notes)
    else:
        notes.append("no package.json or go.mod: only the universal checks (PR size, names, review) will run; add the repo's checks by hand")
    cfg.setdefault("checks", {})
    cfg["pr_max_loc"] = 500
    cfg["size_exclude"] = SIZE_EXCLUDE
    for f in ("AGENTS.md", "CLAUDE.md"):
        if (repo / f).exists():
            notes.append("%s found: agents will read it" % f)
    return cfg, notes


def _detect_node(repo, cfg, notes):
    pkg = _json(repo / "package.json")
    scripts = pkg.get("scripts") or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    pm = next((name for lock, name in LOCKS if (repo / lock).exists()), "npm")
    notes.append("package manager: %s" % pm)
    major, src = node_major(repo)
    if major:
        cfg["node_glob"] = "~/.nvm/versions/node/v%d*/bin" % major
        notes.append("node %d (from %s); `ale bootstrap` installs it with nvm if it is missing" % (major, src))
    else:
        notes.append("node version not stated (.nvmrc, .tool-versions, engines): the default node is used. Set node_glob if the repo needs a specific one")
    install = {"yarn": "yarn install", "pnpm": "pnpm install", "npm": "npm ci" if (repo / "package-lock.json").exists() else "npm install"}[pm]
    cfg["setup"] = [install]
    first = lambda *names: next((n for n in names if n in scripts), None)
    gen = first("generate", "codegen")
    if gen:
        cfg["pre"] = _run(pm, gen)
        cfg["setup"].append(_run(pm, gen))
        notes.append("code generation: %s (check whether it needs a token, for example GH_TOKEN)" % gen)
    if "@playwright/test" in deps or "playwright" in deps:
        cfg["setup"].append("npx playwright install chromium")
    checks = {}
    if "prettier" in deps:
        checks["prettier"] = {"files": r"\.(tsx?|jsx?|json|md|css|ya?ml)$", "cmd": "npx prettier --check"}
    if "eslint" in deps:
        checks["eslint"] = {"files": r"\.(tsx?|jsx?)$", "cmd": "npx eslint"}
    ts = first("lint:ts", "typecheck", "type-check", "check-types", "tsc")
    if ts:
        checks["tsc"] = _run(pm, ts)
    elif (repo / "tsconfig.json").exists() and "typescript" in deps:
        checks["tsc"] = "npx tsc --noEmit"
    unit = first("test:unit", "test")
    if unit:
        checks["unit"] = _run(pm, unit)
    else:
        notes.append("no test script found: no unit check")
    e2e = first("test:integration", "test:e2e", "e2e", "playwright")
    if e2e:
        checks["e2e"] = {"cmd": _run(pm, e2e), "timeout": 2400}
    elif any((repo / f).exists() for f in ("playwright.config.ts", "playwright.config.js")):
        checks["e2e"] = {"cmd": "npx playwright test", "timeout": 2400}
    build = first("build", "dist")
    if build:
        checks["build"] = _run(pm, build)
    else:
        notes.append("no build script found: no build check")
    cfg["checks"] = checks
    portvar = lambda n: re.search(r"\$\{?([A-Z][A-Z0-9_]*PORT)\b", str(scripts.get(n, "")))
    runner = re.compile(r"\b(playwright test|jest|vitest|mocha|cypress|karma)\b")
    candidates = [n for n in ("dev", "start", "serve") if n in scripts] + [n for n in scripts if n.endswith(":start") or n.endswith(":dev")]
    candidates = [n for n in candidates if not runner.search(str(scripts.get(n, "")))]   # a test runner is not a dev server
    dev = next((n for n in candidates if portvar(n)), candidates[0] if candidates else None)
    if dev:
        cfg["dev"] = _run(pm, dev)
        m = portvar(dev)
        if m:
            cfg["port_env"] = m.group(1)
            notes.append("dev server: %s, port read from %s" % (dev, m.group(1)))
        else:
            notes.append("dev server found (%s) but no port variable in the script: parallel worktrees may collide on one port" % dev)
    envs = [f for f in (".env.dist", ".env.example", ".env.sample") if (repo / f).exists()]
    if envs and "e2e" in checks:
        notes.append("%s found and an e2e check was added: the suite may need secrets or a staging environment, so a local e2e check can fail for reasons unrelated to the change. Remove it if CI is where it runs" % envs[0])
    if any(repo.glob("src/**/*.tsx")):
        cfg["ui"] = {"paths": ["src/**/*.tsx"], "exclude": ["**/*.spec.tsx", "**/*.test.tsx"]}
        notes.append("UI files found: UI changes will need the verify-ui evidence")


def _detect_go(repo, cfg, notes):
    notes.append("Go module")
    cfg["setup"] = ["go mod download"]
    checks = {"vet": "go vet ./...", "unit": "go test ./...", "build": "go build ./..."}
    if any((repo / f).exists() for f in (".golangci.yml", ".golangci.yaml", ".golangci.toml")):
        checks["lint"] = "golangci-lint run ./..."
    else:
        notes.append("no .golangci config in the repo: CI may use a shared one; add a lint check by hand if needed")
    cfg["checks"] = checks


def render(repo_name, cfg, notes):
    import yaml
    head = ["# Gate config for %s. GENERATED by `ale repo-init` from the repo; review it before trusting it." % repo_name,
            "# Read by `ale gate`, `ale spawn` and `ale bootstrap`. Delete this file and re-run `ale repo-init` to regenerate."]
    head += ["# - " + n for n in notes]
    body = yaml.safe_dump({"generated": True, **cfg}, sort_keys=False, default_flow_style=False, width=120)
    tail = ["", "# Not detected (add if you want them):",
            "#   slack_channel: <name>            where the builder posts the review request",
            "#   slack_channel_id: <C...>         required for the Slack post: the hook allows only this channel"]
    return "\n".join(head) + "\n" + body + "\n".join(tail) + "\n"


RUNNERS = ["npx vitest*", "npx jest*", "npx mocha*", "npx playwright*", "npx tsc*",
           # monorepos: the runner lives in a package, not at the root
           "pnpm exec vitest*", "pnpm --dir * exec vitest*", "pnpm --filter * exec vitest*", "pnpm -F * exec vitest*",
           "pnpm --dir * exec jest*", "pnpm --filter * exec jest*"]


def _cmds(spec):
    if isinstance(spec, str):
        return [spec]
    if isinstance(spec, dict) and isinstance(spec.get("cmd"), str):
        return [spec["cmd"]]
    return []


def extra_permissions(cfg, role):
    """Bash allow rules for the commands this repo's own config checks run, so the reviewer can run what the gate runs (pnpm, go, make, vitest ...).
    Only the reviewer needs them: the builder's Bash is checked by the ale hook (lib/ale_bash.py), not by an allow list."""
    if role != "reviewer":
        return []
    cmds = []
    for spec in (cfg.get("checks") or {}).values():
        cmds += _cmds(spec)
    rules = []
    for c in cmds:
        c = c.strip()
        if c and not re.search(r"[;&|`$<>]", c):    # a plain command only; no chains, redirects or expansions
            rules.append("Bash(%s*)" % c)
    rules += ["Bash(%s)" % r for r in RUNNERS]
    return list(dict.fromkeys(rules))


def rtk_variants(rules, rewrite):
    """For every `Bash(<command>*)` rule, also allow the form a command-rewriting hook (rtk) turns it into.
    The permission check sees the rewritten command (`npx vitest x` becomes `rtk vitest x`), so a rule for the original alone never matches.
    `rewrite(cmd)` returns the rewritten command, or None when nothing changes."""
    out = []
    for r in rules:
        m = re.match(r"^Bash\((.*?)\*?\)$", r)
        if not m or "*" in m.group(1):
            continue
        probe = m.group(1).rstrip() + " x"
        new = rewrite(probe)
        if new and new != probe and new.endswith(" x"):
            rule = "Bash(%s*)" % new[:-2]
            if rule not in rules and rule not in out:
                out.append(rule)
    return out
