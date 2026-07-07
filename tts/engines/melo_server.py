"""MeloTTS 격리 서버 — melo-venv(격리 환경) 안에서 실행된다.

qwen venv의 transformers(4.57)와 MeloTTS의 transformers(4.27) 핀이 충돌하므로,
MeloTTS는 별도 venv에 설치하고 이 작은 stdlib HTTP 서버로 감싼다. 메인 TTS 서버
(tts_server.py)의 MeloTTS 프로바이더가 이 서버를 서브프로세스로 자동 기동하고 POST한다.

모델은 언어별 1회 로드 후 캐시(매 문장 리로드 방지). CPU 전용(로컬·GPU 불필요).
  POST /synth  {text, language?, speed?}  -> audio/wav
  GET  /health -> {ok, loaded:[langs]}
포트: MELO_PORT 환경변수(기본 8902 — 8901은 릴레이 커넥터 점유), 127.0.0.1 바인드.
"""
import os, sys, json, tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("MELO_PORT", "8902"))
_models = {}          # language -> TTS 인스턴스 캐시
_DEFAULT_LANG = os.environ.get("MELO_LANG", "KR")


def _get_model(lang):
    if lang not in _models:
        from melo.api import TTS
        print(f"[melo] loading model language={lang} (cpu) ...", flush=True)
        _models[lang] = TTS(language=lang, device="cpu")
        print(f"[melo] model {lang} ready", flush=True)
    return _models[lang]


def _synth(text, lang, speed):
    # MeloTTS 모델 언어는 EN/ES/FR/ZH/JP/KR. "EN-US" 등은 EN 모델 '내부 화자(악센트)'다.
    model_lang = "EN" if lang.upper().startswith("EN") else lang
    m = _get_model(model_lang)
    spk = m.hps.data.spk2id              # MeloTTS HParams — .get() 없음, keys()/[]만 지원
    keys = list(spk.keys())
    sid = spk[lang] if lang in keys else spk[keys[0]]   # 화자 키 없으면 첫 화자
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        m.tts_to_file(text, sid, path, speed=speed)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try: os.remove(path)
        except OSError: pass


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):        # 콘솔 스팸 억제
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "loaded": list(_models.keys())})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/synth":
            self._json(404, {"error": "not found"}); return
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            text = (data.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "empty text"}); return
            lang = data.get("language") or _DEFAULT_LANG
            speed = float(data.get("speed", 1.0))
            audio = _synth(text, lang, speed)
        except Exception as e:
            self._json(502, {"error": str(e)[:300]}); return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)


if __name__ == "__main__":
    # 부팅 시 기본 언어 모델 미리 로드(첫 요청 지연 최소화)
    try:
        _get_model(_DEFAULT_LANG)
    except Exception as e:
        print(f"[melo] preload failed: {str(e)[:200]}", file=sys.stderr, flush=True)
    print(f"[melo] serving on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
