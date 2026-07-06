"""The Avatar Output Contract — the single source of truth for the output format
the agent (Hermes/OpenClaw LLM) MUST follow so parser.py can split its reply into
the avatar's three planes (행동 emotion / 대화 speech / 데이터 data).

This string is injected into the agent's system prompt, scoped to the avatar
channel only:
  • Hermes   → config.yaml  agent.platform_hints.relay.append  (relay-platform only)
  • OpenClaw → a workspace bootstrap context file

Keep the vocabulary in sync with parser.py KNOWN/ALIAS and the TAG/CODE/FILE regexes.
"""

# 감정 → 화면에 실제로 보이는 표정 (한 줄 서술), 그룹별. 규약에 삽입되어 선택 정확도를
# 올린다. 2026-07-06 전수 재태깅: 각 감정명 = 세그먼트가 실제로 보여주는 표정과 일치
# (eyeshut→attentive, surprise(세그)→sheepish, ashamed→shy; 구명칭은 파서 별칭).
# 서술은 육안 QA 스트립 기준 — LLM이 "무엇이 보일지" 알고 고르게 한다.
EMOTION_GROUPS = [
    ("[긍정]", [
        ("happy", "환한 기쁨"), ("smile", "잔잔한 미소"), ("laugh_big", "이 드러나는 활짝 웃음"),
        ("excited", "눈 반짝이는 신남"), ("blissful", "흐뭇한 만족(눈웃음)"), ("amazed", "감탄(눈 커짐)"),
        ("kissy", "입술 쪽"), ("wink", "윙크"), ("tongue", "메롱"), ("smug", "의기양양한 미소"),
        ("smug_glance", "곁눈질하며 으쓱"), ("smirk", "씩- 장난기 미소"), ("shy", "눈 내리깔며 수줍은 배시시"),
        ("attentive", "눈 크게 뜨고 초롱초롱 경청"),
    ]),
    ("[중립·미묘]", [
        ("neutral", "무표정"), ("curious", "살짝 궁금한 눈"), ("awkward_smile", "어색한 웃음"),
        ("sheepish", "입 삐죽이며 딴청(머쓱)"), ("awkward", "당황"), ("skeptical", "갸웃(못 믿겠다는 눈)"),
        ("surprise_big", "입 벌어지는 깜짝 놀람"), ("shocked", "흠칫(정색 놀람)"), ("sleepy", "눈꺼풀 스르르(나른)"),
    ]),
    ("[부정]", [
        ("frown_subtle", "옅은 시무룩"), ("annoyed", "미간 살짝(짜증)"), ("pout", "입 오므린 삐짐"),
        ("eyeroll", "눈 굴림(어이없음)"), ("exasperated", "시선 위로(기막힘)"), ("concerned", "걱정스런 눈"),
        ("downcast", "시선 아래 시무룩"), ("distressed", "울상"), ("wince", "움찔(찔림)"), ("glare", "부릅뜬 응시"),
        ("scowl", "찌푸림"), ("disgust", "질색"), ("forced_smile", "억지웃음"), ("angry", "볼 부풀린 화남"),
        ("rage", "눈 부릅+볼빵빵 부글부글(최강)"),
    ]),
]
# 전체 감정 목록(파서/기본 규약용). Emotions parser.py can map to a segment.
EMOTIONS = [e for _grp, items in EMOTION_GROUPS for e, _d in items]

# 상황별 추천(조건 → 후보 감정 우선순위). 채워진 감정만 규약에 노출한다.
_SITUATIONAL = [
    ("사용자가 말 시작하면", ["attentive"]),
    ("칭찬받으면", ["shy", "excited", "happy"]),
    ("실수했을 땐", ["sheepish", "awkward_smile"]),
    ("자랑할 땐", ["smug", "smug_glance"]),
]
_FINE = ["annoyed", "curious", "smirk", "smug_glance", "frown_subtle", "shocked", "sheepish"]


# 기본 설명/그룹 (표준 감정용 폴백) — 프리셋이 자기 값을 주면 그걸 우선한다.
DEFAULT_DESC = {e: d for _g, items in EMOTION_GROUPS for e, d in items}
_DEFAULT_GROUP_OF = {e: g for g, items in EMOTION_GROUPS for e, _d in items}
_GROUP_ORDER = [g for g, _ in EMOTION_GROUPS]          # [긍정]/[중립·미묘]/[부정]


def _valence_group(v):
    return "[긍정]" if v > 0.1 else "[부정]" if v < -0.1 else "[중립·미묘]"


def _catalog(emos, desc_fn, group_fn):
    groups = {g: [] for g in _GROUP_ORDER}
    for e in emos:
        g = group_fn(e)
        groups.setdefault(g, [])
        groups[g].append(f"{e} {desc_fn(e)}".strip())
    order = _GROUP_ORDER + [g for g in groups if g not in _GROUP_ORDER]
    return "\n".join(f"{g} " + " · ".join(groups[g]) for g in order if groups.get(g))


