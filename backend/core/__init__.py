"""공용 모듈 — 라우트와 스케줄러가 함께 쓰는 것들.

    config.py       환경변수·상수 + validate()
    db.py           engine, FK 해결 헬퍼
    s3.py           S3 클라이언트
    auth.py         두 인증 체계 + 역할/보호자 필터
    utils.py        들어오는 스칼라 값 강제 변환
    serializers.py  DB row → JSON 응답 빌더

app.py가 최상위에 남아 있는 건 systemd가 gunicorn을 `app:app`으로 띄우기 때문이다
(자세한 건 app.py 상단 주석 참고). 그 제약을 받지 않는 나머지는 전부 패키지로 묶었다.
"""
