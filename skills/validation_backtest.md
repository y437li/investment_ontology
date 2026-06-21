# Skill: Validation Backtest

Purpose:

Measure future outcomes after discovery is frozen.

Inputs:

- frozen discovery artifacts.
- price data.
- fundamentals data.
- validation config.

Steps:

1. Check discovery artifacts are frozen.
2. Build company exposure baskets.
3. Compute 1M and 3M forward returns.
4. Compare against explicit benchmarks.
5. Write sample size and caveats.

Outputs:

- `validation.csv`

Failure modes:

- Reading future data before discovery.
- Reporting only successful examples.
- Missing benchmark comparison.

