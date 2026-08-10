# Requirement traceability

This table maps the three-page Part B brief and the locked delivery plan to executable evidence. Scene numbers refer to the 14-scene HTML presentation. All runtime examples use synthetic data.

| Brief / plan requirement | Implementation | Executable evidence | Notebook | Presentation |
|---|---|---|---|---|
| One executable AI workflow notebook | `order_to_erp_agent.ipynb`; generated from reviewed cells by `scripts/build_notebook.py` | CI executes it offline with `nbconvert` | Sections 1–11 | Scenes 1, 11 |
| One 15–20 minute C-level presentation | React/TypeScript source in `presentation/src/`; one-file Vite build | Vitest, typecheck, artifact smoke, four desktop viewport checks | Evidence manifest in section 10 | All 14 scenes, 17–19 minute notes |
| Single shared order inbox | `FixtureMailboxAdapter`, `InboundEmail`, stable mailbox/provider IDs | `tests/test_erp_workflow.py`, `tests/test_foundation_state.py` | Sections 1, 4, 6 | Scenes 1, 7 |
| REST-shaped ERP integration | FastAPI `create_erp_app`, `RestERPClient`, typed response/status handling | `tests/test_erp_workflow.py` crosses HTTP boundaries | Sections 3, 6, 7 | Scenes 5–7; deployment appendix |
| Body, PDF, PPTX, and XLSX extraction | `attachments.py`, `ingestion.py`, opaque `AttachmentStore` | Real binary helper plus hostile-boundary tests | Sections 4, 9 | Scene 4; validation appendix |
| LLM API, model choice, orchestration, tools | Replay and optional Gemini adapters; fixed `WorkflowService`; `ScopedToolExecutor` | Replay transcript exercises real tools; live-mode configuration tests | Sections 3, 5; optional live cell | Scenes 5, 6; architecture appendix |
| Explicit model/rules separation | `OrderProposal` versus immutable `ValidatedOrderCommand`; `OrderPolicy` is the only command constructor | Policy corpus proves confidence cannot authorize or deny | Sections 1, 3, 7 | Scenes 1, 5, 6 |
| Traceable logging, storage, tool calls, decisions | SQLite `StateStore`; append-only `AuditSink` with versions, intent/outcome, redaction, sequence, and hash chain | Sequential/concurrent/restart tests; mutation and hash verification tests | Sections 6, 7, 10 | Scenes 6, 7; trace scrubber |
| Prompt injection and malicious input | NFKC normalization, deterministic authoritative screen, fail-closed advisory screen, capability limits | Named subject/body/attachment, obfuscated, multilingual, delimiter, and exfiltration tests | Section 9 | Scenes 2, 9, 10 |
| Ambiguous, incomplete, conflicting, failed orders require human control | Durable processing/security/order reviews; role checks and optimistic locking | Policy matrix, ERP conflict/failure tests, full review-state tests | Sections 8, 9 | Scenes 3, 8, 9 |
| Sender authenticity and customer isolation | Verified webhook and SPF/DKIM/DMARC-style verdicts; deterministic sender resolution; sender-bound ERP client | Spoof/unknown sender and cross-customer tool denial tests | Sections 1, 3, 5 | Scenes 3, 6, 9 |
| Bounded read-only tool use | Exact lookup, bounded search, authenticated-customer history; six turns/eight calls; no mutation tools | Introspection, malformed/unknown/budget/cross-customer/output-limit tests | Section 5 | Scenes 5, 6 |
| Commercial validation | Whole finite Decimal quantity, SKU, EUR catalog price, 2% quote tolerance, aggregate stock, catalog version, EUR 50K cap | 27 named golden/adversarial cases plus nonfinite schema cases | Sections 3, 6–8 | Scenes 3, 5, 8; validation appendix |
| Exactly-once ERP write | Message reservation plus ERP idempotency key/payload digest; atomic recheck/decrement/create; unknown-outcome reconciliation | Duplicate, concurrency, restart, payload conflict, stale catalog, crash-after-success tests | Section 7 | Scene 7; trace scrubber |
| Shadow/auto modes and kill switch | `Settings`, `WriteMode`, `RuntimeEnvironment`; local-sandbox restriction | Configuration and workflow tests | Sections 2, 6, 7 | Scenes 3, 11, 13 |
| Durable human review | `ReviewService`: `PENDING → CLAIMED → CORRECTED → REVALIDATED → WRITE_PENDING → ERP_CREATED → APPROVED` or `REJECTED` | Role, stale-version, evidence, identity anchoring, and verified-receipt tests | Section 8 | Scene 8; HITL appendix |
| Deployment, runtime, infrastructure, database, CI/CD | Locked Python 3.12/uv project, GitHub Actions, cloud-neutral topology, production input checklist | `uv sync`, lint, type, test, coverage, audit, notebook CI | Section 11 | Scenes 11, 13; deployment appendix |
| Monitoring and KPIs | Safety, quality, reliability, cost metrics and alerts in `OPERATIONS_AND_COMPLIANCE.md` | Evidence manifest exposes test/write/audit controls | Sections 10, 11 | Scenes 11–13; monitoring appendix |
| GDPR and EU posture | Data minimization, DPA/transfer, retention, DPIA/Article 22, AI inventory/classification/oversight workstreams | Documentation review; no compliance claim | Section 11 | Scene 11; compliance appendix |
| C-level impact model | Editable, provenance-labeled calculator with hours, costs, capacity, cash realization, payback validity gates | `presentation/src/lib/calculators.test.ts` | Section 11 labels current evidence boundary | Scene 12 |
| Promotion measurement | Proposed zero false auto-approvals among at least 299 reviewed auto-eligible shadow orders | Pilot-sample calculator unit test | Section 11 | Scenes 10, 13 |
| Evidence freshness | `scripts/export_evidence.py` publishes one sanitized manifest to notebook/deck locations; build checks the demonstrated source SHA | `npm run verify:evidence` with `EXPECTED_COMMIT_SHA` | Section 10 | Evidence appendix |

## Deliberate exclusions

OCR/scanned documents, legacy Office formats, multi-order/split-shipment input, real mailbox/ERP deployment, real customer data, model-triggered mutations, update/delete automation, production review UI, cloud provisioning, formal certification, and measured production ROI are not implemented or claimed. Unsupported inputs route to review; they do not silently enter auto-write.
