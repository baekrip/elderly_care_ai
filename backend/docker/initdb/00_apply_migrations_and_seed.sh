#!/bin/bash
# postgres 컨테이너 최초 기동 시 1회 실행된다 (데이터 디렉토리가 비어 있을 때만).
#
# migrations/ 를 /docker-entrypoint-initdb.d 에 직접 마운트하지 않는 이유:
# 시드 파일을 그 안에 겹쳐 마운트하려면 중첩 바인드 마운트가 필요한데
# Docker Desktop(virtiofs)에서 막힌다. 그래서 이 스크립트만 initdb.d 에 두고,
# 실제 SQL은 겹치지 않는 별도 경로(/migrations, /seeds)에서 읽어온다.
#
# ON_ERROR_STOP=1 은 CLAUDE.md 의 운영 마이그레이션 적용 방식과 동일 —
# 중간에 실패하면 조용히 넘어가지 않고 즉시 멈춘다.
set -euo pipefail

apply() {
    local label="$1" dir="$2"
    if [ ! -d "$dir" ]; then
        echo "[initdb] $dir 없음 — 건너뜀"
        return
    fi
    for f in "$dir"/*.sql; do
        [ -e "$f" ] || continue
        echo "[initdb] applying $label: $(basename "$f")"
        psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
    done
}

# 001~007 순서대로 (파일명 정렬 = 번호 순서)
apply "migration" /migrations

# 로컬 전용 더미 데이터. 운영에는 존재하지 않는 경로다.
apply "seed" /seeds

echo "[initdb] 완료 — 스키마 + 시드 적용됨"
