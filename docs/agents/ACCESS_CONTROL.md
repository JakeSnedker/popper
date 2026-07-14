# Agent Access Control

## Purpose

Prevent generator agents from copying winners and collapsing exploration into already-profitable regions.

This is one of Popper's most important design choices.

## Core Rule

Performance-blind agents can see the map, not the results.

They may know:

- What ideas have been tried.
- Which edge types are crowded or empty.
- What features are available.
- How novel a candidate is.

They may not know:

- Profit factor.
- PnL.
- Scorecards.
- Verdicts.
- Which ideas survived.
- Which categories are profitable.

## Current Implementation

- Code: `popper/agents/views.py`
- Blind roles: `hypothesis`, `gatekeeper`, `wildcard`, `synthesis`
- Full roles: `worker`, `director`

## BlindView

Exposes:

- `hypotheses()`
- `novelty(h)`
- `is_stale(h)`
- `coverage()`
- `whitespace()`

Does not expose:

- `record()`
- `experiments`
- `scorecards`
- `verdicts`
- `stats()` by result

## FullView

Delegates to the full memory store.

Used by:

- Director
- Workers

## Known Limitation

`BlindView._mem` exists as a private Python attribute. This is acceptable for the current implementation because agents are not arbitrary code with object access; they only receive rendered prompt text.

For production hardening, enforce the boundary at the service/API layer, not by Python convention.

## Production Upgrade Path

- Render prompts from server-side view objects only.
- Do not pass raw memory objects into model/tool contexts.
- Add prompt logging to prove no performance leakage.
- Add tests that generator prompts contain no verdict/PnL/scorecard terms.
- Use separate API scopes for blind and full roles.
- Add audit logs for every agent memory read.
