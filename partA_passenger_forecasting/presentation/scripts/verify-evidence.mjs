import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const presentationDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const evidence = JSON.parse(
  readFileSync(path.join(presentationDir, "public/presentation_evidence.json"), "utf8"),
);

const failures = [];
const fail = (condition, message) => {
  if (!condition) failures.push(message);
};

fail(evidence.schemaVersion === 1, "schemaVersion must be 1");
fail(["verified", "provisional_not_verified"].includes(evidence.status), "invalid status");
fail(typeof evidence.nonFinal === "boolean", "nonFinal must be boolean");
fail(evidence.fixedFacts?.testRows === 420, "testRows must be 420");
fail(evidence.fixedFacts?.scoreableRows === 417, "scoreableRows must be 417");
fail(evidence.fixedFacts?.stationCount === 10, "stationCount must be 10");
fail(evidence.fixedFacts?.sensorCount === 5, "sensorCount must be 5");
fail(Array.isArray(evidence.evaluation?.stationMetrics), "stationMetrics must be an array");
fail(Array.isArray(evidence.evaluation?.candidates), "candidates must be an array");
fail(Array.isArray(evidence.operations?.failureModes), "failureModes must be an array");

if (evidence.status === "verified") {
  fail(evidence.nonFinal === false, "verified evidence cannot be nonFinal");
  fail(typeof evidence.sourceRevision === "string" && evidence.sourceRevision.length >= 7, "verified evidence needs sourceRevision");
  fail(evidence.evaluation?.status === "verified", "verified evidence needs verified evaluation status");
  fail(Number.isFinite(evidence.evaluation?.officialRmse), "verified evidence needs officialRmse");
  fail(Number.isFinite(evidence.evaluation?.seasonalNaiveRmse), "verified evidence needs seasonalNaiveRmse");
  fail(evidence.evaluation?.stationMetrics?.length === 10, "verified evidence needs ten station metrics");
  fail(evidence.evaluation?.stationMetrics?.every((station) => station.series?.length > 0), "verified station metrics need series");
  fail(Object.values(evidence.artifacts ?? {}).every((value) => typeof value === "string" && value.length >= 12), "verified artifact hashes are incomplete");
} else {
  fail(evidence.nonFinal === true, "provisional evidence must be nonFinal");
  fail(evidence.evaluation?.status === "pending_remote_evidence", "provisional evaluation must remain pending");
  fail(evidence.evaluation?.officialRmse === null, "provisional evidence cannot carry officialRmse");
  fail(evidence.evaluation?.stationMetrics?.length === 0, "provisional evidence cannot carry station results");
}

if (failures.length) {
  console.error(`Presentation evidence failed validation:\n- ${failures.join("\n- ")}`);
  process.exit(1);
}

console.log(
  evidence.status === "verified"
    ? `Verified evidence accepted for ${evidence.sourceRevision.slice(0, 8)}.`
    : "Provisional non-final evidence schema accepted; result views will remain visibly pending.",
);
