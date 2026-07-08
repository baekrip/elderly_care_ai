@echo off
setlocal

if exist ".\.venv_edge_local\Scripts\python.exe" (
  ".\.venv_edge_local\Scripts\python.exe" "tools\run_behavior_training.py" %*
) else (
  python "tools\run_behavior_training.py" %*
)
