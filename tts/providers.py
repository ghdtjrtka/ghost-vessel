"""TTS 프로바이더 추상화 — 백엔드를 설정으로 갈아끼운다.

각 프로바이더는 synth(text, speaker, language) -> (audio_bytes, mimetype) 하나만
구현하면 되고, 서버(tts_server.py)는 활성 프로바이더에 위임한다. 로컬 모델(qwen/piper)은
serialize=True 라 서버의 FIFO 큐를 타고, 클라우드(elevenlabs/openai_compat)는 병렬 허용.

무거운 임포트(torch/qwen)는 전부 load() 안에서 지연 로드 → 클라우드 프로바이더만 쓸 땐
GPU/모델을 건드리지 않는다. API 키는 환경변수로만 받는다(설정 파일에 넣지 않음).
"""
import io, os, sys, json, shutil

# 패키지(frozen)에선 GV_ROOT(런처 설정)/tts_config.json 우선.
_CFG_DIR = os.environ.get("GV_ROOT") or (
    os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(_CFG_DIR, "tts_config.json")

DEFAULT_CONFIG = {
    "provider": "qwen",
    "providers": {
        "qwen": {
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
            "speaker": "Sohee", "language": "Korean", "compile": True,
            "clone": {"ref": "", "text": "", "x_vector_only": False},
        },
        "elevenlabs": {
            "api_key_env": "ELEVENLABS_API_KEY",
            "voice_id": "", "model_id": "eleven_multilingual_v2",
            "output_format": "mp3_44100_128",
        },
        "openai_compat": {
            "base_url": "http://127.0.0.1:8880/v1", "api_key_env": "OPENAI_API_KEY",
            "model": "tts-1", "voice": "alloy", "format": "wav",
        },
        "piper": {"exe": "piper", "model": "", "speaker": ""},
        "edge": {"voice": "ko-KR-SunHiNeural", "rate": "+0%", "pitch": "+0Hz", "volume": "+0%"},
        "melo": {"language": "KR", "speed": 1.0, "port": 8901},
    },
}

# ── 프로바이더별 설치/의존성 메타데이터 (오픈소스: 필수 의존성을 UI·문서에서 전부 안내) ──
# 프로즈 필드는 {ko,en} 이중 표기(UI 언어에 맞춰 표시). kind/links/install_cmd는 언어 무관.
# installable=True 는 앱에서 '설치' 버튼으로 바로 설치 가능(pip 기반). key_env 있으면 API 키 필요.
PROVIDER_META = {
    "qwen": {
        "kind": "local-gpu", "installable": True, "key_env": None, "cloud": False,
        "label": {"ko": "Qwen3-TTS (로컬·GPU·클로닝)", "en": "Qwen3-TTS (local · GPU · cloning)"},
        "quality": {"ko": "고품질 · 한국어 · 음성 클로닝 · 고급/GPU 유저용",
                    "en": "High quality · Korean · voice cloning · for advanced/GPU users"},
        "deps": {"ko": "⚠ CUDA GPU 필수(~2GB VRAM) · PyTorch(CUDA, ~2.5GB) · qwen-tts. flash-attn은 선택(속도↑, "
                       "없으면 sdpa로 자동 폴백). 기본 무GPU 셋에는 미포함 — '설치'로 추가.",
                 "en": "⚠ CUDA GPU required (~2GB VRAM) · PyTorch(CUDA, ~2.5GB) · qwen-tts. flash-attn optional "
                       "(faster; auto-falls back to sdpa). Not in the default no-GPU set — add via Install."},
        "setup": {"ko": "'설치' 버튼으로 메인 venv에 CUDA torch+qwen-tts 설치(수 분). GPU가 없으면 동작하지 않음.",
                  "en": "Install adds CUDA torch+qwen-tts to the main venv (a few minutes). Won't run without a GPU."},
        "links": {"model": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"},
    },
    "edge": {
        "kind": "cloud-free", "installable": True, "key_env": None, "cloud": True,
        "label": {"ko": "Edge TTS (무료 클라우드·키 없음)", "en": "Edge TTS (free cloud · no key)"},
        "quality": {"ko": "한국어 뉴럴 보이스 우수 · 거의 실시간", "en": "Great Korean neural voices · near-realtime"},
        "deps": {"ko": "edge-tts 파이썬 패키지 · 인터넷 연결. API 키 불필요.",
                 "en": "edge-tts Python package · internet. No API key."},
        "setup": {"ko": "'설치' 버튼으로 메인 venv에 edge-tts 설치. 클라우드라 텍스트가 Microsoft로 전송됨.",
                  "en": "Click Install to add edge-tts to the main venv. Cloud: text is sent to Microsoft."},
        "links": {"docs": "https://github.com/rany2/edge-tts"},
    },
    "melo": {
        "kind": "local-cpu", "installable": True, "key_env": None, "cloud": False,
        "label": {"ko": "MeloTTS (로컬·CPU·다국어)", "en": "MeloTTS (local · CPU · multilingual)"},
        "quality": {"ko": "완전 로컬(오프라인) · KR/EN/JP/ZH · ⚠ CPU라 느림(~15~20초/문장). 오프라인이 꼭 필요할 때.",
                    "en": "Fully local (offline) · KR/EN/JP/ZH · ⚠ slow on CPU (~15-20s/sentence). Use when offline is a must."},
        "deps": {"ko": "격리 venv(자동) · CPU PyTorch(~130MB) · MeloTTS(소스 빌드) · g2pkk + eunjeon(한국어 MeCab) · "
                       "unidic 사전(~526MB) · BERT prosody 모델·음성 모델(최초 자동 다운로드). "
                       "⚠ Windows에서 eunjeon 빌드에 Visual Studio Build Tools 필요(없으면 설치 실패 → Tier-1 번들은 미리 빌드된 wheel 포함).",
                 "en": "Isolated venv (auto) · CPU PyTorch(~130MB) · MeloTTS(source) · g2pkk + eunjeon (Korean MeCab) · "
                       "unidic dict (~526MB) · BERT prosody + voice models (auto-download). "
                       "⚠ On Windows eunjeon needs Visual Studio Build Tools to compile (else install fails → the Tier-1 bundle ships a prebuilt wheel)."},
        "setup": {"ko": "'설치'로 별도 venv에 설치(qwen과 충돌 없음, 수 분). Windows는 VS Build Tools 필요. 이후 오프라인 동작.",
                  "en": "Install into a separate venv (no qwen conflict, a few min). Windows needs VS Build Tools. Offline afterwards."},
        "links": {"docs": "https://github.com/myshell-ai/MeloTTS", "model": "https://huggingface.co/myshell-ai/MeloTTS-Korean"},
    },
    "elevenlabs": {
        "kind": "cloud-key", "installable": False, "key_env": "ELEVENLABS_API_KEY", "cloud": True,
        "label": {"ko": "ElevenLabs (클라우드·고품질·클로닝)", "en": "ElevenLabs (cloud · premium · cloning)"},
        "quality": {"ko": "최고 품질 · 음성 클로닝 · 다국어", "en": "Top quality · voice cloning · multilingual"},
        "deps": {"ko": "인터넷 · ElevenLabs API 키(환경변수 ELEVENLABS_API_KEY) · voice_id. 추가 패키지 없음(표준 라이브러리).",
                 "en": "Internet · ElevenLabs API key (env ELEVENLABS_API_KEY) · voice_id. No extra packages (stdlib only)."},
        "setup": {"ko": "elevenlabs.io에서 키 발급 → 환경변수 ELEVENLABS_API_KEY 설정 → 설정에서 voice_id 입력. 사용량 과금.",
                  "en": "Get a key at elevenlabs.io → set env ELEVENLABS_API_KEY → enter voice_id in settings. Usage-billed."},
        "links": {"keys": "https://elevenlabs.io/app/settings/api-keys", "voices": "https://elevenlabs.io/app/voice-library"},
    },
    "openai_compat": {
        "kind": "cloud-or-server", "installable": False, "key_env": "OPENAI_API_KEY", "cloud": True,
        "label": {"ko": "OpenAI 호환 (클라우드 또는 로컬 서버)", "en": "OpenAI-compatible (cloud or local server)"},
        "quality": {"ko": "엔드포인트에 따라 다름 · 로컬 서버로 완전 로컬 가능",
                    "en": "Depends on endpoint · fully local via a local server"},
        "deps": {"ko": "/audio/speech 호환 엔드포인트. OpenAI 클라우드면 OPENAI_API_KEY, 또는 로컬 서버"
                       "(kokoro-fastapi, openedai-speech 등)의 base_url. 추가 패키지 없음.",
                 "en": "An /audio/speech-compatible endpoint. OpenAI cloud needs OPENAI_API_KEY, or point base_url at a "
                       "local server (kokoro-fastapi, openedai-speech, ...). No extra packages."},
        "setup": {"ko": "설정에서 base_url·voice 지정. 로컬 서버를 별도로 띄우면 오프라인·무키로 사용 가능.",
                  "en": "Set base_url·voice in settings. Run a local server for offline/keyless use."},
        "links": {"openai": "https://platform.openai.com/docs/guides/text-to-speech",
                  "kokoro_server": "https://github.com/remsky/Kokoro-FastAPI",
                  "openedai": "https://github.com/matatonic/openedai-speech"},
    },
    "piper": {
        "kind": "local-cpu", "installable": False, "key_env": None, "cloud": False,
        "label": {"ko": "Piper (초경량 로컬·실시간)", "en": "Piper (ultra-light local · realtime)"},
        "quality": {"ko": "가벼움·빠름 · ⚠ 한국어 공식 보이스 없음(영어 등 타 언어용)",
                    "en": "Light·fast · ⚠ no official Korean voice (English/other languages)"},
        "deps": {"ko": "piper 실행파일(.exe) + 음성 모델(.onnx). 설정에서 exe·model 경로 지정. ⚠ 한국어는 표준 보이스 없음.",
                 "en": "piper executable (.exe) + a voice model (.onnx). Set exe·model paths in settings. ⚠ No standard Korean voice."},
        "setup": {"ko": "릴리스에서 piper 바이너리 다운로드 + 원하는 언어 .onnx 보이스 받기 → 설정에 경로 입력.",
                  "en": "Download the piper binary + a .onnx voice for your language → set the paths in settings."},
        "links": {"binary": "https://github.com/rhasspy/piper/releases", "voices": "https://huggingface.co/rhasspy/piper-voices"},
    },
}


# 합성 지연 추정(초) ≈ base + per_char × 글자수. 플레이어가 필러 ETA(넣을지/tier)를 계산한다.
# 실측 기반: edge ~0.5s 고정, melo ~18s 고정오버헤드, qwen RTF>1(경합 시 더 큼). 리액티브
# 필러 루프가 과소추정을 덮으므로 보수적으로 낮게 잡아도 안전.
PROVIDER_LATENCY = {
    "edge": {"base": 0.5, "per_char": 0.0},
    "melo": {"base": 18.0, "per_char": 0.12},
    "qwen": {"base": 1.5, "per_char": 0.5},
    "elevenlabs": {"base": 0.8, "per_char": 0.02},
    "openai_compat": {"base": 1.0, "per_char": 0.03},
    "piper": {"base": 0.3, "per_char": 0.02},
}


def provider_meta():
    """언어 무관 메타 + 런타임 설치상태를 합쳐 반환(UI/문서/설치 안내용)."""
    out = {}
    for name, cls in REGISTRY.items():
        m = dict(PROVIDER_META.get(name, {}))
        try:
            inst = cls({})
            m["installed"] = inst.installed() if hasattr(inst, "installed") else inst.ready()
        except Exception:
            m["installed"] = False
        out[name] = m
    return out


def _main_py():
    import sys
    return sys.executable        # 메인 TTS venv 파이썬(=qwen venv)

def _melo_paths():
    eng = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines")
    return eng, os.path.join(eng, "melo-venv", "Scripts", "python.exe")


def install_steps(name):
    """앱 내 '설치'가 실행할 명령 시퀀스(크로스플랫폼, bash 불필요). 없으면 None."""
    if name == "edge":
        return [[_main_py(), "-m", "pip", "install", "edge-tts"]]
    if name == "melo":
        eng, mpy = _melo_paths()
        idx = ["--index-url", "https://download.pytorch.org/whl/cpu"]
        return [
            [_main_py(), "-m", "venv", os.path.join(eng, "melo-venv")],
            [mpy, "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"],
            [mpy, "-m", "pip", "install", "torch", "torchaudio", *idx],
            [mpy, "-m", "pip", "install", "git+https://github.com/myshell-ai/MeloTTS.git"],
            [mpy, "-m", "pip", "install", "g2pkk"],
            [mpy, "-m", "pip", "install", "eunjeon"],  # 한국어 MeCab(g2pkk가 요구). ⚠ Windows는 컴파일 필요(VS Build Tools)
            [mpy, "-m", "unidic", "download"],       # 일본어 MeCab 사전(필수) — 없으면 import 시 죽음
        ]
    if name == "qwen":
        # 고급/GPU 유저 전용: CUDA torch + qwen-tts. flash-attn은 윈도우 빌드가 까다로워
        # 자동설치에서 제외(없으면 load()가 sdpa로 폴백). CUDA GPU가 있어야 실제 동작.
        mpy = _main_py()
        return [
            [mpy, "-m", "pip", "install", "torch==2.6.0", "--index-url", "https://download.pytorch.org/whl/cu124"],
            [mpy, "-m", "pip", "install", "qwen-tts==0.1.1"],
        ]
    return None


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))          # deep copy
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        cfg["provider"] = user.get("provider", cfg["provider"])
        for k, v in (user.get("providers") or {}).items():
            cfg["providers"].setdefault(k, {}).update(v or {})
        # provider 외 top-level 키(fillers 등) 보존 — 안 그러면 다음 save가 파일에서 날림
        for k, v in user.items():
            if k not in ("provider", "providers"):
                cfg[k] = v
    except FileNotFoundError:
        save_config(cfg)                                   # 최초 실행 시 기본 파일 생성
    except Exception as e:
        print("[tts] config 로드 실패, 기본값 사용:", str(e)[:120], flush=True)
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[tts] config 저장 실패:", str(e)[:120], flush=True)


