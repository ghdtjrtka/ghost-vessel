# Ghost Vessel — give your local agent a vessel

> **Bind your local LLM agent (the *ghost*) into an avatar body (the *vessel*).**
> A monitor-resident, video-call-style avatar that **fronts your personal AI agent**
> (Hermes / OpenClaw, or anything) — a full **replacement for the Telegram/messenger chat**
> you keep open to talk to it, **serving that agent's exact slash-command menu** right in the
> avatar's window. Not a waifu toy: a real agent client that happens to have a face.

Your agent replies; the avatar **performs** it: fine-grained facial emotion beats, a
persistent **mood** that sinks when you scold it and brightens when you praise it, idle
states that breathe, blink, and sometimes rest their eyes — all from **pre-rendered
clips**, so the GPU stays free at runtime (no live inference).

## Demo

![Ghost Vessel — Yeoreum performing a work loop while the agent runs a task](docs/hero.gif)

▶ **[Watch the full 45s demo with sound](https://github.com/ghdtjrtka/ghost-vessel/releases/download/v0.1.0/GhostVessel-demo-EN.mp4)** — emotion beats, the real Hermes slash menu, work loops, code cards, human-in-the-loop approval, and the floating monitor-resident windows.

📦 **[Download for Windows](https://ghostvessel.space)** — dependency-free (no Python). Pay-what-you-want lifetime key (package from $5, preset from $1) at [ghostvessel.space](https://ghostvessel.space).

📝 **[How it works — a writeup](https://dev.to/member_f0346839/i-replaced-the-chat-window-for-my-local-ai-agent-with-a-face-3e1k)** — the emotion-beat output contract, the no-runtime-GPU avatar, and hooking a real agent in as a connector.

## How it works

```
you type ──► chat UI ──► bridge ──► relay connector ──► your agent (Hermes/OpenClaw)
                                                            │ reply (emotion-tagged)
video window ◄── performance player ◄── parser (3 planes) ◄─┘
  ▲ emotion beat clips + mood-based idle           │
  └── mood/affinity tracker (praise/scold, persistent state)
```

- **3-plane output**: the reply splits into action (emotion beats) / dialogue (spoken
  via local TTS) / data (code & files → chat cards, never read aloud).
- **Emotion engine**: expression segments (valence/arousal-tagged), tag-less fallback
  (emoji/keyword inference), blink-aligned seamless idle loops, a head-pose "settle
  gate" so expressions reveal when the head is frontal.
- **Mood & affinity**: short-term mood decays toward a long-term relationship baseline.
  Keep scolding → it rests in a subdued idle. Persistent across restarts.
- **Agent-agnostic**: Hermes (relay connector contract, WS server) and OpenClaw
  (gateway WS client) adapters, plus a demo responder with zero setup. The agent is
  told only the emotions your avatar actually has, so partial avatars just work.
- **The messenger's menu, in the avatar**: type `/` and the chat pulls your agent's
  **live command menu** — the same one you'd see in Telegram (e.g. 52 commands for Hermes:
  `/model`, `/new`, `/goal`, `/status`, `/compress`, …), served per-agent (Hermes or
  OpenClaw). A real control surface, not a canned list; commands pass straight to the agent.
- **Swappable voice**: local (Qwen3-TTS with voice cloning, MeloTTS, Piper) or cloud
  (Edge — free & keyless, ElevenLabs, any OpenAI-compatible) — picked in settings, with
  an in-app installer that surfaces each engine's deps. Voice input via VAD + local STT.

## Quickstart (Windows)

1. Prereqs: Python 3.11, an agent (optional — demo mode works without one).
2. Get an avatar preset → drop its folder into `presets/`, set `presets/active.txt`.
3. Link your agent: `python bridge/setup_connector.py` (picks Hermes/OpenClaw,
   Windows/WSL2, writes the connection + injects the avatar output contract into the
   agent's prompt, scoped to this channel).
4. Run the stack — TTS (:8899), bridge (:8900), connector (:8901), and a static server for
   the player (`python -m http.server 8777`). Then either open
   `http://127.0.0.1:8777/player/index.html` in a browser, **or** run the desktop shell
   (`cd src-tauri && cargo tauri dev`) for a frameless, always-on-top avatar window.
   (Each component is a small local server — wire up a launch script for your own setup.)

## Avatars (presets)

An avatar is a **pure-data bundle** (clips + persona + theme + voice + emotion map) —
no code runs, so installing one is safe. Presets are **folder-mapped**: drop a folder
of clips named by convention (`happy.mp4`, `angry.mp4`, `idle.mp4`, …) into `presets/`
and the engine maps them automatically — the folder name is the avatar's name.

**Any art style works.** The engine plays video clips and doesn't care what produced the
pixels — photoreal, 2D anime, a 3D render, pixel art, an abstract shape. You can even take
an existing **Live2D or VRM model and pre-render its expressions into clips**; a rig-based
shell can't do the reverse.

**Make your own — free, local, no subscription.** The reproducible build method and the
bundle/filename spec are in [`docs/PRESET_FORMAT.md`](docs/PRESET_FORMAT.md):

1. One neutral, front-facing still of your character.
2. Drive it into expression clips with **[LivePortrait](https://github.com/KwaiVGI/LivePortrait)**
   — runs locally on a consumer GPU, retargets your still using driving videos. (Any
   image-to-video model works too if you'd rather go faster; the engine only needs MP4s
   named by emotion.)
3. Loop-cut the segments and drop the folder into `presets/`.

Keep it SFW; own your likeness rights.

Prefer to skip the production step? **[Get the demo avatar (Yeoreum)](https://ghostvessel.space)**
(pay-what-you-want) or **commission a custom one** — open an issue or ask at
[ghostvessel.space](https://ghostvessel.space).

The repo ships **engine only** — no avatar bundled (`presets/_template/` is a starter
skeleton).

## License

Engine: **MIT** (see `LICENSE`). Presets are separately licensed by their creators.
Open-sourced in donation-ware spirit — issues / PRs welcome. 🛠️
