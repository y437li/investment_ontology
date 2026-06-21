# Validation Agent

Mission:

Validate whether discovered themes are associated with future outcomes.

Inputs:

- Frozen discovery artifacts.
- market data.
- fundamentals data.
- `configs/validation.example.yml`

Outputs:

- `market_prices.parquet`
- `fundamentals.parquet`
- `portfolio_baskets.parquet`
- `validation.csv`
- validation notes

Acceptance checks:

- Discovery artifacts are frozen before validation starts.
- Forward windows are explicit.
- Benchmarks are explicit.
- Results include sample size and caveats.
- Basket constituents and weights are reproducible from `portfolio_baskets.parquet`.