def _wav_bytes(samples, sr):
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV")
    buf.seek(0)
    return buf.read()


class BaseTTS:
    name = "base"
    serialize = False           # True면 서버 FIFO 큐로 직렬화(로컬 단일GPU/CPU 모델)
    cloning = False             # 음성 클로닝 지원 여부(UI 표시용)
    needs_gpu = False

    def __init__(self, cfg):
        self.cfg = cfg or {}

    def load(self):
        pass

    def ready(self):
        return True

    def info(self):
        return {"provider": self.name, "ready": self.ready(),
                "cloning": self.cloning, "needs_gpu": self.needs_gpu,
                "serialize": self.serialize}

    def synth(self, text, speaker=None, language=None):
        raise NotImplementedError


class QwenTTS(BaseTTS):
    name = "qwen"; serialize = True; cloning = True; needs_gpu = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self._m = None
        self._clone_prompt = None      # create_voice_clone_prompt 결과(레퍼런스 1회 인코딩 캐시)

    def installed(self):
        # torch + qwen_tts 패키지 존재 여부(고급/GPU 유저가 인앱 설치). find_spec은 임포트 안 함.
        import importlib.util
        return (importlib.util.find_spec("torch") is not None
                and importlib.util.find_spec("qwen_tts") is not None)

    def ready(self):
        return self._m is not None

    def load(self):
        if self._m is not None:
            return
        import torch
        from qwen_tts import Qwen3TTSModel
        model = self.cfg.get("model") or os.environ.get(
            "QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
        print(f"[tts] loading qwen {model} ...", flush=True)
        try:
            self._m = Qwen3TTSModel.from_pretrained(
                model, device_map="cuda:0", dtype=torch.bfloat16,
                attn_implementation="flash_attention_2")
        except Exception as e:      # flash-attn 미설치/미빌드 → sdpa로 폴백(약간 느림)
            print("[tts] flash_attention_2 불가, sdpa 폴백:", str(e)[:120], flush=True)
            self._m = Qwen3TTSModel.from_pretrained(
                model, device_map="cuda:0", dtype=torch.bfloat16,
                attn_implementation="sdpa")
        print("[tts] qwen loaded", flush=True)
        if self.cfg.get("compile", True) and os.environ.get("QWEN_TTS_COMPILE", "1") == "1":
            self._compile()
        # 설정에 클론 레퍼런스가 있으면 프롬프트 미리 계산
        cl = self.cfg.get("clone") or {}
        if cl.get("ref") and os.path.exists(cl["ref"]):
            try: self._build_clone_prompt(cl["ref"], cl.get("text") or None, bool(cl.get("x_vector_only")))
            except Exception as e: print("[tts] clone prompt 실패:", str(e)[:160], flush=True)

    def _build_clone_prompt(self, ref_path, ref_text, x_vector_only):
        import torch
        with torch.inference_mode():
            self._clone_prompt = self._m.create_voice_clone_prompt(
                ref_audio=ref_path, ref_text=ref_text, x_vector_only_mode=x_vector_only)
        print(f"[tts] voice clone armed (ref={os.path.basename(ref_path)}, "
              f"text={'있음' if ref_text else '없음'}, xvec_only={x_vector_only})", flush=True)

    def set_clone(self, ref_path, ref_text=None, x_vector_only=False):
        """런타임 클론 설정: 레퍼런스 오디오 → 프롬프트 재계산 + 설정 반영."""
        self.load()
        self._build_clone_prompt(ref_path, ref_text, x_vector_only)
        self.cfg["clone"] = {"ref": ref_path, "text": ref_text or "", "x_vector_only": bool(x_vector_only)}

    def clear_clone(self):
        self._clone_prompt = None
        self.cfg["clone"] = {"ref": "", "text": "", "x_vector_only": False}

    def clone_status(self):
        cl = self.cfg.get("clone") or {}
        return {"active": self._clone_prompt is not None,
                "ref": os.path.basename(cl.get("ref", "")) if cl.get("ref") else "",
                "has_text": bool(cl.get("text")), "x_vector_only": bool(cl.get("x_vector_only"))}

    def _compile(self):
        # 반복 디코더 레이어만 regional 컴파일(런치 오버헤드 감소, RTF ~31%).
        # 전제: triton + cl.exe(vcvars). 실패 시 eager 유지.
        try:
            import importlib.util, torch, time
            if importlib.util.find_spec("triton") is None:
                print("[tts] compile skip: triton 미설치", flush=True); return
            import torch._dynamo as dyn
            dyn.config.cache_size_limit = 256
            tk = self._m.model.talker
            cp = getattr(tk, "code_predictor", None)
            layers = list(tk.model.layers)
            if cp is not None and hasattr(cp, "model"):
                layers += list(getattr(cp.model, "layers", []))
            for layer in layers:
                layer.forward = torch.compile(layer.forward, mode="default", dynamic=True)
            print(f"[tts] regional compile armed: {len(layers)} layers (warmup)", flush=True)
            t0 = time.time()
            sp = self.cfg.get("speaker", "Sohee"); lg = self.cfg.get("language", "Korean")
            with torch.inference_mode():
                for w in ("네 알겠어요.", "오빠 이거 방금 확인했는데 결과 정리해서 보여줄게요."):
                    self._m.generate_custom_voice(text=w, language=lg, speaker=sp)
            print(f"[tts] warmup done ({time.time()-t0:.0f}s), compiled path live", flush=True)
        except Exception as e:
            print("[tts] compile skipped (eager 유지):", str(e)[:160], flush=True)

    def synth(self, text, speaker=None, language=None):
        import torch
        self.load()
        lg = language or self.cfg.get("language", "Korean")
        with torch.inference_mode():
            if self._clone_prompt is not None:        # 클론 모드: 캐시된 프롬프트 재사용
                wavs, sr = self._m.generate_voice_clone(
                    text=text, language=lg, voice_clone_prompt=self._clone_prompt)
            else:                                     # 프리셋 스피커 모드
                sp = speaker or self.cfg.get("speaker", "Sohee")
                wavs, sr = self._m.generate_custom_voice(text=text, language=lg, speaker=sp)
        return _wav_bytes(wavs[0], sr), "audio/wav"


class ElevenLabsTTS(BaseTTS):
    name = "elevenlabs"; serialize = False; cloning = True; needs_gpu = False

    def _key(self):
        return os.environ.get(self.cfg.get("api_key_env", "ELEVENLABS_API_KEY"), "")

    def ready(self):
        return bool(self._key()) and bool(self.cfg.get("voice_id"))

    def synth(self, text, speaker=None, language=None):
        import urllib.request
        key = self._key()
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY 환경변수 미설정")
        voice = speaker or self.cfg.get("voice_id")
        if not voice:
            raise RuntimeError("elevenlabs voice_id 미설정")
        fmt = self.cfg.get("output_format", "mp3_44100_128")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format={fmt}"
        body = json.dumps({"text": text, "model_id": self.cfg.get("model_id", "eleven_multilingual_v2")}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"xi-api-key": key, "Content-Type": "application/json"})
        audio = urllib.request.urlopen(req, timeout=60).read()
        mt = "audio/mpeg" if fmt.startswith("mp3") else "audio/wav"
        return audio, mt


