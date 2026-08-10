# OMMAX Part A - Passenger Forecasting

This directory contains the executable Part A deliverable: a point-in-time-safe,
hourly batch forecasting package and an evidence-driven offline HTML presentation.
It predicts the next seven complete `Europe/Berlin` service dates, inclusive hours
07:00-12:00, for ten stations (420 records per batch).

The OMMAX PDF requires the forecast, final-seven-day test split, overall and
per-station RMSE, and a CEO-level presentation. The package, remote training,
operational controls, governance notes, and HTML format are additional delivery
scope.

## Reproducible setup

Requirements: Python 3.12 and `uv 0.10.2`.

```bash
uv sync --locked --all-groups
uv run forecast --help
```

Raw CSVs remain outside Git. Point `PF_DATA_DIR` at a directory containing:

- `station_metadata.csv`
- `timeseries_with_target.csv`
- `traffic_hourly.csv`
- `weather_hourly.csv`

Copy `.env.example` to an untracked `.env` only when environment-based settings
are useful. Every CLI operation also accepts explicit configuration and output
paths; configuration errors and failed contracts exit non-zero.

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

`backtest`, `train`, and `evaluate` refuse full supplied-data execution unless the
configuration explicitly declares the approved `hetzner_prod` execution profile. The
remote script is the supported way to invoke those commands; the examples above document
their complete interface rather than authorizing a local run.
Local and CI checks use only deterministic synthetic fixtures.

## Focused local checks

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy --strict src
uv run pytest -q
uv run pip-audit
cd presentation && npm ci && npm test && npm run build
```

The presentation build must emit exactly
`presentation/dist/ommax_part_a.html`. It is self-contained and performs no
runtime network requests.

## Remote full-data run

Full validation, bounded tuning, champion refit, and official evaluation run only
on the configured eight-core `hetzner_prod` host. The source revision is committed
and pushed before execution. A versioned directory is created under
`/home//ommax-task-a/runs/<source-sha>/`; code and the four raw CSVs are sent
with non-destructive `rsync`, checksums are verified remotely, and a pinned
user-local `uv` is used without modifying system Python. See
[docs/OPERATIONS_AND_MONITORING.md](docs/OPERATIONS_AND_MONITORING.md) and
[docs/SUBMISSION_QA.md](docs/SUBMISSION_QA.md) for the exact procedure.

Only the compact selected model and sanitized derived evidence return to Git.
Raw data, credentials, tuning studies, caches, and mutable runtime workspaces are
ignored.

## Deliverables and boundaries

- Python package and `forecast` CLI under `src/passenger_forecast/`
- Immutable evaluation evidence and artifact digests under `artifacts/evidence/`
- Compact champion artifact and versioned feature schema under `artifacts/model/`
- Architecture, contracts, model, operations, security, privacy, governance, and
  traceability documentation under `docs/`
- Tested offline executive presentation at
  `presentation/dist/ommax_part_a.html`

This system supports aggregate operational planning. It is not an online serving
API, safety controller, individual employee decision system, live Azure
deployment, infrastructure-as-code package, ROI model, or compliance
certification. Model promotion always requires human approval.
