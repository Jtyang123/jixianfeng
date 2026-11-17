@echo off
setlocal
cd /d "%~dp0"

set "FARMMATCH_DB=backend\farmmatch.demo-runtime.db"
set "LLM_ENABLED=false"
set "AGENTARTS_ENABLED=false"
set "RESERVATION_SWEEP_ENABLED=false"

if not exist "backend\farmmatch.demo-runtime.db" (
  copy /Y "backend\demo\farmmatch-demo.db" "backend\farmmatch.demo-runtime.db" >nul
  echo Demo database copied to backend\farmmatch.demo-runtime.db
)

call "%~dp0start.cmd" -NoBuild %*