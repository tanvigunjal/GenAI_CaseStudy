# Operations and monitoring

## Hourly run

1. Create a run ID and acquire metadata, passenger history, weather forecast, and traffic history.
2. Validate schema/key/time contracts and persist aggregate `SourceSnapshot` lineage.
3. Stop on metadata or passenger-history/target failure. Do not move `current.json`.
4. Use the champion only when optional sources and its artifact/schema/config binding are healthy;
   otherwise select the seasonal-naive/historical-median hierarchy and mark `degraded`.
5. Build all 420 rows, validate them, and atomically publish versioned CSV, Parquet, and manifest.
6. Update the current pointer last; emit structured aggregate telemetry.
7. When labels arrive, score the rolling window and compare champion and baseline.

Safe retries reuse the content-derived batch ID. Existing batches are accepted only after their CSV
and Parquet digests match `publication.json`. Backfilled source content changes its source snapshot
hash and therefore creates a new immutable batch instead of overwriting history.

## Failure policy

| Condition | Result | Operator action |
|---|---|---|
| Metadata absent/invalid, unsupported station | Block publication | Correct contract or approve retraining; retain last successful current batch |
| Passenger history/target duplicate, stale, absent, invalid | Block publication | Restore source or correct data; never silently serve a partial/stale batch as current |
| Weather or traffic absent/invalid | Publish full fallback batch as `degraded` | Alert; restore optional source; compare once recovered |
| Champion missing, corrupt, schema/config mismatch, invalid prediction | Publish full fallback batch as `degraded` | Quarantine artifact; restore prior approved version |
| Fallback cannot produce 420 finite records | Block publication | Escalate data incident; retain last successful batch |
| Output write or checksum failure | Block current-pointer update | Inspect storage; safely retry identical batch |
| Champion underperforms baseline after labels arrive | Keep serving state; alert | Review drift and shadow evidence; human decides promotion/rollback |

## Telemetry

Log run ID, batch ID, source watermarks/digests, model/config hashes, record counts, duration,
selected role, warnings, health, and output URI. Never log source rows, prediction rows, secrets,
connection strings, tokens, or individual movement records.

`monitor_forecasts` reports:

- valid unique records divided by the 420-row contract;
- per-source freshness in hours against source-specific limits;
- rolling overall RMSE and signed bias on observed labels;
- all available per-station RMSE, bias, and sample counts;
- fixed-baseline RMSE and relative champion lift; and
- severity plus `none`, `alert`, `fallback`, or `block_publication` action.

Initial freshness/performance thresholds must be approved from shadow-run evidence, not invented
from the one holdout. Completeness is non-negotiable: normal publication requires 1.0. Any threshold
change is versioned configuration and changes the evidence/config hash.

## Alert ownership and response

| Alert | Primary owner | Acknowledge/response objective |
|---|---|---|
| Critical source or incomplete batch | Data/platform on-call | Acknowledge within the hourly cycle; preserve last good pointer |
| Optional source degradation | Data/platform on-call | Confirm fallback and source recovery path within the hourly cycle |
| Model artifact integrity | ML owner | Quarantine version immediately; fallback/prior approved artifact |
| RMSE, bias, station hotspot, baseline regression | ML owner + operations analyst | Review before next promotion; urgent escalation only if operationally material |
| Storage/publication integrity | Platform owner | Prevent pointer update and safely retry after storage check |

The exact organizational names and paging targets are deployment prerequisites, not fabricated in
this submission.

## Promotion and rollback runbook

Promotion requires a verified evidence manifest, passing code/tests/security checks, model-owner
recommendation, operations review of station behavior, and explicit human approval. Record approver,
timestamp, registry model version, feature schema, config hash, and evidence digest. Do not promote
solely because one holdout metric improved.

Rollback selects the previous approved immutable model/config pair, verifies its hashes, and runs a
new full batch. Historical outputs are never deleted or rewritten. If the source contract is
critical-failed, rollback does not bypass the publication block.

