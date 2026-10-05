-- 004_clips_unique_s3_key.sql
--
-- clips.s3_key 컬럼에 UNIQUE 제약 추가.
-- 이유: code-logic-auditor (2026-05-25) 권장. s3_key는 UUID 포함이라 이미 사실상 unique하지만
-- DB 레벨에서 명시적으로 보장하여 INSERT 중복 방지 + 멱등성 보강.
--
-- 적용: ssh capstone "sudo -u postgres psql -v ON_ERROR_STOP=1 -d capstone_db" < migrations/004_clips_unique_s3_key.sql
--
-- CLAUDE.md 팀원 영향도 분류: "인덱스 추가 = 자유 적용 OK"
-- (UNIQUE 제약은 동작상 인덱스 + 무결성 제약. 기존 데이터에 중복이 없으면 적용 가능.)

BEGIN;

-- 중복 검사 먼저 (있으면 트랜잭션 ROLLBACK)
DO $$
DECLARE
    dup_count integer;
BEGIN
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT s3_key, COUNT(*) AS c
        FROM public.clips
        WHERE s3_key IS NOT NULL
        GROUP BY s3_key
        HAVING COUNT(*) > 1
    ) AS dups;

    IF dup_count > 0 THEN
        RAISE EXCEPTION 'Duplicate s3_key values exist in clips table (% groups). Clean up before applying UNIQUE constraint.', dup_count;
    END IF;
END $$;

-- UNIQUE 제약 추가
ALTER TABLE public.clips
    ADD CONSTRAINT clips_s3_key_unique UNIQUE (s3_key);

COMMIT;
