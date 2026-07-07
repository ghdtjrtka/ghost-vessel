"""Local TTS service for the avatar — provider-agnostic (spec 8장 VoiceEngine).

백엔드는 tts_config.json / TTS_PROVIDER 로 갈아끼운다: qwen(로컬·클로닝) / elevenlabs
(클라우드·클로닝) / openai_compat(경량 로컬·OpenAI TTS) / piper(초경량 로컬). 실제 로직은
providers.py. 이 파일은 HTTP 계약과 활성 프로바이더 위임/전환만 담당한다.

POST /tts        {text, speaker?, language?} -> audio/wav|mpeg
GET  /health     활성 provider + ready
GET  /providers  등록된 백엔드 목록 + 설정/준비 상태
POST /provider   {name} 활성 백엔드 전환(설정 저장, 다음 합성 때 지연 로드)
"""
import os, sys, threading, itertools, time
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
    # active provider의 설정도 함께 반환(UI가 현재 보이스 등을 표시). cfg엔 키 '값'이 아니라
    # api_key_env 같은 참조만 있어 노출 안전(실제 키는 환경변수).
    settings = (_cfg.get("providers") or {}).get(_active.name, {})
    return jsonify(active=_active.name, providers=P.list_providers(_cfg), settings=settings)

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
    _ensure_fillers()                      # 새 목소리로 필러 재합성(백그라운드)
    return jsonify(ok=True, active=_active.name, ready=_active.ready())

# ── 인앱 설치 (오픈소스: 의존성을 앱에서 바로 해결) ──────────────────────────
# provider의 install_steps()를 백그라운드 스레드에서 실행하고 진행상황을 폴링으로 노출.
import subprocess
_install = {"running": False, "provider": None, "ok": None, "done": False, "log": []}
_install_lock = threading.Lock()

