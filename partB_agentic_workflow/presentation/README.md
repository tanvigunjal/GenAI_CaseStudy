# Part B presentation

This directory contains the 14-scene React/TypeScript presentation and its
self-contained offline build.

```bash
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e
```

The build emits exactly `dist/part_b.html`. It contains the JavaScript,
CSS, Inter fallback font files, and presentation evidence; it makes no runtime
network request.

## Evidence handoff

The canonical notebook evidence export should overwrite
`public/presentation_evidence.json` before the final build. Its
`sourceCommitSha` identifies the demonstrated code revision.

```bash
npm run verify:evidence
npm run build
```

The required manifest fields are `schemaVersion`, `sourceCommitSha`,
`generatedAt`, `mode`, `source`, `status`, `toolAllowlist`, and `claims`; the
claims object includes `coreScenes: 14` and `promotionSampleFloor: 299`.

## GitHub Pages

`.github/workflows/pages.yml` publishes the checked-in self-contained
artifact as the site root whenever the presentation is pushed, or when the
workflow is started manually. For this repository, the expected Pages address
is `https://tanvigunjal.github.io/OMMAX_CaseStudy/`. The deployment must be
verified before that address is presented as live.

## Presenter controls

- Arrow keys, Page Up/Down, Home, and End navigate.
- `O` opens the overview, `N` the presenter notes, `A` the appendix, and `F`
  fullscreen.
- Every core scene is deep-linkable as `#scene-1` through `#scene-14`.
- Click controls and horizontal touch swipes are supported.
