# Commands you type

Two kinds: **messages you type in an agent's pane** (a hook reads them; nothing else counts as approval, and text relayed by
other agents never does), and **`ale` commands you run in a shell**. Check this file when you forget one.

## Typed in the builder pane (exact text, nothing else in the message)
| You type | What happens |
|---|---|
| `approve plan` or `approve plan <hash>` | Approves PLAN.md (the hash pins the exact version) and unlocks code edits. Moves the Jira ticket to In Progress. |
| `one more round` | Grants one extra review round after the limit (3) is used up. Needed whenever the builder says the review rounds are used. |
| `scope: <text>` | Records a binding scope decision in `.ale/DECISIONS.md`. It is pasted into every plan and code review request and shown to the builder at session start. Use it when the ticket text and your intent differ, e.g. `scope: Notion, Zoho and Airtable keep their current events; only the nine apps in the plan are in scope`. |
| `post ticket <code>` / `post comment <code>` / `post edit <code>` | Posts exactly the Jira write that the builder previewed; the 6-character code comes from the preview. |

A plain "yes" or "ok" in chat approves nothing and raises no limit.

## In a shell (`ale <command>`)
| Command | Use |
|---|---|
| `ale spawn <repo> <id> --branch <b>` | Start a task space (builder + reviewer + dev tab). Usually done by Alejandro. |
| `ale alejandro [--here]` | Open the Alejandro (HQ) pane that starts tasks and reports status. |
| `ale repo-info <repo>` | Recent branches, commit subjects, worktrees and whether local main is behind (read-only). |
| `ale plan-summary [--task-dir <dir>]` | Short summary of a task's plan, to decide on approval. |
| `ale context` | Print a task's restored context (status, latest review, approval, user decisions); the session hook runs it. |
| `ale status` | One line per task: phase, step, what is waiting for you. |
| `ale decide <task-id> "<text>"` | Record a scope decision from a shell (the same file as `scope:`). Refused for builder, reviewer and lead. |
| `ale approve [--task-dir <dir>]` | Approve a plan. From your shell it always works; from Alejandro or the lead only when the tier is `main`; from the builder only when `ale plan-gate` says AUTO-APPROVE. See `docs/approval.md`. |
| `ale resume <id> [builder\|reviewer]` | Reopen a pane for a task (the reviewer starts fresh; the builder offers its conversation picker). |
| `ale retarget [main]` | Builder or you, after a stacked PR's base was squash-merged and the branch was rebased onto main: changes the task's base so the gate diffs against main. Refuses while the old base branch still exists on origin. |
| `ale cost [task] [--all] [--write]` | What a task cost: dollars per role and session (builder, reviewer, lead), turns, starting context size, cache rebuilds, and rule-based suggestions. `--all` lists every task; `--write` saves `.ale/artifacts/cost.md`. Prices come from `ccusage`. |
| `ale hook-log [--since 7d]` | What the Bash policy denied, grouped by reason. Use it to find wrong denials. |
| `ale cleanup <repo> <id> --yes` | After the PR is merged: stop servers, remove the worktree and tab. |
| `ale repo-init <repo>` | Draft a repo config (`repos/<repo>.yaml`) from the repo's files. |
| `ale init [--non-interactive --github-owner ... --jira-host ... --ticket-prefix ... --slack-channel ... --integrations a,b --webfetch d1,d2 --mcp t1,t2 --env-vars N1,N2 --a11y-skill name]` | First setup: asks for your GitHub org, Jira, Slack and integrations and writes `~/.ale/config.yaml` (and a starter `~/.ale/rules.local.md`). Keys you do not answer are kept. Run `ale install` after. |
| `ale install` | Re-render policies and links after changing templates, agents or skills. Run it after any framework change. |
| `ale jira get\|move\|done <KEY>` | Read a ticket (summary, description, links, parent epic and its comments) or move it (writes with text need your typed `post ...`). |
| `ale trust <path>` | Pre-trust a folder so the agent does not show the trust prompt. |

Builder-side commands you do not type (`ale gate`, `ale review`, `ale pr-ready`, `ale set`, `ale plan-gate`, `ale watch-pr`,
`ale sync-branch`, `ale bootstrap`, `ale port`) are described in `skills/ticket-to-pr/SKILL.md`.
