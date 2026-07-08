"""
두 AI 모델(Claude + OpenAI)의 의견을 종합해서 구성.md 계획을 작성하는 도구.

사용 전:
  1. .env.example 을 복사해서 .env 로 저장
  2. .env 에 실제 OPENAI_API_KEY 입력
  3. python tools/plan_advisor.py --doc docs/구성.md

설정 변경:
  - 모델: .env 파일의 OPENAI_MODEL 값 변경
  - 키: .env 파일의 OPENAI_API_KEY 값 변경
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# .env 파일 자동 로드
def load_env(env_path: str = ".env") -> None:
    """간단한 .env 파서 (python-dotenv 없이 동작)."""
    env_file = Path(env_path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

load_env()

# --- 설정 (변경하려면 .env 파일 수정) ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL   = os.environ.get("OPENAI_MODEL", "gpt-4o")
MAX_TOKENS     = int(os.environ.get("OPENAI_MAX_TOKENS", "4000"))

SYSTEM_PROMPT = """
당신은 독거노인 케어 AI 시스템의 시니어 엔지니어다.
Edge(Jetson) + Server 구조에서 낙상 감지, 행동 분류, ST-GCN, XGBoost, 실시간 영상 전송을 다룬다.
주어진 구성.md 내용을 보고 다음을 제공한다:
1. 현재 계획의 문제점 (간결하게)
2. 개선 제안 (구체적, 실현 가능한 순서)
3. 빠진 부분 (endtask 목표 기준)
한국어로 답변. 코드 예시는 최소화.
"""


def call_openai(prompt: str) -> str:
    """OpenAI API 호출."""
    try:
        from openai import OpenAI
    except ImportError:
        return "[ERROR] openai 패키지 없음: pip install openai"

    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-proj-여기에"):
        return "[ERROR] .env 파일에 OPENAI_API_KEY를 입력해야 한다."

    client = OpenAI(api_key=OPENAI_API_KEY)
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        return f"[ERROR] OpenAI 호출 실패: {exc}"


def claude_perspective(doc_text: str) -> str:
    """Claude(현재 모델) 관점의 분석 — 코드 내에서 직접 작성."""
    # Claude는 이 코드를 실행하는 에이전트이므로,
    # 실행 시점에 별도 API 호출 없이 직접 분석 결과를 반환.
    # 실제 운영에서는 이 함수를 Claude API로 교체 가능.
    return (
        "[Claude 관점]\n"
        "이 함수는 도구 실행 시 Claude 에이전트가 직접 채워 넣는 영역이다.\n"
        "tools/plan_advisor.py 를 실행하면 OpenAI 의견만 자동 출력된다.\n"
        "Claude 의견은 에이전트가 직접 채팅창에서 제공한다."
    )


def synthesize(claude_view: str, openai_view: str) -> str:
    """두 모델의 의견을 종합."""
    separator = "\n" + "="*60 + "\n"
    return (
        "# 계획 검토 결과\n\n"
        "## [Claude 분석]\n" + claude_view + separator +
        "## [GPT 분석 — 모델: " + OPENAI_MODEL + "]\n" + openai_view + separator +
        "## [종합 의견]\n"
        "위 두 분석을 바탕으로 구성.md를 업데이트하려면:\n"
        "  1. 위 제안 중 동의하는 항목 선택\n"
        "  2. 에이전트에게 '위 제안 반영해서 구성.md 업데이트' 요청\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="구성.md 계획 AI 검토 도구")
    parser.add_argument("--doc", default="docs/구성.md", help="검토할 문서 경로")
    parser.add_argument("--model", default=None, help="OpenAI 모델 오버라이드 (기본: .env의 OPENAI_MODEL)")
    args = parser.parse_args()

    if args.model:
        global OPENAI_MODEL
        OPENAI_MODEL = args.model

    doc_path = Path(args.doc)
    if not doc_path.exists():
        print(f"[ERROR] 파일 없음: {doc_path}")
        sys.exit(1)

    doc_text = doc_path.read_text(encoding="utf-8")
    print(f"문서 로드: {doc_path} ({len(doc_text)} 바이트)")
    print(f"사용 모델: {OPENAI_MODEL}\n")

    prompt = f"아래는 현재 구성.md 내용이다. 분석해달라:\n\n{doc_text[:6000]}"

    print("OpenAI 호출 중...")
    openai_view = call_openai(prompt)

    claude_view = claude_perspective(doc_text)

    result = synthesize(claude_view, openai_view)
    print(result)

    output_path = Path("docs/plan_review_output.md")
    output_path.write_text(result, encoding="utf-8")
    print(f"\n결과 저장: {output_path}")


if __name__ == "__main__":
    main()
