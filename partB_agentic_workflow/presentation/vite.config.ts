import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const presentationDir = path.dirname(fileURLToPath(import.meta.url));
const evidence = JSON.parse(
  readFileSync(
    path.join(presentationDir, "public/presentation_evidence.json"),
    "utf8",
  ),
);

export default defineConfig({
  plugins: [
    react(),
    viteSingleFile(),
    {
      name: "part-b-html-filename",
      enforce: "post",
      generateBundle(_options, bundle) {
        const html = bundle["index.html"];
        if (!html) throw new Error("Vite did not emit index.html for renaming.");
        html.fileName = "part_b.html";
        bundle["part_b.html"] = html;
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
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
});
