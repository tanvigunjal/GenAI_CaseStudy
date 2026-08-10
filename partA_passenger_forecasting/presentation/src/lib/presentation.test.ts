import { describe, expect, it } from "vitest";
import {
  baselineLiftPercent,
  chartPoints,
  errorHotspots,
  navigateScene,
  parseSceneHash,
  selectedStation,
  sortedAblations,
} from "./presentation";

describe("navigation state", () => {
  it("clamps sequential and direct navigation", () => {
    expect(navigateScene(0, "previous", 7)).toBe(0);
    expect(navigateScene(0, "next", 7)).toBe(1);
    expect(navigateScene(4, "last", 7)).toBe(6);
    expect(navigateScene(4, { scene: 99 }, 7)).toBe(6);
  });

  it("parses only valid deep links", () => {
    expect(parseSceneHash("#scene-4", 7)).toBe(3);
    expect(parseSceneHash("#scene-0", 7)).toBeNull();
    expect(parseSceneHash("#appendix", 7)).toBeNull();
  });
});

describe("evidence transformations", () => {
  const series = [
    { timestamp: "a", actual: 10, forecast: 12, hour: 7, leadHours: 24 },
    { timestamp: "b", actual: 20, forecast: 19, hour: 7, leadHours: 25 },
    { timestamp: "c", actual: null, forecast: 8, hour: 8, leadHours: 26 },
  ];

  it("derives lift without manual result values", () => {
    expect(baselineLiftPercent(8, 10)).toBeCloseTo(20);
    expect(baselineLiftPercent(null, 10)).toBeNull();
  });

  it("maps verified series into finite SVG points", () => {
    expect(chartPoints(series, "forecast", 320, 160)).toMatch(/^18\.00,/);
    expect(chartPoints([], "forecast", 320, 160)).toBe("");
  });

  it("groups error hotspots by hour and excludes missing labels", () => {
    expect(errorHotspots(series)).toEqual([{ hour: 7, mae: 1.5, count: 2 }]);
  });

  it("selects stations and ranks ablations deterministically", () => {
    const stations = [
      { stationId: "A", stationLabel: "A", sampleCount: 1, rmse: 2, bias: 0, series: [] },
      { stationId: "B", stationLabel: "B", sampleCount: 1, rmse: 1, bias: 0, series: [] },
    ];
    expect(selectedStation(stations, "B")?.stationId).toBe("B");
    expect(selectedStation(stations, "missing")?.stationId).toBe("A");
    expect(
      sortedAblations([
        { featureFamily: "weather", rmseWithout: 2, deltaRmse: 0.2 },
        { featureFamily: "traffic", rmseWithout: 3, deltaRmse: 0.7 },
      ])[0].featureFamily,
    ).toBe("traffic");
  });
});
