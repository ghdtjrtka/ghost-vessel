@echo off
chcp 65001 >nul
title Ghost Vessel — 서비스 런처

echo ================================================================
echo  Ghost Vessel 서비스 런처
echo  Bridge :8900  /  TTS :8899  /  STT :8898
echo ================================================================
echo.
echo 의존성 미설치 시: pip install -r bridge/requirements.txt 등 참고
echo.

:: 현재 스크립트 위치(프로젝트 루트) 기준으로 실행
set ROOT=%~dp0

:: ── Bridge 서버 (:8900) ──────────────────────────────────
start "GV Bridge :8900" /D "%ROOT%bridge" cmd /k "python bridge_server.py"

:: ── TTS 서버 (:8899) ─────────────────────────────────────
start "GV TTS    :8899" /D "%ROOT%tts"    cmd /k "python tts_server.py"

:: ── STT 서버 (:8898) ─────────────────────────────────────
start "GV STT    :8898" /D "%ROOT%stt"    cmd /k "python stt_server.py"

echo 3개 서버 창이 열렸습니다.
echo 모두 닫으려면 각 창을 수동으로 닫거나 Tauri 앱을 종료하세요.
echo.
timeout /t 5
