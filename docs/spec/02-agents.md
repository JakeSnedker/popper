# 02 — The Agent Roster & Access Control

The multi-agent research pipeline. Source: `design-source/42_Project_Research_Notes.html` §6–8,
`42_System_Proposal.html` §4–5.

## The load-bearing rule
**Results are hidden from the generator.** If the hypothesis agent can see performance, it copies winners
and the system tunnels. "Separation" = **scoped read-access over one store** (the alpha memory / map), not
duplicated databases. Enforce this at the memory-view layer (`04`), not by trust.

## The roster
| Agent | Job | Sees | Hidden from it | Runtime model (DECISION) |
|-------|-----|------|----------------|--------------------------|
| **Hypothesis** | generate novel, falsifiable alphas aimed at sparse regions / whitespace | the map (coverage, gaps) | **results / performance** | Sonnet (high volume) |
| **Gatekeeper** | novelty check + route to a provisional category | map + embeddings | performance | Sonnet / Haiku |
| **Workers** (×N async, 1 per live category) | refine/exploit *within* one category (intentional local tunnelling) | their category's results + behaviour | other categories | Opus 4.8 |
| **Director** (conductor) | steer generation, allocate compute, prune dead categories, hold the **explore/exploit dial** | everything | — | Opus 4.8 |
| **Wildcard** | inject off-map random ideas, seed brand-new categories | the map (to know what's off it) | results | Sonnet |
| **Synthesis** | Hypothesis → valid DSL StrategySpec, with compiler-feedback repair loop | the DSL + one hypothesis | performance | Opus 4.8 |

> Synthesis is listed separately (it's the `Hypothesis → StrategySpec` compiler agent from the repo
> architecture). Hypothesis proposes the *claim*; Synthesis expresses it in the DSL (`01`).

## The Director (conductor — above the pipeline)
Not a pipeline stage; supervises continuously. It is the embodiment of the explore/exploit decision — the
most important single piece. It acts at:
- **Generate** — where to look + the explore/exploit dial (how much novelty vs performance).
- **Falsify** — compute budget per candidate.
- **Re-categorise / Exploit** — assign workers, prune dead categories.
- **Feedback** — update priorities from what the map has learned.

Guardrail (from the exploration toolkit, `42_Project_Research_Notes.html` §16): a **hard budget split**
(recommended default **70 exploit / 20 explore / 10 wildcard**), novelty as a scored metric, favour
low-correlation alphas, a diversity alarm that triggers mutation storms when generation narrows.

## Two engines (how the roster splits)
- **Explore engine** (long loop): Hypothesis + Wildcard + Gatekeeper + falsification gauntlet → new alphas into the map.
- **Exploit engine** (short inner loop): Workers refine → re-falsify → record, async per live category.
- The Director balances compute between them.

## Agent contract (each agent, for the build)
Every agent is a stateless function over a **scoped memory view**: `view(role) -> context → artifact`.
Spec each with: input artifact, output artifact, the memory view it's granted, its model, its tools, and a
**per-call token budget** (the Director enforces the aggregate). No agent holds state between calls — state
is the artifacts + map.

## DECISIONs
- Confirm the model split above (cost: Fable 5 is build-only; runtime inner loops must not use it).
- Agent orchestration substrate: plain Python asyncio loop (recommended, minimal) vs an existing framework.
  Ponytail default: asyncio + the artifact contracts — no framework until it measurably falls short.
