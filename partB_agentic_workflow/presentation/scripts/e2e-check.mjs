import { existsSync, readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const presentationDir = path.resolve(scriptDir, "..");
const distDir = path.join(presentationDir, "dist");
const artifactPath = path.join(distDir, "ommax_part_b.html");

if (!existsSync(artifactPath)) {
  throw new Error("Build artifact is missing. Run npm run build first.");
}

const outputs = readdirSync(distDir).filter((name) => !name.startsWith("."));
if (outputs.length !== 1 || outputs[0] !== "ommax_part_b.html") {
  throw new Error(`Expected one self-contained HTML file; found: ${outputs.join(", ")}`);
}

const html = readFileSync(artifactPath, "utf8");
const requiredPatterns = [
  [/^<!doctype html>/i, "HTML5 doctype"],
  [/<html lang="en">/i, "document language"],
  [/<meta charset="UTF-8"\s*\/>/i, "UTF-8 metadata"],
  [/name="viewport"/i, "viewport metadata"],
  [/I built a controlled path from inbox to ERP/i, "opening scene copy"],
  [/three decisions/i, "closing decision copy"],
  [/presenter notes/i, "presenter notes control"],
  [/prefers-reduced-motion/i, "reduced-motion CSS"],
];

for (const [pattern, label] of requiredPatterns) {
  if (!pattern.test(html)) throw new Error(`Artifact is missing ${label}.`);
}

const externalRuntimeAsset =
  /<(?:script|img)[^>]+src=["']https?:\/\//i.test(html) ||
  /<link[^>]+href=["']https?:\/\/[^"']+["'][^>]+rel=["']stylesheet["']/i.test(html);
if (externalRuntimeAsset) {
  throw new Error("Artifact contains an external runtime asset.");
}

if (/src=["'][^"']+\.js["']/i.test(html) || /href=["'][^"']+\.css["']/i.test(html)) {
  throw new Error("Artifact still references separate JavaScript or CSS files.");
}

if (html.includes("ï¿½") || html.includes("â€”") || html.includes("â†’")) {
  throw new Error("Artifact contains likely mojibake.");
}

console.log(`Self-contained artifact verified (${Math.round(html.length / 1024)} KiB).`);
