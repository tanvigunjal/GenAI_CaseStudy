"""Fixed inbox-to-review/ERP orchestration with a deterministic write boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal

from .attachments import AttachmentBoundaryError, AttachmentLimits, AttachmentScanner, AttachmentStore
from .audit import AuditSink, AuditStatus
from .config import Settings
from .domain import (
    InboundEmail,
    IssueSeverity,
    OrderProposal,
    ProcessingOutcome,
    ProcessingResult,
    ValidationIssue,
)
from .erp_client import ERPConflict, ERPError, ERPNotFound, ERPUnknownWriteOutcome, RestERPClient
from .extraction import ExtractionContext, ExtractionFailure, ExtractionModel, OrderExtractor
from .ingestion import ingest_attachments, transport_security_issues
from .review import ReviewService
from .security import SecurityClassifier, screen_content
from .state import MessageReservation, ReservationDisposition, StateConflictError, StateStore, WorkflowJob
from .tool_executor import ScopedToolExecutor, ToolDenied
from .validation import OrderPolicy


class WorkflowFailure(RuntimeError):
    pass


ExtractionModelFactory = Callable[[str], ExtractionModel]


def _result_from_job(job: WorkflowJob, *, deduplicated: bool) -> ProcessingResult:
    return ProcessingResult(
        trace_id=job.trace_id,
        outcome=job.outcome,
        reason_codes=job.reason_codes,
        order_id=job.order_id,
        deduplicated=deduplicated,
    )


class WorkflowService:
    def __init__(
        self,
        *,
        settings: Settings,
        state: StateStore,
        audit: AuditSink,
        erp: RestERPClient,
        attachment_store: AttachmentStore,
        scanner: AttachmentScanner,
        security_classifier: SecurityClassifier,
        extraction_model_factory: ExtractionModelFactory,
        after_erp_success: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.audit = audit
        self.erp = erp
        self.attachment_store = attachment_store
        self.scanner = scanner
        self.security_classifier = security_classifier
        self.extraction_model_factory = extraction_model_factory
        self.reviews = ReviewService(state)
        self._after_erp_success = after_erp_success

    def _attachment_limits(self) -> AttachmentLimits:
        return AttachmentLimits(
            max_attachments=self.settings.max_attachments,
            max_bytes_per_attachment=self.settings.max_attachment_bytes,
            max_total_bytes=self.settings.max_total_attachment_bytes,
            max_pdf_pages=self.settings.max_pdf_pages,
            max_pptx_slides=self.settings.max_pptx_slides,
            max_xlsx_sheets=self.settings.max_xlsx_sheets,
            max_xlsx_rows_per_sheet=self.settings.max_xlsx_rows_per_sheet,
            max_xlsx_cells=self.settings.max_xlsx_cells,
            max_extracted_chars=self.settings.max_extracted_characters,
            parser_timeout_seconds=self.settings.parser_timeout_seconds,
        )

    def _ensure_security_review(self, trace_id: str, reason_codes: tuple[str, ...]) -> None:
        review_id = f"review-security-{trace_id}"
        if not self.state.review_revisions(review_id):
            self.reviews.create_security_review(
                trace_id=trace_id,
                reason_codes=reason_codes,
                review_id=review_id,
            )

    def _route_security(
        self, trace_id: str, reason_codes: tuple[str, ...], *, deduplicated: bool = False
    ) -> ProcessingResult:
        self._ensure_security_review(trace_id, reason_codes)
        self.state.transition_job(trace_id, ProcessingOutcome.SECURITY_REVIEW, reason_codes=reason_codes)
        return ProcessingResult(
            trace_id=trace_id,
            outcome=ProcessingOutcome.SECURITY_REVIEW,
            reason_codes=reason_codes,
            deduplicated=deduplicated,
        )

    def _route_order_review(
        self,
        trace_id: str,
        proposal: OrderProposal,
        issues: tuple[ValidationIssue, ...],
        customer_id: str,
        input_digest: str,
        *,
        deduplicated: bool = False,
    ) -> ProcessingResult:
        review_id = f"review-order-{trace_id}"
        if not self.state.review_revisions(review_id):
            self.reviews.create_order_review(
                trace_id=trace_id,
                proposal=proposal,
                issues=issues,
                authenticated_customer_id=customer_id,
                input_digest=input_digest,
                review_id=review_id,
            )
        reason_codes = tuple(issue.code for issue in issues)
        self.state.transition_job(trace_id, ProcessingOutcome.REVIEW_REQUIRED, reason_codes=reason_codes)
        return ProcessingResult(
            trace_id=trace_id,
            outcome=ProcessingOutcome.REVIEW_REQUIRED,
            reason_codes=reason_codes,
            deduplicated=deduplicated,
        )

    def _route_processing_review(self, trace_id: str, reason_codes: tuple[str, ...]) -> ProcessingResult:
        review_id = f"review-processing-{trace_id}"
        if not self.state.review_revisions(review_id):
            self.reviews.create_processing_review(
                trace_id=trace_id,
                reason_codes=reason_codes,
                review_id=review_id,
            )
        self.state.transition_job(trace_id, ProcessingOutcome.REVIEW_REQUIRED, reason_codes=reason_codes)
        return ProcessingResult(
            trace_id=trace_id,
            outcome=ProcessingOutcome.REVIEW_REQUIRED,
            reason_codes=reason_codes,
        )

    def _audit_event_callback(self, trace_id: str) -> Callable[[str, Mapping[str, object]], None]:
        intents: dict[tuple[str, str], str] = {}

        def emit(event: str, payload: Mapping[str, object]) -> None:
            identifier = str(payload.get("call_id", payload.get("turn", "")))
            family = "tool" if event.startswith("tool_") else "model"
            key = (family, identifier)
            if event.endswith("intent"):
                intent = self.audit.record_intent(
                    trace_id,
                    f"{family}_execution",
                    payload=payload,
                )
                intents[key] = intent.event_id
                return
            intent_id = intents.get(key)
            if intent_id is None:
                return
            status = AuditStatus.SUCCESS
            if event.endswith("denied"):
                status = AuditStatus.DENIED
            elif event.endswith("failure"):
                status = AuditStatus.FAILURE
            self.audit.record_outcome(trace_id, intent_id, status, payload=payload)

        return emit

    def _handle_duplicate(self, reservation: MessageReservation) -> ProcessingResult | None:
        job = reservation.job
        if reservation.disposition is ReservationDisposition.CONTENT_CONFLICT:
            self.audit.append(
                trace_id=job.trace_id,
                event_type="message_reservation",
                status=AuditStatus.DENIED,
                idempotency_key=job.idempotency_key,
                payload={"code": "MESSAGE_ID_CONTENT_CONFLICT"},
            )
            return self._route_security(job.trace_id, ("MESSAGE_ID_CONTENT_CONFLICT",), deduplicated=True)
        if job.outcome not in {ProcessingOutcome.RECEIVED, ProcessingOutcome.PROCESSING}:
            return _result_from_job(job, deduplicated=True)
        try:
            existing_order = self.erp.lookup_by_idempotency_key(job.idempotency_key)
        except ERPError:
            self.audit.append(
                trace_id=job.trace_id,
                event_type="erp_write_reconciliation",
                status=AuditStatus.FAILURE,
                idempotency_key=job.idempotency_key,
                payload={"code": "ERP_RECONCILIATION_FAILURE"},
            )
            return self._route_processing_review(job.trace_id, ("ERP_RECONCILIATION_FAILURE",))
        if existing_order is not None:
            order_id = str(existing_order["order_id"])
            self.audit.append(
                trace_id=job.trace_id,
                event_type="erp_write_reconciliation",
                status=AuditStatus.SUCCESS,
                idempotency_key=job.idempotency_key,
                outcome=ProcessingOutcome.ERP_CREATED.value,
                payload={"order_id": order_id},
            )
            self.state.transition_job(
                job.trace_id,
                ProcessingOutcome.ERP_CREATED,
                reason_codes=("ERP_WRITE_RECONCILED",),
                order_id=order_id,
            )
            return ProcessingResult(
                trace_id=job.trace_id,
                outcome=ProcessingOutcome.ERP_CREATED,
                reason_codes=("ERP_WRITE_RECONCILED",),
                order_id=order_id,
                deduplicated=True,
            )
        if job.outcome is ProcessingOutcome.RECEIVED:
            return None
        return _result_from_job(job, deduplicated=True)

    def process(self, email: InboundEmail) -> ProcessingResult:
        reservation = self.state.reserve_message(email)
        job = reservation.job
        if reservation.disposition is not ReservationDisposition.NEW:
            duplicate = self._handle_duplicate(reservation)
            if duplicate is not None:
                return duplicate
        try:
            self.state.transition_job(
                job.trace_id,
                ProcessingOutcome.PROCESSING,
                expected_outcome=ProcessingOutcome.RECEIVED,
            )
        except StateConflictError:
            current = self.state.get_job(job.trace_id)
            if current is None:
                raise WorkflowFailure("Reserved job disappeared") from None
            return _result_from_job(current, deduplicated=True)

        ingest_intent = self.audit.record_intent(
            job.trace_id,
            "ingestion",
            input_digest=job.content_digest,
            idempotency_key=job.idempotency_key,
            payload={"attachment_count": len(email.attachments)},
        )
        transport_issues = transport_security_issues(email)
        if transport_issues:
            self.audit.record_outcome(
                job.trace_id,
                ingest_intent.event_id,
                AuditStatus.DENIED,
                outcome=ProcessingOutcome.SECURITY_REVIEW.value,
                payload={"reason_codes": transport_issues},
            )
            return self._route_security(job.trace_id, transport_issues)

        customer_intent = self.audit.record_intent(job.trace_id, "customer_resolution")
        try:
            customer = self.erp.resolve_customer(email.envelope_sender)
        except ERPNotFound:
            self.audit.record_outcome(
                job.trace_id,
                customer_intent.event_id,
                AuditStatus.DENIED,
                outcome=ProcessingOutcome.SECURITY_REVIEW.value,
                payload={"code": "UNKNOWN_SENDER"},
            )
            return self._route_security(job.trace_id, ("UNKNOWN_SENDER",))
        except ERPError:
            self.audit.record_outcome(job.trace_id, customer_intent.event_id, AuditStatus.FAILURE)
            return self._route_security(job.trace_id, ("CUSTOMER_RESOLUTION_FAILURE",))
        self.audit.record_outcome(job.trace_id, customer_intent.event_id, AuditStatus.SUCCESS)

        try:
            ingested = ingest_attachments(
                email,
                store=self.attachment_store,
                scanner=self.scanner,
                limits=self._attachment_limits(),
            )
        except AttachmentBoundaryError as exc:
            status = AuditStatus.TIMEOUT if exc.code.endswith("TIMEOUT") else AuditStatus.DENIED
            self.audit.record_outcome(
                job.trace_id,
                ingest_intent.event_id,
                status,
                outcome=ProcessingOutcome.REVIEW_REQUIRED.value,
                payload={"code": exc.code},
            )
            if exc.code in {"MALWARE_DETECTED", "MALWARE_SCAN_TIMEOUT"}:
                return self._route_security(job.trace_id, (exc.code,))
            return self._route_processing_review(job.trace_id, (exc.code,))
        except Exception:
            self.audit.record_outcome(job.trace_id, ingest_intent.event_id, AuditStatus.FAILURE)
            return self._route_processing_review(job.trace_id, ("ATTACHMENT_BOUNDARY_FAILURE",))
        self.audit.record_outcome(job.trace_id, ingest_intent.event_id, AuditStatus.SUCCESS)

        attachment_texts = tuple(item.normalized_text for item in ingested.parsed_attachments)
        security_intent = self.audit.record_intent(job.trace_id, "security_screen")
        security = screen_content(
            subject=email.subject,
            body=email.body,
            attachment_texts=attachment_texts,
            advisory_classifier=self.security_classifier,
        )
        if security.disposition != "allow":
            status = AuditStatus.DENIED if security.disposition == "block" else AuditStatus.FAILURE
            self.audit.record_outcome(
                job.trace_id,
                security_intent.event_id,
                status,
                outcome=ProcessingOutcome.SECURITY_REVIEW.value,
                payload={"reason_codes": security.reason_codes, "hits": security.deterministic_hits},
            )
            return self._route_security(job.trace_id, security.reason_codes)
        self.audit.record_outcome(job.trace_id, security_intent.event_id, AuditStatus.SUCCESS)

        customer_id = str(customer["customer_id"])
        scoped_erp = self.erp.for_customer(customer_id)
        emit = self._audit_event_callback(job.trace_id)
        executor = ScopedToolExecutor(
            scoped_erp,
            max_calls=self.settings.max_tool_calls,
            max_returned_characters=self.settings.max_tool_result_characters,
            event_sink=emit,
        )
        extractor = OrderExtractor(
            self.extraction_model_factory(email.provider_message_id),
            executor,
            max_model_turns=self.settings.max_tool_iterations,
            event_sink=emit,
        )
        fragments = tuple(
            {
                "attachment_id": parsed.attachment_id,
                **fragment.model_dump(mode="json"),
            }
            for parsed in ingested.parsed_attachments
            for fragment in parsed.fragments
        )
        try:
            proposal = extractor.extract(
                ExtractionContext(
                    message_id=email.provider_message_id,
                    authenticated_customer_id=customer_id,
                    subject=email.subject,
                    body=email.body,
                    attachment_fragments=fragments,
                )
            )
        except (ExtractionFailure, ToolDenied, ERPError) as exc:
            code = exc.code if isinstance(exc, (ExtractionFailure, ToolDenied)) else "ERP_LOOKUP_FAILURE"
            return self._route_processing_review(job.trace_id, (code,))

        policy_intent = self.audit.record_intent(
            job.trace_id,
            "deterministic_validation",
            input_digest=ingested.input_digest,
            idempotency_key=job.idempotency_key,
        )
        policy = OrderPolicy(
            scoped_erp,
            price_tolerance=Decimal(str(self.settings.price_tolerance_pct)),
            max_order_value=Decimal(str(self.settings.max_order_value_circuit_breaker)),
            policy_version=self.settings.policy_version,
        )
        decision = policy.validate(
            proposal,
            customer_id=customer_id,
            input_digest=ingested.input_digest,
            idempotency_key=job.idempotency_key,
        )
        if not decision.approved or decision.command is None:
            self.audit.record_outcome(
                job.trace_id,
                policy_intent.event_id,
                AuditStatus.DENIED,
                outcome=ProcessingOutcome.REVIEW_REQUIRED.value,
                payload={"reason_codes": [issue.code for issue in decision.issues]},
            )
            return self._route_order_review(
                job.trace_id,
                proposal,
                decision.issues,
                customer_id=customer_id,
                input_digest=ingested.input_digest,
            )
        self.audit.record_outcome(job.trace_id, policy_intent.event_id, AuditStatus.SUCCESS)

        if not self.settings.writes_enabled:
            reason = "KILL_SWITCH" if self.settings.kill_switch else "SHADOW_MODE"
            self.state.transition_job(
                job.trace_id,
                ProcessingOutcome.SHADOW_APPROVED,
                reason_codes=(reason,),
            )
            self.audit.append(
                trace_id=job.trace_id,
                event_type="write_gate",
                status=AuditStatus.DENIED,
                idempotency_key=job.idempotency_key,
                outcome=ProcessingOutcome.SHADOW_APPROVED.value,
                payload={"code": reason},
            )
            return ProcessingResult(
                trace_id=job.trace_id,
                outcome=ProcessingOutcome.SHADOW_APPROVED,
                reason_codes=(reason,),
            )

        write_intent = self.audit.record_intent(
            job.trace_id,
            "erp_create_order",
            input_digest=decision.command.input_digest,
            idempotency_key=decision.command.idempotency_key,
            payload={"customer_id": decision.command.customer_id, "line_count": len(decision.command.lines)},
        )
        try:
            order = self.erp.create_validated_order(decision.command)
        except ERPConflict:
            self.audit.record_outcome(job.trace_id, write_intent.event_id, AuditStatus.DENIED)
            issue = ValidationIssue(
                code="ERP_RECHECK_CONFLICT",
                severity=IssueSeverity.ERROR,
                explanation="ERP price, stock, customer, or catalog changed before write.",
            )
            return self._route_order_review(
                job.trace_id,
                proposal,
                (issue,),
                customer_id=customer_id,
                input_digest=ingested.input_digest,
            )
        except ERPUnknownWriteOutcome:
            self.audit.record_outcome(job.trace_id, write_intent.event_id, AuditStatus.FAILURE)
            return self._route_processing_review(job.trace_id, ("ERP_WRITE_OUTCOME_UNKNOWN",))
        except ERPError:
            self.audit.record_outcome(job.trace_id, write_intent.event_id, AuditStatus.FAILURE)
            return self._route_processing_review(job.trace_id, ("ERP_WRITE_FAILURE",))
        if self._after_erp_success is not None:
            self._after_erp_success(order)
        order_id = str(order["order_id"])
        self.audit.record_outcome(
            job.trace_id,
            write_intent.event_id,
            AuditStatus.SUCCESS,
            outcome=ProcessingOutcome.ERP_CREATED.value,
            payload={"order_id": order_id, "deduplicated": bool(order.get("deduplicated"))},
        )
        self.state.transition_job(
            job.trace_id,
            ProcessingOutcome.ERP_CREATED,
            order_id=order_id,
        )
        return ProcessingResult(
            trace_id=job.trace_id,
            outcome=ProcessingOutcome.ERP_CREATED,
            order_id=order_id,
            deduplicated=bool(order.get("deduplicated")),
        )
