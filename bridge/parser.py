"""LLM output -> performance payload parser (connector-side).

Splits an LLM reply into two planes (사용자 요구: 대화 ↔ 데이터 분리):
  - dialogue : the conversational sentences the AVATAR SPEAKS (with emotion tags).
  - data[]   : code blocks / files / structured output -> the CHAT/DATA panel,
               NOT spoken by the avatar.

Emotion cues are inline tags [emotion] / [emotion:intensity]; [confirm] = HITL.
Code fences ```lang ... ``` -> data{type:code}. File markers [[file:path|name]]
-> data{type:file}.
"""
import re, json

# Gemini 서브컬쳐 감정셋(2026-07-07 피봇). 실제 available은 preset emotion_map/manifest가 결정.
KNOWN = {
    "shy","blissful","kissy","smile","wink","tongue","smirk","smug","excited","laugh_big",
    "curious","skeptical","surprise_big","pout","annoyed","distressed",
    "wave","pondering","sigh",
    "working","reading","dance","tehe","crying","leaveitto","searching","stretch",
    "neutral","talking",
}
# 옛 감정어/동의어 → 신 16셋 중 가장 가까운 것으로 매핑(LLM이 예전 태그를 뱉어도 안전).
ALIAS = {
    "smiling":"smile","happy":"smile","joy":"smile","glad":"smile","warm":"smile",
    "laugh":"laugh_big","lol":"laugh_big",
    "content":"blissful","satisfied":"blissful","cozy":"blissful",
    "bashful":"shy","ashamed":"shy","embarrassed":"shy",
    "kiss":"kissy","heart":"kissy","love":"kissy",
    "playful":"tongue","teasing":"tongue","tease":"tongue",
    "smirking":"smirk","sly":"smirk","mischievous":"smirk",
    "proud":"smug","confident":"smug","doya":"smug","smug_glance":"smug","sideeye":"smug",
    "wonder":"curious","interested":"curious","attentive":"curious","eyeshut":"curious","listening":"curious",
    "doubt":"skeptical","suspicious":"skeptical","unsure":"skeptical",
    "think":"pondering","thinking":"pondering","ponder":"pondering","hmm":"pondering","considering":"pondering",
    "sigh":"sigh","exhale":"sigh","tired":"sigh","weary":"sigh","relieved":"sigh",
    "wave":"wave","hi":"wave","hello":"wave","hey":"wave","greet":"wave","greeting":"wave","bye":"wave","goodbye":"wave",
    "surprise":"surprise_big","startled":"surprise_big","shock":"surprise_big","shocked":"surprise_big",
    "stunned":"surprise_big","amazed":"surprise_big","astonished":"surprise_big",
    "sulky":"pout","frown":"pout","frown_subtle":"pout","grumpy":"pout",
    "annoy":"annoyed","irritated":"annoyed","frustrated":"annoyed","displeased":"annoyed",
    "mad":"annoyed","angry":"annoyed","rage":"annoyed","jitome":"annoyed","exasperated":"annoyed","eyeroll":"annoyed",
    "sad":"distressed","teary":"distressed","upset":"distressed","downcast":"distressed",
    "worried":"distressed","concerned":"distressed","wince":"distressed",
    # 신셋에 없어 근사 매핑되는 옛 감정
    "awkward":"shy","awkward_smile":"shy","sheepish":"shy","forced_smile":"smile",
    "disgust":"annoyed","scowl":"annoyed","glare":"annoyed","sleepy":"neutral",
    # ── 액션 세트(2026-07-08) 별칭 ──
    "typing":"working","noting":"working","busy":"working","processing":"working","note":"working",
    "review":"reading","reviewing":"reading","examine":"reading","inspecting":"reading","scanning":"reading",
    "dancing":"dance","celebrate":"dance","celebrating":"dance",
    "oops":"tehe","mybad":"tehe","teehee":"tehe","whoops":"tehe","tehepero":"tehe",
    "cry":"crying","sob":"crying","sobbing":"crying","bawl":"crying","wail":"crying","weeping":"crying",
    "leaveit":"leaveitto","gotit":"leaveitto","onit":"leaveitto","willdo":"leaveitto",
    "search":"searching","looking":"searching","lookup":"searching","find":"searching","finding":"searching",
    "stretching":"stretch",
}
TAG  = re.compile(r"\[\s*([a-zA-Z_]+)\s*(?::\s*([01](?:\.\d+)?|\.\d+))?\s*\]")
CODE = re.compile(r"```([a-zA-Z0-9_+-]*)\n?(.*?)```", re.S)
FILE = re.compile(r"\[\[\s*file\s*:\s*([^\|\]]+?)\s*(?:\|\s*([^\]]+?))?\s*\]\]")

