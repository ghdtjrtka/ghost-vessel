# Preset template

Copy this folder to `presets/<your-id>/` and fill it in. A preset is **pure data**
(assets + config + prompt text) — no code runs when a preset is installed, so
buying/installing a preset is safe.

> **Full standardized build process (human- or LLM-executable, step-by-step with
> commands + acceptance criteria): [`docs/PRESET_BUILD.md`](../../docs/PRESET_BUILD.md)**
> Acceptance gate: `python tools/validate_preset.py presets/<your-id>` → PASS.

## What a preset bundles
| File / dir | What it is |
| --- | --- |
| `preset.json` | Manifest binding everything together (id, name, font, theme, voice, paths). |
| `avatar/` | Your avatar: `manifest.json` + `segments/*.mp4` (expression/motion clips) + `source.png`. The heavy, valuable part. |
| `fonts/` | UI font for this preset. **Only bundle fonts licensed for redistribution + commercial use** (SIL OFL / Google Fonts). |
| `role.md` | The persona / system-prompt injected into the agent at install. |
| `emotion_map.json` | How this persona emotes (emoji/keyword → expression). Must match `avatar/manifest.json` segment names. |
| `theme.json` | (optional) inline color theme, or set `theme.id` in preset.json to a built-in. |
| `voice.json` | (optional) TTS speaker/params, or set `voice` in preset.json. |
| `cover.png` | Store thumbnail (Gumroad). |
| `LICENSE` | Your license terms for the bundle. |

## Make your avatar assets
Use the engine's render pipeline (LivePortrait expression segments + a live idle
loop) on your OWN source image. **Do not ship someone else's likeness**, and check
the resale terms of any image/video generator you used (Higgsfield / Nano Banana /
etc.) before selling.

## Install / test
1. Drop the folder in `presets/`.
2. `echo <your-id> > presets/active.txt` (or pick it in the app's preset selector).
3. Restart the bridge; the engine loads your preset (`GET /preset`).

Keep it **SFW**. GitHub + Gumroad both have content policies.
