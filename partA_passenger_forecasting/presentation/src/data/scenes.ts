export interface SceneDefinition {
  id: string;
  eyebrow: string;
  title: string;
  timing: string;
  notes: string[];
  sources: string[];
}

export const scenes: SceneDefinition[] = [
  {
    id: "scene-1",
    eyebrow: "OMMAX · Data Science Case Study",
    title: "Classic ML: From Passenger Forecasts To Operational Decisions",
    timing: "00:40",
    notes: [
      "I treated this as an operational decision system, not a leaderboard exercise.",
      "I will show the forecast contract, the leakage controls, the model gate, the evidence, and the pilot decision I recommend.",
    ],
    sources: ["OMMAX case-study PDF, Part A", "Verified evaluation manifest when status=verified"],
  },
  {
    id: "scene-2",
    eyebrow: "01 · Decision Contract",
    title: "Decision Contract",
    timing: "01:05",
    notes: [
      "I predict six service hours across ten stations for seven complete local dates: exactly 420 records.",
      "The origin is a hard information boundary. Weather is an allowed forecast proxy; contemporaneous held-out traffic is not.",
    ],
    sources: ["Presentation evidence: fixedFacts", "TASK_A_IMPLEMENTATION_PLAN.md § Forecast contract"],
  },
  {
    id: "scene-3",
    eyebrow: "02 · Data Reality",
    title: "Data Reality",
    timing: "01:10",
    notes: [
      "Three years and ten stations sound simple until traffic arrives as five coordinate-only sensors and the targets include gaps.",
      "I map each station to a stable nearest sensor, preserve the mapping distance, lag passenger and traffic history, and never treat station capacity as a ceiling.",
    ],
    sources: ["Presentation evidence: datasets, fixedFacts, dataQuality", "OMMAX supplied CSV inventory"],
  },
  {
    id: "scene-4",
    eyebrow: "03 · Model Gate",
    title: "Model Gate",
    timing: "01:10",
    notes: [
      "The portfolio starts with two fixed baselines, then compares LightGBM, CatBoost, and an interpretable Tweedie challenger on three rolling origins.",
      "I did not run a distorted TabPFN v2 subsample: the training set and CPU-only host fail the resource-and-fit gate.",
    ],
    sources: ["Presentation evidence: evaluation.candidates, tabpfn", "TabPFN Nature paper and official repository"],
  },
  {
    id: "scene-5",
    eyebrow: "04 · Performance Results",
    title: "Performance Results",
    timing: "01:20",
    notes: [
      "Every result on this scene is read from the immutable evaluation manifest.",
      "The honest denominator is 417 observed labels from 420 forecasts. If the verified manifest is absent, I show that explicitly rather than inventing a score.",
    ],
    sources: ["Presentation evidence: evaluation, fixedFacts", "Evaluation manifest artifact hashes"],
  },
  {
    id: "scene-6",
    eyebrow: "05 · Station-Level Evidence",
    title: "Station-Level Evidence",
    timing: "01:25",
    notes: [
      "The selector lets me move from the portfolio number to a station trace, bias, hour-level hotspots, and feature-family ablations.",
      "Ablation deltas are predictive diagnostics, not causal claims.",
    ],
    sources: ["Presentation evidence: stationMetrics, series, ablations"],
  },
  {
    id: "scene-7",
    eyebrow: "06 · My Recommendation",
    title: "My Recommendation",
    timing: "01:10",
    notes: [
      "I recommend an hourly shadow pilot with named monitoring owners and a champion-versus-baseline view.",
      "The system publishes only a complete 420-record batch, fails closed on critical contracts, and falls back visibly when optional sources fail.",
      "The decisions I need are pilot ownership, service-level thresholds, and the human promotion gate.",
    ],
    sources: ["Presentation evidence: operations, architecture, governance"],
  },
];

export const totalRehearsalSeconds = 480;
