# Controlled email-to-ERP workflow

OMMAX case study, Part B. This branch hardens the original demo into a local, production-shaped vertical slice while keeping the model behind a deterministic authorization boundary.

The default run is offline, keyless, deterministic replay in shadow mode. An optional Gemini adapter is available only for synthetic-data shadow checks. Automatic writes are restricted to the local REST ERP sandbox.

## Deliverables

| Path | Purpose |
|---|---|
| `order_to_erp_agent.ipynb` | Executable first-person walkthrough and evidence run |
| `src/order_pipeline/` | Strict contracts, safe ingestion, controlled extraction, policy, state, audit, review, and ERP sandbox |
| `tests/` | Golden, adversarial, state, audit, attachment, REST, idempotency, and review controls |
| `presentation/dist/ommax_part_b.html` | Self-contained 14-scene executive/technical presentation |
| `presentation_evidence.json` | Sanitized evidence manifest generated from the replay run |
| `TASK_B_IMPLEMENTATION_PLAN.md` | Locked implementation plan |
| `BASELINE.md` | Original commit, inventory, and 31-test result |
| `ARCHITECTURE.md` | Code-level control flow and trust boundaries |
| `docs/THREAT_MODEL.md` | Threat/control/test mapping and residual risks |
| `docs/OPERATIONS_AND_COMPLIANCE.md` | Deployment, monitoring, GDPR/EU, and runbook posture |
| `docs/TRACEABILITY.md` | Brief-to-code/notebook/test/presentation map |
| `docs/SUBMISSION_QA.md` | Final command matrix, evidence revision, and integrity checks |

The source case-study PDF remains unchanged at repository root.

## Setup

Requirements: Python 3.12, `uv`, and Node.js/npm for the presentation.

```bash
cd partB_agentic_workflow
uv sync --locked --all-groups
```

Replay/shadow starts without credentials or network access:

```bash
uv run python -c "from pathlib import Path; from order_pipeline.demo import run_demo_scenarios; print(run_demo_scenarios(Path('workspace/demo'), include_auto_example=True))"
```

Runtime modes are explicit:

- `MODEL_MODE=replay` is the default and uses checked-in action transcripts.
- `MODEL_MODE=live` requires `GOOGLE_API_KEY`, `GEMINI_EXTRACTION_MODEL`, and `GEMINI_SCREENING_MODEL`.
- Live Gemini construction is restricted to `WRITE_MODE=shadow`.
- `WRITE_MODE=auto` is accepted only for `RUNTIME_ENVIRONMENT=local_sandbox`.
- `KILL_SWITCH=true` prevents writes regardless of a valid decision.

See `.env.example`; do not commit `.env` or credentials.

## Notebook

Execute the committed notebook without network access:

```bash
uv run jupyter nbconvert --execute --to notebook order_to_erp_agent.ipynb \
  --output /tmp/order_to_erp_agent.executed.ipynb
```

The default notebook run demonstrates:

- English and German input.
- Body, PDF, PPTX, and XLSX extraction with provenance.
- Shadow approval, security block, parser failure, ambiguity/review, correction/revalidation.
- One isolated local sandbox write and duplicate replay.
- Stable trace, audit verification, and evidence export.

The optional live section is disabled unless explicitly requested and remains synthetic-data, shadow-only.

## Python quality gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run pip-audit
```

The 90% branch-coverage gate applies to the deterministic authorization and persistence core: domain contracts, schemas, validation, state, and audit. Adapter and presentation-layer modules remain exercised by the full test suite but are not misrepresented as part of that deterministic-core metric. The original 31-test baseline is recorded separately because those tests targeted the removed demonstration APIs.

## Presentation

```bash
cd presentation
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e
```

The build produces exactly `dist/ommax_part_b.html`: one offline file with bundled React, CSS, font assets, evidence, presenter notes, overview, appendix, scenario tabs, trace scrubber, value calculator, and pilot-sample calculator.

## Scope and claims

This is a synthetic-data local prototype, not a real mailbox/ERP deployment. It deliberately excludes OCR, legacy Office formats, split orders/shipments, real customer data, model-triggered mutations, a production review UI, and production ROI/accuracy claims. Tested auto-write language scope is English and German; all other languages route to review.
