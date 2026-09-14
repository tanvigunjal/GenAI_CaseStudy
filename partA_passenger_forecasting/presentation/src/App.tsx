import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { scenes, totalRehearsalSeconds } from "./data/scenes";
import {
  baselineLiftPercent,
  chartPoints,
  errorHotspots,
  navigateScene,
  parseSceneHash,
  selectedStation,
  sortedAblations,
} from "./lib/presentation";
import type { CandidateResult, PresentationEvidence, StationMetric } from "./types";

const evidence: PresentationEvidence = __PRESENTATION_EVIDENCE__;
const isVerified = evidence.status === "verified" && evidence.evaluation.status === "verified";

type AppendixSection =
  | "stations"
  | "folds"
  | "features"
  | "quality"
  | "tabpfn"
  | "monitoring"
  | "azure";

const appendixSections: Array<{ id: AppendixSection; label: string }> = [
  { id: "stations", label: "Station metrics" },
  { id: "folds", label: "Rolling folds" },
  { id: "features", label: "Features" },
  { id: "quality", label: "Data quality" },
  { id: "tabpfn", label: "TabPFN" },
  { id: "monitoring", label: "Failure modes" },
  { id: "azure", label: "Azure & governance" },
];

const number = (value: number | null, digits = 1) =>
  value === null || !Number.isFinite(value)
    ? "Pending"
    : new Intl.NumberFormat("en-DE", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      }).format(value);

const integer = (value: number) =>
  new Intl.NumberFormat("en-DE", { maximumFractionDigits: 0 }).format(value);

function Provenance({ children }: { children: React.ReactNode }) {
  return <span className="provenance">{children}</span>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function PendingEvidence({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`pending-evidence ${compact ? "compact" : ""}`}>
      <span>NON-FINAL EVIDENCE STATE</span>
      <strong>I am waiting for the verified remote evaluation manifest.</strong>
      {!compact && (
        <p>
          The schema, transformations, and interactions are live. Model scores and station traces
          stay blank until the evidence verifier accepts all required fields and hashes.
        </p>
      )}
    </div>
  );
}

function CandidateRow({ candidate }: { candidate: CandidateResult }) {
  return (
    <div className={`candidate-row ${candidate.selected ? "selected" : ""}`}>
      <span className="candidate-role">{candidate.role}</span>
      <strong>{candidate.model.replaceAll("_", " ")}</strong>
      <span>{candidate.meanValidationRmse === null ? "Fixed" : number(candidate.meanValidationRmse)}</span>
      <i>{candidate.selected ? "Selected" : candidate.role === "fixed baseline" ? "Reference" : isVerified ? "Compared" : "Awaiting run"}</i>
    </div>
  );
}

function ForecastChart({ station }: { station: StationMetric | null }) {
  const width = 680;
  const height = 250;
  if (!station || station.series.length === 0) return <PendingEvidence compact />;
  const actual = chartPoints(station.series, "actual", width, height, 24);
  const forecast = chartPoints(station.series, "forecast", width, height, 24);
  return (
    <figure className="forecast-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Forecast versus actual for ${station.stationLabel}`}>
        {[0.25, 0.5, 0.75].map((position) => (
          <line key={position} x1="24" x2={width - 24} y1={height * position} y2={height * position} className="grid-line" />
        ))}
        <polyline points={forecast} className="forecast-line" />
        <polyline points={actual} className="actual-line" />
      </svg>
      <figcaption>
        <span><i className="legend forecast" />Forecast</span>
        <span><i className="legend actual" />Actual</span>
        <span>{station.series.length} hourly records</span>
      </figcaption>
    </figure>
  );
}

