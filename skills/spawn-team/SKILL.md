---
name: spawn-team
description: Start a task space (builder + reviewer, or a lead for an epic) with `ale spawn`. Use when the user gives Alejandro a ticket or an epic to start.
---

# spawn-team (Alejandro, or the lead for child tickets)

1. **Look up the ticket (read-only):** `ale jira get <KEY>`. Note type, parent epic, links, labels, and whether acceptance criteria look large.
2. **Choose the team** with the routing table in your role prompt. A Story with many criteria or linked issues: flag it and suggest a lead. If the user named a team, use exactly that.
3. **Work out the fixed inputs** before spawning; ask the user only for what is missing:
   - branch: `<TICKET>/<short-desc>`; run `ale repo-info <repo>` to see the repo's recent branches, commit subjects, worktrees and whether local main is behind. Do not hand-roll git commands for this: use `ale repo-info`, or `git -C <repo> ...`. Never wrap commands in `( ... )` subshells or `cd ... &&` chains; the permission check cannot allow them.
   - `--type bug|task|story|refactor|chore`, `--size S|M|L`, `--flag` if it touches a feature flag
   - Figma frame links (`--figma <url> ...`) for UI work
   - stacked on another unmerged PR? Then `--base <that branch>` (check with `gh pr view <PR>` that it is not already merged)
4. **State the plan in one line** and wait for a yes unless the user said "spin off":
   `PROJ-123 Bug, S -> builder + reviewer. Go?` (who approves the plan is decided after the plan review, from its risk and the size)
4a. **Repo not cloned?** `ale spawn` (and `ale repo-info`, `ale repo-init`) clone it into `~/projects/<name>` with `gh` first. A bare name uses `github_owner` from `~/.ale/config.yaml` (`ale init`); for another org the user gives `owner/name`. If GitHub says the repo is not found, ask the user for the exact name; do not guess.
4b. **New repo?** If `repos/<repo>.yaml` does not exist, `ale spawn` drafts it from the repo (`ale repo-init`), prints what it found and what it could not detect, and carries on. Read that output, tell the user what to check (Node version, dev port, Slack channel and its id, code-generation tokens), and do not rely on a draft for risky work until they have looked at it.
5. **Dry run once**: `ale spawn <repo> <id> --branch <b> --type .. --size .. --dry-run`, check it, then run it for real (without `--dry-run`).
6. Report the space name and what the user should open. Do not relay or approve inside the space.

For epics: spawn the lead with the `epic` task text; the lead spawns children with `--parent <epic-id>`.
