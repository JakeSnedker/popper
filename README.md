# Popper

[![CI](https://github.com/JakeSnedker/popper/actions/workflows/ci.yml/badge.svg)](https://github.com/JakeSnedker/popper/actions/workflows/ci.yml)

> *"A theory that is not refutable by any conceivable event is non-scientific."* — Karl Popper

Popper is an autonomous research platform that generates trading strategy ideas, tries hard to
disprove them, and keeps only what survives. It is for systematic traders and independent researchers
who want strategies tested against overfitting, not just shown with a good backtest.

**Status: alpha, in active development.** Popper is a research tool. It has not yet produced a strategy
with a proven live record, and nothing here is financial advice.

## How it works

Propose a falsifiable hypothesis → express it in the strategy DSL → backtest → run the falsification
gauntlet → score it on several metrics → record everything in alpha memory → feed the verdicts back
into the next round of hypotheses.

The core bet is **falsification first**. A strategy must survive purged and embargoed cross-validation,
Deflated Sharpe, PBO and parameter-stability checks before it counts as anything.

## What's in the repo

| Part | Where |
|------|-------|
| Strategy DSL, compiler and validator | `popper/dsl/` |
| Backtest runner and metrics | `popper/experiments/` |
| Falsification gauntlet | `popper/falsify/` |
| Research agents and LLM routing | `popper/research/`, `docs/agents/`, `docs/LLM_ROUTING.md` |
| Explore and exploit orchestration | `popper/orchestrator/` |
| Alpha memory (SQLite or Supabase) | `popper/memory/`, `supabase/` |
| Portfolio constructor | `popper/portfolio/` |
| Pine and MQL5 export | `popper/forge/` |
| API and React dashboard | `popper/api/`, `gui/` |

**Built with:** Python 3.11+, FastAPI, pandas, NumPy, React and TypeScript, SQLite or Supabase, and
MetaTrader 5 for market data and execution checks.

Start with `docs/ARCHITECTURE.md`, the specs in `docs/spec/`, and the plan in `ROADMAP.md`.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # Windows: copy .env.example .env

python -m pytest                 # tests that need market data are skipped without it
popper seed                      # add example hypotheses to local alpha memory
popper serve                     # API at http://127.0.0.1:8000
```

Dashboard, in a second terminal:

```bash
cd gui
npm ci
npm run dev                      # http://localhost:5173
```

## What you need to bring

- **Market data.** No market data is stored in Git. `popper run` and `popper research` need your own
  bars in `data/`. `scripts/export_mt5.py` exports them from a MetaTrader 5 terminal on Windows.
- **An LLM provider, for autonomous research.** See `docs/LLM_ROUTING.md`.
- **Your own results stay yours.** Alpha memory and data files are gitignored, so strategies you find
  stay on your machine or in your own Supabase project.

## Background

Popper was designed and directed by Jake Snedker and built with AI coding assistants. It draws on
lessons from JJC BOT, an earlier MetaTrader bot Jake built with two collaborators, whose work is used
with their permission.

## License

[Mozilla Public License 2.0](LICENSE).
