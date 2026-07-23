# GitHub 저장소 연동 및 업로드 가이드


> **대상**: PC(코드 작성) ↔ Raspberry Pi 5 ↔ Jetson Orin 3대 기기 동기화  
> 이 문서는 처음 한 번 설정 후 매번 코드를 GitHub에 올리고 각 기기에 내려받는 방법을 순서대로 안내합니다.

---

## ⚡ 빠른 배포 — scp 직접 전송 (Pi5/Orin에 git이 없는 경우)

> Pi5와 Orin에 git이 설치되어 있지 않을 때 사용합니다.  
> PC에서 직접 SSH로 파일을 복사하는 방법입니다.

### 기기 접속 정보
| 기기 | SSH 주소 | 프로젝트 경로 |
|------|---------|-------------|
| Raspberry Pi 5 | `eagleeye@192.168.45.29` | `/home/eagleeye/elderly_care_ai` |
| Jetson Orin | `eagleeye@192.168.45.110` | `/home/eagleeye/elderly_care_ai` |

> IP는 네트워크에 따라 달라질 수 있습니다. `ip a` 명령으로 현재 IP 확인.

### Pi5에 파일 1개 전송

```powershell
# PowerShell에서 실행 (프로젝트 폴더 기준)
scp "device_transfer/camera/edge/pose_estimator.py" eagleeye@192.168.45.29:/home/eagleeye/elderly_care_ai/edge/pose_estimator.py
scp "device_transfer/camera/edge/config.raspi_cam01.yaml" eagleeye@192.168.45.29:/home/eagleeye/elderly_care_ai/edge/config.raspi_cam01.yaml
```

### Orin에 파일 1개 전송

```powershell
scp "device_transfer/Edge/server/services/pi5_pipeline.py" eagleeye@192.168.45.110:/home/eagleeye/elderly_care_ai/server/services/pi5_pipeline.py
scp "device_transfer/Edge/server/config.orin.yaml" eagleeye@192.168.45.110:/home/eagleeye/elderly_care_ai/server/config.orin.yaml
```

### 전송 후 서비스 재시작

```powershell
# Pi5 서비스 재시작
ssh eagleeye@192.168.45.29 "sudo systemctl restart elderly-edge-cam01.service && sudo systemctl status elderly-edge-cam01.service --no-pager -l"

# Orin 서비스 재시작
ssh eagleeye@192.168.45.110 "sudo systemctl restart elderly-orin-server.service && sudo systemctl status elderly-orin-server.service --no-pager -l"
```

### 한 번에 전송 + 재시작 (Pi5 전용 통합 명령)

```powershell
$PI5="eagleeye@192.168.45.29"
$PI5_PATH="/home/eagleeye/elderly_care_ai/edge"

# 파일 전송
scp "device_transfer/camera/edge/pose_estimator.py" "${PI5}:${PI5_PATH}/pose_estimator.py"
scp "device_transfer/camera/edge/config.raspi_cam01.yaml" "${PI5}:${PI5_PATH}/config.raspi_cam01.yaml"

# 재시작
ssh $PI5 "sudo systemctl restart elderly-edge-cam01.service"
ssh $PI5 "sudo systemctl status elderly-edge-cam01.service --no-pager -l | head -20"
```

### 효과 확인 (perf 로그 실시간 보기)

```powershell
# Pi5에서 추론 성능 지표 실시간 확인
ssh eagleeye@192.168.45.29 "tail -f /home/eagleeye/elderly_care_ai/edge/storage/results/perf_stats.jsonl"
```

---

## 1단계 — GitHub 저장소 생성 (최초 1회만)

