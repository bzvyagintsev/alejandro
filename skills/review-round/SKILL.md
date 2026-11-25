---
name: review-round
description: One builder-reviewer round. The builder commits, runs `ale review`, then triages the reviewer's report by severity. Use inside ticket-to-pr.
---

# review-round (builder)

1. Commit everything (the review is for one sha; the tree must be clean). `ale gate` should already be green.
2. Run `ale review`. It writes `.ale/reviews/round-N-request.md` (sha, the pasted ticket and epic, Figma links, evidence folder, report path), rings the reviewer's doorbell, and waits. The reviewer keeps its conversation between rounds (it is cleared only at the first review and at the first code round after plan reviews), so a follow-up round asks it to check its earlier findings and review the whole PR again. It refuses round 4 and writes needs-user. If it reports the reviewer is blocked, set needs-user and stop; do not touch the reviewer's pane.
3. Read `.ale/reviews/round-N.md` yourself. Header: `VERDICT`, `sha`, `CRITICAL`, `MAJOR`.
4. **Triage every finding:**
   - Reproduce the claim first (a failing test or command). A claim you cannot reproduce is a hypothesis: say so.
   - Fix all CRITICAL and MAJOR findings, and the MINOR ones that are cheap or valuable.
   - Reject the rest with a one-line reason each, naming the finding (M1, M2...), in `.ale/reviews/round-N-response.md`. `ale review` pastes that file into the next request, so the reviewer sees why. If the reason is a scope decision of the user, ask the user to record it (they type `scope: <text>` in your pane); do not argue it from memory. Surface every rejected item in your final report to the user.
   - If a finding exposes a design smell, answer "why does this exist" before polishing.
5. Fix, run `ale gate`, commit, and go to step 2 until the verdict is `GO` with CRITICAL 0 and MAJOR 0, or the round limit stops you.
6. Fixes after the last review larger than the re-review limit in `policy/limits.yaml` need another round (the gate checks this).
