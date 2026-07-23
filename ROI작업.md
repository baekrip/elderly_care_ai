# ROI 작업 절차

SAM3 checkpoint/cache가 없는 환경에서 Flask 수동 입력으로 `bed`, `floor`,
`chair` ROI를 만들고, 저장 상태를 검증한 뒤 Pi/Orin에 배치하는 절차다.

이 문서는 좌표를 자동 추정하지 않는다. 실제 운영 카메라에서 침대·바닥·의자가
식별되는 프레임을 확보한 뒤 사용자가 각 ROI를 직접 클릭해야 한다.

## 작업 원칙

- Pi: `192.168.45.29`
- Orin: `192.168.45.110`
- 필수 ROI: `bed`, `floor`, `chair`
- 저장 형식: 실제 프레임 기준 정규화 polygon과 bounding box
- 저장 위치: `edge/storage/roi/manual_roi_state.json`
- state는 디스크에 남아 재부팅 후에도 재사용된다.
- ROI state가 없거나 잘못되면 runtime은 빈 ROI로 계속 실행하지 않고 fail-closed다.
- 이 절차에서는 모델 변경, 재학습, 서비스 재시작, 알림 활성화를 수행하지 않는다.
- Orin은 shadow 상태와 `alerts_enabled=false`를 유지한다.

현재 프레임에 방 구조가 보이지 않으면 좌표를 만들지 않는다. `video/validation`
영상이나 임의의 대표 좌표를 대신 사용하지 않는다.

## 1. PowerShell 준비

프로젝트 루트에서 실행한다.

```powershell
$Root = "C:\Users\jju03\Desktop\university\program development\elderly_care_ai"
Set-Location -LiteralPath $Root

if (-not $Python) {
  $Python = (Get-Command python -ErrorAction Stop).Source
}

$Frame = Join-Path $Root "tmp\pi5_roi_frame.jpg"
$RoiState = Join-Path $Root "tmp\manual_roi_state.json"
$Reports = Join-Path $Root "experiments\behavior_training\reports"

New-Item -ItemType Directory -Force -Path (Split-Path $Frame) | Out-Null
New-Item -ItemType Directory -Force -Path $Reports | Out-Null

& $Python --version
& $Python -m tools.manual_roi_flask --help
if ($LASTEXITCODE -ne 0) {
  throw "BLOCKED_ROI_FLASK_CLI"
}
```

Flask가 설치되지 않은 경우에만 설치한다.

```powershell
& $Python -c "import flask; print('flask=' + flask.__version__)"
if ($LASTEXITCODE -ne 0) {
  & $Python -m pip install -r "$Root\tools\manual_roi_flask_requirements.txt"
  if ($LASTEXITCODE -ne 0) {
    throw "BLOCKED_FLASK_INSTALL"
  }
}
```

## 2. 실제 운영 프레임 캡처

현재 Pi stream publisher는 `BACKEND_URL` override로 다음 외부 RTSP를 사용한다.
현재 확인된 캡처 URL은 `rtsp://54.116.119.98:8554/P001`이다. 실제 배포 전에
stream 주소가 바뀌었다면 현재 Pi runtime이 출력하는 URL을 사용한다.

```powershell
Remove-Item -LiteralPath $Frame -Force -ErrorAction SilentlyContinue

ffmpeg -y -loglevel error -rtsp_transport tcp `
  -i "rtsp://54.116.119.98:8554/P001" `
  -frames:v 1 `
  "$Frame"

if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Frame)) {
  throw "BLOCKED_RTSP_FRAME_CAPTURE"
}

$FrameInfo = Get-Item -LiteralPath $Frame
$FrameInfo | Select-Object FullName,Length,LastWriteTime | Format-List
```

프레임을 열어 다음 세 항목이 모두 실제로 보이는지 확인한다.

```powershell
Start-Process -FilePath $Frame
```

- `bed`: 침대의 실제 영역
- `floor`: 사람이 이동하는 감시 바닥 영역
- `chair`: 의자의 실제 영역

하나라도 보이지 않으면 카메라 방향·초점·조명을 수정하고 2단계를 반복한다.
보이지 않는 영역을 추정해서 저장하면 안 된다.

## 3. Flask ROI 서버 실행

첫 번째 PowerShell 창에서 서버를 실행하고 창을 닫지 않는다. `--image`와
`--state-path`는 절대 경로를 사용한다.

```powershell
& $Python -m tools.manual_roi_flask `
  --host 127.0.0.1 `
  --port 8787 `
  --image "$Frame" `
  --state-path "$RoiState"
