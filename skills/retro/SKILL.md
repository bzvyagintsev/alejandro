---
name: retro
description: Turn task learnings into proposed changes to rules, skills, repo configs and repo docs. Run after several tasks or when the user asks. The user approves each change.
---

# retro (lead or Alejandro, started by the user)

1. Collect: `ls ~/.ale/tasks/*/LEARNINGS.md`, the `events.jsonl` files (gates failed, rounds, needs-user, interventions), and any reviewer findings that a human later repeated.
2. Group lessons by where they belong:
   - rule for every agent -> `~/projects/agentic-framework/global/ale.md`
   - procedure step -> the skill that owns it
   - gate check -> `lib/ale_gate.py` or `repos/<repo>.yaml`
   - hook rule -> `hooks/ale_hook.py` (with a test in `tests/`)
   - repo knowledge a teammate's agent also needs -> a docs PR to that repo (`docs/*.md`, linked from AGENTS.md); propose it, never push it without approval
3. For each proposed change show the exact old and new text. Drop lessons already covered. Prefer deleting a rule over adding a near-duplicate.
4. Apply only what the user approves. Then run `tests/run.sh`.
5. Spend: run `ale cost --all`, then read `.ale/artifacts/cost.md` of each task in scope. Group the suggestions that repeat across tasks (starting context, cache rebuilds, long sessions, big tool results, many rounds) and propose one change per pattern with the numbers (`docs/cost.md` has the usual fixes). Tasks far above the others get a one-line reason.
6. Report metrics, counted from `events.jsonl` (`gate`, `review_done`, `needs_user`, `round_granted`, `jira_failed`, `pr_watch`): tasks, gate first-pass rate, review rounds, needs-user count, rejected reviewer items.
