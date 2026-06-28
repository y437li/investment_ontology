# Validation Agent

Mission:

Validate whether discovered themes are associated with future outcomes. Produce honest, labelled outputs: single-snapshot results are ILLUSTRATIVE only; a statistical/excess-return claim requires the walk-forward panel.

Inputs:

- Frozen discovery artifacts.
- market data (`market_prices.parquet`).
- fundamentals data.
- `configs/validation.example.yml`

Outputs:

- `market_prices.parquet`
- `fundamentals.parquet`
- `portfolio_baskets.parquet`
- `validation.csv`
- validation notes

Functions:

- `run_validation(run_id)`: Single-snapshot freeze-gated forward-return validation. Always returns `illustrative=true, claim_supported=false`. Writes `portfolio_baskets.parquet` and `validation.csv`.
- `run_walk_forward_validation(run_id)`: Walk-forward panel runner. Reads `walk_forward.as_of_dates` from config (>= 3 monthly dates required). For each point computes per-point `{as_of, theme_basket_return, baseline_return, excess}` using single-snapshot machinery with PIT leakage discipline (prices strictly after each point's as_of). Aggregates to pooled stats (`mean_excess`, `hit_rate`, `n_points`). Returns `claim_supported=true` only when `n_points >= sweep.min_points_for_claim`.

Illustrative discipline (hard rule):

- `run_validation()` (single-snapshot) MUST always emit `illustrative=true` and `claim_supported=false`. No code path may override this.
- An excess-return or association claim requires `run_walk_forward_validation()` with `n_points >= sweep.min_points_for_claim` (default 3).
- Outputs from single-snapshot runs must carry `_SINGLE_SNAPSHOT_CAVEAT` in caveats and must NOT be presented as evidence of a statistical association.
- This rule is enforced by tests (`test_oi1_walk_forward.py`): a test proves single-snapshot never emits `claim_supported=true`.

Walk-forward sweep semantics:

- Panel = list of as_of dates from `walk_forward.as_of_dates` in config.
- For each point: apply coverage gate (max(price_date) >= as_of + forward_window); compute theme basket return (prices strictly after as_of, within forward_window); compute baseline return same way; excess = theme_basket_return - baseline_return.
- Pooled: `mean_excess = mean(excess_i)`, `hit_rate = fraction of points where excess > 0`, `n_points = count of valid points`.
- `claim_supported = n_points >= sweep.min_points_for_claim`.
- Basket composition is fixed from the run's frozen discovery snapshot; walk-forward varies the entry date only.

Acceptance checks:

- Discovery artifacts are frozen before validation starts.
- Forward windows are explicit.
- Benchmarks are explicit.
- Results include sample size and caveats.
- Basket constituents and weights are reproducible from `portfolio_baskets.parquet`.
- Forward-coverage preflight must run before basket scoring.
- If `rules.reject_insufficient_forward_data=true`, validation must fail before scoring when any run snapshot lacks required market forward rows.
- Coverage errors must include: `run_id`, `as_of_date`, `holding_window`, `last_available_date`, `required_end_date`.
- Single-snapshot results always carry `illustrative=true, claim_supported=false`.
- Walk-forward panel results carry `claim_supported=true` only when `n_points >= sweep.min_points_for_claim`.
