import { describe, expect, it } from "vitest";
import {
  calculateValue,
  requiredZeroDefectSample,
  zeroDefectUpperBound,
  type ValueInputs,
} from "./calculators";

const validInputs: ValueInputs = {
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
  cashRealizationShare: 0.5,
  currentReworkShare: 0.04,
  currentReworkMinutes: 10,
};

describe("value calculator", () => {
  it("computes transparent steady-state outputs", () => {
    const output = calculateValue(validInputs);
    expect(output.valid).toBe(true);
    expect(output.currentAnnualHours).toBeCloseTo(2480);
    expect(output.currentLaborCost).toBeCloseTo(119_040);
    expect(output.futureReviewHours).toBeCloseTo(550);
    expect(output.futureLaborCost).toBeCloseTo(26_400);
    expect(output.variableAiCost).toBeCloseTo(1_920);
    expect(output.capacityHoursReleased).toBeCloseTo(1_930);
    expect(output.costEquivalentBenefit).toBeCloseTo(92_640);
    expect(output.cashBenefit).toBeCloseTo(46_320);
    expect(output.netAnnualBenefit).toBeCloseTo(20_400);
    expect(output.paybackMonths).toBeCloseTo(44.176, 2);
  });

  it("withholds cash and payback when realization is not supplied", () => {
    const output = calculateValue({ ...validInputs, cashRealizationShare: null });
    expect(output.valid).toBe(true);
    expect(output.costEquivalentBenefit).not.toBeNull();
    expect(output.cashBenefit).toBeNull();
    expect(output.netAnnualBenefit).toBeNull();
    expect(output.paybackMonths).toBeNull();
  });

  it("rejects shares that do not total 100%", () => {
    const output = calculateValue({ ...validInputs, autoShare: 0.6 });
    expect(output.valid).toBe(false);
    expect(output.errors.join(" ")).toContain("must total 100%");
  });
});

describe("zero-defect pilot calculator", () => {
  it("requires 299 zero-defect reviews for the selected 1% / 95% gate", () => {
    expect(requiredZeroDefectSample(0.01, 0.95)).toBe(299);
  });

  it("returns a one-sided upper bound below 1% at n=299", () => {
    expect(zeroDefectUpperBound(299, 0.95)).toBeLessThan(0.01);
    expect(zeroDefectUpperBound(298, 0.95)).toBeGreaterThan(0.01);
  });

  it("rejects invalid statistical inputs", () => {
    expect(() => requiredZeroDefectSample(0, 0.95)).toThrow(RangeError);
    expect(() => zeroDefectUpperBound(0, 0.95)).toThrow(RangeError);
  });
});
