"""Generate the thin, executable case-study notebook from reviewed source cells."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import nbformat

ROOT = Path(__file__).resolve().parents[1]


def markdown(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_markdown_cell(dedent(text).strip())


def code(text: str) -> nbformat.NotebookNode:
    return nbformat.v4.new_code_cell(dedent(text).strip())


cells = [
    markdown(
        """
        # I built a controlled path from a shared order inbox to ERP

        **OMMAX case study · Part B · Tanvi Gunjal**

        ## 1. Objective and acceptance contract

        My model may interpret untrusted order content; it cannot authorize a write. A deterministic policy either
        produces an immutable validated command, routes the item to human review, or blocks it for security review.
        The default walkthrough is offline replay in shadow mode. The only demonstrated write uses a separate local
        ERP sandbox, and duplicate delivery must still create exactly one order.

        This notebook uses synthetic data only. It is control-path evidence—not a production accuracy, ROI, cost,
        latency, legal-compliance, or readiness claim.
        """
    ),
    markdown(
        """
        ## 2. Reproducible setup and version manifest

        From this directory, `uv sync --locked --all-groups` recreates Python 3.12 dependencies from `uv.lock`.
        The code, prompt, policy, replay, and model versions below are attached to audit traces. Runtime artifacts stay
        inside the ignored `workspace/notebook_run/` directory.
        """
    ),
    code(
        """
        # ruff: noqa: E402
        import json
        import os
        import shutil
        import warnings
        from pathlib import Path

        import pandas as pd
        from IPython.display import display

        warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient` is deprecated.*")
        warnings.filterwarnings("ignore", message="You should not use the 'timeout' argument with the TestClient.*")

        from order_pipeline.config import Settings
        from order_pipeline.demo import (
            run_binary_attachment_examples,
            run_demo_scenarios,
            run_failure_examples,
            run_human_review_example,
        )
        from order_pipeline.evidence import build_evidence_manifest

        ROOT = Path.cwd().resolve()
        if ROOT.name != "partB_agentic_workflow":
            raise RuntimeError("Run this notebook from partB_agentic_workflow/")
        WORKSPACE = ROOT / "workspace" / "notebook_run"
        if WORKSPACE.exists():
            shutil.rmtree(WORKSPACE)
        WORKSPACE.mkdir(parents=True)

        canonical_evidence = ROOT / "presentation_evidence.json"
        evidence_seed = json.loads(canonical_evidence.read_text()) if canonical_evidence.exists() else {}
        source_commit_sha = evidence_seed.get("sourceCommitSha", "0" * 40)
        settings = Settings(workspace_dir=WORKSPACE, state_db_path=WORKSPACE / "state.sqlite3")
        pd.DataFrame([settings.version_manifest()]).T.rename(columns={0: "version"})
        """
    ),
    markdown(
        """
        ## 3. Architecture and responsibility split

        `fixture mailbox → reserve/idempotency → transport + attachment gates → security screen → scoped extraction
        tools → deterministic policy → shadow/review or keyed REST POST → audit + durable state`

        | Probabilistic, never authorizes | Deterministic, owns authorization |
        |---|---|
        | Language/field proposal, ambiguity, source excerpts, advisory security signal | Identity, tool scope, schema, quantities, EUR price tolerance, stock, catalog version, value cap, write mode, idempotency |

        The writer accepts only `ValidatedOrderCommand`; no raw proposal or model tool can create, update, or delete
        ERP data. See `ARCHITECTURE.md` for the trust-boundary diagram.
        """
    ),
    markdown(
        """
        ## 4. Synthetic inbox and real binary attachments

        I generate valid PDF, PPTX, and XLSX bytes, store them behind server-issued opaque references, then exercise
        signature/container checks and bounded subprocess parsers. The output below exposes provenance locators but not
        raw content or machine paths. English and German authorization behavior is covered by the named policy corpus;
        unsupported languages route to review.
        """
    ),
    code(
        """
        attachments = run_binary_attachment_examples(WORKSPACE / "attachments")
        attachment_rows = []
        for format_name, result in attachments.items():
            first_locator = result["locators"][0]
            attachment_rows.append(
                {
                    "format": format_name.upper(),
                    "outcome": result["outcome"],
                    "writes": result["write_count"],
                    "provenance": {key: value for key, value in first_locator.items() if value is not None},
                }
            )
        pd.DataFrame(attachment_rows)
        """
    ),
    markdown(
        """
        ## 5. Model modes and exact tool allowlist

        Replay is the required keyless mode. Live Gemini requires explicit credentials/models and is hard-restricted
        to shadow mode. The model sees exactly three bounded, read-only tools; customer identity is already bound by
        the authenticated envelope sender, so purchase history accepts no customer ID argument.
        """
    ),
    code(
        """
        from order_pipeline.tool_executor import TOOL_SCHEMAS

        pd.DataFrame(
            [
                {
                    "tool": schema["name"],
                    "arguments": sorted(schema["parameters"]["properties"]),
                    "mutation": False,
                }
                for schema in TOOL_SCHEMAS
            ]
        )
        """
    ),
    markdown(
        """
        ## 6. Shadow batch summary

        This execution runs the real replay transcript through the scoped tools and local REST ERP boundary. Shadow
        approval records the decision without a POST. A separate auto workspace is created only for the explicit
        exactly-once example in the next section.
        """
    ),
    code(
        """
        demo = run_demo_scenarios(WORKSPACE / "workflow", include_auto_example=True)
        shadow = demo["shadow"]
        pd.DataFrame(
            [
                {
                    "mode": shadow["mode"],
                    "outcome": item["outcome"],
                    "reason_codes": item["reason_codes"],
                    "writes": shadow["write_count"],
                    "audit_events": shadow["audit_event_count"],
                    "audit_verified": shadow["audit_verified"],
                }
                for item in shadow["scenario_outcomes"]
            ]
        )
        """
    ),
    markdown(
        """
        ## 7. One safe local auto-write and duplicate replay

        Auto mode is accepted only in `local_sandbox`. The POST carries an idempotency key, canonical payload digest,
        and catalog version; the ERP transaction rechecks customer, price, stock, and catalog version before mutation.
        The duplicate resolves to the original order and stock is decremented once.
        """
    ),
    code(
        """
        auto = demo["auto"]
        pd.DataFrame(
            [
                {
                    "delivery": index + 1,
                    "outcome": item["outcome"],
                    "deduplicated": item["deduplicated"],
                    "orders_after_both_deliveries": auto["write_count"],
                    "audit_verified": auto["audit_verified"],
                }
                for index, item in enumerate(auto["scenario_outcomes"])
            ]
        )
        """
    ),
    markdown(
        """
        ## 8. Ambiguous order correction and revalidation

        A reviewer claims the item, corrects only the evidenced quantity, and re-runs the deterministic policy. The
        write command remains anchored to the reserved message and authenticated customer. Approval is possible only
        after the ERP client returns a verified creation receipt.
        """
    ),
    code(
        """
        review = run_human_review_example(WORKSPACE / "review")
        display(pd.DataFrame({"state": review["states"], "sequence": range(1, len(review["states"]) + 1)}))
        display(
            {
                "corrected_fields": review["correction_changes"],
                "validated_total_eur": review["validated_total"],
                "erp_orders": review["write_count"],
            }
        )
        """
    ),
    markdown(
        """
        ## 9. Security and attachment failures: zero model calls, zero writes

        An authoritative injection hit blocks before advisory screening or extraction. A corrupt PDF fails the
        attachment boundary and becomes durable review work. Neither path can reach ERP create.
        """
    ),
    code(
        """
        failures = run_failure_examples(WORKSPACE / "failures")
        pd.DataFrame(
            [
                {
                    "case": name,
                    "outcome": result["outcome"],
                    "reason_codes": result["reason_codes"],
                    "model_calls": result["model_calls"],
                    "writes": result["write_count"],
                }
                for name, result in failures.items()
            ]
        )
        """
    ),
    markdown(
        """
        ## 10. Compact audit verification and evidence export

        The manifest deliberately strips random trace/order IDs, raw messages, local paths, and secrets. It binds the
        demonstrated code revision to scenario outcomes, write counts, audit-chain verification, tool scope, and test
        status. The canonical committed copy is generated by `scripts/export_evidence.py` after the source commit.
        """
    ),
    code(
        """
        quality_seed = evidence_seed.get("quality", {})
        manifest = build_evidence_manifest(
            source_commit_sha=source_commit_sha,
            demo=demo,
            attachments=attachments,
            failures=failures,
            review=review,
            test_count=int(quality_seed.get("testCount", 0)),
            deterministic_core_branch_coverage=int(quality_seed.get("deterministicCoreBranchCoveragePercent", 0)),
        )
        run_evidence_path = WORKSPACE / "presentation_evidence.json"
        run_evidence_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\\n")
        {
            "status": manifest["status"],
            "source_commit": manifest["sourceCommitSha"][:12],
            "shadow_writes": manifest["observations"]["shadow"]["writeCount"],
            "auto_duplicate_orders": manifest["observations"]["isolatedAutoDuplicate"]["writeCount"],
            "blocked_path_writes": manifest["quality"]["blockedPathWriteCount"],
            "audit_verified": manifest["observations"]["isolatedAutoDuplicate"]["auditVerified"],
            "run_artifact": str(run_evidence_path.relative_to(ROOT)),
        }
        """
    ),
    markdown(
        """
        ## Optional live Gemini shadow smoke

        This cell is disabled in CI. Set `RUN_LIVE_GEMINI=1` plus `GOOGLE_API_KEY`,
        `GEMINI_EXTRACTION_MODEL`, and `GEMINI_SCREENING_MODEL` to construct the same structured adapters for synthetic
        shadow evaluation. `build_live_gemini_models` rejects replay settings and any non-shadow live configuration.
        """
    ),
    code(
        """
        if os.getenv("RUN_LIVE_GEMINI") == "1":
            from order_pipeline.config import ModelMode, WriteMode
            from order_pipeline.live_gemini import build_live_gemini_models

            live_settings = Settings(model_mode=ModelMode.LIVE, write_mode=WriteMode.SHADOW)
            live_models = build_live_gemini_models(live_settings)
            display({"status": "configured", "versions": live_settings.version_manifest()})
        else:
            display({"status": "skipped by default", "required_mode": "live + shadow"})
        """
    ),
    markdown(
        """
        ## 11. Deployment, human oversight, monitoring, and KPIs

        **Cloud-neutral production shape:** `mailbox webhook → durable queue → quarantine/parser boundary → stateless
        workflow workers → scoped LLM/ERP clients → transactional database/object store → review UI → observability`.

        Before a real-email pilot I would require approved provider/ERP semantics, DPA and retention decisions,
        encryption and secrets management, RBAC/access reviews, backups and recovery targets, incident response,
        independently retained audit evidence, and legal/DPO review. Security reviewers quarantine/reject malicious
        cases; order reviewers correct evidenced business fields. High-value and security-blocked items remain outside
        automatic release in this sprint.

        I would monitor false auto-approvals, writes per idempotency key, blocked-path writes, hash-chain failures,
        correction rate, review age, parser/provider failures, queue lag, ERP reconciliation, and segmented cost. The
        proposed promotion gate is **zero false auto-approvals among at least 299 reviewed auto-eligible shadow orders**,
        a one-sided 95% rule-of-three upper bound below 1%; this notebook does not claim the prototype has met it.

        Production cloud, volume, SLA, retention periods, and ERP update/delete semantics remain intentionally unset
        because the brief does not provide those facts. `docs/OPERATIONS_AND_COMPLIANCE.md` and
        `docs/TRACEABILITY.md` contain the full handoff.
        """
    ),
]

notebook = nbformat.v4.new_notebook(
    cells=cells,
    metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
    },
)
nbformat.write(notebook, ROOT / "order_to_erp_agent.ipynb")
