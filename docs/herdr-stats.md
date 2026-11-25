# Seeing task costs in the Herdr desktop app

`ale cost` reads what each task cost from Claude Code's session transcripts (prices from `ccusage`). This page shows how to put those
numbers in the Herdr desktop app, or how to get them without the app.

What the view shows, one row per ticket: total, builder and reviewer cost and turns, review rounds, lines added and deleted, files,
starting context size and cache rebuilds (builder / reviewer), plus totals, average, median, reviewer share and a cost-per-ticket chart.

## Needs
- the Herdr desktop app (`herdr-app`), opened at least once;
- this framework installed (`ale install`) with some finished tasks under `~/.ale/tasks`;
- `ccusage` for the dollars (`npm i -g ccusage`). Without it the rows show tokens only;
- for the GitHub cards (cycle time, review quality, merged PRs, lines added): `gh` and a `GH_TOKEN` in your login shell. The scripts ask a login shell for it
  because the desktop app does not inherit your shell's environment, and they never store it.

## Option 1: install the whole Stats page (recommended)
`herdr/stats/` is the Stats page of this setup: cards for cycle time, review quality, agent sessions (all Claude Code sessions, from `ccusage`),
the **Ale tasks** table, merged PRs per day, lines added per month and spawn-to-merge time. It is the `stats` mod, one folder with `main.js`, `mod.json`
and one `fetch-*.sh` script per card.
```sh
sh ~/projects/agentic-framework/herdr/install.sh      # copies herdr/stats to ~/.config/herdr-app/mods/stats
herdr-app do mod.open --name stats                    # opens it as an overlay; run it again to close
```
- The installer records where the `ale` CLI is in `mods/stats/ale-path` and runs `herdr-app check`. If a different `stats` mod already exists it stops and
  tells you how to compare (`diff -r`); `--force` replaces it and keeps yours as `stats.bak-<time>` next to `mods/`.
- Each card has its own script and refresh time (5 to 30 minutes), and a failed script shows its error in that card only. Cached data is in `~/.cache/herdr-stats/`.
- The Ale card runs `fetch-ale.sh` -> `ale cost --all --stats` through a login shell (`ccusage` is a node script and the app has no node on its PATH).
- A key or a button is a Herdr layout matter: ask your Herdr agent, for example "bind ctrl+shift+s to `mod.open stats`" or "add Stats to the tools nav".
  The agent reads `~/.config/herdr-app/AGENTS.md` for how. (On this setup the Stats button in the left rail already opens this page.)
- To change the page, edit the files in `herdr/stats/` and run the installer again (it overwrites only with `--force` if your installed copy differs), or edit the installed
  copy and copy it back into the repo. The mod hot-reloads when its files change.
- To keep only the costs, delete the other cards from `SOURCES` and `render()` in `main.js` and their `fetch-*.sh`.

Not checked live: the overlay size in `mod.json` (1180 x 700). Change `overlay.width` and `overlay.height` there if it looks cramped.

## Option 2: only the data
- Terminal: `ale cost --all` (one line per task) or `ale cost PROJ-123` (per role and session, with suggestions).
- JSON for anything else: `ale cost --all --stats` (compact rows and totals, the mod's input) or `ale cost --all --json` (everything).
- Ask Herdr or any agent to draw it: *"Read ~/.config/herdr-app/AGENTS.md. Write a mod `my-costs` that runs
  `python3 ~/projects/agentic-framework/bin/ale cost --all --stats` through a login shell (`zsh -lic`) every 10 minutes and shows one row per task
  from `rows[]` (task, total, builder.cost, reviewer.cost, rounds, code.added, code.deleted). Run `herdr-app check` before you finish."*

## When it shows nothing
- `ale error: ...` in the card header: run `sh ~/.config/herdr-app/mods/stats/fetch-ale.sh` in a terminal and read its message.
- Rows without dollars (`n/a`, or a `+` after a total): `ccusage` is missing or could not fetch prices (`ale cost` says which).
- The page is stale after changing the mod: close it and open it again. Errors are listed by `herdr-app status`.
- A ticket is missing: only tasks with builder, reviewer or lead sessions in the task's worktree folder are counted (`docs/cost.md`).
