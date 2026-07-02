-- Popper alpha memory — initial schema. Apply to your Supabase project when POPPER_STORE=supabase.
-- JSON-shaped fields use jsonb; the Supabase repo passes native lists/dicts.

create table if not exists hypotheses (
    id                text primary key,
    claim             text not null,
    mechanism         text,
    rationale         text default '',
    required_features jsonb default '[]',
    expected_regime   text default 'unknown',
    novelty_score     double precision default 0,
    status            text default 'proposed',
    parent_id         text,
    created_at        timestamptz default now()
);

create table if not exists specs (
    id            text primary key,
    hypothesis_id text references hypotheses(id),
    dsl_version   text default '0.1',
    name          text not null,
    universe      jsonb default '[]',
    timeframe     text,
    features      jsonb default '[]',
    signal        jsonb default '{}',
    entry         jsonb default '{}',
    exit          jsonb default '{}',
    sizing        jsonb default '{}',
    constraints   jsonb default '{}',
    param_space   jsonb default '{}',
    created_at    timestamptz default now()
);

create table if not exists experiments (
    id           text primary key,
    spec_id      text references specs(id),
    dataset      text,
    period_start text,
    period_end   text,
    trades_n     integer default 0,
    raw_metrics  jsonb default '{}',
    equity_curve jsonb default '[]',
    created_at   timestamptz default now()
);

create table if not exists scorecards (
    id                text primary key,
    experiment_id     text references experiments(id),
    robustness        double precision default 0,
    regime_stability  double precision default 0,
    capacity          double precision default 0,
    execution_realism double precision default 0,
    novelty           double precision default 0,
    deflated_sharpe   double precision default 0,
    pbo               double precision default 1,
    created_at        timestamptz default now()
);

create table if not exists verdicts (
    id            text primary key,
    hypothesis_id text references hypotheses(id),
    experiment_id text references experiments(id),
    type          text not null,
    reasons       jsonb default '[]',
    created_at    timestamptz default now()
);

create index if not exists idx_hypotheses_status on hypotheses(status);
create index if not exists idx_specs_hypothesis on specs(hypothesis_id);
create index if not exists idx_experiments_spec on experiments(spec_id);
create index if not exists idx_verdicts_hypothesis on verdicts(hypothesis_id);
