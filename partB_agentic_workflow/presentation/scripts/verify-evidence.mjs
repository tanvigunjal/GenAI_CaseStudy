import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const presentationDir = path.resolve(scriptDir, "..");
const evidencePath = path.join(
  presentationDir,
  "public/presentation_evidence.json",
);

const evidence = JSON.parse(readFileSync(evidencePath, "utf8"));
const sourceCommitSha = evidence.sourceCommitSha ?? evidence.commitSha;

if (
  evidence.schemaVersion !== 1 ||
  typeof sourceCommitSha !== "string" ||
  !Array.isArray(evidence.toolAllowlist) ||
  evidence.claims?.coreScenes !== 14 ||
  evidence.claims?.promotionSampleFloor !== 299
) {
  console.error("Presentation evidence does not match the required schema.");
  process.exit(1);
}

console.log(`Evidence verified for ${sourceCommitSha.slice(0, 8)}.`);
