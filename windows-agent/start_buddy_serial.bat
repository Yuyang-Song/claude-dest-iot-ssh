@echo off
rem ===========================================================
rem  Claude Buddy Serial - Windows agent silent launcher.
rem  Double-click to run; exits into background, kill pythonw.exe
rem  in Task Manager when you want it gone.
rem
rem  On crash, traceback goes to buddy_serial_agent.log next to
rem  this file (pythonw has no console).
rem
rem  If conda is installed somewhere odd, edit CONDA_BAT below.
rem  If you use a different env name, edit ENV_NAME below.
rem ===========================================================

cd /d "%~dp0"

set CONDA_BAT=C:\ProgramData\Anaconda3\condabin\conda.bat
set ENV_NAME=llm

if not exist "%CONDA_BAT%" set CONDA_BAT=%USERPROFILE%\Anaconda3\condabin\conda.bat
if not exist "%CONDA_BAT%" set CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat
if not exist "%CONDA_BAT%" set CONDA_BAT=%USERPROFILE%\AppData\Local\miniconda3\condabin\conda.bat
if not exist "%CONDA_BAT%" set CONDA_BAT=%USERPROFILE%\miniforge3\condabin\conda.bat

if exist "%CONDA_BAT%" (
    call "%CONDA_BAT%" activate %ENV_NAME%
) else (
    echo [warn] conda.bat not found, falling back to system pythonw
)

set PORT=auto
set BAUD=115200
set HTTP_PORT=47654

start "" pythonw "%~dp0buddy_serial_agent.py" --port %PORT% --baud %BAUD% --http-port %HTTP_PORT%
exit
