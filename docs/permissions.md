# How builder Bash is permitted

The builder runs in `dontAsk` mode. Instead of a list of allowed command strings, the PreToolUse hook
(`hooks/ale_hook.py` -> `lib/ale_bash.py`) reads each command and **denies what is dangerous; everything else is allowed**.
The reviewer, lead and Alejandro keep their allowlists.

## What the hook checks (builder)
- Parses the command like a shell would: quotes, heredocs, `$(...)`, backticks, `sh -c`, `eval`, `find -exec`, wrappers
  (`env`, `xargs`, `timeout`, `nice`, `rtk`...), `VAR=` prefixes, `cd` inside a chain.
- git: push only `origin` and only the task branch (`-u` and a green-gate `--force-with-lease` allowed); no `git -c`, no `GIT_*=`
  overrides, no hook skipping; `git config` only for proven reads; rebase only onto `origin/<base>`, `origin/<branch>` or `origin/main|master` on a clean tree (and `--onto origin/main <old base>` when a stacked base was squash-merged);
  `reset --hard` only to `origin/<branch>`; `pull --ff-only` only; no `clean`, `filter-branch`, `update-ref`, `reflog expire`.
- gh: default deny. Allowed: `pr view|list|diff|checks|status|update-branch|create|edit(title, labels)`, `run list|view|watch|rerun|download`,
  `repo view`, `auth status`, `api` GET. Non-draft `pr create` needs the gate.
- Package managers: no publish, login, global installs, `dlx`; `npx` only for a tool in `node_modules/.bin` or a short known list.
- Machine tools: no `sudo`, `ssh`, `scp`, `rsync`, `nc`, `osascript`, `security`, `brew`, `docker`, `pkill`, `killall`; `hc` only `send|wait|get|read`.
- Writes (redirects, `cp`, `mv`, `tee`, `sed -i`, `dd of=`...) never reach the signing key, approvals, counters, rendered policy,
  `~/.claude`, `~/.ssh`, the framework checkout, any `.claude/settings*.json`, or `.git/config` and `.git/hooks`.
- Anything it cannot read that mentions a risky program is denied; a hook error blocks the call (exit 2) and is logged.

## Limits
Not a sandbox: `python3 -c` or `node -e` can still do anything the user can. This prevents accidents, not an adversary.
Settings `deny` rules are not a reliable backup (see below); the hook is the only layer for git and gh.

## Facts verified on Claude Code 2.1.329 (live probes, 2026-11-20)
| Claim | Result |
|---|---|
| A hook `allow` runs a Bash command that has no allow rule in dontAsk | yes (pipes, `cd &&`, `$(...)` ran) |
| A hook `deny` blocks; a hook `ask` in dontAsk | blocks / acts as deny |
| Our hook sees the original command when the user-level rtk hook rewrites it | yes; both run, the allow still applies |
| Settings `deny` rules beat a hook allow | yes, but they match the **rtk-rewritten** text: `Bash(gh pr merge*)` misses `rtk gh pr merge` |
| PreToolUse exit 2 blocks, even over a JSON allow | yes |
| Exit 1 or an uncaught Python exception | "no objection": the call goes ahead (hence the exit-2 wrapper for pre-* events) |
| Exit 2 on Stop / UserPromptSubmit | blocks stopping / swallows the prompt, so those events never exit 2 |
| Unverified | two hooks returning competing `updatedInput`; `agent_id` in hook input |

## No fallback list
The builder template has no `Bash(...)` allow rules: the hook decides every command. If the hook crashes, Claude's own `dontAsk` mode denies
whatever is not allowed (fail closed). Only the reviewer still runs on a list of allowed commands, extended with the commands of the repo's checks.

## Improving it
`ale hook-log [--since 7d]` groups denials and errors by reason (global log `~/.ale/logs/hook.jsonl`, per task `events.jsonl`).
A wrong denial: fix the table in `lib/ale_bash.py` and add a case to `tests/test_bash_policy.py`.
`sh tests/smoke_permissions.sh` runs the real rendered settings through `claude -p` with and without rtk (opt-in, costs tokens).