def _run_install(name, steps):
    log = _install["log"]
    ok = True
    for step in steps:
        log.append("$ " + " ".join(str(s) for s in step))
        try:
            p = subprocess.Popen(step, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace")
            for line in p.stdout:
                log.append(line.rstrip())
                if len(log) > 600:
                    del log[:len(log) - 600]
            p.wait()
            if p.returncode != 0:
                ok = False; log.append(f"[exit {p.returncode}] 중단"); break
        except Exception as e:
            ok = False; log.append("ERROR: " + str(e)[:200]); break
    _install.update(running=False, ok=ok, done=True)
    log.append("✅ 설치 완료" if ok else "❌ 설치 실패")

@app.route("/install", methods=["POST", "OPTIONS"])
def install():
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if name not in P.REGISTRY:
        return jsonify(error=f"unknown provider '{name}'"), 400
    steps = P.install_steps(name)
    if steps is None:
        return jsonify(error=f"'{name}'는 앱 내 설치 대상이 아닙니다(수동 설정)."), 400
    with _install_lock:
        if _install["running"]:
            return jsonify(error="다른 설치가 진행 중입니다.", provider=_install["provider"]), 409
        _install.update(running=True, provider=name, ok=None, done=False, log=[])
        threading.Thread(target=_run_install, args=(name, steps), daemon=True).start()
    return jsonify(ok=True, started=True, provider=name)

@app.route("/install/status")
def install_status():
    return jsonify(running=_install["running"], provider=_install["provider"],
                   ok=_install["ok"], done=_install["done"], log=_install["log"][-40:])

VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")

# ── 필러(시간벌기) 프리셋 ────────────────────────────────────────────────────
# 합성 지연(melo ~20s, qwen RTF>1)을 덮기 위해, 짧은 페르소나 필러를 활성 목소리로
# 미리 합성해 캐시(디스크 영속)해 두고 즉시 재생한다. LLM이 대화 최전위에 [fill:<id>]를
# 붙이면 그 id를, 안 붙이면 시스템이 예상시간으로 tier를 골라 재생(player 쪽 로직).
FILLER_DIR = os.path.join(VOICES_DIR, "fillers")
FILLER_PHRASES = {                      # id → 발화 (프리셋이 fillers.json으로 덮어쓸 수 있음)
    "s1": "음~", "s2": "어…", "s3": "하아…",
    "m1": "음, 그게요…", "m2": "잠깐만요.",
    "l1": "음, 잠깐만요. 정리해서 보여줄게요.",
}
FILLER_TIERS = {"short": ["s1", "s2", "s3"], "med": ["m1", "m2"], "long": ["l1"]}
# 필러 재생 중 아바타가 지을 감정 loop (id별) — 시간벌기의 시각적 절반. "음~"=골똘, "하아"=한숨.
FILLER_EMO = {
    "s1": "pondering", "s2": "pondering", "s3": "sigh",
    "m1": "pondering", "m2": "pondering", "l1": "pondering",
}
DEFAULT_FILLER_EMO = "pondering"
_EXT = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac", "audio/aac": "aac"}
_MT = {v: k for k, v in _EXT.items()}

_filler = {"sig": None, "clips": {}, "building": False}   # clips: id -> (bytes, mimetype)
_filler_lock = threading.Lock()

def _load_filler_phrases():
    # 문구는 tts_config.json 의 "fillers"에서(사용자가 설정 UI에서 채움). 없으면 기본.
    f = _cfg.get("fillers") or {}
    phrases = f.get("phrases") or FILLER_PHRASES
    tiers = f.get("tiers") or FILLER_TIERS
    emos = f.get("emotions") or FILLER_EMO
    # tier가 실제 존재하는 id만 참조하도록 정리(사용자 편집 안전)
    tiers = {t: [i for i in ids if i in phrases] for t, ids in tiers.items()}
    # 필러 감정: 존재하는 id마다(없으면 기본 감정)
    emotions = {i: (emos.get(i) or DEFAULT_FILLER_EMO) for i in phrases}
    return phrases, tiers, emotions

def _filler_sig():
    # 목소리를 결정하는 필드로 시그니처(엔진 바뀌거나 보이스 바뀌면 재합성).
    c = _active.cfg or {}
    key = "|".join(str(c.get(k, "")) for k in ("voice", "speaker", "language", "voice_id", "model"))
    return f"{_active.name}:{key}"

def _build_fillers():
    """활성 목소리로 필러를 합성해 캐시(디스크 우선). 백그라운드 스레드에서 호출."""
    sig = _filler_sig()
    phrases, _tiers, _emos = _load_filler_phrases()
    d = os.path.join(FILLER_DIR, "".join(ch if ch.isalnum() else "_" for ch in sig))
    os.makedirs(d, exist_ok=True)
    clips = {}
    for fid, text in phrases.items():
        hit = None                                   # 디스크 캐시 히트?
        for ext in _EXT.values():
            fp = os.path.join(d, f"{fid}.{ext}")
            if os.path.exists(fp):
                hit = (open(fp, "rb").read(), _MT[ext]); break
        if hit is None:
            for attempt in range(3):                 # edge 무료 endpoint 버스트 스로틀 대비: 재시도+백오프
                try:
                    if _active.serialize:
                        with _fifo(): audio, mt = _active.synth(text)
                    else:
                        audio, mt = _active.synth(text)
                    ext = _EXT.get(mt, "wav")
                    open(os.path.join(d, f"{fid}.{ext}"), "wb").write(audio)
                    hit = (audio, mt); break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1.2 * (attempt + 1)); continue
                    print(f"[tts] filler '{fid}' 합성 실패: {str(e)[:120]}", flush=True)
            if hit is None:
                continue
            time.sleep(0.4)                           # 성공 후에도 다음 요청과 간격(버스트 완화)
        clips[fid] = hit
        with _filler_lock:                           # 하나씩 준비되는 대로 노출
            if _filler["sig"] == sig:
                _filler["clips"][fid] = hit
    with _filler_lock:
        _filler["building"] = False
    print(f"[tts] fillers ready: {list(clips.keys())} ({sig})", flush=True)

def _ensure_fillers():
    # 사용자가 설정 UI에서 필러를 만들기 전엔(config에 fillers 없음) 자동 합성하지 않는다.
    # 기본 문구는 UI 입력칸을 채우는 용도일 뿐, 확정/합성은 사용자 액션.
    # 비활성(enabled=False)이면 합성 안 함 — 빠른 엔진(edge 등)은 필러 불필요.
    f = _cfg.get("fillers") or {}
    if not f.get("phrases") or f.get("enabled") is False:
        return
    sig = _filler_sig()
    with _filler_lock:
        if _filler["sig"] == sig and (_filler["clips"] or _filler["building"]):
            return
        _filler.update(sig=sig, clips={}, building=True)
    threading.Thread(target=_build_fillers, daemon=True).start()

