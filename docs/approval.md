# Who approves a plan

After the builder writes `.ale/PLAN.md` it asks the reviewer to check it (`ale review --plan`). The reviewer's report, `.ale/reviews/plan.md`, starts with:

```
VERDICT: APPROVE | REVISE
BLOCKERS: <n>
plan_sha: <hash of the plan it read>
RISK: green | yellow | red
RISK_WHY: <one line>
```

`ale plan-gate` then decides who approves, from the task's size (`TASK.md`) and that risk, using `policy/approval.yaml` (first matching rule wins):

| | size S, M | size L |
|---|---|---|
| **green** (good tests, isolated) | auto | user |
| **yellow** (weak or mixed tests) | auto, after a first commit that pins today's behaviour | user |
| **red** (auth, billing, permissions, payroll, data deletion, migrations) | user | user |
| no risk label, stale review, REVISE | user | user |

## The tiers
- **auto**: the builder runs `ale approve` itself. It is recorded in `approval.json` as `by: auto` with the risk, and pinned to the plan hash: change the plan and the approval is void.
- **user**: you type `approve plan` in the builder pane (a hook records it; nothing an agent writes counts).
- **main**: parked. The code still supports it (Alejandro or the lead runs `ale approve`) but no rule uses it; the idea is in `IDEAS.md`.

## Where things are stored
- the **risk**: the reviewer's report, `~/.ale/tasks/<id>/reviews/plan.md` (`RISK:` and `RISK_WHY:` lines). A risk counts only when that report's `plan_sha` matches the current plan.
- the **tier**: not stored. It is computed each time from the rules, the size and flag in `TASK.md`, and the risk. (Older tasks have a stale `tier:` line in `TASK.md`; it is ignored.) To pin a tier by hand for one task add `tier_override: user|main|auto` to its `TASK.md`.
- the **approval**: `~/.ale/tasks/<id>/approval.json` (who, tier, risk, plan hash). Only `ale approve` and the typed-message hook write it.
- the **rules**: `policy/approval.yaml`.

## Notes
- Risk is the reviewer's judgement. It can only raise the risk (the reviewer prompt says so), and the code review, the gate and your teammates' PR review still follow.
- Review and approval are separate from `reviewer_keep_context` and from the cost tools.
