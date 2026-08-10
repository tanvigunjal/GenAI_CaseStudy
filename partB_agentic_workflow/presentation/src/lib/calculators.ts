export interface ValueInputs {
  annualVolume: number;
  currentMinutesPerOrder: number;
  loadedLaborCostPerHour: number;
  autoShare: number;
  escalationShare: number;
  blockedShare: number;
  escalationMinutes: number;
  securityMinutes: number;
  screeningCostPerEmail: number;
  extractionCostPerEmail: number;
  infrastructureCostPerEmail: number;
  fixedAnnualCost: number;
  oneTimeCost: number;
  rampMonths: number;
  cashRealizationShare: number | null;
  currentReworkShare: number;
  currentReworkMinutes: number;
}

export interface ValueOutputs {
  valid: boolean;
  errors: string[];
  currentAnnualHours: number | null;
  currentLaborCost: number | null;
  futureReviewHours: number | null;
  futureLaborCost: number | null;
  variableAiCost: number | null;
  fixedOperatingCost: number | null;
  capacityHoursReleased: number | null;
  costEquivalentBenefit: number | null;
  cashBenefit: number | null;
  netAnnualBenefit: number | null;
  paybackMonths: number | null;
}

const finiteNonNegative = (value: number) =>
  Number.isFinite(value) && value >= 0;

export function calculateValue(inputs: ValueInputs): ValueOutputs {
  const errors: string[] = [];
  const required: Array<[string, number]> = [
    ["Annual volume", inputs.annualVolume],
    ["Current minutes", inputs.currentMinutesPerOrder],
    ["Loaded labor cost", inputs.loadedLaborCostPerHour],
    ["Auto share", inputs.autoShare],
    ["Escalation share", inputs.escalationShare],
    ["Blocked share", inputs.blockedShare],
    ["Escalation minutes", inputs.escalationMinutes],
    ["Security minutes", inputs.securityMinutes],
    ["Screening cost", inputs.screeningCostPerEmail],
    ["Extraction cost", inputs.extractionCostPerEmail],
    ["Infrastructure cost", inputs.infrastructureCostPerEmail],
    ["Fixed annual cost", inputs.fixedAnnualCost],
    ["One-time cost", inputs.oneTimeCost],
    ["Ramp period", inputs.rampMonths],
    ["Current rework share", inputs.currentReworkShare],
    ["Current rework minutes", inputs.currentReworkMinutes],
  ];

  for (const [label, value] of required) {
    if (!finiteNonNegative(value)) errors.push(`${label} must be non-negative.`);
  }
  if (inputs.annualVolume <= 0) errors.push("Annual volume must be positive.");
  if (inputs.currentMinutesPerOrder <= 0)
    errors.push("Current minutes must be positive.");
  if (inputs.loadedLaborCostPerHour <= 0)
    errors.push("Loaded labor cost must be positive.");
  if (inputs.rampMonths > 24) errors.push("Ramp period cannot exceed 24 months.");

  const shares = inputs.autoShare + inputs.escalationShare + inputs.blockedShare;
  if (Math.abs(shares - 1) > 0.0001)
    errors.push("Auto, escalation, and blocked shares must total 100%.");
  if (
    inputs.autoShare > 1 ||
    inputs.escalationShare > 1 ||
    inputs.blockedShare > 1 ||
    inputs.currentReworkShare > 1
  ) {
    errors.push("Shares must be between 0% and 100%.");
  }
  if (
    inputs.cashRealizationShare !== null &&
    (!finiteNonNegative(inputs.cashRealizationShare) ||
      inputs.cashRealizationShare > 1)
  ) {
    errors.push("Cash realization must be between 0% and 100%.");
  }

  if (errors.length > 0) {
    return {
      valid: false,
      errors,
      currentAnnualHours: null,
      currentLaborCost: null,
      futureReviewHours: null,
      futureLaborCost: null,
      variableAiCost: null,
      fixedOperatingCost: null,
      capacityHoursReleased: null,
      costEquivalentBenefit: null,
      cashBenefit: null,
      netAnnualBenefit: null,
      paybackMonths: null,
    };
  }

  const currentAnnualHours =
    (inputs.annualVolume *
      (inputs.currentMinutesPerOrder +
        inputs.currentReworkShare * inputs.currentReworkMinutes)) /
    60;
  const currentLaborCost = currentAnnualHours * inputs.loadedLaborCostPerHour;
  const futureReviewHours =
    (inputs.annualVolume *
      (inputs.escalationShare * inputs.escalationMinutes +
        inputs.blockedShare * inputs.securityMinutes)) /
    60;
  const futureLaborCost = futureReviewHours * inputs.loadedLaborCostPerHour;
  const variableAiCost =
    inputs.annualVolume *
    (inputs.screeningCostPerEmail +
      inputs.extractionCostPerEmail +
      inputs.infrastructureCostPerEmail);
  const capacityHoursReleased = Math.max(
    0,
    currentAnnualHours - futureReviewHours,
  );
  const costEquivalentBenefit =
    capacityHoursReleased * inputs.loadedLaborCostPerHour;
  const cashBenefit =
    inputs.cashRealizationShare === null
      ? null
      : costEquivalentBenefit * inputs.cashRealizationShare;
  const netAnnualBenefit =
    cashBenefit === null
      ? null
      : cashBenefit - variableAiCost - inputs.fixedAnnualCost;
  const paybackMonths =
    netAnnualBenefit !== null && netAnnualBenefit > 0
      ? (inputs.oneTimeCost / netAnnualBenefit) * 12 + inputs.rampMonths / 2
      : null;

  return {
    valid: true,
    errors,
    currentAnnualHours,
    currentLaborCost,
    futureReviewHours,
    futureLaborCost,
    variableAiCost,
    fixedOperatingCost: inputs.fixedAnnualCost,
    capacityHoursReleased,
    costEquivalentBenefit,
    cashBenefit,
    netAnnualBenefit,
    paybackMonths,
  };
}

export function requiredZeroDefectSample(
  targetDefectRate: number,
  confidence: number,
): number {
  if (
    !Number.isFinite(targetDefectRate) ||
    !Number.isFinite(confidence) ||
    targetDefectRate <= 0 ||
    targetDefectRate >= 1 ||
    confidence <= 0 ||
    confidence >= 1
  ) {
    throw new RangeError("Rate and confidence must both be between 0 and 1.");
  }
  return Math.ceil(Math.log(1 - confidence) / Math.log(1 - targetDefectRate));
}

export function zeroDefectUpperBound(sampleSize: number, confidence: number): number {
  if (
    !Number.isInteger(sampleSize) ||
    sampleSize <= 0 ||
    !Number.isFinite(confidence) ||
    confidence <= 0 ||
    confidence >= 1
  ) {
    throw new RangeError("Sample size must be positive and confidence in (0, 1). ");
  }
  return 1 - Math.pow(1 - confidence, 1 / sampleSize);
}
