import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { scenes, totalRehearsalSeconds } from "./data/scenes";
import {
  calculateValue,
  requiredZeroDefectSample,
  zeroDefectUpperBound,
  type ValueInputs,
} from "./lib/calculators";

const evidence = __PRESENTATION_EVIDENCE__;

type Scenario = "approve" | "review" | "block";
type CalculatorSection = "operations" | "routing" | "costs";
type AppendixSection =
  | "deployment"
  | "validation"
  | "monitoring"
  | "compliance"
  | "method";

const scenarioContent: Record<
  Scenario,
  { label: string; verdict: string; detail: string; steps: string[] }
> = {
  approve: {
    label: "Approve",
    verdict: "Validated command",
    detail: "Known sender, clear evidence, current catalog, sufficient stock.",
    steps: ["Propose", "Validate", "Persist intent", "Create once"],
  },
  review: {
    label: "Review",
    verdict: "Durable work item",
    detail: "Ambiguity, conflict, unsupported language, or commercial exception.",
    steps: ["Explain", "Claim", "Correct", "Revalidate"],
  },
  block: {
    label: "Block",
    verdict: "Security quarantine",
    detail: "Failed sender authentication, replay collision, or authoritative threat hit.",
    steps: ["Contain", "Record", "Deny tools", "Zero writes"],
  },
};

const traceEvents = [
  { name: "Email reserved", kind: "state", detail: "Stable trace + idempotency key" },
  { name: "Sender authenticated", kind: "gate", detail: "Webhook and transport verdicts pass" },
  { name: "Attachments cleared", kind: "gate", detail: "Signature, limits, scan, parse" },
  { name: "Customer resolved", kind: "scope", detail: "Authenticated sender scope only" },
  { name: "Proposal returned", kind: "model", detail: "Structured fields + provenance" },
  { name: "Policy validated", kind: "gate", detail: "Immutable command minted" },
  { name: "Audit intent persisted", kind: "state", detail: "Fail closed before write" },
  { name: "ERP order created", kind: "write", detail: "Atomic recheck + keyed POST" },
  { name: "Replay reconciled", kind: "state", detail: "Original order returned; no new write" },
];

const initialValueInputs: ValueInputs = {
  annualVolume: 12_000,
  currentMinutesPerOrder: 12,
  loadedLaborCostPerHour: 48,
  autoShare: 0.7,
  escalationShare: 0.25,
  blockedShare: 0.05,
  escalationMinutes: 8,
  securityMinutes: 15,
  screeningCostPerEmail: 0.02,
  extractionCostPerEmail: 0.1,
  infrastructureCostPerEmail: 0.04,
  fixedAnnualCost: 24_000,
  oneTimeCost: 70_000,
  rampMonths: 6,
  cashRealizationShare: null,
  currentReworkShare: 0.04,
  currentReworkMinutes: 10,
};

function Provenance({
  kind,
}: {
  kind:
    | "Task-stated"
    | "Demo-observed"
    | "Illustrative input"
    | "User input"
    | "External source";
}) {
  return <span className="provenance">{kind}</span>;
}

function BigStatement({ children }: { children: React.ReactNode }) {
  return <p className="big-statement">{children}</p>;
}