```

서버가 실행된 뒤 두 번째 PowerShell 창에서 다음을 실행한다.

```powershell
Start-Process -FilePath "http://127.0.0.1:8787/"
```

브라우저에서 화면이 열리지 않으면 서버 창의 오류를 확인한다. 별도 브라우저
검증이 필요하면 다음 명령을 사용할 수 있다.

```powershell
agbrowse start
agbrowse navigate "http://127.0.0.1:8787/"
agbrowse snapshot --interactive
agbrowse screenshot
```

## 4. Flask 화면에서 세 ROI 입력

화면의 `ROI` 선택 상자와 이미지 위 canvas를 사용한다.

1. `bed`를 선택한다.
2. 침대 외곽을 따라 꼭짓점을 3개 이상 클릭한다.
3. `floor`를 선택한다.
4. 실제 감시할 바닥 영역의 꼭짓점을 3개 이상 클릭한다.
5. `chair`를 선택한다.
6. 의자 외곽의 꼭짓점을 3개 이상 클릭한다.
7. 잘못 찍은 점은 `Undo point`, 현재 ROI 전체는 `Clear current`로 수정한다.
8. 세 ROI가 모두 보이는지 확인한 뒤 `Save all ROIs`를 누른다.

점 입력은 현재 표시 프레임의 원본 해상도 좌표로 자동 변환된다. 브라우저 창의
크기를 기준으로 좌표를 직접 계산할 필요가 없다.

저장 validator는 다음을 검사한다.

- `schema_version=1`
- 양의 정수 frame width/height
- 정확히 `bed`, `floor`, `chair` 세 ROI
- ROI별 3개 이상의 서로 다른 점
- 모든 점이 프레임 경계 안에 있음
- polygon 자기교차 없음
- polygon 면적이 0보다 큼

저장 오류가 발생하면 오류 메시지를 수정하고 다시 저장한다. 자기교차·일직선·
화면 밖 좌표는 저장하지 않는다.

## 5. Flask API 상태 확인

세 ROI를 저장하기 전에는 `pending_review`가 정상이다.

```powershell
$Pending = Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/roi"
$Pending | ConvertTo-Json -Depth 10
```

저장 후에는 다음 검사를 실행한다.

```powershell
$State = Invoke-RestMethod -Uri "http://127.0.0.1:8787/api/roi"
$State | ConvertTo-Json -Depth 10

if ($State.status -ne "ready") {
  throw "BLOCKED_ROI_STATUS: $($State.status)"
}
if (($State.required_rois -join ",") -ne "bed,floor,chair") {
  throw "BLOCKED_ROI_REQUIRED_SET"
}
if (-not $State.persistent_across_reboot) {
  throw "BLOCKED_ROI_PERSISTENCE"
}
if (-not (Test-Path -LiteralPath $RoiState)) {
  throw "BLOCKED_ROI_STATE_FILE_MISSING"
}

Write-Host "ROI_LOCAL_API_GATE_PASS"
```

## 6. state 파일을 Python validator로 재검증

PowerShell에 Python dictionary를 직접 입력하지 말고, 아래처럼 here-string을
Python stdin으로 전달한다. `@'`와 `'@`는 반드시 줄의 첫 칸에 둔다.

```powershell
$env:ROI_STATE_PATH = $RoiState
@'
from pathlib import Path
import os

from tools.manual_roi_store import load_manual_roi_state

state = load_manual_roi_state(Path(os.environ["ROI_STATE_PATH"]))
if state is None:
    raise SystemExit("BLOCKED_ROI_STATE_NOT_READY")
if state.get("status") != "ready":
    raise SystemExit("BLOCKED_ROI_STATE_STATUS")
if state.get("required_rois") != ["bed", "floor", "chair"]:
    raise SystemExit("BLOCKED_ROI_REQUIRED_SET")
if state.get("persistent_across_reboot") is not True:
    raise SystemExit("BLOCKED_ROI_PERSISTENCE")

print("status=", state["status"])
print("frame_size=", state["frame_size"])
print("required_rois=", state["required_rois"])
print("room_rois=", state["room_rois"])
print("ROI_STORE_LOAD_GATE_PASS")
'@ | & $Python -
$ExitCode = $LASTEXITCODE
Remove-Item Env:ROI_STATE_PATH -ErrorAction SilentlyContinue
if ($ExitCode -ne 0) {
  throw "BLOCKED_ROI_STORE_VALIDATION"
}
```

state의 `room_rois`는 `[x1, y1, x2, y2]` 형식의 normalized box이고,
`room_roi_polygons`는 normalized polygon이다. 각 좌표는 `0.0` 이상 `1.0`
이하여야 한다.

## 7. 로컬 bundle mirror와 계약 manifest

manifest는 device-transfer bundle 안의 두 edge state도 검사한다. 먼저 방금
검증한 state를 로컬 bundle mirror에 복사한다. 이것은 원격 장치 변경이 아니다.

```powershell
$PiBundleState = Join-Path $Root "device_transfer\camera\edge\storage\roi\manual_roi_state.json"
$OrinBundleState = Join-Path $Root "device_transfer\Edge\edge\storage\roi\manual_roi_state.json"

