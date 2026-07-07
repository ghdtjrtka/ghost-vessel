"""Avatar connector install/setup flow.

Answers the three install-time questions and wires the connection location:

  1. agent          : hermes | openclaw
  2. agent_runtime  : windows | wsl2      (where the AGENT runs; avatar is Windows)
  3. connection     : computed from (1)+(2) and written to both sides.

Why runtime matters — the two agents connect in OPPOSITE directions, and WSL2's
localhost forwarding is asymmetric:

  hermes  : the gateway DIALS the connector (connector = server on Windows).
            - agent windows : gateway → ws://127.0.0.1:<port>/relay
            - agent wsl2    : WSL2 must reach the Windows host, so the connector
              binds 0.0.0.0 and the gateway dials ws://<windows-host-ip>:<port>.
  openclaw: the avatar DIALS the gateway (gateway = server).
            - agent windows : avatar → ws://127.0.0.1:18790
            - agent wsl2    : WSL2 auto-forwards listening ports to Windows
              localhost, so ws://127.0.0.1:18790 still works (fallback: wsl ip).

Writes connector_config.json (avatar side) and, for hermes, GATEWAY_RELAY_URL
into the gateway's ~/.hermes/.env (Windows path or inside WSL). Idempotent.

Also injects, scoped to the avatar channel:
  - the Avatar Output Contract (emotion/3-plane format) — auto-built from the
    active preset's available emotions.
  - the PERSONA (character) into the agent's SOUL.md — default name from the
    preset (여름). `--name X` renames the persona to X and propagates it. An
    existing hand-written SOUL.md (no managed marker) is kept unless --name is
    given, so a user's custom persona (e.g. 미나미) is never silently clobbered.

Usage:
  python setup_connector.py                         # interactive
  python setup_connector.py --agent hermes --agent-runtime windows
  python setup_connector.py --agent hermes --agent-runtime windows --name 미나미
  python setup_connector.py --agent openclaw --agent-runtime wsl2 --dry-run
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agent_contract import build_contract

# 활성 프리셋이 채운 감정만 규약에 노출 (부분 프리셋 지원 — 없는 감정 태그를 안 뱉게).
# manifest를 못 읽으면 전체 감정으로 폴백.
def _contract():
    try:
        import preset
        emo = (preset.active() or {}).get("emotion", {}) or {}
        return build_contract(
            preset.available_emotions(),
            axis=emo.get("axis"), labels=emo.get("labels"),
            descriptions=emo.get("descriptions") or emo.get("catalog"),
        )
    except Exception:
        return build_contract()

AVATAR_OUTPUT_CONTRACT = _contract()

# Hermes config.yaml (Windows-native install). Override with HERMES_CONFIG env.
HERMES_CONFIG = os.environ.get("HERMES_CONFIG") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local")), "hermes", "config.yaml")
OPENCLAW_WORKSPACE = os.path.expanduser("~/.openclaw/workspace")
# 프리즌 배포(gv-setup.exe)에선 GV_ROOT(패키지 루트)에 써야 커넥터가 읽는다.
_CFG_ROOT = os.environ.get("GV_ROOT") or (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else HERE)
CFG_PATH = os.path.join(_CFG_ROOT, "connector_config.json")
OPENCLAW_DEFAULT_PORT = 18790


def load_cfg():
    return json.load(open(CFG_PATH, encoding="utf-8"))


def save_cfg(cfg):
    json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ── WSL helpers ────────────────────────────────────────────────────────────
def wsl_available() -> bool:
    try:
        r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def windows_host_ip_from_wsl() -> str | None:
    """The IP a WSL2 process uses to reach the Windows host (the default route
    gateway). Returns None if it can't be resolved (e.g. mirrored-mode where
    localhost already works)."""
    try:
        r = subprocess.run(
            ["wsl", "-e", "bash", "-lc", "ip route show default | awk '{print $3; exit}'"],
            capture_output=True, text=True, timeout=8)
        ip = (r.stdout or "").strip()
        return ip or None
    except Exception:
        return None


def wsl_distro_ip() -> str | None:
    """The WSL2 distro's own IP (for the openclaw-in-wsl2 fallback)."""
    try:
        r = subprocess.run(["wsl", "-e", "bash", "-lc", "hostname -I | awk '{print $1}'"],
                           capture_output=True, text=True, timeout=8)
        ip = (r.stdout or "").strip()
        return ip or None
    except Exception:
        return None