function Metric({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className={`metric ${muted ? "metric-muted" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function NumberInput({
  label,
  value,
  onChange,
  suffix,
  step = 1,
  min = 0,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  suffix?: string;
  step?: number;
  min?: number;
}) {
  return (
    <label className="number-field">
      <span>{label}</span>
      <span className="number-control">
        <input
          type="number"
          value={Number.isFinite(value) ? value : ""}
          min={min}
          step={step}
          onChange={(event) => onChange(event.target.valueAsNumber)}
        />
        {suffix && <em>{suffix}</em>}
      </span>
    </label>
  );
}

const euro = (value: number | null) =>
  value === null
    ? "—"
    : new Intl.NumberFormat("en-DE", {
        style: "currency",
        currency: "EUR",
        maximumFractionDigits: 0,
      }).format(value);

const hours = (value: number | null) =>
  value === null
    ? "—"
    : `${new Intl.NumberFormat("en-DE", { maximumFractionDigits: 0 }).format(value)} h`;

function App() {
  const [active, setActive] = useState(() => {
    const match = window.location.hash.match(/^#scene-(\d+)$/);
    if (!match) return 0;
    return Math.min(scenes.length - 1, Math.max(0, Number(match[1]) - 1));
  });
  const [scenario, setScenario] = useState<Scenario>("approve");
  const [traceIndex, setTraceIndex] = useState(traceEvents.length - 1);
  const [overviewOpen, setOverviewOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [appendixOpen, setAppendixOpen] = useState(false);
  const [appendixSection, setAppendixSection] =
    useState<AppendixSection>("deployment");
  const [calcSection, setCalcSection] =
    useState<CalculatorSection>("operations");
  const [valueInputs, setValueInputs] =
    useState<ValueInputs>(initialValueInputs);
  const [valueDirty, setValueDirty] = useState(false);
  const [targetRatePct, setTargetRatePct] = useState(1);
  const [confidencePct, setConfidencePct] = useState(95);
  const touchStart = useRef<{ x: number; y: number } | null>(null);

  const navigate = useCallback((next: number) => {
    setActive(Math.min(scenes.length - 1, Math.max(0, next)));
  }, []);

  useEffect(() => {
    const nextHash = `#${scenes[active].id}`;
    if (window.location.hash !== nextHash) {
      window.history.replaceState(null, "", nextHash);
    }
    document.title = `${active + 1}/${scenes.length} · ${scenes[active].title}`;
  }, [active]);

  useEffect(() => {
    const onHash = () => {
      const match = window.location.hash.match(/^#scene-(\d+)$/);
      if (match) navigate(Number(match[1]) - 1);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [navigate]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select")) return;
      if (event.key === "ArrowRight" || event.key === "PageDown") navigate(active + 1);
      if (event.key === "ArrowLeft" || event.key === "PageUp") navigate(active - 1);
      if (event.key === "Home") navigate(0);
      if (event.key === "End") navigate(scenes.length - 1);
      if (event.key.toLowerCase() === "o") setOverviewOpen((open) => !open);
      if (event.key.toLowerCase() === "n") setNotesOpen((open) => !open);
      if (event.key.toLowerCase() === "a") setAppendixOpen((open) => !open);
      if (event.key.toLowerCase() === "f") void toggleFullscreen();
      if (event.key === "Escape") {
        setOverviewOpen(false);
        setNotesOpen(false);
        setAppendixOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, navigate]);

  const valueOutputs = useMemo(() => calculateValue(valueInputs), [valueInputs]);
  const pilotSample = useMemo(
    () => requiredZeroDefectSample(targetRatePct / 100, confidencePct / 100),
    [targetRatePct, confidencePct],
  );
  const pilotUpperBound = useMemo(
    () => zeroDefectUpperBound(pilotSample, confidencePct / 100),
    [pilotSample, confidencePct],
  );

  const updateValue = (field: keyof ValueInputs, value: number | null) => {
    setValueDirty(true);
    setValueInputs((current) => ({ ...current, [field]: value }));
  };

  const toggleFullscreen = async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  };

  const sceneContent = (() => {
    switch (active) {
      case 0:
        return (
          <div className="cover-layout">
            <div className="cover-accent" aria-hidden="true" />
            <p className="cover-kicker">Controlled email-to-ERP workflow</p>
            <h1>{scenes[0].title}</h1>
            <div className="cover-footer">
              <p>
                <strong>Tanvi Gunjal</strong>
                <span>OMMAX · Part B</span>
              </p>
              <p className="cover-scope">
                Offline-first prototype<br />Synthetic data only
              </p>
            </div>
          </div>
        );
      case 1:
        return (
          <div className="tension-layout">
            <div className="tension-question">
              <span>Model output</span>
              <BigStatement>“This looks like an order.”</BigStatement>
            </div>
            <div className="tension-divider" aria-hidden="true">
              ≠
            </div>
            <div className="tension-answer">
              <span>Write authorization</span>
              <BigStatement>“Every deterministic gate passed.”</BigStatement>
            </div>
            <p className="bottom-thesis">
              I treat uncertainty as a routing signal, not a confidence threshold.
            </p>
          </div>
        );
      case 2: {
        const current = scenarioContent[scenario];
        return (
          <div className={`scenario-layout scenario-${scenario}`}>
            <div className="tab-row" role="tablist" aria-label="Outcome scenarios">
              {(Object.keys(scenarioContent) as Scenario[]).map((key) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={scenario === key}
                  className={scenario === key ? "active" : ""}
                  onClick={() => setScenario(key)}
                >
                  {scenarioContent[key].label}
                </button>
              ))}
            </div>
            <div className="scenario-main">
              <div>
                <span className="outcome-index">{scenario === "approve" ? "A" : scenario === "review" ? "R" : "B"}</span>
                <h3>{current.verdict}</h3>
                <p>{current.detail}</p>
              </div>
              <ol className="scenario-steps">
                {current.steps.map((step, index) => (
                  <li key={step}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    {step}
                  </li>
                ))}
              </ol>
            </div>
            <div className="scenario-contract">
              <strong>One trace</strong><strong>Explicit reasons</strong><strong>Observable terminal state</strong>
              <Provenance kind="Task-stated" />
            </div>
          </div>
        );
      }
      case 3:
        return (
          <div className="scope-layout">
            <div className="scope-languages">
              <span className="scope-caption">Auto-write language scope</span>
              <p><strong>English</strong><strong>Deutsch</strong></p>
            </div>
            <div className="format-ribbon" aria-label="Supported attachment formats">
              {[
                ["PDF", "pages + text provenance"],
                ["PPTX", "slides + shape provenance"],
                ["XLSX", "sheets + cell provenance"],
              ].map(([format, description]) => (
                <div key={format}>
                  <strong>{format}</strong>
                  <span>{description}</span>
                </div>
              ))}
            </div>
            <div className="scope-rule">
              <span>One authenticated sender</span>
              <span>One email</span>
              <span>One order</span>
              <span>One trace</span>
              <Provenance kind="Task-stated" />
            </div>
          </div>
        );
      case 4:
        return (
          <div className="boundary-layout">
            <div className="boundary-lane boundary-model">
              <span className="lane-label">Probabilistic</span>
              <h3>Propose</h3>
              <p>Language, lines, quantity, evidence, uncertainty</p>
              <span className="lane-limit">Bounded turns · bounded tools</span>
            </div>
            <div className="boundary-crossing" aria-label="Deterministic boundary">
              <span>Schema</span><span>→</span><strong>POLICY</strong><span>→</span><span>Command</span>
            </div>
            <div className="boundary-lane boundary-policy">
              <span className="lane-label">Deterministic</span>
              <h3>Authorize</h3>
              <p>Identity, catalog, price, stock, conflicts, value, mode</p>
              <span className="lane-limit">Only this lane can mint a write command</span>
            </div>
            <div className="boundary-badge"><Provenance kind="Task-stated" /> 6 model turns · 8 tool calls · 5 search results max</div>
          </div>
        );
      case 5:
        return (
          <div className="tools-layout">
            <div className="tool-list">
              {[
                ["01", "Exact product lookup", "SKU → allowlisted catalog fields"],
                ["02", "Bounded product search", "Query length · five results max"],
                ["03", "Purchase history", "Authenticated customer · five orders"],
              ].map(([number, title, detail]) => (
                <div className="tool-row" key={number}>
                  <span>{number}</span><strong>{title}</strong><p>{detail}</p>
                </div>
              ))}
            </div>
            <div className="capability-denial">
              <span>Not registered</span>
              <p>Customer search</p><p>Create</p><p>Update</p><p>Delete</p>
              <strong>No hidden state-changing path</strong>
            </div>
            <div className="tools-source"><Provenance kind="Task-stated" /></div>
          </div>
        );
      case 6: {
        const event = traceEvents[traceIndex];
        return (
          <div className="trace-layout">
            <div className="trace-head">
              <div>
                <span>Trace</span>
                <code>tr_7f2a…901c</code>
              </div>
              <div>
                <span>Idempotency key</span>
                <code>orders-eu/msg-042</code>
              </div>
              <Provenance kind="Task-stated" />
            </div>
            <div className="trace-focus">
              <span className={`trace-kind kind-${event.kind}`}>{event.kind}</span>
              <p>{event.name}</p>
              <strong>{event.detail}</strong>
              {event.kind === "write" && <mark>the only write point</mark>}
            </div>
            <div className="trace-track" aria-hidden="true">
              {traceEvents.map((item, index) => (
                <span
                  key={item.name}
                  className={`${index <= traceIndex ? "visited" : ""} ${item.kind === "write" ? "write" : ""}`}
                />
              ))}
            </div>
            <label className="trace-scrubber">
              <span>Scrub the audit trace</span>
              <input
                type="range"
                min="0"
                max={traceEvents.length - 1}
                value={traceIndex}
                onChange={(event) => setTraceIndex(event.target.valueAsNumber)}
              />
              <output>{traceIndex + 1} / {traceEvents.length}</output>
            </label>
            <p className="trace-result">
              Replay returns the original order. <strong>Stock changes once.</strong>
            </p>
          </div>
        );
      }
      case 7:
        return (
          <div className="review-layout">
            <div className="revision-before">
              <span>Proposal · v1</span>
              <p>“20 filters”</p>
              <strong>SKU unresolved</strong>
            </div>
            <div className="revision-arrow" aria-hidden="true">→</div>
            <div className="revision-action">
              <span>Human evidence</span>
              <p>Customer PO<br />row 14</p>
              <strong>ORDER_REVIEWER</strong>
            </div>
            <div className="revision-arrow" aria-hidden="true">→</div>
            <div className="revision-after">
              <span>Revision · v2</span>
              <p>20 × FLT-200</p>
              <strong>Ready to revalidate</strong>
            </div>
            <div className="review-state">
              PENDING <b>→</b> CLAIMED <b>→</b> CORRECTED <b>→</b> REVALIDATED
            </div>
            <p className="review-rule">The revision is immutable. Approval without correction and revalidation fails.</p>
          </div>
        );
      case 8:
        return (
          <div className="security-layout">
            <div className="security-bar level-one">
              <span>01</span><strong>Authenticate transport</strong><p>Failed sender verdict → security review</p>
            </div>
            <div className="security-bar level-two">
              <span>02</span><strong>Contain attachments</strong><p>Signature · limits · scan · bounded parser</p>
            </div>
            <div className="security-bar level-three">
              <span>03</span><strong>Screen normalized content</strong><p>Authoritative hit → no model call</p>
            </div>
            <div className="security-bar level-four">
              <span>04</span><strong>Limit useful capability</strong><p>Sender-scoped reads · no mutation tools</p>
            </div>
            <div className="security-zero">
              <strong>0</strong><span>ERP writes on blocked paths</span><Provenance kind="Task-stated" />
            </div>
          </div>
        );
      case 9:
        return (
          <div className="findings-layout">
            {[
              ["01", "Retries are normal", "Idempotency belongs in the domain, not in a retry wrapper."],
              ["02", "Read-only can still leak", "Capability must be customer-scoped, allowlisted, and bounded."],
              ["03", "A demo is not a rate", "Control coverage proves paths, not production accuracy or STP."],
            ].map(([index, finding, implication]) => (
              <div className="finding" key={index}>
                <span>{index}</span><h3>{finding}</h3><p>{implication}</p>
              </div>
            ))}
            <div className="findings-badge"><Provenance kind="Task-stated" /></div>
          </div>
        );
      case 10:
        return (
          <div className="gates-layout">
            {[
              ["Engineering", "Replay, audit, idempotency, review, zero-write negatives"],
              ["Integration", "Real mailbox semantics, ERP contract, unknown-outcome drills"],
              ["Operating", "Named owners, kill switch, incident and review SLAs"],
              ["Evidence", "Shadow sample passes the agreed false-approval gate"],
              ["Governance", "DPO/legal review, data retention, provider terms, staff literacy"],
            ].map(([label, description], index) => (
              <div className="gate-row" key={label}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{label}</strong><p>{description}</p><i aria-hidden="true" />
              </div>
            ))}
            <div className="gate-callout">
              Proposed promotion gate: <strong>zero false auto-approvals</strong> in the selected shadow sample.
              <Provenance kind="Task-stated" />
            </div>
          </div>
        );
      case 11:
        return (
          <div className="calculator-layout">
            <div className="calculator-inputs">
              <div className="calculator-tabs" role="tablist" aria-label="Value input groups">
                {(["operations", "routing", "costs"] as CalculatorSection[]).map((section) => (
                  <button
                    key={section}
                    role="tab"
                    aria-selected={calcSection === section}
                    className={calcSection === section ? "active" : ""}
                    onClick={() => setCalcSection(section)}
                  >
                    {section}
                  </button>
                ))}
              </div>
              <div className="calculator-fields">
                {calcSection === "operations" && (
                  <>
                    <NumberInput label="Annual order volume" value={valueInputs.annualVolume} onChange={(v) => updateValue("annualVolume", v)} />
                    <NumberInput label="Handling time" value={valueInputs.currentMinutesPerOrder} suffix="min/order" step={0.5} onChange={(v) => updateValue("currentMinutesPerOrder", v)} />
                    <NumberInput label="Loaded labor cost" value={valueInputs.loadedLaborCostPerHour} suffix="€/hour" step={1} onChange={(v) => updateValue("loadedLaborCostPerHour", v)} />
                    <NumberInput label="Current rework rate" value={valueInputs.currentReworkShare * 100} suffix="%" step={1} onChange={(v) => updateValue("currentReworkShare", v / 100)} />
                    <NumberInput label="Rework effort" value={valueInputs.currentReworkMinutes} suffix="min" step={0.5} onChange={(v) => updateValue("currentReworkMinutes", v)} />
                  </>
                )}
                {calcSection === "routing" && (
                  <>
                    <NumberInput label="Auto share" value={valueInputs.autoShare * 100} suffix="%" step={1} onChange={(v) => updateValue("autoShare", v / 100)} />
                    <NumberInput label="Escalation share" value={valueInputs.escalationShare * 100} suffix="%" step={1} onChange={(v) => updateValue("escalationShare", v / 100)} />
                    <NumberInput label="Blocked share" value={valueInputs.blockedShare * 100} suffix="%" step={1} onChange={(v) => updateValue("blockedShare", v / 100)} />
                    <NumberInput label="Escalation review" value={valueInputs.escalationMinutes} suffix="min" step={0.5} onChange={(v) => updateValue("escalationMinutes", v)} />
                    <NumberInput label="Security review" value={valueInputs.securityMinutes} suffix="min" step={0.5} onChange={(v) => updateValue("securityMinutes", v)} />
                  </>
                )}
                {calcSection === "costs" && (
                  <>
                    <NumberInput label="Screening / email" value={valueInputs.screeningCostPerEmail} suffix="€" step={0.01} onChange={(v) => updateValue("screeningCostPerEmail", v)} />
                    <NumberInput label="Extraction / email" value={valueInputs.extractionCostPerEmail} suffix="€" step={0.01} onChange={(v) => updateValue("extractionCostPerEmail", v)} />
                    <NumberInput label="Infrastructure / email" value={valueInputs.infrastructureCostPerEmail} suffix="€" step={0.01} onChange={(v) => updateValue("infrastructureCostPerEmail", v)} />
                    <NumberInput label="Fixed annual platform" value={valueInputs.fixedAnnualCost} suffix="€" step={1000} onChange={(v) => updateValue("fixedAnnualCost", v)} />
                    <NumberInput label="One-time implementation" value={valueInputs.oneTimeCost} suffix="€" step={1000} onChange={(v) => updateValue("oneTimeCost", v)} />
                    <NumberInput label="Ramp period" value={valueInputs.rampMonths} suffix="months" step={1} onChange={(v) => updateValue("rampMonths", v)} />
                    <label className="cash-toggle">
                      <input
                        type="checkbox"
                        checked={valueInputs.cashRealizationShare !== null}
                        onChange={(event) => updateValue("cashRealizationShare", event.target.checked ? 0.5 : null)}
                      />
                      Supply cash-realization input
                    </label>
                    {valueInputs.cashRealizationShare !== null && (
                      <NumberInput label="Cash realization" value={valueInputs.cashRealizationShare * 100} suffix="%" step={5} onChange={(v) => updateValue("cashRealizationShare", v / 100)} />
                    )}
                  </>
                )}
              </div>
              <div className="calculator-provenance">
                <Provenance kind={valueDirty ? "User input" : "Illustrative input"} />
                <button onClick={() => { setValueInputs(initialValueInputs); setValueDirty(false); }}>Reset</button>
              </div>
            </div>
            <div className="calculator-results" aria-live="polite">
              {!valueOutputs.valid ? (
                <div className="calc-error">
                  <strong>Inputs need attention</strong>
                  <p>{valueOutputs.errors[0]}</p>
                </div>
              ) : (
                <>
                  <div className="result-primary">
                    <Metric label="Capacity released" value={hours(valueOutputs.capacityHoursReleased)} />
                    <Metric label="Cost-equivalent benefit" value={euro(valueOutputs.costEquivalentBenefit)} />
                  </div>
                  <div className="result-secondary">
                    <Metric label="Current labor" value={euro(valueOutputs.currentLaborCost)} />
                    <Metric label="Future review labor" value={euro(valueOutputs.futureLaborCost)} />
                    <Metric label="Variable AI" value={euro(valueOutputs.variableAiCost)} />
                    <Metric label="Fixed operating" value={euro(valueOutputs.fixedOperatingCost)} />
                  </div>
                  <div className="cash-result">
                    <span>Cash case</span>
                    {valueOutputs.cashBenefit === null ? (
                      <p>Supply a realization percentage to calculate cash benefit, net benefit, and payback.</p>
                    ) : (
                      <p><strong>{euro(valueOutputs.netAnnualBenefit)}</strong> net annual · <strong>{valueOutputs.paybackMonths?.toFixed(1)} months</strong> payback</p>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        );
      case 12:
        return (
          <div className="pilot-layout">
            <div className="pilot-controls">
              <label>
                <span>Target false auto-approval rate</span>
                <output>{targetRatePct.toFixed(1)}%</output>
                <input type="range" min="0.2" max="5" step="0.1" value={targetRatePct} onChange={(event) => setTargetRatePct(event.target.valueAsNumber)} />
              </label>
              <label>
                <span>One-sided confidence</span>
                <output>{confidencePct}%</output>
                <input type="range" min="80" max="99" step="1" value={confidencePct} onChange={(event) => setConfidencePct(event.target.valueAsNumber)} />
              </label>
              <Provenance kind={targetRatePct === 1 && confidencePct === 95 ? "Task-stated" : "User input"} />
            </div>
            <div className="pilot-result" aria-live="polite">
              <span>Required reviewed auto-eligible orders</span>
              <strong>{pilotSample}</strong>
              <p>with <b>zero false auto-approvals</b></p>
              <small>One-sided upper bound: {(pilotUpperBound * 100).toFixed(2)}%</small>
            </div>
            <div className="pilot-phases">
              <div><span>01</span><strong>Shadow</strong><p>Compare decisions; no writes</p></div>
              <div><span>02</span><strong>Review</strong><p>Label every auto-eligible result</p></div>
              <div><span>03</span><strong>Promote</strong><p>Only if the agreed gate holds</p></div>
            </div>
          </div>
        );
      case 13:
        return (
          <div className="closing-layout">
            <div className="recommendation-block">
              <span>My recommendation</span>
              <BigStatement>Shadow first. Keep the write path narrow. Let evidence earn autonomy.</BigStatement>
            </div>
            <div className="decision-list">
              <p><span>01</span><strong>Who owns the shadow pilot and review queue?</strong></p>
              <p><span>02</span><strong>Which risk gate authorizes the first bounded write path?</strong></p>
              <p><span>03</span><strong>Which operating baseline makes the value case real?</strong></p>
            </div>
            <div className="closing-signoff">Tanvi Gunjal · OMMAX Part B</div>
          </div>
        );
      default:
        return null;
    }
  })();

  return (
    <main
      className="stage"
      onPointerDown={(event) => {
        if (event.pointerType === "touch") touchStart.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerUp={(event) => {
        if (event.pointerType !== "touch" || !touchStart.current) return;
        const target = event.target as HTMLElement;
        if (target.closest("input, button, a")) return;
        const deltaX = event.clientX - touchStart.current.x;
        const deltaY = event.clientY - touchStart.current.y;
        touchStart.current = null;
        if (Math.abs(deltaX) > 50 && Math.abs(deltaX) > Math.abs(deltaY))
          navigate(active + (deltaX < 0 ? 1 : -1));
      }}
    >
      <section className={`deck scene-theme-${active + 1}`} aria-label="Interactive presentation">
        <article className="scene" aria-labelledby={`title-${active}`} key={active}>
          {active > 0 && (
            <header className="scene-header">
              <p>{scenes[active].eyebrow}</p>
              <h2 id={`title-${active}`}>{scenes[active].title}</h2>
            </header>
          )}
          <div className="scene-content">{sceneContent}</div>
        </article>

        <nav className="deck-nav" aria-label="Presentation navigation">
          <button onClick={() => navigate(active - 1)} disabled={active === 0} aria-label="Previous scene">←</button>
          <div className="nav-progress" aria-label={`Scene ${active + 1} of ${scenes.length}`}>
            <span style={{ width: `${((active + 1) / scenes.length) * 100}%` }} />
          </div>
          <span className="scene-count">{String(active + 1).padStart(2, "0")} / {scenes.length}</span>
          <button onClick={() => setAppendixOpen(true)}>Appendix</button>
          <button onClick={() => void toggleFullscreen()}>Fullscreen</button>
          <button onClick={() => navigate(active + 1)} disabled={active === scenes.length - 1} aria-label="Next scene">→</button>
        </nav>
      </section>

      {overviewOpen && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Scene overview">
          <div className="overlay-panel overview-panel">
            <div className="overlay-head"><h2>Fourteen-scene narrative</h2><button onClick={() => setOverviewOpen(false)}>Close</button></div>
            <div className="overview-grid">
              {scenes.map((scene, index) => (
                <button key={scene.id} className={index === active ? "current" : ""} onClick={() => { navigate(index); setOverviewOpen(false); }}>
                  <span>{String(index + 1).padStart(2, "0")}</span><strong>{scene.title}</strong>
                </button>
              ))}
            </div>
            <p className="overview-time">Rehearsal: {Math.floor(totalRehearsalSeconds / 60)} min {totalRehearsalSeconds % 60} sec · Press O to toggle</p>
          </div>
        </div>
      )}

      {notesOpen && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Presenter notes">
          <aside className="overlay-panel notes-panel">
            <div className="overlay-head"><div><span>Scene {active + 1} · {scenes[active].timing}</span><h2>Presenter notes</h2></div><button onClick={() => setNotesOpen(false)}>Close</button></div>
            <h3>{scenes[active].title}</h3>
            {scenes[active].notes.map((note) => <p key={note}>{note}</p>)}
            <div className="sources-block"><strong>[Sources]</strong>{scenes[active].sources.map((source) => <span key={source}>{source}</span>)}</div>
            <p className="notes-shortcut">Press N to toggle · Arrow keys still navigate</p>
          </aside>
        </div>
      )}

      {appendixOpen && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Appendix">
          <div className="overlay-panel appendix-panel">
            <div className="overlay-head"><h2>Appendix</h2><button onClick={() => setAppendixOpen(false)}>Close</button></div>
            <div className="appendix-tabs" role="tablist" aria-label="Appendix sections">
              {(["deployment", "validation", "monitoring", "compliance", "method"] as AppendixSection[]).map((section) => (
                <button key={section} role="tab" aria-selected={appendixSection === section} className={appendixSection === section ? "active" : ""} onClick={() => setAppendixSection(section)}>{section}</button>
              ))}
            </div>
            <AppendixContent section={appendixSection} />
            <p className="appendix-evidence">Evidence source commit · {evidence.sourceCommitSha.slice(0, 8)} · {evidence.mode} · <Provenance kind="Task-stated" /></p>
          </div>
        </div>
      )}
    </main>
  );
}

function AppendixContent({ section }: { section: AppendixSection }) {
  if (section === "deployment") {
    return (
      <div className="appendix-content appendix-flow">
        <p>Mailbox webhook</p><span>→</span><p>Durable queue</p><span>→</span><p>Quarantine + parser</p><span>→</span><p>Workflow workers</p><span>→</span><p>Scoped LLM / ERP</p><span>→</span><p>Transactional state</p><span>→</span><p>Review UI + observability</p>
        <div className="appendix-note">Cloud-neutral target topology. Provider, SLA, retention, and ERP semantics remain explicit startup decisions.</div>
      </div>
    );
  }
  if (section === "validation") {
    return (
      <div className="appendix-content appendix-columns">
        <div><h3>Identity + scope</h3><p>Verified webhook and sender verdicts</p><p>Exactly one resolved customer and order</p><p>English or German auto-write only</p></div>
        <div><h3>Commercial</h3><p>Known SKU · positive whole quantity</p><p>ERP EUR price authoritative</p><p>Quoted price within 2%</p><p>Aggregate stock sufficient</p></div>
        <div><h3>Control</h3><p>No unresolved ambiguity or conflict</p><p>Current catalog version</p><p>Value ≤ €50,000 demo cap</p><p>Sandbox auto mode · kill switch clear</p></div>
        <Provenance kind="Task-stated" />
      </div>
    );
  }
  if (section === "monitoring") {
    return (
      <div className="appendix-content appendix-columns">
        <div><h3>Safety</h3><p>False auto-approval rate</p><p>Blocked-path write count</p><p>Idempotency conflicts</p><p>Audit-chain verification</p></div>
        <div><h3>Operations</h3><p>Queue age and review SLA</p><p>Parser / provider failure rate</p><p>Unknown POST reconciliation</p><p>Kill-switch state</p></div>
        <div><h3>Value</h3><p>Eligible vs escalated share</p><p>Review minutes</p><p>Released capacity</p><p>Cash realization, if evidenced</p></div>
      </div>
    );
  }
  if (section === "compliance") {
    return (
      <div className="appendix-content appendix-columns">
        <div><h3>Before real email</h3><p>Purpose, lawful basis, minimization</p><p>Retention, deletion, rights handling</p><p>DPIA and Article 22 assessment</p></div>
        <div><h3>Provider posture</h3><p>DPA, region, retention and training terms</p><p>Subprocessors and transfer mechanism</p><p>Encryption, secrets, RBAC, incidents</p></div>
        <div><h3>AI governance</h3><p>Inventory and classification</p><p>Human oversight and literacy</p><p>No unsupported compliance claim</p></div>
        <div className="source-links"><Provenance kind="External source" /><a href="https://commission.europa.eu/law/law-topic/data-protection/rules-business-and-organisations/principles-gdpr/overview-principles/what-data-can-we-process-and-under-which-conditions_en" target="_blank" rel="noreferrer">EU GDPR principles</a><a href="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex%3A32024R1689" target="_blank" rel="noreferrer">Regulation (EU) 2024/1689</a></div>
      </div>
    );
  }
  return (
    <div className="appendix-content method-copy">
      <h3>Value model</h3>
      <p>Current hours = volume × (handling minutes + rework rate × rework minutes) ÷ 60.</p>
      <p>Future review hours = volume × (escalation share × escalation minutes + blocked share × security minutes) ÷ 60.</p>
      <p>Capacity value = released hours × loaded labor cost. Cash benefit applies only the supplied realization share. Net annual benefit deducts variable AI and fixed operating cost. Payback adds half the stated ramp period to steady-state payback.</p>
      <h3>Pilot sample</h3>
      <p>For zero observed defects, n = ceil[log(1 − confidence) ÷ log(1 − target defect rate)]. The selected 1% / 95% gate requires 299 reviewed auto-eligible orders.</p>
      <Provenance kind="Task-stated" />
    </div>
  );
}

export default App;
