"""Avatar bridge: Hermes gateway  <->  avatar frontend.

Outbound (Hermes -> avatar):
  POST /hermes/out {content, metadata?, seq?, output_mode?}
    -> parser extracts emotion markers -> performance payload
    -> pushed to all /events (SSE) subscribers (the player).

Inbound (avatar -> Hermes):  [stub]
  POST /hermes/in {text}  -> queued; a real relay connector forwards this to the
    gateway as a MessageEvent. For now it echoes back for local testing.

This is the connector-side transform the Relay Contract leaves to us (performance
is not native to Hermes). Swap /hermes/out's caller with the real relay WS
connector (gateway dials it, delivers `send{content,metadata}`) — the parse+push
core stays identical.
"""
import json, queue, threading, time, os, re
from flask import Flask, request, jsonify, Response, send_file
import parser as P
import preset as PRE
from mood import MoodTracker

app = Flask(__name__)

# Load the active preset's emotion map into the parser at boot (each preset tunes
# how it emotes via presets/<id>/emotion_map.json) + the mood/affinity tracker
# (state persists in the preset dir, so relationship survives restarts).
MOOD = None
try:
    _active = PRE.active()
    P.apply_emotion_map(_active.get("emotion", {}))
    MOOD = MoodTracker(_active, os.path.join(PRE.PRESETS_DIR, PRE.active_id()))
    print("[bridge] mood tracker up:", MOOD.snapshot())
except Exception as _e:
    print("[bridge] preset/mood load failed:", _e)
_subs = []            # list of Queue, one per SSE client
_subs_lock = threading.Lock()
_seq = 0
_inbound = queue.Queue()

def publish(payload):
    data = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    with _subs_lock:
        for q in list(_subs):
            try: q.put_nowait(data)
            except Exception: pass

# 로컬 전용 서버 — 악성 웹페이지의 크로스사이트 접근(임의 파일 읽기·에이전트 제어) 차단.
_LOCAL_ORIGIN = re.compile(r"^(https?://(127\.0\.0\.1|localhost)(:\d+)?|tauri://localhost|file://|null)$", re.I)

@app.before_request
def _origin_guard():
    if request.method == "OPTIONS":
        return None
    origin = request.headers.get("Origin")
    if origin and not _LOCAL_ORIGIN.match(origin):          # 크로스사이트 fetch 차단
        return jsonify(error="cross-origin blocked"), 403
    host = (request.headers.get("Host") or "").split(":")[0]
    if host and host not in ("127.0.0.1", "localhost"):     # DNS-rebinding 차단
        return jsonify(error="bad host"), 403

@app.after_request
def cors(r):
    origin = request.headers.get("Origin")
    if origin and _LOCAL_ORIGIN.match(origin):
        r.headers["Access-Control-Allow-Origin"] = origin   # allowlist된 로컬 오리진만 반사
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    r.headers["Vary"] = "Origin"
    return r

@app.route("/health")
def health():
    with _subs_lock: n = len(_subs)
    return jsonify(ok=True, subscribers=n)

import re as _re, urllib.request
HERMES_SOUL = os.environ.get("HERMES_SOUL") or os.path.join(
    os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "hermes"),
    "SOUL.md")

# Relay connector control endpoint (relay_connector.py). Port = relay.port + 1000.
def _connector_ctrl():
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(__file__), "connector_config.json"), encoding="utf-8"))
        return "http://127.0.0.1:%d" % (int(cfg.get("relay", {}).get("port", 8901)) + 1000)
    except Exception:
        return "http://127.0.0.1:9901"
CONNECTOR_CTRL = _connector_ctrl()

def _connector_forward(text):
    """Forward user text to the real agent via the relay connector. Returns True
    when a live agent took it (a reply arrives async over /hermes/out), False when
    no connector/agent is up (caller falls back to the demo responder)."""
    try:
        st = json.load(urllib.request.urlopen(CONNECTOR_CTRL + "/status", timeout=1.5))
        if not st.get("connected"):
            return False
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(CONNECTOR_CTRL + "/say", data=data,
                                     headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=10))
        return bool(r.get("ok"))
    except Exception:
        return False

