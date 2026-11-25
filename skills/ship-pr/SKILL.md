---
name: ship-pr
description: Open the PR after the gate is green, watch CI, mark it ready through `ale pr-ready`, and post the review request and hand over the media. Use at the end of ticket-to-pr.
---

# ship-pr (builder)

Named `ship-pr`, not `ship` (like `ticket-to-pr` and `babysit-pr`): Claude Code has a built-in `/ship` command that commits, pushes and opens a PR by itself, and it takes precedence over a skill of the same name.

1. **Squash** only if the user wants one commit, only before the final review round, and by the squash rule in the global rules (to the merge-base, with the exact commands shown to the user first). Never after the final review.
2. Stop any server or process you started yourself and remove scratch files you created in the repo (`rm <file>`). The framework's `dev` tab server stays. Then `ale gate` must be GREEN on the final HEAD and a clean tree (untracked files fail it).
3. Push with an explicit refspec: `git push -u origin HEAD:<branch>`. Then `git branch --unset-upstream` if it tracks the base.
4. Check for a PR template and fill it. Open a **draft** PR: `gh pr create --draft --title "type(<TICKET>): title" --body-file <file>`. If the work was stacked and the base PR merged meanwhile, rebase onto main first (confirm with the user; `git rebase --onto origin/main <old base> HEAD`), then `ale retarget main` so the gate diffs against main, do not stack a dead branch.
5. Watch CI: `gh pr checks --watch`. On a failure, diagnose and fix at the root cause, `ale gate`, push. At most 2 fix attempts (`policy/limits.yaml`), then `ale set needs-user=..`.
6. When CI is green and the gate is green for the pushed HEAD: `ale pr-ready`. It marks the PR ready and moves Jira to the review status ("In Review"; "Code Review" for bugs), going through "In Progress" if needed. If Jira cannot reach it, it prints what is offered and you tell the user; never guess a transition.
7. **Slack review request.** Only after `ale pr-ready` succeeded. The channel is known: use `slack_channel_id` from TASK.md; never search for it. Post it with `slack_send_message` (a hook allows exactly one post per task, to that channel, with the ticket key and the PR link, and no mentions or thread replies). The tool takes standard markdown links, so write it exactly like this:
   ```
   Hey team, [PROJ-123](https://example.atlassian.net/browse/PROJ-123): <one plain line on what the PR does>. Please review: <PR url>
   ```
   For stacked PRs, one line per PR in review order, plus the base note. No process talk. If the hook denies the post, create a `slack_send_message_draft` with the same text and `ale set needs-user="Slack post denied: <reason>"`. Do not try another channel.
8. Re-read the PR body (`gh pr view --json body`): required lines (the attribution) must have survived the Jira bot.
9. Hand the user the media paths (`.ale/artifacts/video`, `screenshots`): `gh` cannot upload them. Report: PR link, review round counts, every rejected reviewer item with its reason, anything not verified.
10. `ale set phase=watching pr=<url>`, then run the `babysit-pr` skill. Learnings are written when the PR is merged or closed, not before.
