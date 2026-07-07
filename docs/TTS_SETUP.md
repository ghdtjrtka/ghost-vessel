# TTS Engines — Setup & Dependencies / 음성 엔진 설정과 의존성

Ghost Vessel ships **no bundled TTS binaries** (it's open-source, not a commercial package).
Instead the voice engine is **swappable** and every option's required dependencies are shown in
three places: at install time, in this doc, and in the app UI (🎨 settings → **Voice engine**).

고스트 베슬은 상용 패키지가 아니라 오픈소스라 **TTS 바이너리를 번들하지 않습니다.** 대신 음성
엔진을 **교체 가능**하게 두고, 각 옵션의 필수 의존성을 **설치 단계 · 이 문서 · 앱 UI**(🎨 설정 →
**음성 엔진**) 세 곳에서 모두 안내합니다.

## Switching engines / 엔진 전환
- **In-app / 앱에서**: 🎨 → *Voice engine* dropdown. Pick an engine; if it needs a one-step
  install you'll see an **Install this engine** button (runs the steps below for you, with a live log).
- **Config file / 설정 파일**: edit `tts/tts_config.json` → `"provider"` and restart the TTS server.
- **Env var / 환경변수**: `TTS_PROVIDER=<name>` overrides the config.

API keys are **environment variables only** — never stored in config or the repo.
API 키는 **환경변수로만** 받습니다(설정 파일·저장소에 저장하지 않음).

---

## Engine matrix / 엔진 비교

| Engine | Local? | GPU | API key | Korean | Cloning | In-app install |
|---|---|---|---|---|---|---|
| **qwen** | ✅ local | ✅ needs GPU | – | ✅ | ✅ | bundled (base setup) |
| **melo** | ✅ local | ❌ CPU | – | ✅ | – | ✅ one-click |
| **edge** | ☁ cloud | ❌ | – (keyless) | ✅ | – | ✅ one-click |
| **elevenlabs** | ☁ cloud | ❌ | ✅ | ✅ | ✅ | key only (no pkg) |
| **openai_compat** | ☁/local | ❌ | ✅* | dep. | – | endpoint only |
| **piper** | ✅ local | ❌ CPU | – | ⚠ none | – | manual |

\* `openai_compat` needs a key only when pointed at OpenAI's cloud; a local server needs none.

---

## qwen — Qwen3-TTS (local · GPU · cloning)
- **Best quality Korean + voice cloning, fully local.** Default engine.
- **Requires / 필수**: CUDA GPU (~2 GB VRAM), PyTorch(CUDA), `qwen_tts`, flash-attn (recommended).
  Already installed in the main TTS venv (`tts/venv`) by the base setup.
- **Notes / 참고**: shares the GPU with other work — under contention it slows down. Use when the
  GPU is free. Model: <https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice>

## melo — MeloTTS (local · CPU · multilingual)
- **Fully local & offline, no GPU, no key.** KR/EN/JP/ZH. Good (not top) quality.
- **Requires / 필수**: an **isolated venv** (auto-created at `tts/engines/melo-venv`), CPU PyTorch
  (~130 MB), MeloTTS (source build), `g2pkk` (Korean G2P), and the **unidic MeCab dictionary
  (~526 MB, `python -m unidic download`)** — MeloTTS imports its Japanese module even for Korean and
  crashes without it. First run auto-downloads the Korean model (~hundreds of MB) from HuggingFace.
  *Isolated because MeloTTS pins `transformers==4.27`, which would break qwen's newer transformers.*
- **Install / 설치**: click **Install this engine** in the UI, or run:
  ```bash
  bash tts/engines/install_melo.sh
  ```
- Docs: <https://github.com/myshell-ai/MeloTTS> · Model: <https://huggingface.co/myshell-ai/MeloTTS-Korean>

## edge — Microsoft Edge TTS (free cloud · no key)
- **Free, no API key, near-realtime, great Korean neural voices.** Cloud (text sent to Microsoft).
- **Requires / 필수**: `edge-tts` Python package + internet.
- **Install / 설치**: click **Install this engine**, or `tts/venv/Scripts/python -m pip install edge-tts`.
- Voices in-app: SunHi(여)/InJoon(남)/Hyunsu(다국어)/Nanami(JP)/Ava(EN). Docs: <https://github.com/rany2/edge-tts>

## elevenlabs — ElevenLabs (cloud · premium · cloning)
- **Top quality + cloning, multilingual.** Usage-billed.
- **Requires / 필수**: internet, `ELEVENLABS_API_KEY` env var, a `voice_id`. No extra Python packages.
- **Setup / 설정**: get a key → set `ELEVENLABS_API_KEY` → enter `voice_id` in the UI.
  Keys: <https://elevenlabs.io/app/settings/api-keys> · Voices: <https://elevenlabs.io/app/voice-library>

## openai_compat — OpenAI-compatible (cloud or local server)
- **Point at any `/audio/speech` endpoint** — OpenAI cloud, or a local server for offline/keyless use.
- **Requires / 필수**: a compatible endpoint. OpenAI cloud → `OPENAI_API_KEY`; local server → just a
  `base_url`. No extra Python packages. Set `base_url`/`voice` in `tts/tts_config.json`.
- Local servers: <https://github.com/remsky/Kokoro-FastAPI> · <https://github.com/matatonic/openedai-speech>
  OpenAI: <https://platform.openai.com/docs/guides/text-to-speech>

## piper — Piper (ultra-light local · realtime)
- **Tiny & fast, fully local.** ⚠ **No standard Korean voice** — use for English/other languages.
- **Requires / 필수**: the `piper` executable + a voice model (`.onnx`). Set `exe`/`model` paths in
  `tts/tts_config.json`.
- Binary: <https://github.com/rhasspy/piper/releases> · Voices: <https://huggingface.co/rhasspy/piper-voices>

---

### Adding your own engine / 엔진 직접 추가
Implement a `BaseTTS` subclass in `tts/providers.py` (one method: `synth(text, speaker, language)
-> (bytes, mimetype)`), add it to `REGISTRY`, and add a `PROVIDER_META` entry (bilingual `deps`/
`setup`, `links`, `installable`). If it's pip-installable, add an `install_steps()` branch and the
UI gives it an **Install** button automatically.
