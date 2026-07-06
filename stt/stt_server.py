"""Local STT service (faster-whisper) for voice input to the avatar.

POST /stt   body = WAV bytes (any sr/mono/stereo; browser sends 16k mono)
            -> {"text": "...", "language": "ko", "duration": 1.8}
GET  /health

Pairs with the browser-side VAD in the player: mic -> Silero VAD utterance ->
WAV -> here -> text -> the normal chat path (/hermes/in). Model defaults to
"small" (good Korean, ~0.5GB); override with STT_MODEL. Tries CUDA, falls back
to CPU int8 (fine for short utterances).
"""
import io, os, sys, threading
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

from flask import Flask, request, jsonify

MODEL_ID = os.environ.get("STT_MODEL", "small")
LANG = os.environ.get("STT_LANG", "ko") or None      # "" -> autodetect

app = Flask(__name__)
_model = None
_lock = threading.Lock()

def get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel
                last = None
                # 기본 CPU: 12GB VRAM은 TTS+로컬LLM 몫 — whisper small int8은 CPU로도
                # 짧은 발화에 충분하고, CUDA로 올리면 VRAM 포화로 TTS가 기어감(실측).
                pref = os.environ.get("STT_DEVICE", "cpu")
                order = ((("cuda","float16"), ("cpu","int8")) if pref == "cuda"
                         else (("cpu","int8"), ("cuda","float16")))
                for device, ctype in order:
                    try:
                        print(f"[stt] loading {MODEL_ID} on {device}/{ctype} ...", flush=True)
                        _model = WhisperModel(MODEL_ID, device=device, compute_type=ctype)
                        print(f"[stt] ready ({device})", flush=True)
                        break
                    except Exception as e:
                        last = e
                        print(f"[stt] {device} failed: {str(e)[:120]}", flush=True)
                if _model is None:
                    raise last
    return _model

@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return r

@app.route("/health")
def health():
    return jsonify(ok=True, loaded=_model is not None, model=MODEL_ID)

@app.route("/stt", methods=["POST", "OPTIONS"])
def stt():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_data()
    if not data or len(data) < 128:
        return jsonify(error="empty audio"), 400
    m = get_model()
    # ?hint=미나미 → 고유명(에이전트 이름 등) 인식 바이어스
    hint = (request.args.get("hint") or os.environ.get("STT_HINT") or "").strip()
    segs, info = m.transcribe(io.BytesIO(data), language=LANG, beam_size=2,
                              vad_filter=True,      # 서버측 2차 VAD (무음 컷)
                              initial_prompt=(f"{hint}와의 대화." if hint else None),
                              condition_on_previous_text=False)
    text = " ".join(s.text.strip() for s in segs).strip()
    return jsonify(text=text, language=getattr(info, "language", LANG),
                   duration=round(getattr(info, "duration", 0.0), 2))

if __name__ == "__main__":
    if os.environ.get("STT_PRELOAD", "1") == "1":
        try: get_model()
        except Exception as e: print("[stt] preload failed:", e, flush=True)
    app.run(host="127.0.0.1", port=8898, threaded=True)