@app.route("/fillers")
def fillers_list():
    # phrases = 사용자 설정값(있으면) 아니면 기본값(UI 입력칸 프리필용). configured=사용자가 확정했는지.
    phrases, tiers, emotions = _load_filler_phrases()
    fcfg = _cfg.get("fillers") or {}
    configured = bool(fcfg.get("phrases"))
    enabled = fcfg.get("enabled", True)
    with _filler_lock:
        ready = list(_filler["clips"].keys()); building = _filler["building"]
    return jsonify(ready=ready, building=building, tiers=tiers, configured=configured,
                   enabled=enabled,
                   defaults=FILLER_PHRASES, default_tiers=FILLER_TIERS, emotions=emotions,
                   default_emotions=FILLER_EMO,
                   phrases={k: phrases[k] for k in phrases}, provider=_active.name)

@app.route("/fillers", methods=["POST", "OPTIONS"])
def fillers_set():
    """필러 문구 저장(설정 UI에서 입력칸 확정). {phrases:{id:text}, tiers?} → config 저장 후 재합성."""
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    phrases = data.get("phrases")
    if not isinstance(phrases, dict) or not phrases:
        return jsonify(error="phrases(dict) 필요"), 400
    phrases = {str(k).strip(): str(v).strip() for k, v in phrases.items() if str(v).strip()}
    tiers = data.get("tiers") if isinstance(data.get("tiers"), dict) else FILLER_TIERS
    emotions = data.get("emotions") if isinstance(data.get("emotions"), dict) else FILLER_EMO
    with _lock:
        prev = _cfg.get("fillers") or {}
        _cfg["fillers"] = {"phrases": phrases, "tiers": tiers, "emotions": emotions,
                           "enabled": prev.get("enabled", True)}   # on/off 상태 보존
        P.save_config(_cfg)
    with _filler_lock:                         # 캐시 무효화 → 재합성 강제
        _filler.update(sig=None, clips={}, building=False)
    _ensure_fillers()
    return jsonify(ok=True, phrases=phrases, tiers=tiers)

@app.route("/fillers/enabled", methods=["POST", "OPTIONS"])
def fillers_enabled():
    """필러 on/off. 끄면 즉시 재생 중단(clips 비움) + 합성 안 함. 켜면 재합성."""
    if request.method == "OPTIONS":
        return ("", 204)
    data = request.get_json(force=True, silent=True) or {}
    en = bool(data.get("enabled", True))
    with _lock:
        _cfg.setdefault("fillers", {})["enabled"] = en
        P.save_config(_cfg)
    if en:
        _ensure_fillers()
    else:
        with _filler_lock:
            _filler.update(clips={}, building=False)   # 즉시 중단(플레이어도 enabled로 게이트)
    return jsonify(ok=True, enabled=en)

@app.route("/fillers/build", methods=["POST", "OPTIONS"])
def fillers_build():
    """현재 문구를 현재 목소리로 (재)합성 트리거."""
    if request.method == "OPTIONS":
        return ("", 204)
    with _filler_lock:
        _filler.update(sig=None, clips={}, building=False)
    _ensure_fillers()
    return jsonify(ok=True, building=True)

@app.route("/filler")
def filler_get():
    fid = request.args.get("id", "")
    tier = request.args.get("tier", "")
    with _filler_lock:
        clips = dict(_filler["clips"])
    if not fid and tier:
        _phrases, tiers = _load_filler_phrases()
        cands = [i for i in tiers.get(tier, []) if i in clips]
        if cands:
            fid = cands[hash(request.args.get("n", "")) % len(cands)]   # tier 내 결정적 선택
    if fid and fid in clips:
        audio, mt = clips[fid]
        return Response(audio, mimetype=mt, headers={"X-Filler-Id": fid})
    return jsonify(error="filler not ready", building=_filler["building"]), 503

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
