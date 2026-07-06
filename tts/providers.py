"""TTS 프로바이더 추상화 — 백엔드를 설정으로 갈아끼운다.

각 프로바이더는 synth(text, speaker, language) -> (audio_bytes, mimetype) 하나만
구현하면 되고, 서버(tts_server.py)는 활성 프로바이더에 위임한다. 로컬 모델(qwen/piper)은
serialize=True 라 서버의 FIFO 큐를 타고, 클라우드(elevenlabs/openai_compat)는 병렬 허용.

무거운 임포트(torch/qwen)는 전부 load() 안에서 지연 로드 → 클라우드 프로바이더만 쓸 땐
GPU/모델을 건드리지 않는다. API 키는 환경변수로만 받는다(설정 파일에 넣지 않음).
"""
import io, os, json, shutil

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_config.json")

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
    },
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))          # deep copy
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        cfg["provider"] = user.get("provider", cfg["provider"])
        for k, v in (user.get("providers") or {}).items():
            cfg["providers"].setdefault(k, {}).update(v or {})
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
        self._m = Qwen3TTSModel.from_pretrained(
            model, device_map="cuda:0", dtype=torch.bfloat16,
            attn_implementation="flash_attention_2")
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


REGISTRY = {c.name: c for c in (QwenTTS, ElevenLabsTTS, OpenAICompatTTS, PiperTTS)}


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
        out.append(d)
    return out