@app.route("/config")
def config():
    # Agent name comes from Hermes' persona (SOUL.md), not hardcoded.
    name, title = "Assistant", ""
    try:
        with open(HERMES_SOUL, encoding="utf-8") as f:
            soul = f.read()
        m = _re.search(r"이름:\*\*\s*([^\n(]+?)\s*(?:\(|$|\n)", soul)          # "- **이름:** 여름 (Yeoreum)"
        if not m: m = _re.search(r"name:\*\*\s*([^\n(]+)", soul, _re.I)
        if m: name = m.group(1).strip()
        h = _re.search(r"^#\s*(.+)$", soul, _re.M)                            # "# 여름 (Yeoreum) — ..."
        if h: title = h.group(1).split("—")[0].strip()
        if title and name == "Assistant": name = title.split("(")[0].strip()
    except Exception as e:
        title = f"(soul read failed: {e})"
    return jsonify(name=name, title=title)

@app.route("/preset")
def preset():
    # Resolved config for the ACTIVE preset (name, font, theme, voice, avatar
    # asset URLs, emotion labels) — the frontend applies it.
    try:
        return jsonify(PRE.active())
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/presets")
def presets():
    return jsonify(active=PRE.active_id(), presets=PRE.list_presets())

@app.route("/pack/<path:sub>")
def pack_serve(sub):
    # 단일 팩(.gvp) 아바타의 에셋을 메모리에서 서빙(source/manifest/emotion_map/web clips).
    import mimetypes
    data = PRE.pack_file(sub)
    if data is None:
        return ("not found", 404)
    mt = mimetypes.guess_type(sub)[0] or "application/octet-stream"
    resp = Response(data, mimetype=mt)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

@app.route("/preset/reload", methods=["POST", "OPTIONS"])
def preset_reload():
    if request.method == "OPTIONS": return ("", 204)
    a = PRE.active()
    P.apply_emotion_map(a.get("emotion", {}))
    return jsonify(ok=True, active=a["id"])

@app.route("/mood")
def mood():
    return jsonify(MOOD.snapshot() if MOOD else {"base": "neutral", "mood": 0, "affinity": 0, "rate": 1.0})

# ── 시스템 상태 + 에이전트 게이트웨이 제어 ─────────────────────────────
import subprocess

def _probe(url, t=1.2):
    try:
        return json.load(urllib.request.urlopen(url, timeout=t))
    except Exception:
        return None

def _cfg_agent():
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(__file__), "connector_config.json"), encoding="utf-8"))
        return cfg.get("agent", "hermes")
    except Exception:
        return "hermes"

def _hermes_exe():
    cand = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "hermes-agent",
                        "venv", "Scripts", "hermes.exe")
    return cand if os.path.isfile(cand) else "hermes"

def _openclaw_mjs():
    return os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules",
                        "openclaw", "openclaw.mjs")

# 슬래시 명령 = 설치한 에이전트가 실제로 서빙하는 명령(텔레그램 메뉴와 동일). 채팅 자동완성용.
# Hermes는 hermes_cli.commands.telegram_menu_commands()에서 동적 생성(빌트인+스킬) → 그걸 그대로 뽑는다.
_FALLBACK_COMMANDS = {
    "hermes":   [{"cmd": c, "desc": ""} for c in ("/help", "/new", "/model", "/status", "/restart", "/stop")],
    # OpenClaw 채널 슬래시 명령(docs/channels 기준). connector_config "commands"로 override 가능.
    "openclaw": [
        {"cmd": "/help",    "desc": "Show available commands"},
        {"cmd": "/status",  "desc": "Show bot/session status"},
        {"cmd": "/new",     "desc": "Start a new session"},
        {"cmd": "/reset",   "desc": "Reset the current session"},
        {"cmd": "/model",   "desc": "Show or switch the AI model"},
        {"cmd": "/compact", "desc": "Compact the session context"},
        {"cmd": "/stop",    "desc": "Stop running processes"},
        {"cmd": "/config",  "desc": "Show or change config"},
        {"cmd": "/goal",    "desc": "Set a standing goal"},
        {"cmd": "/agents",  "desc": "Show active agents"},
    ],
}
_cmd_cache = {}

