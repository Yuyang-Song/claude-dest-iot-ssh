@echo off
rem ============================================================
rem  Claude Buddy Serial — Windows agent 无黑窗启动脚本
rem  双击运行即可,后台跑,退出用任务管理器杀 pythonw.exe
rem
rem  错误排查:pythonw 无控制台,崩溃日志会写到同目录下
rem           buddy_serial_agent.log
rem ============================================================

cd /d "%~dp0"

rem --- 激活 conda env(和 claw-jump 同一套环境) ---
rem 如果你的 conda 装在别处,改 CONDA_BAT 路径;env 名改 ENV_NAME
set "CONDA_BAT=C:\ProgramData\Anaconda3\condabin\conda.bat"
set "ENV_NAME=llm"

if not exist "%CONDA_BAT%" set "CONDA_BAT=%USERPROFILE%\Anaconda3\condabin\conda.bat"
if not exist "%CONDA_BAT%" set "CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not exist "%CONDA_BAT%" set "CONDA_BAT=%USERPROFILE%\AppData\Local\miniconda3\condabin\conda.bat"

if exist "%CONDA_BAT%" (
    call "%CONDA_BAT%" activate %ENV_NAME%
) else (
    echo [warn] conda.bat not found, using system pythonw
)

rem --- 启动参数(按需调整) ---
set "PORT=auto"
set "BAUD=115200"
set "HTTP_PORT=47654"

rem --- 无黑窗启动 ---
start "" pythonw "%~dp0buddy_serial_agent.py" --port %PORT% --baud %BAUD% --http-port %HTTP_PORT%
exit
