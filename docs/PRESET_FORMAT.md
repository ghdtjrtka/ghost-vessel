# Preset Format — build your own avatar

A **preset** is a pure-data bundle (no executable code) that skins the engine with a
new avatar: its clips, persona, theme, font, voice, and emotion map. Drop a valid
preset in `presets/<id>/`, point `presets/active.txt` at it, and the engine loads it.

This document is the **bundle specification** — the exact files, names, locations, and
formats the engine reads — plus a high-level overview of how the avatar clips are
produced. It gives you everything needed to *assemble* a preset and *attempt* one.

> The hand-tuned production recipe (which driving frames map to which emotion, the
> delta/anatomy tuning, the emotion-harvesting passes) is **not** part of this repo —
> that craft is what a commissioned preset buys you. See "Commissions" at the bottom.

---

## 0. The easy path — folder-drop by convention

The engine **auto-discovers** presets: a preset is just a folder under `presets/`, and
**the folder name is the avatar's name**. Clips inside are mapped by **filename**, so the
minimal preset is *a folder of correctly-named clips* — no `manifest.json`, no path
config. A buyer literally unzips the folder into `presets/` and it works.

Filename → what it becomes:

| filename | maps to |
|---|---|
| `<emotion>.mp4` (e.g. `happy.mp4`, `angry.mp4`) | that emotion's expression |
| `<emotion>__pos.mp4` / `__neg.mp4` | mood-specific variant (3-axis) |
| `idle.mp4`, `alive_idle.mp4`, `talking*.mp4`, `blink.mp4` | neutral idle/talking loop |
| `idle_positive.mp4` / `alive_idle_neg.mp4` (idle + mood) | 3-axis mood idle |
| `*_rest.mp4` | eyes-closed resting idle |
| any other `name.mp4` | a custom emotion named `name` |

Optional files alongside the clips: `role.md` (persona), `preset.json` (display name /
theme / voice / font overrides), `emotion_map.json` (else the built-in default is used),
`cover.png`, `LICENSE`. The engine reads clip metadata (frames/fps) automatically and
only advertises the emotions you actually included (partial presets are fine — see §4).

Sections 1–4 below are the **full spec** — the explicit form (with an authored
`manifest.json`) for when you want precise control. Both are supported; folder-drop
wins for distribution, the explicit form for authoring.

---

## 1. Bundle layout (explicit form)

```
presets/<id>/
  preset.json          # bindings: avatar, font, theme, voice, role, emotion_map
  role.md              # persona / system prompt injected into the agent (this channel only)
  emotion_map.json     # emotion vocabulary + axis + mood bases + tag-less fallback
  cover.png            # thumbnail (store/picker)              [optional]
  LICENSE              # your terms for this preset
  avatar/
    source.png         # the neutral source still (all expressions are rendered from this)
    manifest.json      # lists every segment clip + metadata
    segments/          # the rendered clips
      *.mp4            # H.264/AAC, the emotion + idle clips
      web/             # (optional) faststart-transcoded copies for the browser
  fonts/               # (optional)
    <Font>.woff2       # redistributable font + its license file
```

`<id>` is a lowercase-kebab slug (e.g. `my-avatar`). Copy `presets/_template/` to start.

---

## 2. `preset.json`

Binds the bundle together. Paths are URLs the player fetches; use **preset-relative**
paths (`avatar/...`, `fonts/...`) so the bundle is self-contained.

| field | type | notes |
|---|---|---|
| `id` | string | must equal the folder name |
| `name` / `name_en` | string | display names (picker, title bar) |
| `version` | semver | your preset version |
| `author` | string | credit |
| `sfw` | bool | must be `true` to distribute |
| `engine` | semver range | min engine version, e.g. `">=0.1.0"` |
| `description` | string | one line for the picker |
| `avatar.source` | url | the neutral source image |
| `avatar.manifest` | url | `avatar/manifest.json` |
| `avatar.segments_base` | url | dir the manifest's `file` names resolve against (usually `avatar/segments` or `avatar/segments/web`) |
| `font.family` / `font.url` | string / url\|null | `@font-face` family + `.woff2`; `null` for a system font |
| `theme` | id \| object | a built-in id (`graphite`,`crimson`,`ember`,`gold`,`teal`,`rose`,`azure`,`paper`,`rose_l`,`sky_l`,`sand_l`) or an inline theme object |
| `voice` | object | `{engine, speaker, language, sample_rate}` — passed to the TTS service (see the voice-engine settings for swappable backends) |
| `role` | filename | `role.md` |
| `emotion_map` | filename | `emotion_map.json` |

---

## 3. `avatar/manifest.json`

The catalog of clips. The player builds its emotion→clip table from this.

