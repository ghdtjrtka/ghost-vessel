# 에이전트 ↔ 아바타 연동 스키마 & 통신 (v2)

## 1. 데이터 흐름 (실배선 완료)
```
[Hermes 게이트웨이] --relay WS dial-out--> [relay_connector.py :8901 /relay]
[OpenClaw 게이트웨이] <--WS dial-in------- [relay_connector.py (client 모드)]
        send{content}/터미널 res ──────────► POST 브리지 /hermes/out
                                              │ parser.py (3-플레인 + 감정비트)
                                              │ + mood 스냅샷 첨부
                                              ▼ SSE /events
                                        [플레이어 index.html]
사용자 타이핑 → /hermes/in ─┬→ mood.on_user_message() → 즉시 반응 페이로드(SSE)
                            └→ 커넥터 /say → 게이트웨이 inbound (없으면 데모 응답)
```
- `performance`는 에이전트 네이티브가 아님 → **커넥터/브리지측 파싱**으로 생성.
- 백엔드 전환: `connector_config.json`의 `"agent": "hermes" | "openclaw"`.

## 2. LLM 출력 규약 (감정 비트 + 3-플레인)
정본 = `bridge/agent_contract.py` `AVATAR_OUTPUT_CONTRACT` (설치 시 자동 주입:
Hermes → `config.yaml agent.platform_hints.relay` 채널 한정 / OpenClaw →
workspace `AGENTS.md` 마커 블록).
- `[emotion]` / `[emotion:0~1]` = 새 비트 시작. `[confirm]` = HITL.
- 코드펜스 → data(code), `[[file:path|name]]` → data(file). 발화 제거.
- **태그 없어도 동작**: 파서 폴백이 이모지/키워드로 감정 추론 + 이모지 제거
  (LLM 준수 불안정 대비 — big-pickle 검증 사례).

## 3. 감정 어휘 (39종, 프리셋 소유)
정본 = 활성 프리셋의 `emotion_map.json` (`emotions/aliases/axis/emoji/keywords/
labels/bases/mood`). 세그먼트 레시피 = `segments/render_recipe.json` (38 표정).
파서는 브리지 부팅/`/preset/reload` 시 `apply_emotion_map()`으로 로드.

## 4. 페이로드 (parser 출력 + mood)
```json
{
  "session_id": "hermes_local_01", "seq": 42,
  "dialogue": { "text": "빌드 통과했어!", "voice_marker": "m01",
                "beats": [ {"emotion":"happy","text":"빌드 통과했어!","intensity":0.85} ] },
  "data": [ {"type":"code","lang":"python","content":"..."},
            {"type":"file","path":"G:/out/r.pdf","name":"r.pdf"} ],
  "performance": { "emotion": "happy", "intensity": 0.85, "beats": [...] },
  "context": { "trigger_type": "general", "requires_confirmation": false },
  "mood": { "mood": 0.21, "affinity": 0.05, "base": "neutral",
            "base_segment": "alive_idle", "rate": 1.017 }
}
```
**즉시 반응 페이로드** (사용자 칭찬/질책 감지 시, 에이전트 응답 전에 발행):
`context.trigger_type = "user_reaction"`, `dialogue.text=""` (채팅 버블 없음),
비트 1개(움찔/반색). 플레이어는 settle 대기 없이 즉시 재생.

## 5. 브리지 엔드포인트 (:8900)
| 경로 | 용도 |
| --- | --- |
| `POST /hermes/out {content}` | 에이전트 응답 → 파싱+mood → SSE |
| `POST /hermes/in {text}` | 사용자 입력 → 즉시반응 + 커넥터 포워딩(폴백 데모) |
| `GET /events` | SSE (performance + mood) |
| `GET /mood` | 현재 무드/호감도/base 스냅샷 |
| `GET /preset` · `/presets` · `POST /preset/reload` | 활성 프리셋 |
| `GET /config` | 에이전트 이름 (Hermes SOUL.md) |
| `GET /file?path=` | data 플레인 파일 서빙 |
커넥터 컨트롤(:9901): `POST /say {text}`, `GET /status`.

## 6. 무드/호감도 (bridge/mood.py)
- **mood**: 비트 valence EMA. 시간 감쇠 목적지 = **affinity 기준선** (0 아님).
- **affinity**: 장기 스탯, `presets/<id>/state.json` 영속.
- 신호: 사용자 메시지 렉시콘(칭찬/질책, 한국어) = 주 신호(즉시·결정적).
  에이전트 비트 = 절반 가중 보조.
- base 판정: `emotion_map.mood.base_thresholds` → bases 매핑(neg/neu/pos idle)
  + 재생속도 노브(0.9~1.06). 플레이어는 rest 변주(`style:"rest_eyes_closed"`)를
  대기 18~32초마다 1회 재생(응시 방지).
