"""Preset loader — resolves the active avatar preset (bundle of avatar clips +
font + theme + voice + emotion map + role) for the engine.

A preset is a directory under presets/ with a preset.json. The active one is named
in presets/active.txt. Asset paths in preset.json are either root-absolute URLs
(served by the static server, e.g. /segments/web) or preset-relative (e.g.
avatar/segments) which resolve to /presets/<id>/... . Presets are pure data.
"""
import json, os

import sys
# 패키지(frozen)에선 GV_ROOT(런처가 설정) 우선 — presets/segments를 패키지에서 찾게.
ROOT = os.environ.get("GV_ROOT") or (
    os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRESETS_DIR = os.path.join(ROOT, "presets")

# ── 단일 팩(.gvp) 지원: presets/<id>.gvp 파일 하나에 아바타 전체가 들어감 ──
# 폴더 프리셋(커뮤니티 모드)과 공존. 팩이면 클립을 디스크에 안 풀고 브리지가 메모리 서빙.
try:
    import gvpack
except Exception:
    gvpack = None
_BRIDGE_URL = "http://127.0.0.1:8900"        # 팩 에셋을 브리지가 서빙(플레이어가 이 URL로 fetch)
_PACK = {"id": None, "files": None}

def _pack_path(pid):
    return os.path.join(PRESETS_DIR, (pid or "_") + ".gvp")

def _load_pack(pid):
    """활성 프리셋이 <pid>.gvp면 메모리로 로드(캐시). files dict 반환, 아니면 None."""
    if not gvpack:
        return None
    p = _pack_path(pid)
    if not (pid and os.path.isfile(p) and gvpack.is_pack(p)):
        return None
    if _PACK["id"] != pid:
        _PACK["id"], _PACK["files"] = pid, gvpack.read(p)
    return _PACK["files"]

def pack_file(sub):
    """브리지 /pack/<sub> 서빙용 — 로드된 팩에서 바이트 반환(없으면 None)."""
    return (_PACK["files"] or {}).get(sub)


def _resolve_url(pid, p):
    if not p:
        return p
    if p.startswith("/") or "://" in p:
        return p                                   # root-absolute / external
    return f"/presets/{pid}/{p.lstrip('./')}"      # preset-relative


def active_id():
    try:
        with open(os.path.join(PRESETS_DIR, "active.txt"), encoding="utf-8") as f:
            return f.read().strip() or "_template"
    except Exception:
        return "_template"


# ── convention-over-config: 폴더=프리셋, 폴더명=이름, 파일명=매핑 ──
# 프리셋 폴더에 규칙대로 이름 붙은 mp4만 넣으면 manifest를 자동 생성한다(공존:
# preset.json/manifest.json이 있으면 그걸 우선). 파일명 규칙:
#   <emotion>.mp4              → 감정 표현(transition)
#   <emotion>__pos|neg|neu.mp4 → 무드별 변주
#   idle*, alive_idle*, talking*, groove*, blink → 중립 대기 루프
#   idle_positive / alive_idle_neg 등 (idle+무드)  → 3축 mood_idle
#   *_rest.mp4                  → 눈감고 쉬는 rest
_SEG_SUBDIRS = ["avatar/segments", "segments", ""]     # 클립 위치 후보(우선순)
_manifest_cache = {}                                    # pid -> (signature, manifest dict)


def _mood_in(low):
    if "positive" in low or "_pos" in low or low.endswith("pos"): return "positive"
    if "negative" in low or "_neg" in low or low.endswith("neg"): return "negative"
    if "neutral" in low or "_neu" in low: return "neutral"
    return None


def _classify(stem):
    """파일명(확장자 제외) → 세그먼트 분류 dict (name/file/frames/fps 제외)."""
    low = stem.lower()
    if "__" in stem:                                    # 무드 변주: emotion__pos
        emo, mood = stem.rsplit("__", 1)
        mf = {"pos": "positive", "neg": "negative", "neu": "neutral",
              "positive": "positive", "negative": "negative", "neutral": "neutral"}.get(mood.lower())
        if mf:
            return {"emotion": emo, "kind": "transition", "group": "mood_variant",
                    "mood": mf, "tier": "mood_variant"}
    if low.endswith("rest"):                            # 눈감고 쉬기
        return {"emotion": "neutral", "kind": "mood_idle", "group": "mood",
                "mood": _mood_in(low) or "positive", "style": "rest_eyes_closed"}
    if "idle" in low and _mood_in(low):                 # 무드 대기(3축)
        return {"emotion": "neutral", "kind": "mood_idle", "group": "mood", "mood": _mood_in(low)}
    if (low in ("idle", "blink") or low.startswith("idle") or low.startswith("alive_idle")
            or low.startswith("talking") or low.startswith("groove")):
        return {"emotion": "neutral", "kind": "loop", "group": "alive"}
    return {"emotion": stem, "kind": "transition", "group": "emotion"}   # 기본: 감정 표현


def _probe(path):
    """mp4 → (frames, fps) 최선노력. ffmpeg 없으면 (0, 24.0) 기본값(재생엔 지장 없음)."""
    import re, subprocess
    exe = None
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        exe = shutil.which("ffmpeg")
    if not exe:
        return 0, 24.0
    try:
        out = subprocess.run([exe, "-i", path], capture_output=True, text=True, timeout=20).stderr
        fps = 24.0
        mfps = re.search(r"([\d.]+)\s*fps", out)
        if mfps: fps = float(mfps.group(1))
        md = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
        frames = 0
        if md:
            dur = int(md.group(1)) * 3600 + int(md.group(2)) * 60 + float(md.group(3))
            frames = int(round(dur * fps))
        return frames, round(fps, 3)
    except Exception:
        return 0, 24.0


def _find_segdir(pid):
    """클립이 있는 디렉토리(abs, url-base) 반환. 없으면 (None, None)."""
    for rel in _SEG_SUBDIRS:
        d = os.path.join(PRESETS_DIR, pid, rel.replace("/", os.sep)) if rel else os.path.join(PRESETS_DIR, pid)
        if os.path.isdir(d) and any(f.lower().endswith(".mp4") for f in os.listdir(d)):
            url = f"/presets/{pid}" + (("/" + rel) if rel else "")
            return d, url
    return None, None


def _auto_manifest(pid):
    """폴더 스캔으로 manifest dict 생성(+캐시). 클립 없으면 None."""
    segdir, segurl = _find_segdir(pid)
    if not segdir:
        return None, None
    clips = sorted(f for f in os.listdir(segdir) if f.lower().endswith(".mp4"))
    # 파일 목록 + mtime으로 캐시 키 구성 — 같은 이름의 클립을 교체해도 갱신됨
    sig = tuple((f, os.path.getmtime(os.path.join(segdir, f))) for f in clips)
    cached = _manifest_cache.get(pid)
    if cached and cached[0] == sig:
        return cached[1], segurl
    segs = []
    for f in clips:
        stem = f[:-4]
        d = _classify(stem)
        frames, fps = _probe(os.path.join(segdir, f))
        d.update({"name": stem, "file": f, "frames": frames, "fps": fps})
        segs.append(d)
    man = {"source": "auto", "version": str(len(clips)), "segments": segs}
    _manifest_cache[pid] = (sig, man)
    return man, segurl


def _resolve_manifest(pid, m):
    """explicit(preset.json avatar.manifest) → 그 URL. 없으면 auto-manifest를 파일로 써서
    URL 제공(공존). (manifest_url, segments_base_url) 반환."""
    av = (m or {}).get("avatar", {}) or {}
    raw = av.get("manifest")
    if raw:                                             # 명시 매니페스트 우선
        return _resolve_url(pid, raw), _resolve_url(pid, av.get("segments_base"))
    man, segurl = _auto_manifest(pid)                   # 자동
    if not man:
        return None, None
    mp = os.path.join(PRESETS_DIR, pid, "manifest.json")
    try:
        with open(mp, "w", encoding="utf-8") as fh:
            json.dump(man, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return f"/presets/{pid}/manifest.json", segurl


def _default_emotion_map():
    try:
        with open(os.path.join(PRESETS_DIR, "_template", "emotion_map.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def list_presets():
    out = []
    if not os.path.isdir(PRESETS_DIR):
        return out
    for name in sorted(os.listdir(PRESETS_DIR)):
        pdir = os.path.join(PRESETS_DIR, name)
        if not os.path.isdir(pdir) or name.startswith("."):
            continue
        has_json = os.path.isfile(os.path.join(pdir, "preset.json"))
        has_clips = _find_segdir(name)[0] is not None
        if not (has_json or has_clips):                 # 프리셋으로 안 침
            continue
        disp, sfw = name, True
        if has_json:
            try:
                with open(os.path.join(pdir, "preset.json"), encoding="utf-8") as f:
                    mj = json.load(f)
                disp = mj.get("name", name); sfw = mj.get("sfw", True)
            except Exception:
                pass
        out.append({"id": name, "dir": name, "name": disp, "sfw": sfw,
                    "template": name.startswith("_")})
    return out


def _load_from_pack(pid, files):
    """팩(.gvp) 내부 데이터로 프리셋 구성. 에셋 URL은 브리지 /pack 엔드포인트로
    (클립은 메모리에서 서빙 — 디스크에 낱개로 안 풀림). 나머지는 폴더 프리셋과 동일 형태."""
    def _j(n):
        try: return json.loads(files[n].decode("utf-8"))
        except Exception: return {}
    m = _j("preset.json")
    emo = _j("emotion_map.json") or _default_emotion_map()
    base = _BRIDGE_URL + "/pack"
    srcname = next((k for k in files if k.startswith("source.")), "source.png")
    theme = m.get("theme")
    font = dict(m.get("font") or {})
    if font.get("url") and "://" not in font["url"]:
        font["url"] = base + "/" + font["url"].lstrip("/")
    return {
        "id": m.get("id", pid), "name": m.get("name", pid),
        "name_en": m.get("name_en", m.get("name", pid)),
        "theme": (theme or {}).get("id") if isinstance(theme, dict) else theme,
        "theme_obj": theme if isinstance(theme, dict) and "id" not in theme else None,
        "voice": m.get("voice", {}), "font": font,
        "avatar": {"source": base + "/" + srcname,
                   "manifest": base + "/manifest.json",
                   "segments_base": base + "/web"},
        "emotion": emo, "sfw": m.get("sfw", True), "packed": True,
    }


def load(pid=None):
    pid = pid or active_id()
    files = _load_pack(pid)
    if files is not None:                                # 단일 팩(.gvp) 프리셋
        return _load_from_pack(pid, files)
    pdir = os.path.join(PRESETS_DIR, pid)
    # preset.json은 이제 선택 — 없으면 폴더명/기본값으로 자동 구성
    m = {}
    pj = os.path.join(pdir, "preset.json")
    if os.path.isfile(pj):
        try:
            with open(pj, encoding="utf-8") as f:
                m = json.load(f)
        except Exception: m = {}
    # emotion map: 명시 파일 > preset.json 인라인 > 프리셋 내 emotion_map.json > 내장 기본
    emo = {}
    emo_ref = m.get("emotion_map")
    if isinstance(emo_ref, dict):
        emo = emo_ref
    else:
        for cand in ([emo_ref] if isinstance(emo_ref, str) else []) + ["emotion_map.json"]:
            try:
                with open(os.path.join(pdir, cand), encoding="utf-8") as f:
                    emo = json.load(f)
                break
            except Exception:
                continue
    if not emo:
        emo = _default_emotion_map()
    font = dict(m.get("font") or {})
    if font.get("url"):
        font["url"] = _resolve_url(pid, font["url"])
    manifest_url, seg_base = _resolve_manifest(pid, m)
    av = m.get("avatar", {}) or {}
    src = av.get("source")
    if not src:                                         # 자동: source.png 있으면 사용
        for cand in ("avatar/source.png", "source.png"):
            if os.path.isfile(os.path.join(pdir, cand.replace("/", os.sep))):
                src = cand; break
    return {
        "id": m.get("id", pid),
        "name": m.get("name", pid),                    # 기본 = 폴더명
        "name_en": m.get("name_en", m.get("name", pid)),
        "theme": (m.get("theme", {}) or {}).get("id") if isinstance(m.get("theme"), dict) else m.get("theme"),
        "theme_obj": m.get("theme") if isinstance(m.get("theme"), dict) and "id" not in m.get("theme") else None,
        "voice": m.get("voice", {}),
        "font": font,
        "avatar": {
            "source": _resolve_url(pid, src),
            "manifest": manifest_url,
            "segments_base": seg_base,
        },
        "emotion": emo,
        "sfw": m.get("sfw", True),
    }


def active():
    return load(active_id())


def available_emotions(pid=None):
    """활성 프리셋이 실제로 '채워둔' 감정 = manifest(명시 또는 자동)의 transition emotion들
    (+ neutral). 부분 프리셋에서 에이전트에게 이 감정만 알린다. 못 구하면 None(→ 전체 폴백)."""
    pid = pid or active_id()
    files = _load_pack(pid)
    if files is not None:                                # 팩: 내부 매니페스트에서 감정 추출
        try:
            man = json.loads(files["manifest.json"].decode("utf-8"))
            emos = {s.get("emotion") for s in man.get("segments", [])
                    if s.get("kind") == "transition" and s.get("emotion")}
            emos.add("neutral")
            return emos or None
        except Exception:
            return None
    try:
        m = {}
        pj = os.path.join(PRESETS_DIR, pid, "preset.json")
        if os.path.isfile(pj):
            m = json.load(open(pj, encoding="utf-8"))
        raw = (m.get("avatar") or {}).get("manifest")
        if raw:                                         # 명시 매니페스트 파일
            p = os.path.join(ROOT, raw.lstrip("/").replace("/", os.sep)) if raw.startswith("/") \
                else os.path.join(PRESETS_DIR, pid, raw.replace("/", os.sep))
            with open(p, encoding="utf-8") as f:
                man = json.load(f)
        else:                                           # 자동
            man, _ = _auto_manifest(pid)
        emos = {s.get("emotion") for s in (man or {}).get("segments", [])
                if s.get("kind") == "transition" and s.get("emotion")}
        emos.add("neutral")
        return emos or None
    except Exception:
        return None