function App() {
  const [active, setActive] = useState(() => parseSceneHash(window.location.hash, scenes.length) ?? 0);
  const [overviewOpen, setOverviewOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [appendixOpen, setAppendixOpen] = useState(false);
  const [appendixSection, setAppendixSection] = useState<AppendixSection>("stations");
  const [stationId, setStationId] = useState<string | null>(
    evidence.evaluation.stationMetrics[0]?.stationId ?? null,
  );
  const [systemMessage, setSystemMessage] = useState("");
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const overlayRef = useRef<HTMLDivElement | null>(null);

  const station = useMemo(
    () => selectedStation(evidence.evaluation.stationMetrics, stationId),
    [stationId],
  );
  const lift = useMemo(
    () => baselineLiftPercent(evidence.evaluation.officialRmse, evidence.evaluation.seasonalNaiveRmse),
    [],
  );
  const hotspots = useMemo(() => errorHotspots(station?.series ?? []), [station]);
  const ablations = useMemo(() => sortedAblations(evidence.evaluation.ablations), []);

  const navigate = useCallback((intent: Parameters<typeof navigateScene>[1]) => {
    setActive((current) => navigateScene(current, intent, scenes.length));
  }, []);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (!document.fullscreenEnabled || !document.documentElement.requestFullscreen) {
        setSystemMessage("Fullscreen is unavailable in this browser. The presentation remains fitted to the window.");
        return;
      }
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      setSystemMessage("Fullscreen was declined. The presentation remains fitted to the window.");
    }
  }, []);

  useEffect(() => {
    const nextHash = `#${scenes[active].id}`;
    if (window.location.hash !== nextHash) window.history.replaceState(null, "", nextHash);
    document.title = `${active + 1}/${scenes.length} · ${scenes[active].title}`;
  }, [active]);

  useEffect(() => {
    const onHashChange = () => {
      const scene = parseSceneHash(window.location.hash, scenes.length);
      if (scene !== null) navigate({ scene });
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [navigate]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select")) return;
      if (event.key === "ArrowRight" || event.key === "PageDown") navigate("next");
      else if (event.key === "ArrowLeft" || event.key === "PageUp") navigate("previous");
      else if (event.key === "Home") navigate("first");
      else if (event.key === "End") navigate("last");
      else if (event.key.toLowerCase() === "o") setOverviewOpen((value) => !value);
      else if (event.key.toLowerCase() === "n") setNotesOpen((value) => !value);
      else if (event.key.toLowerCase() === "a") setAppendixOpen((value) => !value);
      else if (event.key.toLowerCase() === "f") void toggleFullscreen();
      else if (event.key === "Escape") {
        setOverviewOpen(false);
        setNotesOpen(false);
        setAppendixOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, toggleFullscreen]);

  useEffect(() => {
    if (overviewOpen || notesOpen || appendixOpen) {
      window.requestAnimationFrame(() => overlayRef.current?.focus());
    }
  }, [overviewOpen, notesOpen, appendixOpen]);

  const sceneContent = (() => {
    switch (active) {
      case 0:
        return (
          <div className="cover-layout">
            <div className="cover-rule" aria-hidden="true" />
            <p className="cover-kicker">Passenger forecasting · Munich operations</p>
            <h1>{scenes[0].title}</h1>
            <div className="cover-footer">
              <p><strong>Tanvi Gunjal</strong><span>Part A</span></p>
              <p><span>7-day horizon</span><strong>10 stations · 420 decisions</strong></p>
            </div>
          </div>
        );
      case 1:
        return (
          <div className="contract-layout">
            <div className="contract-equation" aria-label="Forecast decision contract">
              <div><strong>{evidence.fixedFacts.stationCount}</strong><span>stations</span></div>
              <b>×</b>
              <div><strong>{evidence.fixedFacts.serviceHours.length}</strong><span>hours</span></div>
              <b>×</b>
              <div><strong>{evidence.fixedFacts.horizonDays}</strong><span>local dates</span></div>
              <b>=</b>
              <div className="contract-total"><strong>{evidence.fixedFacts.testRows}</strong><span>forecast records</span></div>
            </div>
            <div className="origin-line">
              <span>Information boundary</span>
              <strong>{evidence.fixedFacts.evaluationOrigin.slice(0, 10)}</strong>
              <i aria-hidden="true" />
            </div>
            <div className="availability-grid">
              <div className="allowed"><span>Allowed at target time</span><strong>Forecast weather</strong><p>Task-provided perfect 10-day proxy</p></div>
              <div className="lagged"><span>Lagged behind target</span><strong>Passenger + traffic history</strong><p>7/14/28-day cutoff-safe lags</p></div>
              <div className="forbidden"><span>Forbidden</span><strong>Held-out traffic + targets</strong><p>Never available to selection or fitting</p></div>
            </div>
          </div>
        );
      case 2:
        return (
          <div className="data-layout">
            <div className="data-scale">
              <Metric label="Target coverage" value={`${evidence.fixedFacts.targetStartDate.slice(0, 4)}–${evidence.fixedFacts.targetEndDate.slice(0, 4)}`} detail={`${integer(evidence.fixedFacts.targetRows)} target rows`} />
              <Metric label="Unmapped traffic" value={`${evidence.fixedFacts.sensorCount} sensors`} detail="Coordinates only" />
              <Metric label="Missing targets" value={integer(evidence.dataQuality.nullTargets)} detail={`${evidence.fixedFacts.missingTestLabels} in holdout`} />
            </div>
            <div className="feature-policy">
              <div className="feature-orbit">
                <span className="feature-core">origin<br />cutoff</span>
                <span className="feature-node calendar">calendar</span>
                <span className="feature-node station">station</span>
                <span className="feature-node passenger">passenger lags</span>
                <span className="feature-node weather">forecast weather</span>
                <span className="feature-node traffic">lagged traffic</span>
              </div>
              <div className="mapping-copy">
                <span>Traffic policy</span>
                <strong>Nearest stable sensor, not a timestamp-only join.</strong>
                <p>{evidence.dataQuality.mappingPolicy}</p>
              </div>
            </div>
            <p className="capacity-note">{evidence.dataQuality.capacityPolicy}</p>
          </div>
        );
      case 3:
        return (
          <div className="model-layout">
            <div className="candidate-list" aria-label="Model candidates">
              <div className="candidate-head"><span>Role</span><strong>Candidate</strong><span>CV RMSE</span><i>Status</i></div>
              {evidence.evaluation.candidates.map((candidate) => <CandidateRow key={candidate.model} candidate={candidate} />)}
            </div>
            <div className="selection-rule">
              <span>Locked selection order</span>
              <ol>
                <li><b>01</b>Mean rolling RMSE</li>
                <li><b>02</b>Worst-station RMSE within 1%</li>
                <li><b>03</b>Latency</li>
                <li><b>04</b>Artifact size</li>
              </ol>
            </div>
            <div className="tabpfn-gate">
              <span>TabPFN v2</span>
              <strong>limited compute</strong>
              <p>{evidence.tabpfn.labeledTrainingRows.toLocaleString("en-DE")} labeled rows · {evidence.tabpfn.host}</p>
            </div>
          </div>
        );
      case 4:
        return (
          <div className="results-layout">
            <div className="result-hero">
              <Metric label="Official overall RMSE" value={number(evidence.evaluation.officialRmse)} detail="Directly across observed holdout labels" />
              <Metric label="Lift vs seasonal naive" value={lift === null ? "Pending" : `${number(lift)}%`} detail="Derived from manifest values" />
              <Metric label="Score coverage" value={`${evidence.fixedFacts.scoreableRows}/${evidence.fixedFacts.testRows}`} detail={`${evidence.fixedFacts.missingTestLabels} labels unavailable`} />
            </div>
            {isVerified ? (
              <div className="station-table result-table">
                <div className="table-head"><span>Station</span><span>n</span><span>RMSE</span><span>Bias</span></div>
                {evidence.evaluation.stationMetrics.map((item) => (
                  <div key={item.stationId}><strong>{item.stationLabel}</strong><span>{item.sampleCount}</span><span>{number(item.rmse)}</span><span>{number(item.bias)}</span></div>
                ))}
              </div>
            ) : <PendingEvidence />}
            <p className="result-foot">Predictions exist for all 420 decisions; RMSE excludes only missing labels.</p>
          </div>
        );
      case 5:
        return (
          <div className="operations-layout">
            <div className="station-tabs" role="tablist" aria-label="Station evidence selector">
              {evidence.evaluation.stationMetrics.length ? evidence.evaluation.stationMetrics.map((item) => (
                <button key={item.stationId} role="tab" aria-selected={station?.stationId === item.stationId} className={station?.stationId === item.stationId ? "active" : ""} onClick={() => setStationId(item.stationId)}>
                  {item.stationLabel}
                </button>
              )) : <button disabled>No verified station series</button>}
            </div>
            <div className="operations-main">
              <ForecastChart station={station} />
              <div className="station-diagnostics">
                <div className="diagnostic-metrics">
                  <Metric label="Station RMSE" value={station ? number(station.rmse) : "Pending"} />
                  <Metric label="Bias" value={station ? number(station.bias) : "Pending"} />
                </div>
                <div className="hotspots">
                  <span>Error hotspots by hour</span>
                  {hotspots.length ? hotspots.map((hotspot) => (
                    <div key={hotspot.hour}><b>{String(hotspot.hour).padStart(2, "0")}:00</b><i style={{ width: `${Math.min(100, hotspot.mae)}%` }} /><span>{number(hotspot.mae)} MAE</span></div>
                  )) : <p>Waiting for verified station traces.</p>}
                </div>
              </div>
            </div>
            <div className="ablation-strip">
              <span>Ablation delta RMSE · predictive, not causal</span>
              {ablations.length ? ablations.slice(0, 5).map((item) => (
                <div key={item.featureFamily}><strong>{item.featureFamily}</strong><b>+{number(item.deltaRmse)}</b></div>
              )) : <p>Verified ablation evidence pending</p>}
            </div>
          </div>
        );
      case 6:
        return (
          <div className="recommendation-layout">
            <div className="recommendation-copy">
              <span>My recommendation</span>
              <strong>Run hourly in shadow mode. Let evidence earn promotion.</strong>
              <p>Planning support first; no safety control, staffing automation, or invented ROI.</p>
            </div>
            <div className="pilot-path">
              <div><span>01</span><strong>Shadow</strong><p>Compare champion, baseline, coverage, bias</p></div>
              <div><span>02</span><strong>Own</strong><p>Name alert, data, model, and operations owners</p></div>
              <div><span>03</span><strong>Promote</strong><p>Human decision after agreed service gates</p></div>
            </div>
            <div className="decision-asks">
              <p><span>Decision 1</span><strong>Which stations and resource decisions enter the pilot?</strong></p>
              <p><span>Decision 2</span><strong>Which freshness, bias, and RMSE thresholds trigger fallback?</strong></p>
              <p><span>Decision 3</span><strong>Who authorizes model promotion after shadow evidence?</strong></p>
            </div>
            <p className="atomic-policy">{evidence.operations.publishPolicy}</p>
          </div>
        );
      default:
        return null;
    }
  })();

  const appendixContent = (() => {
    switch (appendixSection) {
      case "stations":
        return isVerified ? (
          <div className="station-table appendix-table">
            <div className="table-head"><span>Station</span><span>n</span><span>RMSE</span><span>Bias</span></div>
            {evidence.evaluation.stationMetrics.map((item) => <div key={item.stationId}><strong>{item.stationLabel}</strong><span>{item.sampleCount}</span><span>{number(item.rmse)}</span><span>{number(item.bias)}</span></div>)}
          </div>
        ) : <PendingEvidence />;
      case "folds":
        return (
          <div className="appendix-split">
            <div>
              <h3>Expanding rolling origins</h3>
              {evidence.evaluation.folds.map((fold) => <p key={fold.fold}><strong>Fold {fold.fold}</strong><span>{fold.validationStart} → {fold.validationEnd}</span><b>{number(fold.rmse)} RMSE</b></p>)}
            </div>
            <div>
              <h3>Champion parameters</h3>
              {isVerified ? Object.entries(evidence.evaluation.candidates.find((candidate) => candidate.selected)?.parameters ?? {}).map(([key, value]) => <p key={key}><strong>{key}</strong><span>{String(value)}</span></p>) : <PendingEvidence compact />}
            </div>
          </div>
        );
      case "features":
        return (
          <div className="appendix-split">
            <div><h3>Feature families</h3><p>Calendar and local service time</p><p>Station geography and metadata</p><p>Passenger lags and same-hour aggregates</p><p>Forecast weather</p><p>Lagged citywide and nearest-sensor traffic</p></div>
            <div><h3>Ablation evidence</h3>{ablations.length ? ablations.map((item) => <p key={item.featureFamily}><strong>{item.featureFamily}</strong><span>RMSE without {number(item.rmseWithout)}</span><b>Δ {item.deltaRmse >= 0 ? "+" : ""}{number(item.deltaRmse)}</b></p>) : <PendingEvidence compact />}<small>These comparisons are predictive diagnostics; they do not establish causal effects.</small></div>
          </div>
        );
      case "quality":
        return (
          <div className="quality-appendix">
            <div><span>Target grain</span><strong>{integer(evidence.fixedFacts.targetRows)} rows</strong><p>Global last-seven-date split; never physical tail order.</p></div>
            <div><span>Missing labels</span><strong>{evidence.dataQuality.nullTargets} total</strong><p>{evidence.fixedFacts.missingTestLabels} in holdout; never imputed.</p></div>
            <div><span>Traffic mapping</span><strong>{evidence.fixedFacts.sensorCount} sensors · {evidence.dataQuality.trafficMapping.length || evidence.fixedFacts.stationCount} assignments</strong><p>{evidence.dataQuality.trafficMapping.length ? evidence.dataQuality.trafficMapping.map((item) => `S${item.stationId}→${item.sensorId} (${item.distanceKm.toFixed(2)} km)`).join(" · ") : evidence.dataQuality.mappingPolicy}</p></div>
            <div><span>Known limitation</span><strong>No ingestion timestamps</strong><p>{evidence.dataQuality.ingestionTimestampLimitation}</p></div>
          </div>
        );
      case "tabpfn":
        return (
          <div className="tabpfn-appendix">
            <div className="disposition"><span>Disposition</span><strong>limited compute</strong><p>{evidence.tabpfn.labeledTrainingRows.toLocaleString("en-DE")} labeled training rows · {evidence.tabpfn.host}</p></div>
            <ol>{evidence.tabpfn.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ol>
            <div className="later-gate"><span>Later benchmark gate</span><p>{evidence.tabpfn.laterGate}</p></div>
          </div>
        );
      case "monitoring":
        return (
          <div className="failure-table">
            <div className="table-head"><span>Trigger</span><span>Action</span><span>State</span></div>
            {evidence.operations.failureModes.map((mode) => <div key={mode.trigger}><strong>{mode.trigger}</strong><span>{mode.action}</span><b className={`state-${mode.state}`}>{mode.state}</b></div>)}
            <p>Monitor: {evidence.operations.monitoring.join(" · ")}</p>
          </div>
        );
      case "azure":
        return (
          <div className="azure-appendix">
            <div className="azure-flow">{evidence.architecture.map((item, index) => <div key={item}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item}</strong>{index < evidence.architecture.length - 1 && <i>→</i>}</div>)}</div>
            <div className="governance-list">{evidence.governance.map((item) => <p key={item}>{item}</p>)}</div>
          </div>
        );
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
        if (target.closest("input, button, a, select")) return;
        const deltaX = event.clientX - touchStart.current.x;
        const deltaY = event.clientY - touchStart.current.y;
        touchStart.current = null;
        if (Math.abs(deltaX) > 50 && Math.abs(deltaX) > Math.abs(deltaY)) navigate(deltaX < 0 ? "next" : "previous");
      }}
    >
      <section className={`deck scene-theme-${active + 1}`} aria-label="Interactive presentation">
        <article className="scene" aria-labelledby={`title-${active}`} key={active}>
          {active > 0 && (
            <header className="scene-header">
              <div>
                {scenes[active].eyebrow.toLowerCase().endsWith(scenes[active].title.toLowerCase()) ? (
                  <h2 id={`title-${active}`} className="scene-header-merged">
                    <span className="scene-header-index">{scenes[active].eyebrow.split(" · ")[0]}</span>
                    {" · "}
                    {scenes[active].title}
                  </h2>
                ) : (
                  <>
                    <p>{scenes[active].eyebrow}</p>
                    <h2 id={`title-${active}`}>{scenes[active].title}</h2>
                  </>
                )}
              </div>
            </header>
          )}
          <div className="scene-content">{sceneContent}</div>
        </article>
        <nav className="deck-nav" aria-label="Presentation navigation">
          <button onClick={() => navigate("previous")} disabled={active === 0} aria-label="Previous scene">←</button>
          <div className="nav-progress" aria-label={`Scene ${active + 1} of ${scenes.length}`}><span style={{ width: `${((active + 1) / scenes.length) * 100}%` }} /></div>
          <span className="scene-count">{String(active + 1).padStart(2, "0")} / {String(scenes.length).padStart(2, "0")}</span>
          <button onClick={() => setAppendixOpen(true)}>Appendix</button>
          <button onClick={() => void toggleFullscreen()}>Fullscreen</button>
          <button onClick={() => navigate("next")} disabled={active === scenes.length - 1} aria-label="Next scene">→</button>
        </nav>
      </section>

      <p className="sr-only" aria-live="polite">{systemMessage}</p>
      {systemMessage && <button className="system-message" onClick={() => setSystemMessage("")}>{systemMessage}<span>Dismiss</span></button>}

      {overviewOpen && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Scene overview">
          <div className="overlay-panel overview-panel" tabIndex={-1} ref={overlayRef}>
            <div className="overlay-head"><div><span>Eight-minute core story</span><h2>Seven-scene overview</h2></div><button onClick={() => setOverviewOpen(false)}>Close</button></div>
            <div className="overview-grid">{scenes.map((item, index) => <button key={item.id} className={index === active ? "current" : ""} onClick={() => { navigate({ scene: index }); setOverviewOpen(false); }}><span>{String(index + 1).padStart(2, "0")}</span><strong>{item.title}</strong></button>)}</div>
            <p>Rehearsal target: {Math.floor(totalRehearsalSeconds / 60)} minutes · Press O to toggle</p>
          </div>
        </div>
      )}

      {notesOpen && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Presenter notes">
          <aside className="overlay-panel notes-panel" tabIndex={-1} ref={overlayRef}>
            <div className="overlay-head"><div><span>Scene {active + 1} · {scenes[active].timing}</span><h2>Presenter notes</h2></div><button onClick={() => setNotesOpen(false)}>Close</button></div>
            <h3>{scenes[active].title}</h3>
            {scenes[active].notes.map((note) => <p key={note}>{note}</p>)}
            <div className="sources-block"><strong>[Sources]</strong>{scenes[active].sources.map((source) => <span key={source}>{source}</span>)}</div>
            <small>Press N to toggle · Arrow keys still navigate</small>
          </aside>
        </div>
      )}

      {appendixOpen && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Technical appendix">
          <div className="overlay-panel appendix-panel" tabIndex={-1} ref={overlayRef}>
            <div className="overlay-head"><div><span>Technical appendix</span><h2>Evidence, operations, and governance</h2></div><button onClick={() => setAppendixOpen(false)}>Close</button></div>
            <div className="appendix-tabs" role="tablist" aria-label="Appendix sections">{appendixSections.map((section) => <button key={section.id} role="tab" aria-selected={appendixSection === section.id} className={appendixSection === section.id ? "active" : ""} onClick={() => setAppendixSection(section.id)}>{section.label}</button>)}</div>
            <div className="appendix-content">{appendixContent}</div>
            <p className="appendix-footer">Press A to toggle</p>
          </div>
        </div>
      )}
    </main>
  );
}

export default App;
