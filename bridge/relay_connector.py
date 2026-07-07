"""Avatar relay connector — the REAL bridge between the agent gateway and the
avatar frontend (replaces the bridge's demo responder).

Two agent backends, opposite connection directions (see docs research):

  • hermes   — the Hermes gateway DIALS OUT to us. We are a WebSocket SERVER at
               `/relay` speaking the Relay↔Connector Contract v1 (newline-
               delimited JSON: hello/outbound/interrupt in, descriptor/inbound/
               outbound_result out). Enable on the gateway with
               GATEWAY_RELAY_URL=http://<us>:<port>  (see setup_connector.py).

  • openclaw — WE dial the OpenClaw gateway WS (:18790), `connect` handshake,
               then send user turns via the `agent` RPC and read reply events.
               (OpenClaw has no relay-connector contract; clients connect IN.)

Both adapters bridge to the existing bridge_server.py over localhost HTTP:
  agent reply  -> POST  {bridge}/hermes/out {content}   (parse + SSE to player)
  user typed   <- POST  {here}/say {text}               (bridge forwards it)

So the data plane / performance parsing / SSE all stay in bridge_server.py; this
process only owns the agent transport. Run: `python relay_connector.py`.
"""
from __future__ import annotations
import asyncio, base64, hashlib, hmac, json, os, re, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Windows consoles default to cp949 here; force UTF-8 so log lines with em-dash /
# emoji / Korean don't crash the process on write.
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
# 설정 경로: 프리즌 배포는 GV_ROOT(패키지 루트), 아니면 소스 dir. 없으면 기본값으로 데모 동작.
_ROOT = os.environ.get("GV_ROOT") or (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE)
try:
    CFG = json.load(open(os.path.join(_ROOT, "connector_config.json"), encoding="utf-8"))
except (FileNotFoundError, ValueError):
    CFG = {}

AGENT      = CFG.get("agent", "hermes")
BRIDGE_URL = CFG.get("bridge_url", "http://127.0.0.1:8900").rstrip("/")
SESSION    = CFG.get("session", {})
RELAY      = CFG.get("relay", {})
OPENCLAW   = CFG.get("openclaw", {})
CTRL_PORT  = int(RELAY.get("port", 8901)) + 1000   # local control HTTP (/say,/status) -> 9901

