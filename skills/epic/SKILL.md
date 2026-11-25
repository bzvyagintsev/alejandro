---
name: epic
description: The lead's procedure for an epic or a set of tickets: refine, break down, order by dependency, get one approval, then start one tab per child ticket and track progress from files.
---

# epic (lead)

1. **Read** the epic and its child tickets through `ale jira get <KEY>` (links, status, size, labels). Reuse existing tickets; do not invent ones that exist.
2. **Vague or unrefined epic?** Run `/agent-skills:spec` first (it defines *what*; save to `.ale/SPEC.md`). Then always run `agent-skills:planning-and-task-breakdown` (the breakdown: dependency graph, vertical slices, acceptance criteria; save to `.ale/PLAN.md`, not the repo).
3. **Order by dependency** using Jira links and sizes. Keep each child under ~500 LOC (propose splits). Dependencies that must merge first set the child's base: a dependent child is based on the parent's branch (stacked, lowest branch first); an independent child on main.
4. **Write `.ale/EPIC.md`**: table of child ticket, repo, branch, type, size, depends-on, status, PR. Propose new tickets in a list; never create them without approval.
5. **Stop for the user's OK** (`ale set needs-user="EPIC.md ready"`). One approval covers the whole table.
6. **Spawn children** in dependency order, at most 3 in parallel: `ale spawn <repo> <child> --parent <epic> --branch .. --type .. --size .. [--base <parent branch>]`. Each child is a tab in this epic's space, with its own worktree, builder and reviewer.
7. **Track** with `ale status` and `gh pr view`; update EPIC.md. A child's plan at tier `main` with a clean reviewer verdict: run `ale plan-summary --task-dir ~/.ale/tasks/<id>`, check it, then `ale approve --task-dir ...`. Tier `user`: tell the user which tab to open.
8. "Dependency cleared" means a human merged the PR. Then spawn the next child.
9. **Close:** when all children are merged or ready, check every requirement of the epic and TASK.md was delivered, and report gaps to the user.
