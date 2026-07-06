@echo off
REM TTS launcher — sources VS dev env so cl.exe is on PATH for torch.compile
REM (regional decoder-layer compile, ~31% faster). If VS isn't found, TTS still
REM runs; the compile step silently falls back to eager (QWEN_TTS_COMPILE guard).
setlocal
set "PYEXE=%~dp0venv\Scripts\python.exe"
set "VSWHERE=C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -property installationPath`) do set "VSPATH=%%i"
)
if defined VSPATH if exist "%VSPATH%\VC\Auxiliary\Build\vcvars64.bat" (
    echo [tts] VS dev env: %VSPATH%
    call "%VSPATH%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
) else (
    echo [tts] VS not found - torch.compile disabled, running eager
)
"%PYEXE%" "%~dp0tts_server.py"