```jsonc
{
  "source": "avatar/source.png",
  "version": "1",                  // bump to cache-bust the player after re-renders
  "segments": [
    {
      "name": "happy",             // unique clip id
      "file": "segments/happy.mp4",// resolved against avatar.segments_base
      "emotion": "happy",          // which emotion this clip renders
      "kind": "transition",        // transition | loop | mood_idle
      "group": "positive",         // coarse family (for fallbacks)
      "frames": 49, "fps": 25,
      "intensity": 0.9             // optional
    }
    // ...
  ]
}
```

**`kind`** values:
- `transition` — an emotion expression (neutral → emotion → settle). One per emotion.
- `loop` — a seamless idle/talking/blink loop (`neutral`).
- `mood_idle` — a 3-axis resting state; add `"mood": "positive"|"neutral"|"negative"`,
  and for eyes-closed rest variants `"style": "rest_eyes_closed"`.

Optional per-segment fields the engine understands: `tier` (grouping), `loop_style`
(`straight`|`pingpong`), `settle_times`/`settle_frames` (frontal-pose timestamps so
expressions can trigger at the "정위치" pose), `style`.

**Clip format:** MP4, H.264 video (yuv420p) + AAC/none audio, same resolution & aspect
across all clips (mismatched framing reads as a size jump on mood/emotion swaps).

---

## 4. `emotion_map.json`

Defines the emotion vocabulary and how text maps to it. The bridge loads this into the
parser, so tuning emotions = editing this JSON (no code).

```jsonc
{
  "emotions": ["happy","smile","excited", /* ... */, "neutral"],
  "aliases":  { "joy": "happy", "sad": "downcast" },        // synonyms → canonical
  "axis":     { "happy": { "valence": 0.8, "arousal": 0.6 } }, // per emotion, drives mood
  "bases":    { "negative": "alive_idle_neg", "neutral": "alive_idle", "positive": "alive_idle_pos" },
  "mood":     { "beat_ema_alpha": 0.35, "decay_per_min": 0.15,
                "base_thresholds": { "negative": -0.35, "positive": 0.35 },
                "affinity_step": 0.02, "affinity_range": [-1.0, 1.0] },
  "emoji":    { "happy": "😊🙂😄" },                          // tag-less fallback
  "keywords": [ { "emotion": "happy", "words": ["좋아","최고"] } ],
  "labels":   { "happy": "기쁨" },                            // UI display names
  "default_intensity": 0.85
}
```

`axis` / `emotions` / `bases` are keyed by emotion **name**, so they carry over between
avatars — copy `emotion_map.json` and mainly edit `labels` / `keywords` / `aliases` to
fit your persona.

**Partial presets are fine — you do NOT need every emotion.** Render only the emotions
you want; the agent is told about *only the emotions your manifest actually provides*
(the output contract is filtered to your filled emotions at install), so it won't emit
tags the avatar can't show. Nothing is padded or substituted — an emotion you didn't
render simply isn't in the vocabulary. Start with a handful (e.g. happy/smile/neutral/
concerned/angry) and add more anytime; re-run the connector setup after adding emotions
so the agent's vocabulary updates.

---

## 5. Producing the clips — full walkthrough

You supply the assets; the engine just plays them. This is a **complete, reproducible
procedure** with standard tools. Every expression is generated from **one neutral still**,
so identity / lighting / background stay consistent across the whole set.

### 5.0 Tools you need
- An **image-to-video** model that preserves the subject's identity — e.g. **Google Veo /
  Gemini**, or any i2v (Seedance, Kling, …). This is the recommended path: from your still
  + a text prompt it produces genuinely *alive* motion (breathing, soft blinks, micro head
  movement) that canned/parametric methods can't match.
- **ffmpeg** — cut + transcode.
- *Free / local alternative:* ComfyUI + LivePortrait can drive the still with reference
  clips instead of an i2v model — heavier setup, no per-clip cost, same output shape
  (one `<emotion>.mp4` per expression). The rest of this section applies either way.

### 5.1 The source still (`avatar/source.png`)
The single most important asset — every expression inherits from it.
- Neutral: eyes open, mouth closed, relaxed (the *expression* is added when you animate it).
- Front-facing, head level; both eyes fully visible (bangs above the eyes).
- Even neutral light on the face; no strong color cast / one-sided shadow.
- Head + shoulders framing, high resolution; the background is baked in — use your final one.

### 5.2 Generate an expression video (i2v)
Feed the still + a prompt asking for **several expressions in one ~10 s clip**, each rising
from and returning to the **same neutral resting face** so each can later be cut into a loop.
Prompt shape:
> [the same person from the reference image], front-facing, static camera, alive throughout
> (breathing, soft blinks, subtle head motion). Three expressions in sequence — **[A] then
> back to neutral, [B] then back to neutral, [C] then back to neutral** — each understated
> and natural; return to the SAME blank calm resting face between each.
Notes:
- The model **won't obey pacing exactly** — it may give a different count or timing than you
  asked (and sometimes a bonus expression). That's fine; you find the real windows in §5.3.