# ── Fallback emotion inference (when the LLM emits NO [emotion] tags) ──
# The persona-heavy model (big-pickle 갸루) expresses feeling with emojis instead
# of tags. So the avatar still emotes, we map emojis/keywords -> an emotion and
# strip the emojis out of the SPOKEN text (TTS shouldn't read them anyway).
EMOJI_EMO = {
    "excited": "😆🎉✨🔥💥🙌🥳🚀",
    "laugh_big": "😂🤣",
    "smile":   "😊🙂😄😁🤗☺👍",
    "blissful":"😌😍🥰😻💖💕❤🧡💛💚💙💜",
    "kissy":   "😘😗😙😚💋",
    "smug":    "😎😼",
    "smirk":   "😏",
    "wink":    "😉",
    "tongue":  "😝😛😋😜",
    "shy":     "😳",
    "distressed": "😢😞😔🥺😭",
    "annoyed": "😠😡🤬😤😾",
    "surprise_big": "😮😲🙀😱",
    "skeptical": "🧐",
    "pout":    "😤",
    "pondering": "🤔💭",
    "sigh":    "😮‍💨😔😪",
    "wave":    "👋",
}
KEYWORD_EMO = [
    ("excited", ("대박", "미쳤", "레전드", "가보자", "고고", "쩐다", "짱", "신난")),
    ("laugh_big", ("ㅋㅋ", "ㅎㅎ", "웃겨", "빵터")),
    ("smile",   ("좋아", "최고", "고마워", "기뻐", "굿", "반가")),
    ("blissful",("행복", "만족", "사랑", "포근", "좋다")),
    ("smug",    ("거봐", "내가 뭐랬", "당연하지", "봤지", "역시 나")),
    ("tongue",  ("메롱", "약올", "장난", "해해")),
    ("shy",     ("부끄", "수줍", "쑥스")),
    ("pout",    ("흥", "삐졌", "치사", "몰라", "칫")),
    ("annoyed", ("짜증", "귀찮", "됐어", "아 진짜")),
    ("surprise_big",("헐", "헉", "어머", "진짜?", "실화")),
    ("distressed",("미안", "아쉽", "속상", "슬프", "안타깝", "ㅠㅠ")),
    ("skeptical",("글쎄", "확실해", "진짜야", "의심")),
    ("curious", ("뭐지", "궁금", "왜")),
]
_EMOJI_ANY = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤️‍⭐✨]")

def strip_emojis(s):
    return re.sub(r"\s{2,}", " ", _EMOJI_ANY.sub("", s)).strip()

def infer_emotion(text):
    """Best-effort emotion from emojis (primary) then keywords. neutral if none."""
    counts = {}
    for emo, chars in EMOJI_EMO.items():
        c = sum(text.count(ch) for ch in chars)
        if c:
            counts[emo] = counts.get(emo, 0) + c
    if counts:
        return max(counts, key=counts.get)
    low = text
    for emo, kws in KEYWORD_EMO:
        if any(k in low for k in kws):
            return emo
    return "neutral"

def _split_neutral(chunk):
    """A tag-less chunk -> per-emoji-cluster emotion beats (fallback path).
    '좋아 💖 근데 좀 😅' -> [(happy,'좋아'), (awkward_smile,'근데 좀')]."""
    toks = re.split("(" + _EMOJI_ANY.pattern + "+)", chunk)
    out, buf = [], ""
    for tok in toks:
        if tok and _EMOJI_ANY.match(tok):
            txt = strip_emojis(buf)
            if txt:
                out.append((infer_emotion(tok), txt))
            buf = ""
        else:
            buf += tok or ""
    tail = strip_emojis(buf)
    if tail:
        out.append((infer_emotion(tail), tail))   # keyword-based (no emoji)
    merged = []
    for emo, txt in out:
        if merged and merged[-1][0] == emo:
            merged[-1] = (emo, merged[-1][1] + " " + txt)
        else:
            merged.append((emo, txt))
    return merged or [("neutral", strip_emojis(chunk))]


DEFAULT_INTENSITY = 0.85
LABELS = {}

