#!/usr/bin/env bash
# MeloTTS를 격리 venv에 설치 — qwen venv의 transformers(4.57)와 충돌 방지.
# CPU torch만 설치(로컬·GPU 불필요, DA와 무관). 프로바이더는 이 venv를 서브프로세스로 호출.
set -e
cd "$(dirname "$0")"
BASEPY="../venv/Scripts/python.exe"
VENV="melo-venv"
echo "[melo] creating isolated venv..."
"$BASEPY" -m venv "$VENV"
PY="$VENV/Scripts/python.exe"
echo "[melo] upgrading pip/setuptools/wheel..."
"$PY" -m pip install -U pip setuptools wheel
echo "[melo] installing CPU torch (smaller, no CUDA)..."
"$PY" -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
echo "[melo] installing MeloTTS from source..."
"$PY" -m pip install "git+https://github.com/myshell-ai/MeloTTS.git"
echo "[melo] installing Korean g2p (g2pkk)..."
"$PY" -m pip install g2pkk || true
echo "[melo] downloading unidic MeCab dictionary (~526MB, REQUIRED — import fails without it)..."
"$PY" -m unidic download
echo "[melo] DONE"
"$PY" -c "import torch; print('[melo] torch', torch.__version__, 'cuda', torch.cuda.is_available())"