- Group emotions by register per clip (all sweet / all teasing / all sulky) so the flow reads
  natural even where they blend.
- Keep expressions **subtle** — a smile that's too wide or a pose that's too big reads off.

### 5.3 Cut into per-emotion loop segments
Each emotion segment is `[neutral → expression → neutral]`, so it loops and the player can
crossfade it against idle. Find each expression's real window with a **timestamped filmstrip**:
```
# lay every 0.25s frame out with its timestamp burned in
ffmpeg -y -i clip.mp4 -vf "fps=4,scale=200:-1,drawtext=fontfile=<path/to/font.ttf>:\
  text='%{pts}':x=6:y=6:fontsize=22:fontcolor=yellow:box=1:boxcolor=black@0.7,\
  tile=8x5" -frames:v 1 filmstrip.png
```
Read off where each expression sits, then cut each window (re-encoding makes the cut
frame-accurate and browser-ready):
```
ffmpeg -y -ss <start> -t <dur> -i clip.mp4 -c:v libx264 -crf 20 -pix_fmt yuv420p \
       -movflags +faststart -an avatar/segments/web/<emotion>.mp4
```
- Put the cut boundaries at the **low-motion (near-neutral) points** between expressions, so
  each segment starts and ends calm and loops cleanly.
- **Do NOT ping-pong emotion clips.** Playing an expression forward-then-reversed makes it
  "un-happen" and looks unnatural. (Ping-pong is fine for *idle body* loops — see §5.5.)
- Keep **the same resolution and aspect for every clip** — a differently-framed clip reads as
  a size jump when the avatar swaps emotion/mood.
- One mp4 per emotion, named `<emotion>.mp4` (the folder-drop convention, §0).

### 5.5 Idle & mood loops
Idle/talking/mood clips are body-motion videos cut into **seamless loops**:
- Cut the loop's two endpoints on a **blink** (eyes-closed frames) so the seam hides
  inside the blink; or, if you can't match endpoints, build a **ping-pong** (play
  forward then reversed) which is always seamless:
  ```
  ffmpeg -y -i loop_src.mp4 -filter_complex \
    "[0]reverse[r];[0][r]concat=n=2:v=1:a=0,setpts=N/FRAME_RATE/TB" pingpong.mp4
  ```
- Make at least one neutral `loop` idle. Optional: `mood_idle` clips for
  positive/neutral/negative resting states (3-axis mood), tagged with `mood`.
- Anatomy check: view a rendered/loop frame next to the source at the same face height —
  the neck/chin should match the source (generated video + big head-pitch can stretch it).

### 5.6 Assemble the bundle
1. Write `avatar/manifest.json` — one entry per clip (§3). Give each emotion clip
   `kind:"transition"`, idles `kind:"loop"`, mood bases `kind:"mood_idle"`.
2. Copy `presets/_template/emotion_map.json` and edit `labels`/`keywords`/`aliases` for
   your persona (§4). Ensure every `transition` emotion appears in `emotions`.
3. Fill `preset.json` (§2): id, names, `avatar.*` paths, theme, font, voice.
4. Write `role.md` (persona / system prompt), add `cover.png` + `LICENSE`.

Following 5.1–5.6 yields a **working preset**. Making the expressions look *effortlessly
natural* — the prompt craft, the emotion selection that reads on-camera, precise loop
cutting, and the QA tuning we've accumulated — is where a commission saves you the work.

---

## 6. Rules (required to distribute)

1. **SFW only.**
2. **Own the likeness.** No real people's portraits; check your image/video
   generator's commercial & resale terms.
3. **Fonts:** bundle only fonts whose license allows redistribution + commercial use
   (SIL OFL / Google Fonts). Include the license file.
4. **Presets are pure data** — no executable code (js/exe/scripts) in a bundle.
5. Don't redistribute other people's source images or driving clips — ship only your
   rendered results.

---

## 7. Validate & activate

```
echo <id> > presets/active.txt      # make it the active preset
```
Start the stack, open the player, and verify:
- [ ] Avatar loads, name shows, font/theme applied.
- [ ] Each emotion tag plays its clip; unknown emotions fall back to `neutral`.
- [ ] Idle loop plays seamlessly (no visible seam / freeze); mood swaps don't jump size.
- [ ] Reviewed at 2–3 **real window sizes incl. narrow/tall** — what matters is how the
      emotion reads in the displayed crop, not the raw frame.

---

## Commissions

Want a polished, ready-to-run avatar without doing the production yourself? Custom
presets are made to order — see the repo README for how to inquire.
