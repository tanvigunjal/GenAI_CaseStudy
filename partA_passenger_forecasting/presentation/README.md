# OMMAX Part A presentation

Seven core scenes plus eight appendix sections, implemented in React 19,
TypeScript, Vite, and `vite-plugin-singlefile`.

```bash
npm ci
npm test
npm run typecheck
npm run build
npm run verify:artifact
```

The build emits exactly `dist/ommax_part_a.html`. JavaScript, CSS, the supplied
OMMAX logo, Inter font files, and the evidence manifest are bundled in that file.
The presentation performs no runtime network requests.

## Evidence handoff

`public/presentation_evidence.json` is intentionally checked in as a sanitized,
non-final schema fixture. It contains fixed task facts but no model scores,
station traces, fitted parameters, or artifact hashes. Before the final evidence
build, replace it with the verified export while preserving the schema and set:

- `status: "verified"`
- `nonFinal: false`
- `sourceRevision` to the trained source revision
- `evaluation.status: "verified"`
- official and baseline RMSE values
- exactly ten `stationMetrics`, each with sample count, RMSE, bias, and series
- fold results, candidates/parameters, and ablations
- all artifact hashes

`npm run verify:evidence` fails closed if a manifest claims to be verified while
any of those result-bearing fields are missing.

## Presenter controls

- Arrow keys or Page Up/Down navigate; Home/End jump to the boundaries.
- `O` opens the overview, `N` notes, `A` the appendix, and `F` fullscreen.
- Every scene is deep-linkable as `#scene-1` through `#scene-7`.
- Click controls and horizontal touch swipes are supported.