def apply_emotion_map(m):
    """Override the emotion vocabulary/fallback from a preset's emotion_map.json
    (presets/<id>/emotion_map.json). Lets each preset tune how it emotes without
    touching code. Missing keys keep the built-in defaults."""
    global KNOWN, ALIAS, EMOJI_EMO, KEYWORD_EMO, DEFAULT_INTENSITY, LABELS
    if not isinstance(m, dict) or not m:
        return
    if m.get("emotions"):
        KNOWN = set(m["emotions"]) | {"neutral"}
    if isinstance(m.get("aliases"), dict):
        ALIAS = {k.lower(): v for k, v in m["aliases"].items()}
    if isinstance(m.get("emoji"), dict):
        EMOJI_EMO = m["emoji"]
    if isinstance(m.get("keywords"), list):
        KEYWORD_EMO = [(d["emotion"], tuple(d.get("words", [])))
                       for d in m["keywords"] if d.get("emotion")]
    if m.get("default_intensity") is not None:
        DEFAULT_INTENSITY = float(m["default_intensity"])
    if isinstance(m.get("labels"), dict):
        LABELS = m["labels"]


def canon(name):
    n = ALIAS.get(name.strip().lower(), name.strip().lower())
    return n if n in KNOWN else None

def parse(content, seq=0, session_id="hermes_local_01", output_mode="both"):
    data = []
    # 1) pull code blocks -> data (not spoken)
    def code_repl(m):
        data.append({"type":"code","lang":(m.group(1) or "text"),"content":m.group(2).rstrip()})
        return " "
    text = CODE.sub(code_repl, content)
    # 2) pull file markers -> data
    def file_repl(m):
        path = m.group(1).strip(); name = (m.group(2) or path.split("/")[-1].split("\\")[-1]).strip()
        data.append({"type":"file","path":path,"name":name})
        return " "
    text = FILE.sub(file_repl, text)
    # 3) split into emotion BEATS: each [emo] starts a new (emotion, text) chunk.
    #    The avatar performs beats in sequence (action+dialogue per fine emotion).
    confirm = False
    segs = []                    # (emotion, intensity, raw_chunk)
    cur_emo, cur_int = "neutral", None
    last = 0
    for m in TAG.finditer(text):
        segs.append((cur_emo, cur_int, text[last:m.start()])); last = m.end()
        low = m.group(1).strip().lower()
        if low in ("confirm", "hitl"):
            confirm = True
        else:
            c = canon(m.group(1))
            if c: cur_emo, cur_int = c, (float(m.group(2)) if m.group(2) else None)
    segs.append((cur_emo, cur_int, text[last:]))

    beats = []
    for emo, inten, chunk in segs:
        if emo != "neutral":
            t = strip_emojis(chunk).strip()               # explicit tag: keep, drop emojis
            # 텍스트가 비어도 비트를 만든다 = 무음 제스처. LLM이 감정 뒤 액션을 조합할 수 있게
            # (`[happy] 됐어! [dance]` → happy로 말한 뒤 dance 제스처만 무음 재생). 조합은 LLM 선택.
            beats.append({"emotion": emo, "text": t,
                          "intensity": round(inten if inten is not None else DEFAULT_INTENSITY, 2)})
        else:
            for e, t in _split_neutral(chunk):            # no tag: infer from emojis/keywords
                if t:
                    beats.append({"emotion": e, "text": t, "intensity": DEFAULT_INTENSITY})
    if not beats:
        beats = [{"emotion": "neutral", "text": "", "intensity": DEFAULT_INTENSITY}]
    full = " ".join(b["text"] for b in beats).strip()

    return {
        "session_id": session_id, "seq": seq,
        "dialogue": {"text": full, "voice_marker": "m01", "beats": beats},
        "data": data,
        "performance": {
            "primary_clip": None, "emotion": beats[0]["emotion"],
            "intensity": beats[0]["intensity"],
            "beats": [{"emotion": b["emotion"], "intensity": b["intensity"]} for b in beats],
        },
        "context": {"trigger_type": "general", "requires_confirmation": confirm},
        "output_mode": output_mode,
    }


if __name__ == "__main__":
    tests = [
        "[happy] 오빠, 빌드 통과했어! 로그도 깨끗해.",
        "이거 함수야 [smug] 봐봐:\n```python\ndef add(a,b):\n    return a+b\n```\n간단하지?",
        "리포트 정리해뒀어 [smile] [[file:G:/out/report.pdf|분기리포트.pdf]] 확인해봐.",
        "[confirm] 이 파일 지울까? 되돌릴 수 없어.",
    ]
    for t in tests:
        p = parse(t, seq=1)
        print("dialogue:", p["dialogue"]["text"])
        print("emotion :", p["performance"]["emotion"], "confirm:", p["context"]["requires_confirmation"])
        print("data    :", json.dumps(p["data"], ensure_ascii=False)[:120])
        print()
