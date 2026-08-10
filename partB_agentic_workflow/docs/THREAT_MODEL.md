# Threat model

## Scope and security objective

This document covers the local Task B vertical slice, which runs on synthetic data with a fixture mailbox, deterministic replay models, and a local REST ERP sandbox. It's not a production certification.

The primary security objective is simple: untrusted message content must never be able to authorize an ERP write, access unrelated customer data, suppress an audit record, or create more than one order for one mailbox message.

## Assets and trust boundaries

Protected assets:

- ERP customer, catalog, stock, pricing, purchase history, and orders.
- Authenticated mailbox identity and stable provider message identifiers.
- Validated order commands, idempotency keys, review revisions, and terminal outcomes.
- Audit integrity, model/tool version metadata, and operator identities.

Trust boundaries:

1. Mailbox transport metadata is accepted only from the fixture adapter boundary.
2. Email subject, body, filenames, and attachment bytes are untrusted.
3. Parsed text remains untrusted after successful parsing.
4. Model proposals and tool arguments are untrusted, including in replay mode.
5. Deterministic validation is the authorization boundary.
6. Only a `ValidatedOrderCommand` may cross the ERP write boundary.
7. Human corrections are privileged but still require role checks, optimistic locking, and revalidation.

## Threats and controls

| Threat | Control | Required evidence |
|---|---|---|
| Spoofed known-customer sender | Verified webhook plus SPF/DKIM/DMARC-style verdicts; deterministic sender resolution before extraction | Failed authentication creates security review, with zero extraction calls and zero writes |
| Duplicate delivery or replay | Unique mailbox/message reservation; stable idempotency key; separate content digest | Sequential, concurrent, and restart replays resolve to one job and one ERP order |
| Message ID reused with changed content | Compare the stored content digest for the reserved mailbox/message key | Security/data-integrity review; no write |
| Prompt injection in subject, body, PDF, PPTX, or XLSX | Unicode normalization, authoritative deterministic screening, advisory screening that can only add review/block, bounded read-only tools | Maintained injection corpus produces zero writes; authoritative hits skip the model |
| Indirect data exfiltration through tools | Sender-scoped context; exact lookup, bounded search, and authenticated-customer history only; allowlisted fields and output limits | Tool introspection shows no customer search, arbitrary history, or mutation method |
| Path traversal, symlink, or caller-chosen storage path | Server-issued attachment IDs and containment under per-message storage | Hostile path fixtures are rejected before parsing/model calls |
| MIME confusion or polyglot input | Extension, magic bytes, and ZIP container-entry checks | Wrong type, corrupt, encrypted, or unsupported files route to review |
| Decompression bomb or parser resource exhaustion | Attachment/count/size/page/slide/sheet/row/cell/character limits and bounded subprocess timeout | Oversized and timeout fixtures stop before extraction |
| Malware | `AttachmentScanner` port with deterministic clean/infected/timeout fixture | Infected or unavailable scan routes to security review; no production scanner is claimed |
| Partial or low-quality extraction | Parser warnings, source provenance, empty/scanned-PDF detection | Partial, unsupported, or empty extraction cannot silently continue |
| Hallucinated SKU, quantity, price, or currency | Strict schema plus deterministic ERP-backed policy using finite `Decimal` values | Unknown, missing, fractional, NaN/infinite, mismatched, or excessive values produce zero writes |
| Duplicate SKU hides aggregate stock overflow | Aggregate equal SKUs before stock/value checks | Aggregate quantity is checked once against authoritative stock |
| Catalog changes after validation | Carry catalog version into the command and atomically recheck price, stock, and version during POST | Stale commands fail safely and route to review |
| Crash after ERP success | ERP idempotency transaction plus lookup-by-idempotency-key reconciliation | Recovery finds the original order; it never issues an unkeyed retry |
| Audit suppression or mutation | Persist intent before consequential calls; append-only SQLite triggers; hash chain | Mutation fails, trace verification succeeds, and audit failure closes the write path |
| Reviewer bypass or race | Role-scoped state machine, immutable revisions, evidence, optimistic-lock version | Stale/unauthorized transitions and approval without correction/revalidation fail |
| Model confidence overrides policy | Confidence is observable metadata only | Changing confidence cannot change authorization |
| Excessive write authority | Workflow account has create-only local sandbox capability; no update/delete adapter or model tool | Public introspection and tests show mutations are absent |

## Failure posture

- Security failures route to `SECURITY_REVIEWER`; they cannot be released to auto-write in this sprint.
- Commercial, extraction, ambiguity, parser, stale-catalog, and high-value failures route to `ORDER_REVIEWER`.
- Audit intent failure closes the write path.
- Unknown provider/model outcomes become review, not optimistic success.
- Shadow mode is the default; kill switch always wins.

## Residual risks before a real pilot

- The malware scanner is an interface and deterministic fixture, not ClamAV or another deployed engine.
- OCR and legacy Office formats are deliberately unsupported.
- Provider webhook verification, secrets, encryption, network policy, and real ERP authorization must be implemented in the selected environment.
- Regex and model screening cannot prove prompt-injection absence; capability restriction and deterministic policy remain the primary containment layers.
- Local hash-chained SQLite is tamper-evident for the demo, not independently retained WORM storage.
- Security, privacy, and legal classification need owner approval before real email is processed.
