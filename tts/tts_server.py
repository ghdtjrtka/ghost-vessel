"""Local TTS service for the avatar — provider-agnostic (spec 8장 VoiceEngine).

백엔드는 tts_config.json / TTS_PROVIDER 로 갈아끼운다: qwen(로컬·클로닝) / elevenlabs
(클라우드·클로닝) / openai_compat(경량 로컬·OpenAI TTS) / piper(초경량 로컬). 실제 로직은
providers.py. 이 파일은 HTTP 계약과 활성 프로바이더 위임/전환만 담당한다.

POST /tts        {text, speaker?, language?} -> audio/wav|mpeg
GET  /health     활성 provider + ready
GET  /providers  등록된 백엔드 목록 + 설정/준비 상태
POST /provider   {name} 활성 백엔드 전환(설정 저장, 다음 합성 때 지연 로드)
"""
import os, sys, threading, itertools
from contextlib import contextmanager
for _s in (sys.stdout, sys.stderr):      # cmd(cp949) 콘솔 유니코드 print 크래시 방지
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

from flask import Flask, request, Response, jsonify
import providers as P

app = Flask(__name__)
_lock = threading.Lock()
_cfg = P.load_config()
_active = P.make_provider(_cfg)

# 선착순 생성 큐: 로컬 단일모델(serialize=True)이 문장들을 순서대로 합성하도록.
# 일반 Lock은 대기 순서 보장이 없어 1번 문장이 꼴찌로 나오는 역전이 실측됨.
_tickets = itertools.count()
_serving = [0]
_cv = threading.Condition()

@contextmanager
def _fifo():
    my = next(_tickets)
    with _cv:
        while my != _serving[0]:
            _cv.wait()
    try:
        yield
    finally:
        with _cv:
            _serving[0] += 1
            _cv.notify_all()

@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return r

@app.route("/health")
def health():
    p = _active
    return jsonify(ok=True, provider=p.name, loaded=p.ready(), ready=p.ready(),
                   cloning=p.cloning, model=p.cfg.get("model") or p.cfg.get("voice_id") or p.name)

@app.route("/providers")
def providers_list():
    return jsonify(active=_active.name, providers=P.list_providers(_cfg))

@app.route("/provider", methods=["POST", "OPTIONS"])
def switch_provider():
    global _active
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if name not in P.REGISTRY:
        return jsonify(error=f"unknown provider '{name}'"), 400
    with _lock:
        _cfg["provider"] = name
        # 선택적으로 해당 provider 세부설정도 갱신 (voice_id 등)
        if isinstance(data.get("settings"), dict):
            _cfg.setdefault("providers", {}).setdefault(name, {}).update(data["settings"])
        P.save_config(_cfg)
        _active = P.make_provider(_cfg)
    return jsonify(ok=True, active=_active.name, ready=_active.ready())

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

@app.route("/clone", methods=["POST", "OPTIONS"])
def clone():
    """Qwen 음성 클로닝: 레퍼런스 오디오 업로드 → 프롬프트 계산 → 클론 모드 ON.
    body = wav 바이트(Content-Type: audio/*) 또는 multipart 'audio'.
    쿼리: ref_text(전사, 선택), x_vector_only(1이면 화자임베딩만).
    ⚠ 모델 로드(GPU) 필요 — 사용자가 클론을 적용할 때만 트리거된다."""
    if request.method == "OPTIONS":
        return ("", 204)
    if _active.name != "qwen":
        return jsonify(error="클로닝은 qwen 엔진에서만 지원됩니다. 먼저 엔진을 qwen으로 전환하세요."), 400
    # 오디오 바이트 확보
    audio = None
    if request.files.get("audio"):
        audio = request.files["audio"].read()
    elif request.data:
        audio = request.data
    if not audio or len(audio) < 1024:
        return jsonify(error="레퍼런스 오디오가 비어있거나 너무 짧습니다"), 400
    ref_text = (request.args.get("ref_text") or request.form.get("ref_text") or "").strip() or None
    xv = (request.args.get("x_vector_only") or request.form.get("x_vector_only") or "") in ("1", "true", "on")
    os.makedirs(VOICES_DIR, exist_ok=True)
    ref_path = os.path.join(VOICES_DIR, "clone_ref.wav")
    with open(ref_path, "wb") as f:
        f.write(audio)
    try:
        with _lock:
            _active.set_clone(ref_path, ref_text, xv)   # 모델 로드 + 프롬프트 계산(GPU)
            _cfg["providers"]["qwen"]["clone"] = {"ref": ref_path, "text": ref_text or "", "x_vector_only": xv}
            P.save_config(_cfg)
    except Exception as e:
        print("[tts] clone 실패:", str(e)[:200], flush=True)
        return jsonify(error=str(e)[:200]), 502
    return jsonify(ok=True, clone=_active.clone_status())

@app.route("/clone/clear", methods=["POST", "OPTIONS"])
def clone_clear():
    if request.method == "OPTIONS":
        return ("", 204)
    if _active.name == "qwen":
        with _lock:
            _active.clear_clone()
            _cfg["providers"]["qwen"]["clone"] = {"ref": "", "text": "", "x_vector_only": False}
            P.save_config(_cfg)
    return jsonify(ok=True, clone={"active": False})

@app.route("/clone/status")
def clone_status():
    if _active.name == "qwen" and hasattr(_active, "clone_status"):
        return jsonify(supported=True, **_active.clone_status())
    return jsonify(supported=False, active=False)

@app.route("/tts", methods=["POST", "OPTIONS"])
def tts():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="empty text"), 400
    speaker = data.get("speaker") or None
    language = data.get("language") or None
    prov = _active
    try:
        if prov.serialize:                       # 로컬 단일모델은 FIFO 직렬화
            with _fifo():
                audio, mt = prov.synth(text, speaker, language)
        else:                                    # 클라우드는 병렬 허용
            audio, mt = prov.synth(text, speaker, language)
    except Exception as e:
        print(f"[tts] {prov.name} synth 실패:", str(e)[:200], flush=True)
        return jsonify(error=str(e)[:200], provider=prov.name), 502
    return Response(audio, mimetype=mt)

if __name__ == "__main__":
    print(f"[tts] active provider = {_active.name} (serialize={_active.serialize}, "
          f"cloning={_active.cloning}, gpu={_active.needs_gpu})", flush=True)
    # 로컬 GPU 프로바이더는 부팅 시 프리로드(첫 요청 지연 방지). 클라우드는 로드 없음.
    if _active.needs_gpu and os.environ.get("TTS_PRELOAD", "1") == "1":
        try:
            _active.load()
        except Exception as e:
            print("[tts] preload failed:", str(e)[:200], flush=True)
    app.run(host="127.0.0.1", port=8899, threaded=True)
