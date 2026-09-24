@echo off
rem agent-browser shim (Windows) — mirrors the POSIX `agent-browser` shim.
rem See that file for why the daemon-pid check exists.
setlocal EnableExtensions

if not defined BU_AGENT_BROWSER_SHIMCMD (
  echo agent-browser: BU_AGENT_BROWSER_SHIMCMD is not set. 1>&2
  echo This command only works inside an app-spawned agent session. 1>&2
  exit /b 127
)
if not exist "%BU_AGENT_BROWSER_SHIMCMD%" (
  echo agent-browser: cannot read "%BU_AGENT_BROWSER_SHIMCMD%". 1>&2
  exit /b 127
)

call "%BU_AGENT_BROWSER_SHIMCMD%"

if defined BU_AGENT_BROWSER_MISSING (
  echo agent-browser is not installed on this machine. 1>&2
  echo Install it with: npm install -g agent-browser 1>&2
  exit /b 127
)

set "AB_CURRENT="
set "AB_CACHED="
if exist "%AB_PIDFILE%" set /p AB_CURRENT=<"%AB_PIDFILE%"
if exist "%AB_CACHE%" set /p AB_CACHED=<"%AB_CACHE%"

if not exist "%AB_CACHE%" goto rebind
if not "%AB_CURRENT%"=="%AB_CACHED%" goto rebind

rem A pid-file match alone is not enough: a SIGKILLed daemon leaves its pid file
rem behind, so the cache would compare equal while nothing is running and the
rem next command would spawn a fresh daemon bound to t1. Require it to be live.
set "AB_ALIVE="
if defined AB_CURRENT (
  tasklist /FI "PID eq %AB_CURRENT%" /NH 2>nul | find "%AB_CURRENT%" >nul && set "AB_ALIVE=1"
)
if not defined AB_ALIVE goto rebind
goto run

:rebind
set "ELECTRON_RUN_AS_NODE=1"
"%AB_ELECTRON%" "%AB_REBIND%"
if errorlevel 1 exit /b 1

:run
"%AB_BIN%" %*
exit /b %errorlevel%
