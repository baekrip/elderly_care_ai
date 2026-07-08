@echo off
setlocal
cd /d "%~dp0"
set PYTHONPATH=device_transfer\Edge

if not exist ".venv_edge_local\Scripts\python.exe" (
  echo [.venv_edge_local] not found
  echo create it first: python -m venv .venv_edge_local
  exit /b 1
)

if "%1"=="" goto usage
if /I "%1"=="auto-train" goto auto_train

if "%1"=="prepare" (
  .\.venv_edge_local\Scripts\python.exe tools\run_behavior_training.py prepare %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="export-xgb" (
  .\.venv_edge_local\Scripts\python.exe -u tools\export_xgboost_tier_features.py --config device_transfer\camera1\edge\config.raspi_cam01.yaml %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="export-xgb-fall" (
  .\.venv_edge_local\Scripts\python.exe -u tools\export_xgboost_tier_features.py --config device_transfer\camera1\edge\config.raspi_cam01.yaml --label-mode fall_binary --output-csv experiments/behavior_training/features/xgboost_fall_features.csv %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="export-xgb-tier" (
  .\.venv_edge_local\Scripts\python.exe -u tools\export_xgboost_tier_features.py --config device_transfer\camera1\edge\config.raspi_cam01.yaml --label-mode tier --output-csv experiments/behavior_training/features/xgboost_tier_multiclass_features.csv %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="train-xgb" (
  .\.venv_edge_local\Scripts\python.exe tools\train_xgboost_tier.py %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="train-xgb-fall" (
  .\.venv_edge_local\Scripts\python.exe tools\train_xgboost_tier.py --features-csv experiments/behavior_training/features/xgboost_fall_features.csv --model-out edge/models/xgboost_fall_binary.json --meta-out experiments/behavior_training/reports/xgboost_fall_meta.json --label-column model_label
  exit /b %errorlevel%
)

if "%1"=="train-xgb-tier" (
  .\.venv_edge_local\Scripts\python.exe tools\train_xgboost_tier.py --features-csv experiments/behavior_training/features/xgboost_tier_multiclass_features.csv --model-out edge/models/xgboost_tier_multiclass.json --meta-out experiments/behavior_training/reports/xgboost_tier_multiclass_meta.json --label-column tier_label
  exit /b %errorlevel%
)

if "%1"=="export-stgcn" (
  .\.venv_edge_local\Scripts\python.exe -u tools\export_stgcn_sequences.py --config device_transfer\camera1\edge\config.raspi_cam01.yaml %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="export-stgcn-fall" (
  .\.venv_edge_local\Scripts\python.exe -u tools\export_stgcn_sequences.py --config device_transfer\camera1\edge\config.raspi_cam01.yaml --label-mode fall_binary --include-labels NORMAL,DROP --output experiments/behavior_training/sequences/stgcn_sequences_fall.npz --meta-out experiments/behavior_training/reports/stgcn_sequence_meta_fall.json %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="train-stgcn" (
  .\.venv_edge_local\Scripts\python.exe tools\train_stgcn.py %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if "%1"=="train-stgcn-fall" (
  .\.venv_edge_local\Scripts\python.exe tools\train_stgcn.py --input experiments/behavior_training/sequences/stgcn_sequences_fall.npz --meta-in experiments/behavior_training/reports/stgcn_sequence_meta_fall.json --model-out server/models/stgcn_fall_binary.pth --report-out experiments/behavior_training/reports/stgcn_train_report_fall.json --epochs 12 --class-weight-mode inverse
  exit /b %errorlevel%
)

if "%1"=="benchmark-pose" (
  .\.venv_edge_local\Scripts\python.exe tools\benchmark_pose_resolutions.py --video "C:\Users\jju03\Downloads\test.mp4" --frames 30 --report-out experiments/behavior_training/reports/pose_resolution_benchmark.json
  exit /b %errorlevel%
)

:usage
echo usage:
echo   .\run_fall_pipeline.bat prepare
echo   .\run_fall_pipeline.bat export-xgb [args]
echo   .\run_fall_pipeline.bat export-xgb-fall
echo   .\run_fall_pipeline.bat export-xgb-tier
echo   .\run_fall_pipeline.bat train-xgb [args]
echo   .\run_fall_pipeline.bat train-xgb-fall
echo   .\run_fall_pipeline.bat train-xgb-tier
echo   .\run_fall_pipeline.bat export-stgcn [args]
echo   .\run_fall_pipeline.bat export-stgcn-fall
echo   .\run_fall_pipeline.bat train-stgcn [args]
echo   .\run_fall_pipeline.bat train-stgcn-fall
echo   .\run_fall_pipeline.bat benchmark-pose
echo   .\run_fall_pipeline.bat auto-train [--plan] [--train-candidates]
exit /b 1

:auto_train
.\.venv_edge_local\Scripts\python.exe -m tools.run_autolabel_pipeline %*
exit /b %errorlevel%