class OpenAICompatTTS(BaseTTS):
    # OpenAI /audio/speech 호환 — OpenAI TTS + 다수 경량 로컬 서버(kokoro-fastapi,
    # openedai-speech 등)를 한 어댑터로 커버.
    name = "openai_compat"; serialize = False; cloning = False; needs_gpu = False

    def ready(self):
        return bool(self.cfg.get("base_url"))

    def synth(self, text, speaker=None, language=None):
        import urllib.request
        base = (self.cfg.get("base_url") or "").rstrip("/")
        if not base:
            raise RuntimeError("openai_compat base_url 미설정")
        key = os.environ.get(self.cfg.get("api_key_env", "OPENAI_API_KEY"), "")
        fmt = self.cfg.get("format", "wav")
        body = json.dumps({
            "model": self.cfg.get("model", "tts-1"),
            "input": text,
            "voice": speaker or self.cfg.get("voice", "alloy"),
            "response_format": fmt,
        }).encode()
        req = urllib.request.Request(base + "/audio/speech", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        audio = urllib.request.urlopen(req, timeout=120).read()
        mt = {"wav": "audio/wav", "mp3": "audio/mpeg", "opus": "audio/ogg", "flac": "audio/flac", "aac": "audio/aac"}.get(fmt, "audio/wav")
        return audio, mt


class PiperTTS(BaseTTS):
    # 초경량 로컬(비자기회귀 VITS) — RTF <<1, CPU에서도 실시간. 클로닝은 없음.
    name = "piper"; serialize = True; cloning = False; needs_gpu = False

    def ready(self):
        exe = self.cfg.get("exe", "piper")
        has_exe = bool(shutil.which(exe) or os.path.exists(exe))
        return has_exe and bool(self.cfg.get("model")) and os.path.exists(self.cfg.get("model", ""))

    def synth(self, text, speaker=None, language=None):
        import subprocess
        exe = self.cfg.get("exe", "piper")
        model = self.cfg.get("model")
        if not model:
            raise RuntimeError("piper model(.onnx) 경로 미설정")
        cmd = [exe, "--model", model, "--output_file", "-"]
        sp = speaker or self.cfg.get("speaker", "")
        if sp:
            cmd += ["--speaker", str(sp)]
        p = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("piper 실패: " + p.stderr.decode("utf-8", "replace")[:200])
        return p.stdout, "audio/wav"


class EdgeTTS(BaseTTS):
    # Microsoft Edge 뉴럴 TTS — API 키 불필요, GPU 불필요, 거의 실시간. 클라우드(MS 서버)라
    # 텍스트가 외부로 나감. 한국어 뉴럴 보이스 우수(ko-KR-SunHiNeural 여 / ko-KR-InJoonNeural 남).
    name = "edge"; serialize = False; cloning = False; needs_gpu = False

    def ready(self):
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def _synth_async(self, text, voice):
        import edge_tts
        async def _run():
            kw = {"rate": self.cfg.get("rate", "+0%"),
                  "pitch": self.cfg.get("pitch", "+0Hz"),
                  "volume": self.cfg.get("volume", "+0%")}
            c = edge_tts.Communicate(text, voice, **kw)
            data = b""
            async for ch in c.stream():
                if ch["type"] == "audio":
                    data += ch["data"]
            return data
        return _run

    def synth(self, text, speaker=None, language=None):
        import asyncio
        voice = speaker or self.cfg.get("voice", "ko-KR-SunHiNeural")
        run = self._synth_async(text, voice)
        # 호출자가 이미 async 루프 안이면 별도 스레드에서 새 루프로 실행(루프 충돌 방지).
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                data = ex.submit(lambda: asyncio.run(run())).result()
        else:
            data = asyncio.run(run())
        if not data:
            raise RuntimeError("edge-tts: 오디오 수신 없음(보이스명/네트워크 확인)")
        return data, "audio/mpeg"


class MeloTTS(BaseTTS):
    # 로컬 CPU 다국어(한국어/영어/일본어 등) — MeloTTS. qwen과 transformers 핀이 충돌해서
    # 격리 venv(engines/melo-venv)에 설치하고, 서브프로세스 HTTP 서버(melo_server.py)로 감싼다.
    # 모델은 서버가 1회 로드 후 캐시. 클라우드 아님(최초 모델 다운로드만 네트워크).
    name = "melo"; serialize = False; cloning = False; needs_gpu = False

    _ENG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines")
    _VENV_PY = os.path.join(_ENG_DIR, "melo-venv", "Scripts", "python.exe")
    _SERVER = os.path.join(_ENG_DIR, "melo_server.py")
    _proc = None
    _LANGMAP = {"korean": "KR", "kr": "KR", "ko": "KR", "english": "EN-US", "en": "EN-US",
                "en-us": "EN-US", "japanese": "JP", "jp": "JP", "ja": "JP", "chinese": "ZH",
                "zh": "ZH", "spanish": "ES", "es": "ES", "french": "FR", "fr": "FR"}

    def _port(self):
        # 8901은 릴레이 커넥터가 점유 → 멜로는 8902로 격리
        return int(self.cfg.get("port", 8902))

    def installed(self):
        return os.path.exists(self._VENV_PY)

    def ready(self):
        return self.installed()      # 서버는 synth 때 자동 기동

    def _lang(self, language):
        raw = language or self.cfg.get("language", "KR")
        return self._LANGMAP.get(str(raw).lower(),
                                 raw if raw in ("KR", "EN-US", "EN", "JP", "ZH", "ES", "FR")
                                 else self.cfg.get("language", "KR"))

    def _reachable(self):
        import urllib.request
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{self._port()}/health", timeout=1)
            return True
        except Exception:
            return False

    def _ensure_server(self):
        import subprocess, time
        if self._reachable():
            return
        if not self.installed():
            raise RuntimeError("MeloTTS 미설치 — 설정의 '설치' 버튼으로 격리 환경을 먼저 구성하세요.")
        env = dict(os.environ)
        env["MELO_PORT"] = str(self._port())
        env["MELO_LANG"] = self.cfg.get("language", "KR")
        print("[tts] launching melo server (격리 venv) ...", flush=True)
        MeloTTS._proc = subprocess.Popen([self._VENV_PY, self._SERVER], env=env, cwd=self._ENG_DIR)
        for _ in range(120):                        # 최초 모델 로드 대기(최대 120s)
            if self._reachable():
                print("[tts] melo server ready", flush=True); return
            if MeloTTS._proc.poll() is not None:
                raise RuntimeError("MeloTTS 서버가 기동 중 종료됨(로그 확인)")
            time.sleep(1)
        raise RuntimeError("MeloTTS 서버 기동 시간 초과")

    def synth(self, text, speaker=None, language=None):
        import urllib.request
        self._ensure_server()
        body = json.dumps({"text": text, "language": self._lang(language),
                           "speed": float(self.cfg.get("speed", 1.0))}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self._port()}/synth",
                                     data=body, headers={"Content-Type": "application/json"})
        audio = urllib.request.urlopen(req, timeout=180).read()
        return audio, "audio/wav"


