# 05 — GUI: the HUD Control Center

The interface is Popper's showcase (Jake's own use **and** the public demo). Direction, from Jake's
reference dashboards (`design-source/gui-refs/`: `popper dashboard.htm`, `Popper Dashboard 2.jpg`,
`Popper dasboard 3.jpg`): a **dark sci-fi HUD / command-center** — near-black navy surface, glowing
cyan/teal primary, multicolor neon data, modular framed panels around a **central 3D hero**, ring gauges,
bar/line charts, and an alarm-style event stream.

**The interaction Jake asked for:** every tile is **click-to-expand** — a small panel opens into a large
detail view for finer analysis. Reuse the existing `SubsystemView` expand pattern.

## Layout — hero + expandable HUD tiles
Extend the shipped full-screen shell (`gui/` — sidebar + fluid main; keep it). Home becomes the HUD:
- **Center — the 3D hero: the Alpha Memory Map.** The one genuinely-3D element, because the data *is*
  spatial: a projection (UMAP/PCA of alpha embeddings, `04`) of the alpha space. Point cloud where
  **density = crowded vs sparse vs whitespace**, nodes coloured by category (or verdict), clusters = live
  categories. Hover a node → alpha summary; click → full alpha/lineage detail. This *is* the "architect's
  overview" made visual. Tech: **react-three-fiber** (three.js) with instanced points + bloom/glow.
- **Perimeter — HUD tiles** (each a framed neon panel, click to expand):

| Tile | Form (dataviz job) | Grows with tests |
|------|--------------------|------------------|
| Pipeline status | the existing pipeline map, HUD-reskinned | live |
| Survivor / kill rate over time | line (change-over-time) | accrues per cycle |
| Explore/Exploit dial (Director) | radial gauge (single headline) | live balance |
| Category health | horizontal bars — alive vs paused/mined-out | grows w/ categories |
| Verdict stream | alarm-style event list (SURVIVE/KILL/PROMOTE + reasons) | streams |
| Scorecard | radar — robustness/regime/capacity/novelty/deflated-Sharpe | per survivor |
| Compute budget | ring gauge — spent vs budget per engine | live |
| Portfolio / correlation | heatmap (late phase, `04`) | after portfolio engine |

Empty/loading/error states are mandatory per tile (Jake's UI rule — every screen has all four states).
Tiles populate as tests accumulate; a fresh install shows scaffolding + empty states, not blanks.

## Palette — neon-on-dark (CANDIDATE — must be validated, not eyeballed)
Neon-on-near-black is the exact case the dataviz skill says to **compute**. Fable 5 **must** run
`scripts/validate_palette.js "<hexes>" --mode dark` (from the dataviz skill) against the dark surface and
**snap to passing** (CVD ≥ 12, chroma floor, contrast) before shipping any chart. Candidate starting point:
- **Surfaces:** base `#0A0E1A`, panel `#0F1B2E`, panel edge/glow = primary at low alpha.
- **Primary accent:** cyan `#22D3EE`.
- **Categorical (fixed order, never cycled; 9th → "Other"):** `#22D3EE` `#F472B6` `#34D399` `#FBBF24`
  `#A78BFA` `#60A5FA` `#FB923C` `#2DD4BF`.
- **Sequential (magnitude, one hue light→dark):** cyan ramp (for density / heatmaps).
- **Diverging (explore↔exploit, correlation ±):** cyan ↔ magenta, neutral-gray midpoint.
- **Status (reserved — never a data series; ship with icon+label):** good `#34D399` · warning `#FBBF24`
  · serious `#FB923C` · critical `#F87171`.

## Non-negotiable dataviz rules (Fable 5 must follow the skill)
One y-axis ever (two scales → two charts). Categorical hue follows the entity, not its rank. Legend present
for ≥2 series; ≤4 also direct-labelled. Text uses ink tokens, never the series colour. Hover tooltip on
every plot (crosshair on line/area, per-mark on bar/dot/cell). A table view exists for every chart.
Dark-mode steps validated against the dark surface — not an auto-flip. Full method: the **dataviz skill**
(read it at build time).

## Restraint (ponytail)
3D **only** for the map (real spatial data). Everything else is flat neon HUD — do not fake-3D the charts.
The audience is serious quants: it should read as *powerful and legible*, not a toy. Glow/scanlines are
accents, never at the cost of data contrast (the validator enforces this).

## Build timing
Grows across phases (`06`): the Alpha Map hero + density lands with the map (Phase 4); analytics tiles
accrue through Phases 3–7. The HUD reskin of the existing shell can happen early and cheaply.
