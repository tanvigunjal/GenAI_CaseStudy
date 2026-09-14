import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

const presentationDir = path.dirname(fileURLToPath(import.meta.url));
const evidence = JSON.parse(
  readFileSync(path.join(presentationDir, "public/presentation_evidence.json"), "utf8"),
);

export default defineConfig({
  plugins: [
    react(),
    viteSingleFile(),
    {
      name: "part-a-html-filename",
      enforce: "post",
      generateBundle(_options, bundle) {
        const html = bundle["index.html"];
        if (!html) throw new Error("Vite did not emit index.html.");
        html.fileName = "part_a.html";
        bundle["part_a.html"] = html;
        delete bundle["index.html"];
      },
    },
  ],
  base: "./",
  publicDir: false,
  define: {
    __PRESENTATION_EVIDENCE__: JSON.stringify(evidence),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    modulePreload: { polyfill: false },
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