New-Item -ItemType Directory -Force -Path (Split-Path $PiBundleState) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $OrinBundleState) | Out-Null
Copy-Item -LiteralPath $RoiState -Destination $PiBundleState -Force
Copy-Item -LiteralPath $RoiState -Destination $OrinBundleState -Force

$RoiHash = (Get-FileHash -LiteralPath $RoiState -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "local_roi_state_sha256=$RoiHash"
```

manifest를 만든다.

```powershell
$Manifest = Join-Path $Reports "fall_detection_contract_manifest_v1.json"

& $Python -m tools.build_fall_detection_contract_manifest `
  --project-root "$Root" `
  --output "$Manifest" `
  --write

if ($LASTEXITCODE -ne 0) {
  throw "BLOCKED_CONTRACT_MANIFEST_COMMAND"
}

$ManifestObject = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$ManifestObject | Select-Object status,blocking_reasons,unverified_reasons | Format-List

if ($ManifestObject.status -ne "PASS") {
  throw "BLOCKED_CONTRACT_MANIFEST: $($ManifestObject.blocking_reasons -join ',')"
}

Write-Host "LOCAL_CONTRACT_MANIFEST_GATE_PASS"
```

SAM3가 없으면 `unverified_reasons`에 SAM3 관련 항목이 남을 수 있다. manual
ROI state가 유효하면 그것은 manual fallback으로 기록된다. manifest의
`status=BLOCKED`를 임의로 `PASS`로 바꾸지 않는다.

## 8. 원격 루트와 기존 state read-only 확인

원격 파일을 쓰기 전에 두 장치의 루트와 기존 state를 확인한다.

```powershell
$Pi = "eagleeye@192.168.45.29"
$Orin = "eagleeye@192.168.45.110"
$RemoteState = "~/elderly_care_ai/edge/storage/roi/manual_roi_state.json"

ssh $Pi "printf 'pi='; hostname; test -d ~/elderly_care_ai"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_PI_REMOTE_ROOT" }

ssh $Orin "printf 'orin='; hostname; test -d ~/elderly_care_ai"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_ORIN_REMOTE_ROOT" }

ssh $Pi "if test -f $RemoteState; then sha256sum $RemoteState; else echo REMOTE_PI_ROI_MISSING; fi"
ssh $Orin "if test -f $RemoteState; then sha256sum $RemoteState; else echo REMOTE_ORIN_ROI_MISSING; fi"
```

기존 state가 있고 교체하지 않아야 하는 상태라면 hash와 새 local hash를 먼저
비교한다. 기존 state를 덮어쓸지 여부가 불명확하면 9단계를 실행하지 않는다.

## 9. Pi와 Orin에 ROI state만 배치

이 단계부터는 원격 파일 변경이다. 1~8단계의 시각 확인, local validator,
manifest가 통과했을 때만 실행한다. 모델·설정·서비스·알림은 변경하지 않는다.

```powershell
ssh $Pi "mkdir -p ~/elderly_care_ai/edge/storage/roi"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_PI_ROI_DIR" }

ssh $Orin "mkdir -p ~/elderly_care_ai/edge/storage/roi"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_ORIN_ROI_DIR" }

scp "$RoiState" "$($Pi):$RemoteState"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_PI_ROI_COPY" }

scp "$RoiState" "$($Orin):$RemoteState"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_ORIN_ROI_COPY" }
```

원격 hash는 local hash와 정확히 같아야 한다.

```powershell
Write-Host "local=$RoiHash"
Write-Host "pi="
ssh $Pi "sha256sum $RemoteState"
Write-Host "orin="
ssh $Orin "sha256sum $RemoteState"
```

hash가 다르면 서비스 재시작이나 추가 배포를 하지 말고 파일 경로·권한·전송
결과를 확인한다.

## 10. 원격 state read-back 검증

원격에서 다시 내려받아 같은 validator를 실행한다.

```powershell
$PiCheck = Join-Path $Root "tmp\pi_manual_roi_state_check.json"
$OrinCheck = Join-Path $Root "tmp\orin_manual_roi_state_check.json"

scp "$($Pi):$RemoteState" "$PiCheck"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_PI_ROI_READBACK" }

scp "$($Orin):$RemoteState" "$OrinCheck"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_ORIN_ROI_READBACK" }

foreach ($CheckPath in @($PiCheck, $OrinCheck)) {
  $env:ROI_STATE_PATH = $CheckPath
  @'
from pathlib import Path
import os

from tools.manual_roi_store import load_manual_roi_state

state = load_manual_roi_state(Path(os.environ["ROI_STATE_PATH"]))
if state is None or state.get("status") != "ready":
    raise SystemExit("BLOCKED_REMOTE_ROI_STATE")
if state.get("required_rois") != ["bed", "floor", "chair"]:
    raise SystemExit("BLOCKED_REMOTE_ROI_REQUIRED_SET")
if state.get("persistent_across_reboot") is not True:
    raise SystemExit("BLOCKED_REMOTE_ROI_PERSISTENCE")
print("REMOTE_ROI_STATE_GATE_PASS")
'@ | & $Python -
  $ExitCode = $LASTEXITCODE
  Remove-Item Env:ROI_STATE_PATH -ErrorAction SilentlyContinue
  if ($ExitCode -ne 0) {
    throw "BLOCKED_REMOTE_ROI_VALIDATION: $CheckPath"
  }
}
```

## 11. 장치 read-only 계약 점검

ROI state를 배치한 뒤에도 서비스를 재시작하지 않고 현재 상태만 확인한다.

```powershell
& $Python -m tools.remote_device_ops `
  --pi-host 192.168.45.29 `
  --orin-host 192.168.45.110 `
  check

if ($LASTEXITCODE -ne 0) {
  throw "BLOCKED_REMOTE_DEVICE_CHECK"
}
```

보고서에서 다음을 확인한다.

- Pi host가 `192.168.45.29`다.
- Orin host가 `192.168.45.110`이다.
- 양쪽 `manual_roi_state.json`이 존재한다.
- 양쪽 state가 `ready`이고 required ROI가 `bed/floor/chair`다.
- Orin shadow가 유지된다.
- `alerts_enabled=false`다.
- `runtime_activation=disabled`다.
- 모델 교체나 서비스 재시작이 발생하지 않았다.

## 12. 재부팅 후 재사용 확인

이 절차에서는 실제 재부팅을 수행하지 않는다. 별도 승인된 점검 창에서 파일
존재와 hash만 확인한다.

```powershell
ssh $Pi "test -s $RemoteState && sha256sum $RemoteState"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_PI_ROI_AFTER_REBOOT" }

ssh $Orin "test -s $RemoteState && sha256sum $RemoteState"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_ORIN_ROI_AFTER_REBOOT" }
```

재부팅 후에도 같은 파일과 hash가 남아야 한다. state가 없으면 runtime은
fail-closed로 멈추거나 보류 상태를 기록해야 하며, 빈 ROI로 추론을 계속하면
안 된다.

## 13. 수정·초기화·rollback

아직 원격에 배치하지 않은 local Flask state를 초기화하려면 Flask 서버가
실행 중인 상태에서 다음을 사용한다.

```powershell
Invoke-RestMethod -Method Delete -Uri "http://127.0.0.1:8787/api/roi" |
  ConvertTo-Json -Depth 10
```

새 프레임으로 2~7단계를 반복한다.

원격 state 제거는 runtime을 fail-closed로 되돌리는 원격 변경이다. 잘못된
좌표를 제거해야 하고 새 state를 준비하기 전이라는 사실이 확인된 경우에만
실행한다.

```powershell
ssh $Pi "rm -f $RemoteState"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_PI_ROI_REMOVE" }

ssh $Orin "rm -f $RemoteState"
if ($LASTEXITCODE -ne 0) { throw "BLOCKED_ORIN_ROI_REMOVE" }
```

## 완료 게이트

다음 조건을 모두 충족해야 ROI 작업을 `PASS`로 기록한다.

1. 실제 운영 카메라의 usable frame으로 입력했다.
2. `bed`, `floor`, `chair` 세 polygon이 저장됐다.
3. Flask API가 `status=ready`를 반환했다.
4. `manual_roi_store` 재검증이 통과했다.
5. `persistent_across_reboot=true`다.
6. local state SHA-256을 기록했다.
7. Pi와 Orin hash가 local hash와 같다.
8. 양쪽 read-back validator가 통과했다.
9. `remote_device_ops check`가 통과했다.
10. Orin shadow와 알림 비활성 상태가 유지됐다.

ROI 입력 완료는 최종 운영 배포 완료와 같은 의미가 아니다. 이후에도 host/Pi/
Orin parity, thermal/long-run soak, ROI가 실제 runtime에서 적용되는지에 대한
shadow 확인, 최종 validation과 alert gate가 별도로 필요하다.

## 현재 확인된 제한

- 현재 확보된 Pi frame은 wall/bright-light 위주라 `bed/floor/chair`가 식별되지
  않는다.
- 따라서 현재 좌표는 생성하지 않았고 Pi/Orin state도 쓰지 않았다.
- SAM3 checkpoint/cache 설치 또는 모델 선택은 수행하지 않았다.
- `ROI작업.md`의 9단계는 실제 usable frame과 승인된 원격 변경 시점에만 실행한다.
