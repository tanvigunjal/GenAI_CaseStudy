import type { AblationResult, SeriesPoint, StationMetric } from "../types";

export type NavigationIntent =
  | "next"
  | "previous"
  | "first"
  | "last"
  | { scene: number };

export const clampSceneIndex = (index: number, count: number) =>
  Math.max(0, Math.min(Math.max(0, count - 1), index));

export function navigateScene(
  current: number,
  intent: NavigationIntent,
  count: number,
): number {
  if (intent === "next") return clampSceneIndex(current + 1, count);
  if (intent === "previous") return clampSceneIndex(current - 1, count);
  if (intent === "first") return 0;
  if (intent === "last") return Math.max(0, count - 1);
  return clampSceneIndex(intent.scene, count);
}

export function parseSceneHash(hash: string, count: number): number | null {
  const match = hash.match(/^#scene-(\d+)$/);
  if (!match) return null;
  const oneBased = Number(match[1]);
  if (!Number.isInteger(oneBased) || oneBased < 1 || oneBased > count) return null;
  return oneBased - 1;
}

export function baselineLiftPercent(
  championRmse: number | null,
  baselineRmse: number | null,
): number | null {
  if (championRmse === null || baselineRmse === null || baselineRmse <= 0) return null;
  return ((baselineRmse - championRmse) / baselineRmse) * 100;
}

export function extent(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [min - 0.5, max + 0.5];
  return [min, max];
}

export function chartPoints(
  series: SeriesPoint[],
  key: "actual" | "forecast",
  width: number,
  height: number,
  padding = 18,
): string {
  const values = series
    .map((point) => point[key])
    .filter((value): value is number => value !== null && Number.isFinite(value));
  if (series.length === 0 || values.length === 0) return "";
  const [minimum, maximum] = extent(
    series.flatMap((point) =>
      [point.actual, point.forecast].filter(
        (value): value is number => value !== null && Number.isFinite(value),
      ),
    ),
  );
  const x = (index: number) =>
    padding + (index / Math.max(1, series.length - 1)) * (width - padding * 2);
  const y = (value: number) =>
    height - padding - ((value - minimum) / (maximum - minimum)) * (height - padding * 2);
  return series
    .map((point, index) => ({ value: point[key], index }))
    .filter((point): point is { value: number; index: number } => point.value !== null)
    .map((point) => `${x(point.index).toFixed(2)},${y(point.value).toFixed(2)}`)
    .join(" ");
}

export function errorHotspots(series: SeriesPoint[]) {
  const groups = new Map<number, number[]>();
  for (const point of series) {
    if (point.actual === null) continue;
    const bucket = groups.get(point.hour) ?? [];
    bucket.push(Math.abs(point.forecast - point.actual));
    groups.set(point.hour, bucket);
  }
  return [...groups.entries()]
    .map(([hour, errors]) => ({
      hour,
      mae: errors.reduce((sum, value) => sum + value, 0) / errors.length,
      count: errors.length,
    }))
    .sort((left, right) => left.hour - right.hour);
}

export function selectedStation(
  stations: StationMetric[],
  selectedId: string | null,
): StationMetric | null {
  return stations.find((station) => station.stationId === selectedId) ?? stations[0] ?? null;
}

export function sortedAblations(ablations: AblationResult[]) {
  return [...ablations].sort((left, right) => right.deltaRmse - left.deltaRmse);
}
