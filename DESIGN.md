# Design notes

## Who uses it and for what
A developer (or their coding agent) is about to run an A/B test with Flagsmith Experimentation. The task: start an experiment that will produce numbers you can trust, and only roll out a winner the data supports. The result a reviewer should see in under three minutes: a broken experiment stopped before it starts, a person approving the start, and a "winner" on the Flagsmith results page that Gatekeeper refuses to ship yet, with the reason.

## Console layout
Developer tool, so the reference is Vercel's dashboard (tokens from its DESIGN.md, adapted).
- Three columns on wide screens: experiment list (272px), the selected experiment, audit trail (320px). Under 1360px the audit trail moves below; under 860px everything stacks and the stage track scrolls sideways.
- The selected experiment has a five-step track (Preflight, Approval, Running, Verdict, Rollout). It opens on the step that needs attention. Each step has one primary button.
- The Verdict step puts "Flagsmith results page" and "Gatekeeper verdict" side by side. That comparison is the product.

## Tokens
- Type: Geist (UI), Geist Mono (keys, flags, file paths, fingerprints). Sizes 22 / 18 / 14 / 12 px. Numbers use tabular figures and are right-aligned in tables.
- Neutrals: ink `#111214`, body `#3f4248`, mute `#6b6f76`, line `#e7e8ea`, canvas `#ffffff`, soft `#f7f7f8`.
- One accent: caution yellow `#ffd60a`, used only for the approval buttons and the Gatekeeper side of the comparison. It is the gate.
- Status colours carry a shape as well (triangle = blocks, ring = not yet, diamond = pass) so they never rely on colour alone.
- Radius 6px (controls) and 10px (panels). Spacing 4 / 8 / 12 / 16 / 24.
- Motion: one curve `cubic-bezier(.22, 1, .36, 1)`, 150 to 600ms, off under `prefers-reduced-motion`.

## Statistics (frozen on 2026-11-26, before any experiment was run)
- Sample size: two-proportion normal approximation, alpha 0.05 two-sided, power 0.8.
- SRM: chi-squared against the split that was approved, p < 0.001 means invalid.
- Ship: chance to win at least 95% and every group at its planned sample size. Drop: every variant at or below 5%.
- Setup drift: approval is bound to a SHA-256 fingerprint of the flag's variations, the experiment split and the metric definitions. Any change voids it.
- Peeking: every results read is logged; more than 3 reads before the planned sample adds a warning.