REGISTRY = {c.name: c for c in (QwenTTS, ElevenLabsTTS, OpenAICompatTTS, PiperTTS, EdgeTTS, MeloTTS)}


def make_provider(cfg_all, name=None):
    name = name or os.environ.get("TTS_PROVIDER") or cfg_all.get("provider", "qwen")
    if name not in REGISTRY:
        print(f"[tts] 알 수 없는 provider '{name}', qwen으로 폴백", flush=True)
        name = "qwen"
    pcfg = (cfg_all.get("providers") or {}).get(name, {})
    return REGISTRY[name](pcfg)


def list_providers(cfg_all):
    out = []
    for name, cls in REGISTRY.items():
        pcfg = (cfg_all.get("providers") or {}).get(name, {})
        inst = cls(pcfg)
        d = inst.info()
        d["configured"] = inst.ready() if name != "qwen" else True   # qwen은 로드 전이라 항상 사용가능
        d["installed"] = inst.installed() if hasattr(inst, "installed") else True
        d["meta"] = {**PROVIDER_META.get(name, {}),
                     "latency": PROVIDER_LATENCY.get(name, {"base": 1.0, "per_char": 0.1})}
        # 프리즌 패키지엔 Python/pip이 없어 pip 기반 설치 불가 → 설치 버튼 숨김(소스에서만).
        d["can_install"] = (not getattr(sys, "frozen", False)) and (install_steps(name) is not None)
        out.append(d)
    return out
