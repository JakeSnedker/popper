# 04 — Alpha Memory as a Map, Categories & Portfolio

The alpha memory is not a log — it is **the map**: every tested alpha organised so the system can *avoid*
crowded regions, *target* sparse ones, and *go beyond* into whitespace. Source:
`design-source/42_Project_Research_Notes.html` §3,9–12,17; `42_System_Proposal.html` §5–8.

## The map, technically
- A **vector store** of `embedding + behaviour_fingerprint + faceted_metadata` per alpha.
- **pgvector** (in Supabase — already the store) keeps vectors + relational data in one source of truth;
  **HNSW** index keeps nearest-neighbour search fast at scale. Qdrant is the upgrade path if outscaled.
- The shipped memory (`popper/memory/`, SQLite-now / Supabase-ready) extends here: add the vector column +
  behaviour fingerprint + facet tags. Keep the repo factory (`POPPER_STORE`).
- **DECISION (resolved):** embedding model = **OpenAI `text-embedding-3-small`** (API). Chosen to keep
  compute off the laptop; ~$0.02/1M tokens and Jake already runs OpenAI. Note: **Claude has no embeddings
  endpoint** (Anthropic points to Voyage AI — a fallback if provider consolidation is wanted later). The
  hypothesis-text embedding is high-volume and quality-tolerant, so the small model is fine.

## The three uses of the map (the explore/exploit mechanic)
- **Avoid** — don't re-run what's crowded / already done (de-dup; the novelty gate).
- **Target** — steer generation into sparse regions and whitespace.
- **Go beyond** — the Wildcard explores off-map → new territory → new categories.

## Novelty & the memory views
- **Novelty score** = distance to nearest prior alpha (cosine). Gate: below a threshold ⇒ stale ⇒ killed
  before compute (`is_stale()` exists in the repo — extend to embeddings).
- **Role-scoped views** enforce `02`'s access control: the Hypothesis/Wildcard view exposes coverage/gaps
  but **masks performance**; Worker views expose one category's results; the Director sees all.

## Categorisation (faceted, dynamic)
- **Faceted tags**, not one category per combination: `edge_type` (momentum, mean-reversion, breakout,
  arbitrage…) · `context` (market, timeframe) · `behaviour_signature` (added **post-test**: when it
  makes/loses money, regime sensitivity, correlation).
- A **category = a point in tag-space**; **whitespace = sparse facet-combinations** → steer generation there.
- **Features first (pre-test), behaviour corrects (post-test)** — the only causally-possible order. Borderline
  alphas placed provisionally, relocated by behaviour.
- **Surprise relocation** — an alpha that behaves differently than its feature-category predicted moves to a
  niche / spawns a new category. Misfits are a mining ground for new patterns.
- **Dynamic categories** — new category when similarity to nearest is below a calibrated threshold
  (**~0.7 cosine**, tunable), so the map never tunnels at the category level.
- **Dead-category detection** — a category is "mined out" when **marginal improvement per unit compute
  flattens**. **Pause, don't delete** (a regime shift can revive it); don't declare dead too early.

## Wrapper transfer
Alphas in the same behavioural category tend to want similar wrappers → reuse wrapper knowledge instead of
re-optimising from scratch (feeds the exploit-worker loop, `02`/`03`).

## Portfolio / correlation engine
Operates on the pool of **validated** alphas (survivors). Goal = the best **book**, not the best single
alpha. Selects **complementary / uncorrelated** combinations, prunes ones duplicating existing risk.
**Correlation is a first-class metric** (and itself a behavioural signature). Feeds live production
allocation. Built after the loop turns (`06`, late phase).

## Reconciliation with the shipped `Scorecard`
Add fields: `behaviour_signature`, `regime_stability`, `correlation` (vs the validated pool), `novelty`,
`deflated_sharpe`, `pbo`. The GUI's whitespace map (`05`) reads this map directly.
