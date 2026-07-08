@echo off
setlocal

if not exist ".venv_edge_local\Scripts\python.exe" (
  echo [.venv_edge_local] not found
  echo create it first: python -m venv .venv_edge_local
  exit /b 1
)

.\.venv_edge_local\Scripts\python.exe -m edge.run_test %*
