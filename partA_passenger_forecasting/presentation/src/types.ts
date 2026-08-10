export type EvidenceStatus = "verified" | "provisional_not_verified";

export interface SeriesPoint {
  timestamp: string;
  actual: number | null;
  forecast: number;
  hour: number;
  leadHours: number;
}

export interface StationMetric {
  stationId: string;
  stationLabel: string;
  sampleCount: number;
  rmse: number;
  bias: number;
  series: SeriesPoint[];
}

export interface CandidateResult {
  model: string;
  role: string;
  meanValidationRmse: number | null;
  worstStationRmse: number | null;
  latencyMs: number | null;
  artifactBytes: number | null;
  selected: boolean;
  parameters: Record<string, string | number | boolean>;
}

export interface FoldResult {
  fold: number;
  validationStart: string;
  validationEnd: string;
  rmse: number | null;
  scoreableRows: number | null;
}

export interface AblationResult {
  featureFamily: string;
  rmseWithout: number;
  deltaRmse: number;
}

export interface TrafficMapping {
  stationId: string;
  sensorId: string;
  distanceKm: number;
}

export interface PresentationEvidence {
  schemaVersion: number;
  status: EvidenceStatus;
  nonFinal: boolean;
  generatedAt: string;
  sourceRevision: string | null;
  claimBoundary: string;
  fixedFacts: {
    stationCount: number;
    sensorCount: number;
    targetRows: number;
    targetStartDate: string;
    targetEndDate: string;
    serviceHours: number[];
    horizonDays: number;
    trainRows: number;
    testRows: number;
    scoreableRows: number;
    missingTestLabels: number;
    evaluationOrigin: string;
  };
  datasets: Array<{ name: string; rows: number; columns: number; sha256: string }>;
  evaluation: {
    status: "verified" | "pending_remote_evidence";
    champion: string | null;
    officialRmse: number | null;
    seasonalNaiveRmse: number | null;
    historicalMedianRmse: number | null;
    stationMetrics: StationMetric[];
    folds: FoldResult[];
    candidates: CandidateResult[];
    ablations: AblationResult[];
  };
  dataQuality: {
    nullTargets: number;
    trafficMapping: TrafficMapping[];
    mappingPolicy: string;
    ingestionTimestampLimitation: string;
    capacityPolicy: string;
  };
  tabpfn: {
    disposition: string;
    labeledTrainingRows: number;
    host: string;
    reasons: string[];
    laterGate: string;
  };
  operations: {
    cadence: string;
    recordCount: number;
    fallback: string;
    publishPolicy: string;
    monitoring: string[];
    failureModes: Array<{ trigger: string; action: string; state: string }>;
  };
  architecture: string[];
  governance: string[];
  artifacts: Record<string, string | null>;
  sources: Array<{ label: string; reference: string }>;
}