def _hermes_commands():
    if "hermes" in _cmd_cache:
        return _cmd_cache["hermes"]
    try:
        home = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "hermes-agent")
        py = os.path.join(home, "venv", "Scripts", "python.exe")
        snippet = ("import json;from hermes_cli.commands import telegram_menu_commands,telegram_menu_max_commands;"
                   "c,h=telegram_menu_commands(max_commands=telegram_menu_max_commands());"
                   "print(json.dumps([{'cmd':'/'+str(n).lstrip('/'),'desc':d} for n,d in c]))")
        out = subprocess.check_output([py, "-c", snippet], cwd=home, timeout=30,
                                      stderr=subprocess.DEVNULL, creationflags=0x08000000)
        cmds = json.loads(out.decode("utf-8").strip().splitlines()[-1])
        if cmds:
            _cmd_cache["hermes"] = cmds
        return cmds
    except Exception as e:
        print("[bridge] hermes 명령 조회 실패, 폴백:", str(e)[:120], flush=True)
        return None

@app.route("/commands")
def commands():
    a = _cfg_agent()
    # connector_config.json "commands"(에이전트별 dict 또는 배열)로 override 가능
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(__file__), "connector_config.json"), encoding="utf-8"))
        cc = cfg.get("commands")
        ov = cc.get(a) if isinstance(cc, dict) else (cc if isinstance(cc, list) else None)
        if ov:
            return jsonify(agent=a, commands=[{"cmd": x, "desc": ""} if isinstance(x, str) else x for x in ov])
    except Exception:
        pass
    if a == "hermes":
        c = _hermes_commands()
        if c:
            return jsonify(agent=a, commands=c)
    return jsonify(agent=a, commands=_FALLBACK_COMMANDS.get(a, _FALLBACK_COMMANDS["hermes"]))

@app.route("/status_all")
def status_all():
    tts = _probe("http://127.0.0.1:8899/health")
    stt = _probe("http://127.0.0.1:8898/health")
    conn = _probe(CONNECTOR_CTRL + "/status")
    return jsonify(
        bridge=True, agent_type=_cfg_agent(),
        tts=bool(tts), tts_ready=bool(tts and tts.get("loaded")),
        stt=bool(stt), stt_ready=bool(stt and stt.get("loaded")),
        connector=bool(conn), agent_connected=bool(conn and conn.get("connected")),
    )