# ── env writers (hermes GATEWAY_RELAY_URL) ─────────────────────────────────
def _upsert_env_lines(text: str, key: str, value: str) -> str:
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(key + "=")]
    lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _hermes_home() -> str:
    """The gateway loads .env from HERMES_HOME (NOT ~/.hermes on this install)."""
    return (os.environ.get("HERMES_HOME")
            or os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "hermes"))

def write_hermes_env_windows(url: str, dry: bool) -> str:
    path = os.path.join(_hermes_home(), ".env")
    if not os.path.isdir(os.path.dirname(path)):
        path = os.path.join(os.path.expanduser("~"), ".hermes", ".env")   # 폴백
    if dry:
        return f"(dry-run) would write GATEWAY_RELAY_URL={url} to {path}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cur = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if not os.path.exists(path + ".avatar-bak") and cur:
        open(path + ".avatar-bak", "w", encoding="utf-8").write(cur)      # 원본 백업 1회
    open(path, "w", encoding="utf-8").write(_upsert_env_lines(cur, "GATEWAY_RELAY_URL", url))
    return f"wrote GATEWAY_RELAY_URL={url} to {path}"


def write_hermes_env_wsl(url: str, dry: bool) -> str:
    # Upsert inside the WSL home (~/.hermes/.env) via a bash one-liner.
    script = (
        "d=~/.hermes; f=$d/.env; mkdir -p $d; touch $f; "
        "grep -v '^GATEWAY_RELAY_URL=' $f > $f.tmp 2>/dev/null || true; "
        f"echo 'GATEWAY_RELAY_URL={url}' >> $f.tmp; mv $f.tmp $f; echo $f"
    )
    if dry:
        return f"(dry-run) would run in WSL: set GATEWAY_RELAY_URL={url} in ~/.hermes/.env"
    try:
        r = subprocess.run(["wsl", "-e", "bash", "-lc", script],
                           capture_output=True, text=True, timeout=15)
        loc = (r.stdout or "").strip() or "~/.hermes/.env"
        return f"wrote GATEWAY_RELAY_URL={url} to WSL {loc}"
    except Exception as e:
        return f"[!] failed to write WSL env: {e}"