# ── shared: forward an agent reply into the bridge (parse + SSE to player) ──
def push_agent_reply(content: str):
    if not (content or "").strip():
        return
    try:
        data = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(BRIDGE_URL + "/hermes/out", data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print("[relay] push_agent_reply failed:", e, flush=True)

# ── auth (mirrors gateway/relay/auth.py verify_token; used only if a secret is set) ──
def _hmac_hex(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

def verify_upgrade_token(token: str, secret: str) -> bool:
    if not secret:
        return True                      # unauthenticated mode (localhost single-user)
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception:
        return False
    parts = decoded.split(":")
    if len(parts) < 3:
        return False
    sig = parts[-1]
    try:
        exp = int(parts[-2])
    except ValueError:
        return False
    payload = ":".join(parts[:-2])
    if exp != 0 and int(time.time()) > exp:
        return False
    expected = _hmac_hex(f"{payload}:{exp}", secret)
    return hmac.compare_digest(sig, expected)


# ══════════════════════════════════════════════════════════════════════════
#  HERMES adapter — WS SERVER at /relay (gateway dials us)
# ══════════════════════════════════════════════════════════════════════════
class HermesConnector:
    def __init__(self):
        self.conns = set()            # active gateway websockets
        self.loop = None

    def descriptor(self):
        # supports_edit / draft_streaming = False on purpose: the gateway then
        # sends COMPLETE messages (one per segment) instead of incremental edits,
        # so the parser runs once on final text — no partial-performance churn.
        return {
            "contract_version": 1,
            "platform": RELAY.get("descriptor_platform", "relay"),
            "label": RELAY.get("descriptor_label", "Avatar"),
            "max_message_length": 4096,
            "supports_draft_streaming": False,
            "supports_edit": False,
            "supports_threads": False,
            "markdown_dialect": "plain",
            "len_unit": "chars",
            "emoji": "\U0001f9dd",
            "platform_hint": "avatar",
            "pii_safe": False,
        }

    def inbound_frame(self, text: str) -> dict:
        return {"type": "inbound", "event": {
            "source": {
                "platform": RELAY.get("descriptor_platform", "relay"),
                "chat_id": SESSION.get("chat_id", "avatar"),
                "chat_type": "dm",
                "chat_name": SESSION.get("chat_name", "Avatar"),
                "user_id": SESSION.get("user_id", "owner"),
                "user_name": SESSION.get("user_name", "You"),
                "thread_id": None,
                "chat_topic": None,
            },
            "text": text,
            "message_type": "text",
            "message_id": uuid.uuid4().hex,
            "reply_to_message_id": None,
            "media_urls": [],
        }}

    async def send_user_text(self, text: str):
        frame = json.dumps(self.inbound_frame(text)) + "\n"
        dead = []
        for ws in list(self.conns):
            try:
                await ws.send(frame)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.conns.discard(ws)

    async def _handle_outbound(self, ws, frame):
        action = frame.get("action", {}) or {}
        op = action.get("op")
        req_id = frame.get("requestId")
        result = {"success": True, "message_id": "m-" + uuid.uuid4().hex[:8]}
        if op in ("send", "edit", "follow_up"):
            push_agent_reply(action.get("content", ""))
        elif op == "typing":
            result = {"success": True}
        elif op == "get_chat_info":
            result = {"chat_info": {"name": SESSION.get("chat_name", "Avatar"), "type": "dm"}}
        else:
            result = {"success": True}
        if req_id:
            await ws.send(json.dumps({"type": "outbound_result", "requestId": req_id, "result": result}) + "\n")

    async def handler(self, ws):
        # path + auth gate (websockets 13: ws.request)
        path = getattr(getattr(ws, "request", None), "path", "/relay") or "/relay"
        if path.split("?")[0].rstrip("/") not in ("", "/relay"):
            await ws.close(code=1008, reason="bad path"); return
        secret = RELAY.get("auth_secret", "")
        if secret:
            auth = (ws.request.headers.get("Authorization", "") or "")
            token = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not verify_upgrade_token(token, secret):
                await ws.close(code=4401, reason="unauthorized"); return
        self.conns.add(ws)
        print(f"[relay:hermes] gateway connected ({len(self.conns)} live)", flush=True)
        buf = ""
        try:
            async for chunk in ws:
                buf += chunk if isinstance(chunk, str) else chunk.decode()
                *lines, buf = buf.split("\n")
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ftype = frame.get("type")
                    if ftype == "hello":
                        await ws.send(json.dumps({"type": "descriptor", "descriptor": self.descriptor()}) + "\n")
                    elif ftype == "outbound":
                        await self._handle_outbound(ws, frame)
                    elif ftype == "interrupt":
                        pass  # (avatar has no mid-turn stop UI yet)
                    elif ftype == "going_idle":
                        await ws.send(json.dumps({"type": "going_idle_ack"}) + "\n")
                    elif ftype == "inbound_ack":
                        pass
        except Exception as e:
            print("[relay:hermes] conn ended:", e, flush=True)
        finally:
            self.conns.discard(ws)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        host, port = RELAY.get("host", "127.0.0.1"), int(RELAY.get("port", 8901))
        print(f"[relay:hermes] WS server on ws://{host}:{port}/relay "
              f"({'auth' if RELAY.get('auth_secret') else 'no-auth'}) — set gateway "
              f"GATEWAY_RELAY_URL=http://{host}:{port}", flush=True)
        async with websockets.serve(self.handler, host, port):
            await asyncio.Future()   # run forever

    def status(self):
        return {"agent": "hermes", "connected": len(self.conns) > 0, "connections": len(self.conns),
                "listen": f"ws://{RELAY.get('host')}:{RELAY.get('port')}/relay"}


# ══════════════════════════════════════════════════════════════════════════
#  OPENCLAW adapter — WS CLIENT to the OpenClaw gateway (we dial in)
#  Protocol validated end-to-end against openclaw v2026.6.11 (2026-07-06):
#  connect(backend+token)→hello-ok ; agent{message,idempotencyKey,sessionKey}
#  → terminal res(status=ok).result.payloads[].text.
# ══════════════════════════════════════════════════════════════════════════
def _openclaw_token():
    """Shared gateway token for backend loopback auth (config.gateway.auth.token)."""
    if OPENCLAW.get("token"):
        return OPENCLAW["token"]
    try:
        oc = json.load(open(os.path.expanduser("~/.openclaw/openclaw.json"), encoding="utf-8"))
        return ((oc.get("gateway") or {}).get("auth") or {}).get("token", "")
    except Exception:
        return ""

class OpenClawConnector:
    """WS CLIENT to the OpenClaw gateway (protocol validated 2026-07-06, v2026.6.11).
    Envelope: req {type,id,method,params} / res {type,id,ok,payload} / event {type,event,payload}.
    Turn: agent {message, idempotencyKey, sessionKey} -> ack res(status=accepted),
    then terminal res(same id, status=ok, payload.result.payloads[].text)."""
    def __init__(self):
        self.ws = None
        self.loop = None
        self.connected = False
        self._rid = 0
        self.token = _openclaw_token()
        self.session_key = SESSION.get("chat_id", "avatar")

    def _connect_frame(self):
        return {"type": "req", "id": "c1", "method": "connect", "params": {
            "minProtocol": 1, "maxProtocol": 9,
            "client": {"id": "gateway-client", "version": "0.1.0", "platform": "win32", "mode": "backend"},
            "role": "operator", "scopes": OPENCLAW.get("scopes", ["operator.read", "operator.write"]),
            "auth": {"token": self.token}, "userAgent": "avatar-connector/0.1"}}

    async def send_user_text(self, text: str):
        if not self.ws:
            print("[relay:openclaw] not connected; drop user text", flush=True)
            return
        self._rid += 1
        rid = f"m{self._rid}"
        await self.ws.send(json.dumps({"type": "req", "id": rid, "method": "agent", "params": {
            "message": text, "idempotencyKey": rid, "sessionKey": self.session_key}}))

    def _push(self, text):
        # push to bridge off the event loop (blocking HTTP) so the reader keeps flowing
        if text and self.loop:
            self.loop.run_in_executor(None, push_agent_reply, text)

    def _handle(self, f):
        # terminal agent result carries the complete reply (ignore the 'accepted' ack + deltas)
        if f.get("type") == "res":
            p = f.get("payload") or {}
            if p.get("status") == "ok":
                res = p.get("result") or {}
                texts = [x.get("text", "") for x in res.get("payloads", []) if x.get("text")]
                if texts:
                    self._push(" ".join(texts))

    async def run(self):
        self.loop = asyncio.get_running_loop()
        url = OPENCLAW.get("url", "ws://127.0.0.1:18790")
        backoff = 1.0
        while True:
            try:
                print(f"[relay:openclaw] dialing {url} (sessionKey={self.session_key}) …", flush=True)
                async with websockets.connect(url, max_size=None) as ws:
                    self.ws = ws
                    await ws.send(json.dumps(self._connect_frame()))
                    while True:                                    # handshake
                        f = json.loads(await asyncio.wait_for(ws.recv(), 20))
                        if f.get("event") == "connect.challenge":
                            await ws.send(json.dumps(self._connect_frame())); continue
                        if f.get("type") == "res" and f.get("id") == "c1":
                            if not f.get("ok"):
                                raise RuntimeError("connect rejected: %s" % json.dumps(f.get("error")))
                            break
                    self.connected = True
                    backoff = 1.0
                    print("[relay:openclaw] connected", flush=True)
                    async for raw in ws:
                        try:
                            self._handle(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                print("[relay:openclaw] disconnected:", e, flush=True)
            finally:
                self.ws = None
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def status(self):
        return {"agent": "openclaw", "connected": self.connected,
                "dial": OPENCLAW.get("url"), "session_key": self.session_key}


# ══════════════════════════════════════════════════════════════════════════
#  Local control HTTP: /say (user→agent), /status, /health
# ══════════════════════════════════════════════════════════════════════════
CONNECTOR = HermesConnector() if AGENT == "hermes" else OpenClawConnector()

# 로컬 전용 제어 HTTP — 악성 웹페이지가 /say로 에이전트에 명령 못 넣게 오리진/호스트 가드.
_LOCAL_ORIGIN = re.compile(r"^(https?://(127\.0\.0\.1|localhost|tauri\.localhost)(:\d+)?|tauri://localhost|file://|null)$", re.I)

class Ctrl(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _guard(self):
        o = self.headers.get("Origin")
        if o and not _LOCAL_ORIGIN.match(o):
            self._json(403, {"error": "cross-origin blocked"}); return False
        h = (self.headers.get("Host") or "").split(":")[0]
        if h and h not in ("127.0.0.1", "localhost"):
            self._json(403, {"error": "bad host"}); return False
        return True

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        o = self.headers.get("Origin")
        if o and _LOCAL_ORIGIN.match(o):
            self.send_header("Access-Control-Allow-Origin", o)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json(204, {})

    def do_GET(self):
        if not self._guard():
            return
        if self.path.startswith("/status") or self.path.startswith("/health"):
            self._json(200, {"ok": True, **CONNECTOR.status()})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._guard():
            return
        if not self.path.startswith("/say"):
            self._json(404, {"error": "not found"}); return
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        text = (body.get("text") or "").strip()
        if not text:
            self._json(400, {"error": "empty"}); return
        loop = CONNECTOR.loop
        if loop is None:
            self._json(503, {"error": "connector loop not ready"}); return
        fut = asyncio.run_coroutine_threadsafe(CONNECTOR.send_user_text(text), loop)
        try:
            fut.result(timeout=10)
        except Exception as e:
            self._json(502, {"error": str(e)}); return
        self._json(200, {"ok": True, "forwarded": text})


def start_ctrl():
    srv = ThreadingHTTPServer(("127.0.0.1", CTRL_PORT), Ctrl)
    print(f"[relay] control HTTP on http://127.0.0.1:{CTRL_PORT}  (/say /status)", flush=True)
    srv.serve_forever()


def main():
    print(f"[relay] agent={AGENT}  bridge={BRIDGE_URL}", flush=True)
    threading.Thread(target=start_ctrl, daemon=True).start()
    asyncio.run(CONNECTOR.run())


if __name__ == "__main__":
    main()