@app.route("/agent/start", methods=["POST", "OPTIONS"])
def agent_start():
    if request.method == "OPTIONS": return ("", 204)
    a = _cfg_agent()
    try:
        flags = 0x08000010  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP (win)
        if a == "hermes":
            # restart 사용: 게이트웨이가 커넥터보다 먼저 떠서 초기 dial에 실패한 경우
            # (그땐 재시도 안 함) 재기동으로 재연결을 보장한다. 꺼져 있어도 start로 동작.
            subprocess.Popen([_hermes_exe(), "gateway", "restart"],
                             creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["node", _openclaw_mjs(), "gateway", "--port", "18790"],
                             creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify(ok=True, agent=a, msg=f"{a} 게이트웨이 시작 요청 (부팅 수십 초 소요)")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/agent/stop", methods=["POST", "OPTIONS"])
def agent_stop():
    if request.method == "OPTIONS": return ("", 204)
    a = _cfg_agent()
    try:
        if a == "hermes":
            subprocess.Popen([_hermes_exe(), "gateway", "stop"],
                             creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["powershell", "-NoProfile", "-Command",
                "Get-NetTCPConnection -LocalPort 18790 -State Listen -ErrorAction SilentlyContinue | "
                "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"],
                creationflags=0x08000000)
        return jsonify(ok=True, agent=a, msg=f"{a} 게이트웨이 종료 요청")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/hermes/out", methods=["POST", "OPTIONS"])
def hermes_out():
    if request.method == "OPTIONS": return ("", 204)
    global _seq
    body = request.get_json(force=True, silent=True) or {}
    content = body.get("content", "")
    _seq += 1
    payload = P.parse(content, seq=_seq,
                      output_mode=body.get("output_mode", "both"))
    # 진단 로그: LLM이 실제로 [emotion] 태그를 뱉는지 육안 확인용. 태그O = LLM이 직접 감정 출력,
    # 태그X = 파서가 키워드/이모지로 추론(=LLM은 감정 데이터를 안 나눈 것). 원문 → 분리결과를 한 줄로.
    _lead_tag = content.lstrip()[:1] == "["
    _emos = [b.get("emotion") for b in payload.get("performance", {}).get("beats", [])]
    _ndata = len(payload.get("data", []))
    print(f"[hermes/out] 원문[{'태그O' if _lead_tag else '태그X→추론'}]: {content[:180]!r}"
          f"  →  감정beats={_emos} · data평면={_ndata}건", flush=True)
    # agent beats color the mood; every performance carries the mood snapshot
    if MOOD:
        MOOD.on_beats(payload.get("dialogue", {}).get("beats"))
        payload["mood"] = MOOD.snapshot()
    publish(payload)
    return jsonify(ok=True, payload=payload)

@app.route("/hermes/in", methods=["POST", "OPTIONS"])
def hermes_in():
    if request.method == "OPTIONS": return ("", 204)
    global _seq
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify(ok=True, forwarded=text)
    _inbound.put({"text": text})
    # IMMEDIATE reaction to the user's words (praise/scold) — fires BEFORE the
    # agent replies, so the causality is felt ("혼내니까 바로 움찔한다").
    # Performance-only payload: no dialogue text, so no chat bubble.
    if MOOD:
        r = MOOD.on_user_message(text)
        if r:
            global _seq
            _seq += 1
            re_emo = P.canon(r["emotion"]) or "neutral"   # 반응 감정명을 활성 taxonomy로 정규화(옛 이름 안전)
            publish({"session_id": "mood", "seq": _seq,
                     "dialogue": {"text": "", "beats": [
                         {"emotion": re_emo, "text": "", "intensity": r["intensity"]}]},
                     "data": [], "reaction": r["kind"],
                     "performance": {"emotion": re_emo, "intensity": r["intensity"],
                                     "beats": [{"emotion": re_emo, "intensity": r["intensity"]}]},
                     "context": {"trigger_type": "user_reaction", "requires_confirmation": False},
                     "mood": MOOD.snapshot()})
    # REAL path: hand to the relay connector, which delivers it to the live agent
    # gateway (Hermes relay / OpenClaw). The agent's reply arrives async over
    # /hermes/out. A slash command flows through unchanged — the agent owns it.
    if _connector_forward(text):
        return jsonify(ok=True, forwarded=text, mode="agent")
    # DEMO fallback (no connector/agent connected): echo a reply so the messenger
    # loop is visible standalone.
    if text.startswith("/"):
        cmd = text.split()[0]
        reply = f"[skeptical] {cmd} 은 Hermes 명령이야 — 게이트웨이(릴레이) 연결되면 그대로 실행돼."
    else:
        reply = f"[happy] 응, \"{text}\" 받았어! (데모 응답 — 실제로는 Hermes/LLM이 답해)"
    _seq += 1
    publish(P.parse(reply, seq=_seq))
    return jsonify(ok=True, forwarded=text, mode="demo")

# 파일 전송(데이터 평면). 임의 경로 읽기 방지 — allowlist된 outbox 밑만 서빙.
# 기본 outbox = <repo>/files ; 추가 경로는 FILE_OUTBOX 환경변수(os.pathsep 구분).
def _file_bases():
    bases, env = [], os.environ.get("FILE_OUTBOX", "")
    for p in (env.split(os.pathsep) if env else []):
        if p.strip():
            bases.append(os.path.realpath(p.strip()))
    bases.append(os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "files")))
    return bases
_FILE_BASES = _file_bases()

@app.route("/file")
def serve_file():
    path = request.args.get("path", "")
    if not path:
        return jsonify(error="not found"), 404
    rp = os.path.realpath(path)                              # 심볼릭·../ 정규화
    if not (os.path.isfile(rp) and any(rp == b or rp.startswith(b + os.sep) for b in _FILE_BASES)):
        return jsonify(error="forbidden"), 403              # outbox 밖 = 차단
    return send_file(rp, as_attachment=True, download_name=os.path.basename(rp))

@app.route("/events")
def events():
    q = queue.Queue(maxsize=50)
    with _subs_lock: _subs.append(q)
    def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                try: yield q.get(timeout=15)
                except queue.Empty: yield ": keepalive\n\n"
        finally:
            with _subs_lock:
                if q in _subs: _subs.remove(q)
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8900, threaded=True)
