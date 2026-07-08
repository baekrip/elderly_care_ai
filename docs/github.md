# GitHub 저장소 연동 및 업로드 가이드

> **대상**: PC(코드 작성) ↔ Raspberry Pi 5 ↔ Jetson Orin 3대 기기 동기화  
> 이 문서는 처음 한 번 설정 후 매번 코드를 GitHub에 올리고 각 기기에 내려받는 방법을 순서대로 안내합니다.

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

# 원격 저장소 연결
git remote add origin https://github.com/사용자명/elderly-care-ai.git

# 연결 확인
git remote -v

# 기본 브랜치를 main으로 설정
git branch -M main

# 최초 업로드
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

## 주의사항

- `.env` 파일은 `.gitignore`에 등록되어 **절대 GitHub에 올라가지 않습니다** (API 키 보호).
- `MEMORY.md`, `.agent/`, `.agents/`, `runs/`, `scratch/` 등 에이전트 캐시도 자동 제외됩니다.
- 대용량 모델 파일(`.pt`, `.onnx`, `.engine`)도 자동 제외됩니다.
- Private 저장소이므로 본인 계정으로만 접근 가능합니다.

---

*최종 업데이트: 2026-07-08*
