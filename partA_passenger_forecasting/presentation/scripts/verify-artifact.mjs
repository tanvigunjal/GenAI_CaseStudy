import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const presentationDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const distDir = path.join(presentationDir, "dist");
const artifactPath = path.join(distDir, "part_a.html");

if (!existsSync(artifactPath)) throw new Error("Build artifact is missing.");
const outputs = readdirSync(distDir).filter((name) => !name.startsWith("."));
if (outputs.length !== 1 || outputs[0] !== "part_a.html") {
  throw new Error(`Expected exactly dist/part_a.html; found: ${outputs.join(", ")}`);
}

const html = readFileSync(artifactPath, "utf8");
const required = [
  [/^<!doctype html>/i, "HTML5 doctype"],
  [/<html lang="en">/i, "document language"],
  [/I built a forecast operations can plan from/i, "opening scene"],
  [/My recommendation/i, "recommendation scene"],
  [/Presenter notes/i, "notes"],
  [/prefers-reduced-motion/i, "reduced motion"],
  [/#scene-/i, "deep-link support"],
  [/font-family:Inter/i, "bundled Inter font"],
];
for (const [pattern, label] of required) {
  if (!pattern.test(html)) throw new Error(`Artifact is missing ${label}.`);
}

if (/<(?:script|img)[^>]+src=["']https?:\/\//i.test(html)) {
  throw new Error("Artifact contains an external runtime source.");
}
if (/<link[^>]+href=["']https?:\/\//i.test(html)) {
  throw new Error("Artifact contains an external stylesheet or resource link.");
}
if (/src=["'][^"']+\.js["']/i.test(html) || /href=["'][^"']+\.css["']/i.test(html)) {
  throw new Error("Artifact references separate JavaScript or CSS assets.");
}
if (/fetch\(|XMLHttpRequest|WebSocket\(/.test(html)) {
  throw new Error("Artifact includes a runtime network API.");
}
if (html.includes("ï¿½") || html.includes("â€”") || html.includes("â†’")) {
  throw new Error("Artifact contains likely mojibake.");
}

console.log(`Offline single-file artifact verified (${Math.round(html.length / 1024)} KiB).`);