# ── output-format contract injection (THE packaging point) ─────────────────
def inject_hermes_hint(runtime: str, url: str, dry: bool) -> str:
    """Patch Hermes' config.yaml (at $HERMES_HOME) to:
      1. agent.platform_hints.relay.append = Avatar Output Contract  (scoped to the
         relay channel only — the LLM emits emotion-beat/3-plane format on the
         avatar channel, other channels like Telegram untouched).
      2. gateway.relay_url = <url>  (activates the relay adapter + points the
         gateway at our connector — the guaranteed-read path, no .env ambiguity).
    Backed up + verified."""
    if runtime != "windows":
        return ("Hermes runs in WSL2 — run this inside WSL to patch its config, or set\n"
                "        HERMES_CONFIG to the WSL config path. (Your install is windows-native.)")
    import yaml
    path = HERMES_CONFIG
    if not os.path.exists(path):
        return f"[!] Hermes config not found at {path} (set HERMES_CONFIG)"
    if dry:
        return f"(dry-run) would set agent.platform_hints.relay + gateway.relay_url={url} in {path}"
    cfg = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
    cur_hint = (((cfg.get("agent") or {}).get("platform_hints") or {}).get("relay") or {})
    cur_url = (cfg.get("gateway") or {}).get("relay_url")
    if isinstance(cur_hint, dict) and cur_hint.get("append") == AVATAR_OUTPUT_CONTRACT and cur_url == url:
        return "already up to date (platform_hints.relay + gateway.relay_url)"
    bak = path + ".avatar-bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    cfg.setdefault("agent", {}).setdefault("platform_hints", {})["relay"] = {"append": AVATAR_OUTPUT_CONTRACT}
    cfg.setdefault("gateway", {})["relay_url"] = url
    out = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, width=4096, default_flow_style=False)
    open(path, "w", encoding="utf-8").write(out)
    # verify it still parses and both edits are present (stable sentinel = the
    # contract title, which does not change when the body is edited).
    rl = yaml.safe_load(open(path, encoding="utf-8").read())
    if ("아바타 출력 규약" not in rl["agent"]["platform_hints"]["relay"]["append"]
            or rl["gateway"]["relay_url"] != url):
        shutil.copy2(bak, path)
        return "[!] verification failed — restored backup"
    return f"set platform_hints.relay + gateway.relay_url in {path} (backup: {os.path.basename(bak)})"


_OC_MARK_A = "<!-- AVATAR_OUTPUT_CONTRACT:BEGIN (managed by setup_connector.py) -->"
_OC_MARK_B = "<!-- AVATAR_OUTPUT_CONTRACT:END -->"

def write_openclaw_contract(dry: bool) -> str:
    """Inject the contract into the OpenClaw workspace AGENTS.md (bootstrap file
    OpenClaw injects into its system prompt). Idempotent: managed marker block is
    replaced on re-run. NOTE: unlike Hermes' platform_hints.relay this is agent-
    global (OpenClaw has no per-channel prompt hook)."""
    dest = os.path.join(OPENCLAW_WORKSPACE, "AGENTS.md")
    if dry:
        return f"(dry-run) would merge contract block into {dest}"
    try:
        os.makedirs(OPENCLAW_WORKSPACE, exist_ok=True)
        cur = open(dest, encoding="utf-8").read() if os.path.exists(dest) else ""
        block = f"{_OC_MARK_A}\n{AVATAR_OUTPUT_CONTRACT}\n{_OC_MARK_B}"
        if _OC_MARK_A in cur:
            import re as _re2
            new = _re2.sub(_re2.escape(_OC_MARK_A) + r".*?" + _re2.escape(_OC_MARK_B),
                           block, cur, flags=_re2.S)
        else:
            new = (cur.rstrip() + "\n\n" if cur.strip() else "") + block + "\n"
        open(dest, "w", encoding="utf-8").write(new)
        # keep the standalone copy for reference too
        open(os.path.join(OPENCLAW_WORKSPACE, "AVATAR_OUTPUT_CONTRACT.md"),
             "w", encoding="utf-8").write(AVATAR_OUTPUT_CONTRACT)
        return f"merged contract block into {dest} (idempotent markers)"
    except Exception as e:
        return f"[!] could not write OpenClaw contract: {e}"


# ── persona injection (name-driven; 설치 기본 = 프리셋 이름 '여름') ─────────────
# 규약(출력형식)과 별개로, 캐릭터(페르소나)를 에이전트에 주입한다. 이름은 프리셋 name에서
# 오고(기본 여름), --name 으로 바꾸면 그 이름으로 전파된다. 기존에 사용자가 손수 쓴
# 커스텀 SOUL.md(우리 마커 없음)는 --name 없이는 덮지 않는다(사용자의 미나미 등 보호).
_SOUL_MARK = "<!-- AVATAR_PERSONA (managed by setup_connector.py) -->"