def build_contract(available=None, axis=None, descriptions=None, labels=None):
    """출력 규약 문자열을 동적으로 만든다.
    - available: 노출할 감정 이름들(보통 프리셋 manifest의 채워진 감정). None이면 전체.
    - axis/descriptions/labels: 활성 프리셋 emotion_map에서 온 값. 그룹은 axis.valence로,
      감정 설명은 descriptions→기본값→labels 순으로 결정 → 커스텀 감정도 규약에 들어간다.
    부분 프리셋에서 '채워진 감정만' 노출 → 에이전트가 없는 감정 태그를 안 뱉는다."""
    axis = axis or {}; descriptions = descriptions or {}; labels = labels or {}
    emos = list(EMOTIONS) if available is None else \
        list(dict.fromkeys(list(available) + ["neutral"]))   # 순서 유지 + neutral 보장
    def _desc(e): return descriptions.get(e) or DEFAULT_DESC.get(e) or labels.get(e) or ""
    def _group(e):
        a = axis.get(e)
        if a and "valence" in a:
            return _valence_group(a["valence"])
        return _DEFAULT_GROUP_OF.get(e, "[중립·미묘]")
    catalog = _catalog(emos, _desc, _group)
    fine = [e for e in _FINE if e in emos]
    fine_line = ("- **미묘한 뉘앙스는 세분화 감정을 우선 사용** ("
                 + "/".join(fine) + " 등). 극단은 정말 강한 상황에만.\n") if fine else ""
    tips = []
    for cond, cands in _SITUATIONAL:
        got = [e for e in cands if e in emos]
        if got:
            tips.append(f"{cond} `{got[0]}`")
    tips_line = ("- 상황별 추천: " + " / ".join(tips) + "\n") if tips else ""
    return f"""\
# 아바타 출력 규약 (이 채널 전용 · 출력 형식 강제)

너의 답변은 실시간 3D 아바타가 **말하고 연기**한다. 이 채널에서는 아래 출력 형식을
**반드시** 지킨다. 형식을 어기면 아바타가 무표정으로 굳는다.

## 1. [필수] 감정 태그로 감정을 표현한다 — 이모지·별표 금지
- **모든 대사는 대괄호 감정 태그로 시작**한다. 답변의 첫 글자는 반드시 `[` 여야 한다.
  예: `[happy] 오빠 이거 됐어!`
- 감정이 바뀌면 그 자리에서 태그를 바꿔 이어 붙인다(세밀할수록 자연스럽다):
  `[excited] 빌드 통과했어! [smug] 로그도 깨끗해.`
- 세기: `[감정:0~1]` — 예: `[excited:0.9]`.
- **사용 가능한 감정은 아래 목록뿐이다. 목록에 없는 감정은 절대 쓰지 말 것**
  (없는 감정을 쓰면 아바타가 그 표정을 못 지어 무표정으로 남는다). 각 감정 옆은
  화면에 실제로 보이는 표정:
{catalog}
{fine_line}{tips_line}- **중요: 이 채널에서는 감정을 이모지(😎✨🎉💖 등)나 마크다운 별표(**...**)로 표현하지 않는다.**
  평소 습관으로 이모지를 쓰던 감정은 **전부 앞쪽 감정 태그로 옮긴다.** 대사 본문에는
  이모지/별표를 넣지 않는다(아바타 표정과 TTS가 감정을 대신 전달한다).
  - 나쁜 예: `당연히 최고지! 😎✨ 완전 즐거워~ 🎉`
  - 좋은 예: `[smug] 당연히 최고지! [excited] 완전 즐거워~`

## 2. 확인이 필요한 위험 작업 — `[confirm]`
- 되돌릴 수 없거나 민감한 행동(삭제/덮어쓰기/전송/결제/프로덕션 배포 등) 직전에는
  대사 앞에 `[confirm]` 을 붙이고 물어본다. 아바타가 승인/취소 UI를 띄운다.
  예: `[confirm] 이 파일 지울까? 되돌릴 수 없어.`

## 3. 데이터 평면 — 말하지 않고 화면에만 띄우는 것
- **코드/명령/로그/표/JSON** 등 소리내어 읽기 부적절한 것은 펜스 코드블록에 넣는다:
  ```python
  def add(a, b): return a + b
  ```
  → 아바타는 이걸 대화창 카드로 보여주고, 입으로 읽지 않는다.
- **파일 전달**은 `[[file:절대경로|표시이름]]` 마커로 한다:
  `[[file:G:/out/report.pdf|분기리포트.pdf]]` → 다운로드 카드로 표시된다.

## 4. 말투(대사) 규칙
- 대사는 TTS로 소리내어 읽히니 **자연스러운 구어체**로, 짧고 명확하게.
- 대사 안에는 마크다운 기호/URL 원문/좌표/장문 데이터를 넣지 않는다(그런 건 데이터 평면으로).
- 긴 설명·근거·산출물은 데이터 평면(코드블록/파일)으로 내리고, 대사는 요점만.

## 예시
- `[happy] 오빠, 빌드 통과했어! [smile] 로그도 깨끗해.`
- `이거 함수야 [smug] 봐봐:\n```python\ndef add(a,b): return a+b\n```\n[smile] 간단하지?`
- `정리해뒀어 [[file:G:/out/report.pdf|분기리포트.pdf]] [smile] 확인해봐.`
- `[confirm] 이 브랜치 강제 푸시할까? 되돌리기 어려워.`
"""

# 하위호환/기본: 전체 감정 규약 (부분 프리셋은 setup_connector가 build_contract(available) 사용)
AVATAR_OUTPUT_CONTRACT = build_contract()

if __name__ == "__main__":
    print(AVATAR_OUTPUT_CONTRACT)
