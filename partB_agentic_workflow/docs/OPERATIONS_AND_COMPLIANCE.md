# Deployment, operations, privacy, and compliance posture

## Promotion posture

The implemented system is a local, synthetic-data vertical slice. It demonstrates control flow and failure behavior; it does not claim production readiness, measured ROI, universal language coverage, or legal compliance.

The proposed promotion sequence is:

1. Replay plus shadow validation in CI.
2. Synthetic live-model shadow checks when credentials and provider settings are explicitly supplied.
3. A real-email shadow pilot after privacy, security, legal, and operational approvals.
4. Reviewed auto-eligible shadow orders until the promotion gate is met.
5. Limited auto-write only after an owner accepts the residual risk and kill-switch/runbook coverage.

The statistical gate proposed in the plan is zero false auto-approvals across at least 299 reviewed auto-eligible shadow orders. This corresponds to a one-sided 95% “rule of three” upper bound below 1%. It is a promotion criterion, not evidence that the current prototype achieved that quality.

## Cloud-neutral topology

`mailbox webhook -> durable queue -> quarantine/parser boundary -> stateless workflow workers -> scoped LLM/ERP clients -> transactional database/object store -> review UI -> observability`

Production choices intentionally left unset:

- Cloud provider, region, queue, container runtime, object store, and managed database.
- Real mailbox and ERP products, credentials, rate limits, and update/delete semantics.
- Order volume, SLA, RTO/RPO, and support coverage.
- Retention periods for messages, artifacts, reviews, audit events, and backups.

Those values are required deployment inputs; the case brief provides no facts from which to infer them.

## Runtime and infrastructure

- Python 3.12 with locked dependencies.
- Stateless workers consume a durable message key and resume against transactional workflow state.
- A quarantine/parser service has bounded CPU, memory, runtime, and egress.
- Raw artifacts are held separately from redacted standard audit payloads.
- The extraction model receives only sender-scoped context and three read-only tools.
- REST ERP writes use an idempotency key, payload digest, and catalog version.
- Review is a durable role-scoped state machine, not a notebook-only approval.

## Database and storage

The prototype uses separate SQLite stores for workflow/audit state and the ERP sandbox. Production should retain the same logical separation while moving to managed transactional storage and controlled object storage.

Required production properties:

- Transactional unique reservation for mailbox/provider message ID.
- Optimistic locking for review transitions.
- Encrypted storage and backups with tested restoration.
- Independently retained audit records or equivalent write-once controls.
- Object retention/deletion aligned to an approved policy.
- Region and cross-border transfer controls aligned with provider contracts.

## CI/CD gates

Required pull-request gates:

- Locked environment synchronization.
- Ruff lint and formatting.
- Mypy on the source package.
- Deterministic pytest suite and coverage threshold.
- Offline notebook execution.
- Dependency vulnerability audit.
- Presentation unit tests, TypeScript check, self-contained build, evidence freshness, and focused viewport/browser checks.

Production delivery should add artifact signing, image scanning, infrastructure policy checks, environment approvals, database migration rehearsal, and rollback/runbook checks.

## Human oversight

Order reviewers may correct commercial or extraction failures. Security reviewers may quarantine or reject security cases. Corrections must record:

- Authenticated actor and role.
- Before/after values.
- Source evidence and rationale.
- Optimistic-lock version.
- Revalidation result and catalog version.

High-value orders remain review-only. Security-blocked items cannot be released to auto-write in this sprint.

## Monitoring and KPIs

Metrics must be segmented by mode, language, attachment type, outcome, rule code, model version, prompt version, and policy version where applicable.

Safety and control:

- False auto-approvals found in human review (primary promotion metric).
- Duplicate messages, changed-content collisions, idempotency conflicts, and reconciliations.
- ERP creates per unique message and stock decrements per created order.
- Security blocks, advisory-screen failures, and attachment quarantine reasons.
- Audit-intent failures and hash-chain verification failures.
- Reviewer authorization or stale-version denials.

Quality and flow:

- Shadow-approved, review, and blocked shares.
- Field-level correction rate by field and source type.
- Review queue age, time to claim, time to decision, and rework count.
- Parser success/warning/failure rate by format.
- Supported-language share; unsupported languages are reported separately.

Reliability and cost:

- Model/tool attempts, timeouts, retries, and bounded-call denials.
- ERP read/write latency and unknown-outcome reconciliations.
- Queue lag, worker failure rate, and end-to-end processing duration.
- Token/provider and infrastructure cost per email, labeled by source and mode.

Suggested alerts:

- Any duplicate ERP create for one idempotency key.
- Any write without a matching validated command and audit intent.
- Audit hash-chain failure.
- Kill switch active with attempted write.
- Security-blocked item entering an order-approval transition.
- Sustained parser/provider failure, queue-age breach, or review backlog breach.

Thresholds other than the stated promotion gate must be agreed from real volume/SLA data.

## GDPR and EU workstream

Before any real-email pilot, obtain owner and DPO/legal approval for:

- Purpose, lawful basis, data inventory, minimization, transparency/privacy notice, accuracy, retention/deletion, data-subject rights, and processing records.
- Model-provider DPA, training/retention controls, hosting region, subprocessors, and international-transfer mechanism.
- Encryption, secrets management, RBAC, access review, backups, incident response, and breach handling.
- DPIA screening and a GDPR Article 22 assessment if automatic order creation could produce legal or similarly significant effects for a natural person.

These controls follow the [European Commission overview of GDPR processing principles](https://commission.europa.eu/law/law-topic/data-protection/rules-business-and-organisations/principles-gdpr/overview-principles/what-data-can-we-process-and-under-which-conditions_en), including purpose limitation, data minimization, storage limitation, and integrity/confidentiality.

For the [EU Artificial Intelligence Act, Regulation (EU) 2024/1689](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32024R1689), maintain an AI-system inventory, document the applicable classification and obligations, design human oversight, and provide staff AI-literacy measures. This prototype does not assert that ordinary B2B order processing is automatically an Annex III high-risk system and does not claim AI Act compliance.

## Operational runbooks required before production

- Kill switch activation and safe restoration.
- Provider outage and advisory-screen failure.
- Parser or malware-scanner outage.
- ERP timeout/unknown POST reconciliation.
- Catalog-version or price/stock conflict.
- Audit persistence or verification failure.
- Review backlog and privileged-access incident.
- Data-subject request, retention deletion, and legal hold.
- Security incident, notification, recovery, and post-incident evidence preservation.
