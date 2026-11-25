---
name: verify-ui
description: Judgment checks for UI changes: Figma-to-design-system mapping, real-UI walkthrough of every state, Figma comparison, flag-off measurement, accessibility audit, video. Writes .ale/artifacts/ui-check.md which the gate verifies.
---

# verify-ui (builder, or a subagent it starts)

Never repeat build, test or lint here: `ale gate` owns those. Run `ale gate` first and stop if it is red.
Run this as a subagent when you can, so screenshots and Figma payloads stay out of the builder's context; it returns only the path of ui-check.md.

1. **Figma to design system.** For every frame in TASK.md, call `get_design_context` and `get_screenshot` (figma-remote-mcp; create a session first). Map each element to the exact design-system component and variant (use your design-system MCP, if you enabled one). Tokens and component props, not `style`.
2. **Walk the real UI** on your random port (`APP_DIRECTORY_PORT`; check the loaded script URL and that the server's cwd is this worktree). Headless Playwright from the repo is the primary tool; scripts live in `.ale/scratch/`, not in the repo. Use `agent-skills:browser-testing-with-devtools` for DOM, console and network checks. Screenshot every state in the ticket and Figma: default, empty, loading, error, flag-off, drawer-open widths.
3. **Compare** with the Figma screenshots side by side. Figma is intent, not pixel truth. List real differences.
4. **Flag-off and unchanged paths:** screenshot the same view on `origin/main` (a second worktree, another port) and compare. Measure; do not infer "same as before".
5. **Accessibility:** run the accessibility audit skill named in TASK.md (`a11y_skill` in the user's config); without one, use your own checklist (axe, keyboard, screen-reader names). Also check keyboard use, focus after unmount, label-in-name, and live regions mounted before their content changes. Save the result to `artifacts/a11y.md`.
6. **Video** for flows: Playwright `recordVideo`, saved to `artifacts/video/`. Copy screenshots to `artifacts/screenshots/`.
7. **Write `.ale/artifacts/ui-check.md`**, exactly this shape:

```
sha: <git rev-parse --short HEAD>
# UI check
- [PASS] Figma frames mapped to design-system components: artifacts/figma/mapping.md
- [PASS] Default state matches: artifacts/screenshots/default.png
- [FAIL] Empty state: heading wraps at drawer-open width: artifacts/screenshots/empty-narrow.png
- [N-A] Video: no flow
```
Every item is `- [PASS|FAIL|N-A] text`. Referenced files under `artifacts/` must exist. Fix every FAIL (or set needs-user if it needs a design decision), recommit, and re-run this skill; the gate rejects a ui-check.md whose sha is older than the last UI change.

The LLM comparison is soft. The user's PR read is the final gate.
