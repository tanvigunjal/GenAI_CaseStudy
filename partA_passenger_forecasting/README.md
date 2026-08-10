# OMMAX Part A - Passenger Forecasting

This directory holds the Part A deliverable: a point-in-time-safe, hourly batch
forecasting package and an evidence-driven offline HTML presentation. It
forecasts the next seven complete `Europe/Berlin` service dates, hours
07:00-12:00 inclusive, across ten stations, 420 records per batch.


## Reproducible setup

Requirements: Python 3.12 and `uv 0.10.2`.

```bash
uv sync --locked --all-groups
uv run forecast --help
```

Raw CSVs are kept out of Git. Point `PF_DATA_DIR` at a directory containing:

- `station_metadata.csv`
- `timeseries_with_target.csv`
- `traffic_hourly.csv`
- `weather_hourly.csv`

Copy `.env.example` to an untracked `.env` if environment-based settings are
useful. Every CLI command also accepts explicit configuration and output
paths, and configuration errors or failed contracts exit non-zero.

## Commands

```bash
DATA=/absolute/path/to/ommax-task-a-dataset
uv run forecast data-check --config config/default.toml --data-dir "$DATA" --output-dir workspace/audit
uv run forecast backtest --config config/hetzner_prod.toml --data-dir "$DATA" --output-dir workspace/run
uv run forecast train --config config/hetzner_prod.toml --data-dir "$DATA" --output-dir workspace/run
uv run forecast evaluate --config config/hetzner_prod.toml --data-dir "$DATA" --output-dir workspace/run
uv run forecast predict --as-of 2026-01-26T12:00:00+01:00 --config config/default.toml --data-dir "$DATA" --model-dir workspace/run/model --output-dir workspace/predict
uv run forecast monitor --config config/default.toml --data-dir "$DATA" --forecasts workspace/run/predictions.parquet --output-dir workspace/monitor
uv run forecast export-evidence --source-revision "$SOURCE_SHA" --artifact-dir workspace/run --config config/hetzner_prod.toml --data-dir "$DATA" --output-dir artifacts/evidence
uv run forecast verify-evidence --manifest artifacts/evidence/evaluation_evidence.json --config config/hetzner_prod.toml --data-dir "$DATA" --output-dir workspace/verification
```

`backtest`, `train`, and `evaluate` won't run against full supplied data
unless the configuration explicitly declares the approved `hetzner_prod`
execution profile. The remote script is the supported way to invoke them:
the examples above show the full command interface, not a green light to run
them locally. Local and CI checks rely only on deterministic synthetic
fixtures.

## Focused local checks

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src
uv run pytest -q
uv run pip-audit
cd presentation && npm ci && npm test && npm run build
```

The presentation build outputs a single file,
`presentation/dist/ommax_part_a.html`.

## Remote full-data run

Full validation, bounded tuning, champion refit, and the official evaluation
run only happen on the configured eight-core `hetzner_prod` host. The source
revision is committed and pushed before execution starts. A versioned
directory is created under `/home//ommax-task-a/runs/<source-sha>/`; code and
the four raw CSVs are sent over with non-destructive `rsync`, checksums are
verified remotely, and a pinned user-local `uv` handles the run without
touching system Python. See
[docs/OPERATIONS_AND_MONITORING.md](docs/OPERATIONS_AND_MONITORING.md) for
the full procedure.


## Deliverables and boundaries

- Python package and `forecast` CLI under `src/passenger_forecast/`
- Immutable evaluation evidence and artifact digests under `artifacts/evidence/`
- Compact champion artifact and versioned feature schema under `artifacts/model/`
- Tested offline executive presentation at
  `presentation/dist/ommax_part_a.html`

This system is built for aggregate operational planning. It's not an online
serving API, a safety controller, an individual employee decision system, a
live Azure deployment, an infrastructure-as-code package, an ROI model, or a
compliance certification. Model promotion always requires human approval.
