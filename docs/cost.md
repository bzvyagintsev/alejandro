# What a task costs

`ale cost [<task>]` (run it in the task's worktree, or name the task) prints the dollars per role and session, plus suggestions.
`ale cost --all` lists every task. `ale cost --write` also saves `.ale/artifacts/cost.md` and logs a `cost` event. Run it at the end
of a ticket; the `retro` skill compares tasks and turns the suggestions into proposed changes.

## Where the numbers come from
- **Sessions:** Claude Code keeps one transcript per session in `~/.claude/projects/<worktree path with / and . as ->/<sessionId>.jsonl`.
  Each role pane is started with `--name <task>-<role>`, which is written into the transcript, so the role needs no extra recording.
- **Dollars:** `ccusage session --json` (the same source as the herdr stats page and your statusline's monthly figure). `ale cost` asks it for
  online prices. Its cached offline table lacks new models and shows $0 for them; if the online lookup fails, `ale cost` retries offline and says so.
  A cross-check on one session: ccusage $1.12 against Claude Code's own `cost-state` record $1.16.
- **Counted:** builder, reviewer and lead sessions. Other sessions that ran in the same worktree folder (probes, your own) are listed but not counted.
  Alejandro is not counted yet (his sessions are not tied to one task).
- **Subagents:** their transcripts sit under the session folder; ccusage includes them in the parent session (not verified against a task with many subagents).

## Cost per review
`ale cost` also splits the reviewer's price by review: each plan review and code round runs from its request (an event in `events.jsonl`) to the next one. A session's price is shared out by weighted tokens (output 5x, cache write 1.25x, cache read 0.1x of an input token), so it is an estimate; exact only per session. Each line also shows the context the review started with (`start 150k`) and whether the reviewer's conversation was `kept` or `cleared` before it.

What decides the price of a review is the prompt cache, not the clear. Within about five minutes of the previous turn the whole context is read from cache and a round costs about $0.20; after a longer idle gap the cache has expired and the whole context is rewritten (on one real task: $0.82, $1.22 and $1.66 after gaps of 65, 20 and 4 hours).

## The reviewer's session
By default the reviewer keeps its conversation between reviews (`policy/limits.yaml`: `reviewer_keep_context: true`). It is cleared before the first review of a task and before the first code round that follows plan reviews, so an approved plan cannot anchor the code review. Set the option to `false` to clear before every review, and compare the two with `ale cost` (cost, start context, rounds to GO).
A clear has to be typed as keys (`hc keys t99 / c l e a r enter`). Until 2026-11-24 `ale review` pasted the text `/clear` with `hc send`, which Claude Code receives as a chat message: on one real task the reviewer kept its context through all seven reviews and answered "Idle." to each one.

## Code changes
`ale cost` also prints the lines the task changed against its base (added, deleted, files; source and test lines apart), counted like the gate's `pr-size`: generated files, lockfiles and snapshots are left out. The numbers are saved to `.ale/artifacts/diffstat.json` whenever the worktree exists, so they survive `ale cleanup`.

## What the suggestions mean
| Suggestion | Rule | Usual fix |
|---|---|---|
| starts at Nk tokens | the first turn's context is over 100k | trim per-role plugins and MCP servers |
| rebuilt its context N times | a later turn wrote over 100k tokens of cache | fewer idle waits over 5 minutes; keep the reviewer's session between rounds |
| ran N turns, context up to Nk | over 250 turns | restart from files at a phase boundary |
| got a Nk-character X result | one tool result over 20k characters | narrow that command (`rg` flags, a path) |
| reviewer cost more than builder | both priced and reviewer higher | check rounds and the reviewer's start size |
| N review rounds | more than 3 | record scope decisions early (`scope: ...`) |

Parked ideas (per-role plugin trimming, the reviewer keeping its context, a stats screen, Alejandro and lead cost) are in `IDEAS.md`.
