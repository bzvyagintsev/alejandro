---
name: setup-ale
description: Help the user write their local ale rules (~/.ale/rules.local.md) and settings (~/.ale/config.yaml): company names, design system, accessibility skill, variables, repo habits. Use after `ale init`, or when the user says "set up ale" or "add a rule only for me".
---

# setup-ale

Local rules and settings stay out of the repo: `~/.ale/config.yaml` (settings) and `~/.ale/rules.local.md` (rules every agent reads). Never write company or personal details into a tracked file of the framework.

1. Read `~/.ale/config.yaml` and `~/.ale/rules.local.md` (they may not exist yet: run `ale init` first, it asks the basics).
2. Ask, in one batch, only what is missing:
   - the design system and how to look it up (an MCP name, a docs site) and the rule for using it;
   - the accessibility audit skill (its name goes in `a11y_skill`);
   - repo habits worth a rule (a script that must never be a check, how to run one test file, required Node version if not in the repo config);
   - environment variable NAMES agents may rely on (never values);
   - guides to read before infra code (`guides:` in the config; a repo config can override).
3. Put settings in the config (`integrations`, `mcp_allow`, `webfetch_domains`, `env_vars`, `a11y_skill`, `guides`); put prose rules in `rules.local.md` as short bullets under headings. A rule is one sentence an agent can follow without asking.
4. Show the user the diff of both files and wait for their OK. After changing integrations, MCP tools or domains, tell them to run `ale install` and open new panes.
5. Do not copy rules the framework already has (`global/ale.md`) into the local file.
