# Controlled email-to-ERP workflow

OMMAX case study, Part B. This is a locally runnable, production-shaped slice of an email-to-ERP pipeline, with the model kept behind a deterministic authorization boundary. It can propose actions, but it never gets to execute them on its own.

By default everything runs offline: no API keys, no network calls, just deterministic replay in shadow mode. An optional Gemini adapter is available for shadow checks against synthetic data, and even then, automatic writes are limited to a local REST ERP sandbox.

## Deliverables

| Path | Purpose |
|---|---|
| `order_to_erp_agent.ipynb` | Executable first-person walkthrough and evidence run |
| `src/order_pipeline/` | Strict contracts, safe ingestion, controlled extraction, policy, state, audit, review, and ERP sandbox |
| `tests/` | Golden, adversarial, state, audit, attachment, REST, idempotency, and review controls |
| `presentation/dist/ommax_part_b.html` | Self-contained 14-scene executive/technical presentation |
| `presentation_evidence.json` | Sanitized evidence manifest generated from the replay run |
| `ARCHITECTURE.md` | Code-level control flow and trust boundaries |
| `docs/THREAT_MODEL.md` | Threat/control/test mapping and residual risks |
| `docs/OPERATIONS_AND_COMPLIANCE.md` | Deployment, monitoring, GDPR/EU, and runbook posture |


The case-study PDF is included at the repository root.

## Setup

Requirements: Python 3.12, `uv`, and Node.js/npm (for the presentation).

```bash
cd partB_agentic_workflow
uv sync --locked --all-groups
```

Replay/shadow mode works out of the box, no credentials or network access needed:

```bash
uv run python -c "from pathlib import Path; from order_pipeline.demo import run_demo_scenarios; print(run_demo_scenarios(Path('workspace/demo'), include_auto_example=True))"
```

Runtime behavior is controlled by a few environment variables:

- `MODEL_MODE=replay`: the default; uses checked-in action transcripts instead of calling a model.
- `MODEL_MODE=live`: calls Gemini; requires `GOOGLE_API_KEY`, `GEMINI_EXTRACTION_MODEL`, and `GEMINI_SCREENING_MODEL`.
- Live Gemini calls only run when `WRITE_MODE=shadow`.
- `WRITE_MODE=auto` only takes effect when `RUNTIME_ENVIRONMENT=local_sandbox`.
- `KILL_SWITCH=true` blocks all writes regardless of anything else that's set.

See `.env.example` for the full list. Keep `.env` and any real credentials out of version control.

## Notebook

Run the committed notebook with no network access:

```bash
uv run jupyter nbconvert --execute --to notebook order_to_erp_agent.ipynb \
  --output /tmp/order_to_erp_agent.executed.ipynb
```

The default run walks through:

- English and German input.
- Extraction from email body, PDF, PPTX, and XLSX, with provenance tracked throughout.
- Shadow approval, a security block, a parser failure, an ambiguous case routed to review, and a correction/revalidation cycle.
- One write to the isolated local sandbox, plus a duplicate replay.
- A stable trace, audit verification, and evidence export.

The live section is off unless explicitly enabled, and even then it only touches synthetic data in shadow mode.

## Python quality gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run pip-audit
```

The 90% branch-coverage target applies to the deterministic core: domain contracts, schemas, validation, state, and audit, since that's the part handling authorization and persistence.

## Presentation

```bash
cd presentation
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e
```

The build produces a single file, `dist/ommax_part_b.html`.

## Scope and claims

This is a local prototype built on synthetic data, not a live mailbox or ERP deployment. It does not include OCR, legacy Office formats, split orders/shipments, real customer data, model-triggered mutations, a production review UI, or production-grade ROI/accuracy figures. Auto-write is only tested for English and German input; everything else is routed to review.