def _persona(name_override=None):
    """(표시이름, SOUL 마크다운) — 활성 프리셋 role.md 기반. name_override로 이름 전파."""
    import preset
    cfg = preset.active() or {}
    base_ko = cfg.get("name") or "여름"
    base_en = cfg.get("name_en") or base_ko
    name = ((name_override or base_ko).strip() or base_ko)
    body = ""
    try:
        pid = preset.active_id()
        rp = os.path.join(preset.PRESETS_DIR, pid, "role.md")
        if os.path.isfile(rp):
            body = open(rp, encoding="utf-8").read()
        else:                                              # 단일 팩(.gvp) 프리셋
            body = preset.pack_file("role.md").decode("utf-8")
    except Exception:
        body = ""
    if name_override:                                      # 이름 전파: 프리셋 기본명 → 지정명
        for base in (base_ko, base_en):
            if base and base != name:
                body = body.replace(base, name)
    if not body:
        body = f"# {name}\n\n(role.md를 찾지 못함 — 프리셋을 확인하세요.)\n"
    return name, body

def _write_persona_file(path, name, body, dry, label):
    if dry:
        return f"(dry-run) would write persona '{name}' → {path}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cur = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if not os.path.exists(path + ".avatar-bak") and cur:
        open(path + ".avatar-bak", "w", encoding="utf-8").write(cur)   # 원본 1회 백업
    content = (f"{_SOUL_MARK}\n<!-- name={name} · 프리셋 role.md에서 생성 · 이름 변경은 "
               f"preset.json name 또는 --name -->\n\n{body.strip()}\n")
    open(path, "w", encoding="utf-8").write(content)
    return f"wrote persona '{name}' → {path} (backup: {os.path.basename(path)}.avatar-bak)"

def inject_persona(agent, runtime, name_override, dry):
    name, body = _persona(name_override)
    if agent == "hermes":
        if runtime != "windows":
            return "Hermes in WSL2 — run inside WSL to write SOUL.md (skipped)"
        path = os.path.join(_hermes_home(), "SOUL.md")
    else:
        path = os.path.join(OPENCLAW_WORKSPACE, "SOUL.md")
    # 보호: 기존 파일이 커스텀(우리 마커 없음)이고 --name 미지정이면 덮지 않음
    if os.path.exists(path) and name_override is None:
        try: cur = open(path, encoding="utf-8").read()
        except Exception: cur = ""
        if _SOUL_MARK not in cur:
            return (f"기존 커스텀 페르소나 유지 → {path} "
                    f"(신규 설치는 '{name}'로 자동 생성 · 덮으려면 --name)")
    return _write_persona_file(path, name, body, dry, agent)


# ── the plan ────────────────────────────────────────────────────────────────
def compute(agent: str, runtime: str, cfg: dict) -> dict:
    port = int(cfg.get("relay", {}).get("port", 8901))
    out = {"notes": []}
    if agent == "hermes":
        if runtime == "windows":
            out["relay_host"] = "127.0.0.1"
            out["gateway_relay_url"] = f"http://127.0.0.1:{port}"
        else:  # wsl2 → connector must be reachable from inside WSL2
            out["relay_host"] = "0.0.0.0"
            host_ip = windows_host_ip_from_wsl()
            if host_ip:
                out["gateway_relay_url"] = f"http://{host_ip}:{port}"
                out["notes"].append(f"Windows host IP from WSL2 = {host_ip}")
            else:
                out["gateway_relay_url"] = f"http://127.0.0.1:{port}"
                out["notes"].append("Could not detect host IP; assuming WSL mirrored networking "
                                    "(localhost). If the gateway can't connect, set the host IP manually.")
            out["notes"].append("Windows Firewall must allow inbound TCP %d (WSL2 → Windows)." % port)
    else:  # openclaw — avatar dials the gateway
        if runtime == "windows":
            out["openclaw_url"] = f"ws://127.0.0.1:{OPENCLAW_DEFAULT_PORT}"
        else:
            out["openclaw_url"] = f"ws://127.0.0.1:{OPENCLAW_DEFAULT_PORT}"
            out["notes"].append("WSL2 forwards listening ports to Windows localhost, so "
                                "127.0.0.1 should reach the in-WSL gateway.")
            ip = wsl_distro_ip()
            if ip:
                out["notes"].append(f"If localhost fails, use the WSL distro IP: ws://{ip}:{OPENCLAW_DEFAULT_PORT}")
    return out


