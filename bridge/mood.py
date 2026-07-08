"""Mood + affinity tracker — the avatar's 3-axis resting-state brain.

Two layers (design doc: memory avatar-emotion-3axis):
  mood     — short-term valence EMA of conversation beats; decays over time
             toward the affinity baseline (NOT toward 0).
  affinity — long-term relationship stat, persisted per-preset in state.json.
             Moves slowly on praise/scold; idle decay drifts it gently to 0.

The felt loop:
  * user praises  -> immediate positive reaction beat + affinity up
  * user scolds   -> immediate negative reaction beat + affinity down
  * keep scolding -> affinity sinks -> idle BASE becomes negative (그 대기상태)
  * agent's own emotional beats also color the mood (weaker weight)

All tunables come from the active preset's emotion_map.json:
  axis: {emotion: {valence, arousal}}     mood: {beat_ema_alpha, decay_per_min,
  base_thresholds, affinity_step, affinity_range}   reactions: optional override.
"""
from __future__ import annotations
import json, os, re, time, threading

# ── praise / scold lexicon (Korean-first; deterministic & instant) ──────────
PRAISE_STRONG = ["최고야","완벽해","천재","대박이다","진짜 잘했","감동이야","사랑해","짱이야","미쳤다 잘"]
PRAISE_MILD   = ["잘했어","고마워","좋네","좋아요","굿","수고했","역시","맘에 들","괜찮네","잘하네","고맙"]
SCOLD_STRONG  = ["뭐하는 거야","엉망이잖아","실망이야","최악","쓸모없","한심","답답해 죽","제대로 좀","몇 번을 말해"]
SCOLD_MILD    = ["또 틀렸","틀렸잖아","이게 뭐야","별로네","아니잖아","다시 해","실수했네","엉성","부족하네","답답"]

DEFAULT_REACTIONS = {
    "praise_strong": "excited", "praise_mild": "happy",
    "scold_strong": "wince",    "scold_mild": "frown_subtle",
}
DEFAULT_MOOD = {"beat_ema_alpha": 0.35, "decay_per_min": 0.15,
                "base_thresholds": {"negative": -0.35, "positive": 0.35},
                "affinity_step": 0.02, "affinity_range": [-1.0, 1.0]}


class MoodTracker:
    def __init__(self, preset: dict, state_dir: str):
        emo = preset.get("emotion", {}) or {}
        self.axis = emo.get("axis", {}) or {}
        self.params = {**DEFAULT_MOOD, **(emo.get("mood") or {})}
        self.reactions = {**DEFAULT_REACTIONS, **(emo.get("reactions") or {})}
        self.bases = emo.get("bases", {}) or {}
        self.state_path = os.path.join(state_dir, "state.json")
        self._lock = threading.Lock()  # 인스턴스별 독립 Lock (모듈 싱글턴 공유 제거)
        self.mood = 0.0        # short-term valence  [-1, 1]
        self.affinity = 0.0    # long-term baseline  [-1, 1]
        self._ts = time.time()
        self._load()

    # ── persistence ─────────────────────────────────────────────────────
    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                s = json.load(f)
            self.mood = float(s.get("mood", 0.0))
            self.affinity = float(s.get("affinity", 0.0))
            self._ts = float(s.get("updated_at", time.time()))
        except Exception:
            pass

    def _save(self):
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"mood": round(self.mood, 4), "affinity": round(self.affinity, 4),
                           "updated_at": time.time()}, f)
        except Exception:
            pass

    # ── time decay: mood drifts toward affinity; affinity toward 0 (slow) ──
    def _decay(self):
        now = time.time()
        dt_min = max(0.0, (now - self._ts) / 60.0)
        self._ts = now
        if dt_min <= 0:
            return
        k = min(1.0, self.params["decay_per_min"] * dt_min)
        # 비대칭 자동복귀: 긍정 무드만 시간이 지나면 중립(차분)으로 가라앉는다.
        # 부정은 시간으로 복귀하지 않음 — 능동적으로 달래줘야(칭찬/대화) 회복.
        # ("쉬었다 오니 계속 기뻐있는" 건 이상하지만, 삐친 건 풀어줘야 풀리는 게 자연스럽다.)
        if self.mood > 0:
            self.mood += (0.0 - self.mood) * k
        self.affinity *= (1.0 - min(1.0, 0.002 * dt_min))   # 호감도는 며칠 스케일로 중립화

    # ── inputs ──────────────────────────────────────────────────────────
    def on_user_message(self, text: str):
        """Instant reaction to the USER's words. Returns a reaction dict
        {emotion, intensity, kind} or None. Also nudges mood + affinity."""
        with self._lock:
            self._decay()
            t = text or ""
            kind = None
            if any(w in t for w in SCOLD_STRONG):   kind, dv = "scold_strong", -0.5
            elif any(w in t for w in PRAISE_STRONG): kind, dv = "praise_strong", +0.5
            elif any(w in t for w in SCOLD_MILD):   kind, dv = "scold_mild", -0.3
            elif any(w in t for w in PRAISE_MILD):  kind, dv = "praise_mild", +0.3
            if kind is None:
                self._save(); return None
            a = self.params["beat_ema_alpha"]
            self.mood = self.mood * (1 - a) + dv * 1.6 * a          # 즉시 체감되게 강하게
            step = self.params["affinity_step"] * (2 if "strong" in kind else 1)
            lo, hi = self.params["affinity_range"]
            self.affinity = max(lo, min(hi, self.affinity + (step if dv > 0 else -step)))
            self.mood = max(-1.0, min(1.0, self.mood))
            self._save()
            return {"emotion": self.reactions.get(kind, "neutral"),
                    "intensity": 0.9 if "strong" in kind else 0.75, "kind": kind}

    def on_beats(self, beats):
        """Agent's own emotional beats color the mood (weaker than user signal)."""
        with self._lock:
            self._decay()
            a = self.params["beat_ema_alpha"] * 0.5
            for b in beats or []:
                v = (self.axis.get(b.get("emotion", "neutral")) or {}).get("valence", 0.0)
                self.mood = self.mood * (1 - a) + v * a
            self.mood = max(-1.0, min(1.0, self.mood))
            self._save()

    # ── outputs ─────────────────────────────────────────────────────────
    def base(self) -> str:
        th = self.params["base_thresholds"]
        if self.mood <= th["negative"]: return "negative"
        if self.mood >= th["positive"]: return "positive"
        return "neutral"

    def snapshot(self) -> dict:
        with self._lock:
            self._decay()
            base = self.base()
            return {"mood": round(self.mood, 3), "affinity": round(self.affinity, 3),
                    "base": base, "base_segment": self.bases.get(base),
                    # idle playback-rate knob: 처지면 느리게, 밝으면 살짝 생기있게
                    "rate": round(max(0.9, min(1.06, 1.0 + self.mood * 0.08)), 3)}
