@echo off
rem ============================================================
rem  Claude Buddy Serial — Windows agent 无黑窗启动脚本
rem  双击运行即可,后台跑,退出用任务管理器杀 pythonw.exe
rem
rem  用法:
rem    双击 start_buddy_serial.bat
rem
rem  可调项(改下面 PORT / BAUD / HTTP_PORT):
rem    PORT      = auto    (或 COM4 / COM5...)
rem    BAUD      = 115200
rem    HTTP_PORT = 47654   (必须和 Linux 侧 serial_bridge_linux.py --agent-port 一致)
rem ============================================================

setlocal

set SCRIPT_DIR=%~dp0
set PORT=auto
set BAUD=115200
set HTTP_PORT=47654

rem 用 pythonw.exe 不弹控制台;如果想看实时日志改 python.exe
start "" "pythonw.exe" "%SCRIPT_DIR%buddy_serial_agent.py" --port %PORT% --baud %BAUD% --http-port %HTTP_PORT%

endlocal