def apply(agent: str, runtime: str, dry: bool, name: str | None = None) -> None:
    cfg = load_cfg()
    plan = compute(agent, runtime, cfg)
    cfg["agent"] = agent
    cfg["agent_runtime"] = runtime
    if agent == "hermes":
        cfg.setdefault("relay", {})["host"] = plan["relay_host"]
    else:
        cfg.setdefault("openclaw", {})["url"] = plan["openclaw_url"]

    print("\n=== connector setup ===")
    print(f"  agent          : {agent}")
    print(f"  agent runtime  : {runtime}")
    if agent == "hermes":
        print(f"  connector bind : {plan['relay_host']}:{cfg['relay']['port']}  (WS /relay)")
        print(f"  gateway dials  : {plan['gateway_relay_url']}")
    else:
        print(f"  avatar dials   : {plan['openclaw_url']}")
    for n in plan["notes"]:
        print(f"  - {n}")

    if not dry:
        save_cfg(cfg)
        print(f"  [ok] wrote {CFG_PATH}")

    # write the agent side
    if agent == "hermes":
        url = plan["gateway_relay_url"]
        msg = (write_hermes_env_windows if runtime == "windows" else write_hermes_env_wsl)(url, dry)
        print(f"  [ok] {msg}")
        # THE packaging point: activate relay + make the LLM follow the avatar
        # output format on the relay channel only (emotion beats / 3-plane).
        print(f"  [ok] config: {inject_hermes_hint(runtime, url, dry)}")
        print(f"  [ok] persona: {inject_persona('hermes', runtime, name, dry)}")
        print("\n  Next: (re)start Hermes so it reads the env + prompt hint, and start the")
        print("        connector (relay_connector.py, or run start_avatar.bat).")
    else:
        print(f"  [ok] output-contract: {write_openclaw_contract(dry)}")
        print(f"  [ok] persona: {inject_persona('openclaw', runtime, name, dry)}")
        print("\n  Next: start `openclaw gateway --port %d`, then start the connector" % OPENCLAW_DEFAULT_PORT)
        print("        (relay_connector.py / start_avatar.bat). The connector reads the")
        print("        gateway token from ~/.openclaw/openclaw.json automatically.")
    print("=======================\n")


def prompt_choice(label, options, default):
    print(f"\n{label}")
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}" + ("  (default)" if o == default else ""))
    raw = input("  > ").strip()
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return raw if raw in options else default


def main():
    ap = argparse.ArgumentParser(description="Avatar connector setup")
    ap.add_argument("--agent", choices=["hermes", "openclaw"])
    ap.add_argument("--agent-runtime", choices=["windows", "wsl2"])
    ap.add_argument("--name", help="아바타 이름 override (기본=프리셋 name '여름'). 지정 시 페르소나를 그 이름으로 재생성.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    agent = args.agent
    runtime = args.agent_runtime
    interactive = agent is None or runtime is None

    if interactive:
        print("=== Avatar Secretary — connector setup ===")
        if agent is None:
            agent = prompt_choice("1) Which agent backend?", ["hermes", "openclaw"], "hermes")
        if runtime is None:
            opts = ["windows", "wsl2"]
            if not wsl_available():
                print("\n(WSL not detected — defaulting the agent runtime to windows)")
                runtime = "windows"
            else:
                runtime = prompt_choice("2) Where does the agent run?", opts, "windows")

    apply(agent, runtime, args.dry_run, args.name)


if __name__ == "__main__":
    main()
