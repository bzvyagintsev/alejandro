# Alejandro&Co: a local agentic engineering framework

<img width="1536" height="1024" alt="Алехандро и компания среди овечек" src="https://github.com/user-attachments/assets/aa9442b6-628d-4dd9-8e89-3498613a5f49" />

`ale` runs a small team of Claude Code agents on a ticket: a **builder** writes the plan and the code, a **reviewer** reviews it in the pane beside it, a **lead** splits an epic, and **Alejandro** (the HQ agent) starts tasks and reports status. Skills say what to do, `ale gate` defines "ready", and hooks block the next step unless the gate or your approval allows it.

Pure scripts and Markdown: nothing here calls an LLM by itself. The threat model is accidental skipping, not a hostile agent.

## What you need
- Claude Code, Python 3 with PyYAML (`python3 -m pip install --user pyyaml`), `git`, `gh` (logged in).
- Recommended skills: [agent-skills](https://github.com/addyosmani/agent-skills) (the builder and reviewer procedures call `/agent-skills:build`, `/agent-skills:review` and friends) and, for the accessibility audit, the `accessibility` skill of [web-quality-skills](https://github.com/addyosmani/web-quality-skills) (set `a11y_skill: accessibility`). Both are optional: without them the agents use their own checklist. The other web-quality-skills (performance, Core Web Vitals, SEO, best practices) are optional too; install only the ones you use, because each installed skill adds its description to every session.
- Optional: a Jira site and API token (tickets, status moves), Slack MCP (review requests), [Herdr](https://herdr.dev) (panes for the agents, the Stats page), `ccusage` (prices in `ale cost`), `rtk`.

## Install
```
git clone <this repo> ~/projects/agentic-framework
cd ~/projects/agentic-framework
bin/ale init          # asks for your GitHub org, Jira host, ticket prefix, Slack channel, integrations; writes ~/.ale/config.yaml
bin/ale install --dry-run && bin/ale install
```
`ale install` renders the role settings from `policy/*.json.tmpl` plus your config into `~/.ale/policy/`, links the agents and skills into `~/.claude/`, and adds one import line to your `~/.claude/CLAUDE.md`. Open new panes afterwards.

## Your settings stay local
Nothing about your company belongs in this repo. Two files in `~/.ale/` (never committed) hold it:
- `config.yaml`: GitHub owner, Jira host, ticket pattern, workflow statuses and "Type" field, default Slack channel, `integrations` (presets in `policy/integrations/`: atlassian, slack, figma, sourcegraph, glean, notion, chrome-devtools), `mcp_allow` (your own MCP tools), `webfetch_domains`, `env_vars` (names only), `a11y_skill`, `guides`. Defaults: `policy/config.defaults.yaml`.
- `rules.local.md`: rules only you need (design system, habits). Imported at the end of `global/ale.md`. The `setup-ale` skill helps write it.
- `banned-terms.txt` (optional): words that must never appear in a tracked file; `tests/test_public.py` checks it.
Per-repo gate settings live in `repos/<repo>.yaml`, also local only (git-ignored): `ale spawn` drafts one from the repo's package.json and lockfile.

## Use
```
ale spawn <repo> <PROJ-123> --branch PROJ-123/short-desc --type task --size M
```
or ask Alejandro with the `spawn-team` skill. The builder plans, you approve (or the plan's risk says it can go on), the builder builds tests first, `ale review` asks the reviewer for rounds, `ale gate` must be green, `ale pr-ready` opens the review request, and `babysit-pr` waits for CI and humans. Commands you type in a pane are in [docs/commands.md](docs/commands.md).

## Layout
- `global/ale.md`: rules for every agent. `agents/`: thin role prompts. `skills/`: procedures (spawn-team, ticket-to-pr, review-round, verify-ui, ship-pr, babysit-pr, epic, retro, setup-ale).
- `policy/`: settings templates per role, integration presets, limits, approval tiers, model pin.
- `bin/ale`, `lib/`, `hooks/ale_hook.py`: the CLI, gate and hooks. `herdr/`: the Stats page for the Herdr app. `tests/run.sh`: the tests.

## Reference
- [Commands you type](docs/commands.md)
- [What a task costs](docs/cost.md)
- [Task costs in the Herdr app](docs/herdr-stats.md)
- [How builder Bash is permitted](docs/permissions.md)
- [Plan approval and risk](docs/approval.md)
