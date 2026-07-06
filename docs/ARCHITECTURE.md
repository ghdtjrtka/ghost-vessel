# ARCHITECTURE — 현행 시스템 지도

> `AVATAR_AGENT_SPEC.md`는 최초 구상(역사적 문서). **현재 구현의 정본은 이 문서**와
> `bridge/HERMES_SCHEMA.md`(와이어 스키마), `docs/PRESET_BUILD.md`(프리셋 제작)이다.

## 서비스 & 포트
| 서비스 | 포트 | 파일 | 역할 |
| --- | --- | --- | --- |
| TTS (Qwen3-TTS 0.6B) | 8899 | `tts/tts_server.py` | 대화 플레인 음성 (프리셋 voice) |
| STT (faster-whisper) | 8898 | `stt/stt_server.py` | 음성 입력 (브라우저 Silero VAD → 발화 단위 전사, `?hint=`로 이름 바이어스) |
| 브리지 | 8900 | `bridge/bridge_server.py` | 파서·무드·프리셋·SSE 허브 |
| relay 커넥터 | 8901(+9901) | `bridge/relay_connector.py` | 에이전트 게이트웨이 링크 |
| 정적 서버 | 8777 | `python -m http.server` | 플레이어/에셋 서빙 |
| (빌드타임) ComfyUI | 8188 | LivePortrait-KJ | 세그먼트 렌더 전용 — 런타임 불필요 |
런처: `start_avatar.bat` / `stop_avatar.bat` (hermes-orchestrator/windows).

## 에이전트 연동 (방향이 반대인 두 어댑터)
- **Hermes**: 게이트웨이가 커넥터로 **dial-out** (Relay↔Connector Contract v1,
  개행구분 JSON: hello→descriptor, outbound→outbound_result, inbound push).
  활성화 = `config.yaml gateway.relay_url` 한 줄. 출력규약은
  `agent.platform_hints.relay`에 **채널 한정** 주입.
- **OpenClaw**: 우리가 게이트웨이 WS로 **dial-in** (connect 핸드셰이크, backend
  루프백 토큰 = `~/.openclaw/openclaw.json gateway.auth.token` 자동 로드).
  턴 = `agent{message, idempotencyKey, sessionKey}` → 터미널 res의
  `result.payloads[].text`. 규약은 workspace `AGENTS.md` 마커 블록(에이전트 전역).
- 설치 흐름: `bridge/setup_connector.py` — 에이전트×런타임(win/wsl2) 4조합의
  연결위치 자동 계산 + 규약 주입 + 백업. 데모 응답기 폴백 내장.

## 퍼포먼스 파이프라인
1. 에이전트 응답 → `parser.py`: 3-플레인 분해(행동/대화/데이터) + 감정 비트.
   태그 없으면 이모지/키워드 폴백 추론(+이모지 제거). 어휘·별칭·강도는 활성
   프리셋 `emotion_map.json` 소유.
2. `mood.py`: 사용자 메시지 렉시콘(칭찬/질책) → **즉시 반응**(에이전트 응답 전,
   무음 비트) + mood EMA/affinity 갱신(state.json 영속). base(neg/neu/pos) 판정.
3. 플레이어: settle 게이트(고개 정면 순간 표정 공개) · 이중버퍼 크로스페이드 ·
   무드 base idle + 재생속도 노브 · rest 스케줄러(눈감고 쉬기) · TTS 비동기 발화.
4. 음성 입력: 🎙 토글 → 마이크 → **Silero VAD**(`player/vad/` 로컬 번들, CDN 무의존)
   발화 감지 → 16k WAV → STT(:8898) → 타이핑과 동일 채팅 경로. **하프듀플렉스**:
   TTS 재생·발화 중엔 VAD 자동 일시정지(⏸)로 에코 루프 차단.

## 에셋 시스템 (아바타 팩토리)
- **표정 = 소스 이미지(프리셋별) × 구동 레시피(공용)**. 레시피 =
  `segments/render_recipe.json` (38 표정: 클립·프레임창·delta·군·tier).
- 도구: `tools/build_preset_avatar.py`(일괄 렌더) ·
  `tools/build_idle_loop.py`(깜빡임 정렬 루프+settle) ·
  `tools/validate_preset.py`(합격 게이트).
- 대기 루프는 아바타별 몸동작 영상(Gemini 우회)에서 제작. 무드 idle 3축
  (negative/neutral/positive) + rest 변주.
- ⚠️ LP relative 함정: 구동 클립은 중립/눈뜬 프레임에서 시작해야 표정이 전이됨.

## 프리셋 (오픈코어)
엔진(GitHub, MIT) ↔ 프리셋(판매/개인, 순수 데이터). 구조·로더·규격은
`presets/README.md` + `docs/PRESET_BUILD.md`. 활성 프리셋이 이름·폰트·테마·
voice·에셋 경로·감정맵을 공급(`GET /preset`).

## 남은 로드맵 (미구현)
- Tauri 데스크탑 셸 (진짜 OS 멀티윈도우/스냅/항상위/트레이 — 현 HTML이 내용물).
- LivePortraitRetargeting으로 idle에 강한 얼굴 무드 얹기 (phase-2).
- neg 몸동작 영상 교체(쿼터 시), yes-nod 구동클립 소싱.
- 지연 예산 실측 문서화 (스펙 Step 2).
