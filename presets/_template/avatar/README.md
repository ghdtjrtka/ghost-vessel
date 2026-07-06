# avatar/ — your avatar assets

Put here:

- `manifest.json` — segment catalog (name, file, emotion, kind, frames, fps, and
  `settle_times` for alive-idle loops). Same schema the engine renders. See
  `segments/manifest.json` in the repo root for the reference format.
- `segments/*.mp4` — the expression + motion clips (H.264, web-friendly).
- `source.png` — the neutral frontal source image the expressions were built from.

`preset.json` → `avatar.segments_base` / `avatar.manifest` / `avatar.source` point
at these (URLs served from the preset dir).
