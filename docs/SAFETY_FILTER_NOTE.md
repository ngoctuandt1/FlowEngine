# Safety Filter Note

Date: 2026-04-28

Flow's currently reachable UI exposes only binary `Safe mode`, not the
3-level `safety_filter` enum we initially designed (`block_most`,
`block_some`, `block_few`).

Live probing confirmed:

- The L1 and L2 composer surfaces did not expose a mounted safety selector.
- Shipped page copy referenced only binary safe-mode labels such as `On` and
  `Off`.

Conclusion:

- Do not wire or persist a 3-level `safety_filter` through the job API.
- Existing `safety_filter` columns in older SQLite databases may remain; they
  are harmless legacy schema.
- If Flow later exposes a visible safety control, re-probe the UI and design
  against the mounted options rather than latent bundle strings.