1. [https://github.com](https://github.com) 에 로그인
2. 우측 상단 **`+`** → **`New repository`** 클릭
3. 설정:
   - **Repository name**: `elderly-care-ai` (또는 원하는 이름)
   - **Visibility**: `Private` (⚠️ 반드시 Private 선택)
   - **Initialize this repository**: ✅ 체크 하지 않음
4. **Create repository** 클릭
5. 생성 후 표시되는 URL 복사 — 예시: `https://github.com/사용자명/elderly-care-ai.git`

---

## 2단계 — 로컬 PC에서 원격 저장소 연결 (최초 1회만)

PowerShell 또는 터미널에서 아래 명령 실행:

```powershell
# 프로젝트 폴더로 이동
cd "c:\Users\jju03\Desktop\university\program development\elderly_care_ai"

# 0. Git 사용자 정보 등록 (⚠️ 미등록 시 commit 단계에서 에러 발생)
git config --global user.email "본인_GitHub_이메일@example.com"
git config --global user.name "본인_GitHub_닉네임"

# 1. 원격 저장소 연결
git remote add origin https://github.com/사용자명/elderly-care-ai.git

# 2. 연결 확인
git remote -v

# 3. 기본 브랜치를 main으로 설정
git branch -M main

# 4. 최초 업로드
git add .
git commit -m "initial commit"
git push -u origin main
```

> ✅ `git push -u origin main` 은 **최초 1회만** 실행합니다.  
> 이후에는 `git push` 만 입력해도 됩니다.

---

## 3단계 — 매번 작업 후 GitHub에 올리는 방법

### PC에서 코드를 수정하고 GitHub에 업로드할 때

```bash
# 1. 변경된 파일 전체 스테이징
git add .

# 2. 커밋 (작업 내용을 간단히 설명)
git commit -m "YOLO 추론 최적화"

# 3. GitHub에 업로드
git push
```

> 💡 커밋 메시지 예시:  
> - `git commit -m "낙상 감지 임계값 조정"`  
> - `git commit -m "pi5_pipeline.py pose-lost 가드 수정"`  
> - `git commit -m "설정 파일 업데이트"`

---

## 4단계 — 각 기기에서 최신 코드 가져오기 (git pull)

### Raspberry Pi 5에서 실행

```bash
# SSH 접속 후
cd ~/elderly_care_ai   # 또는 실제 프로젝트 경로

# 최신 코드 가져오기
git pull

# 서비스 재시작 (필요시)
sudo systemctl restart elderly-edge-cam01.service
```

### Jetson Orin에서 실행

```bash
# SSH 접속 후
cd ~/elderly_care_ai   # 또는 실제 프로젝트 경로

# 최신 코드 가져오기
git pull

# 서비스 재시작 (필요시)
sudo systemctl restart elderly-orin-server.service
```

---

## 5단계 — 3대 기기 운영 원칙

| 장치 | 역할 | 주요 명령 |
|------|------|----------|
| **PC** | 코드 작성 및 GitHub 관리 | `git add .` → `git commit` → `git push` |
| **Raspberry Pi 5** | 최신 코드 수신 및 실행 | `git pull` → `systemctl restart` |
| **Jetson Orin** | 최신 코드 수신 및 실행 | `git pull` → `systemctl restart` |

### 핵심 규칙

> ⚠️ **작업 시작 전 반드시 `git pull`을 먼저 실행하세요.**  
> 다른 기기에서 이미 push한 내용이 있을 수 있습니다.

```
[PC] 코드 수정
     └─> git add . && git commit -m "설명" && git push

[Pi5] 작업 전 git pull → 서비스 재시작
[Orin] 작업 전 git pull → 서비스 재시작
```

---

## 6단계 — 자주 쓰는 명령 모음

```bash
# 현재 변경 상태 확인
git status

# 변경 내역 로그 보기
git log --oneline -10

# 특정 파일만 스테이징
git add device_transfer/Edge/server/services/pi5_pipeline.py

# 최신 상태로 되돌리기 (수정 취소 — 주의!)
git checkout -- <파일명>

# 원격과 로컬 차이 확인 (pull 전 확인용)
git fetch
git diff HEAD origin/main
```

---

## 7단계 — GitHub 인증 설정 (처음 push 시 필요)

### 방법 A — Personal Access Token (권장)

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. **Generate new token** 클릭
3. 권한: `repo` 체크
4. 생성된 토큰을 복사 (다시 볼 수 없음)
5. `git push` 시 아이디/비밀번호 입력창이 뜨면:
   - Username: GitHub 사용자명
   - Password: 위에서 복사한 토큰 붙여넣기

### 방법 B — SSH 키 (Raspberry Pi / Jetson에서 권장)

```bash
# SSH 키 생성 (기기별 1회)
ssh-keygen -t ed25519 -C "your@email.com"

# 공개키 출력 후 GitHub에 등록
cat ~/.ssh/id_ed25519.pub
```

GitHub → **Settings** → **SSH and GPG keys** → **New SSH key** 에 붙여넣기  
이후 remote URL을 SSH 방식으로 변경:

```bash
git remote set-url origin git@github.com:사용자명/elderly-care-ai.git
```

---
 
## 8단계 — 문제 해결 (Troubleshooting)

### ⚠️ 커밋 시 `Author identity unknown` 에러가 발생하는 경우

Git에 이메일과 이름 정보가 설정되어 있지 않아 발생하는 오류입니다. 터미널(PowerShell)에 아래 명령어를 실행하여 본인의 GitHub 정보를 등록해 주세요.

#### 방법 A. 이 컴퓨터 전체에 설정하는 방법 (글로벌 설정 - 권장)
```powershell
git config --global user.email "본인_이메일_주소@example.com"
git config --global user.name "본인_깃허브_닉네임"
```

#### 방법 B. 현재 프로젝트 폴더에만 설정하는 방법 (로컬 설정)
```powershell
git config user.email "본인_이메일_주소@example.com"
git config user.name "본인_깃허브_닉네임"
```

> 💡 **확인 방법**: 설정이 잘 되었는지 확인하려면 아래 명령어를 입력해 보세요.
> ```powershell
> git config user.email
> git config user.name
> ```
> 설정이 완료되었다면 다시 `git commit -m "initial commit"` 명령을 실행하시면 정상적으로 진행됩니다.

### ⚠️ 최초 푸시 시 `[rejected] main -> main (fetch first)` 에러가 발생하는 경우

GitHub에서 원격 저장소를 생성할 때 **`Add a README`**나 **`.gitignore`** 등을 체크하여 원격 저장소에 이미 파일(커밋)이 존재하고, 로컬 프로젝트의 파일들과 서로 다른 커밋 이력을 가지고 있을 때 푸시가 거절되는 에러입니다.

#### 해결방법 A. 로컬 코드로 원격 저장소를 강제로 덮어쓰는 방법 (⚠️ 가장 빠르고 간단하나, 원격의 기존 README 등은 지워짐)
새로 만든 저장소이고 원격에 살릴 파일이 없다면 이 방법이 가장 편리합니다.
```powershell
git push -u origin main --force
# 또는 줄여서
git push -u origin main -f
```

#### 해결방법 B. 원격 저장소의 파일들을 로컬로 가져와 합친(Merge) 후 푸시하는 방법
원격 저장소의 README 등을 로컬 프로젝트에 안전하게 병합하고 푸시합니다.
```powershell
# 이력을 강제로 병합하며 가져옵니다.
git pull origin main --allow-unrelated-histories

# (선택) 텍스트 에디터 창이 뜨면 머지 메시지를 저장하고 닫습니다.
# 이후 다시 푸시를 실행합니다.
git push -u origin main
```

---

## 주의사항

- `.env` 파일은 `.gitignore`에 등록되어 **절대 GitHub에 올라가지 않습니다** (API 키 보호).
- `MEMORY.md`, `.agent/`, `.agents/`, `runs/`, `scratch/` 등 에이전트 캐시도 자동 제외됩니다.
- 대용량 모델 파일(`.pt`, `.onnx`, `.engine`)도 자동 제외됩니다.
- **실기기(Pi5/Orin) 구동 파일 및 `docs/` 폴더 내의 실행 기록(command.md 등)만 업로드**되며, 분석 및 연구용으로 사용되는 `tests/` (유닛 테스트), `tools/` (개발 도구), 루트의 실행 배치 파일(`.bat`, `.ps1`)들은 `.gitignore` 설정에 따라 자동 제외됩니다.
- Private 저장소이므로 본인 계정으로만 접근 가능합니다.

---

*최종 업데이트: 2026-07-08*
