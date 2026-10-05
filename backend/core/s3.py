"""S3 클라이언트 — 위험 클립 저장용.

버킷명이 설정된 경우에만 초기화한다. 미설정이면 s3_client is None 이고,
S3를 실제로 건드리는 endpoint(clips/upload-url, clips/confirm)는 503을 반환한다.
목록 조회(GET /clips)는 S3 없이도 200으로 정상 동작한다 — 재생 URL만 None.
"""
import boto3
from botocore.config import Config

from .config import AWS_REGION, S3_BUCKET

# AWS 자격증명은 boto3가 환경변수(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)
# 또는 IAM 역할에서 자동 조회.
# ap-northeast-2 같은 비(非)us-east-1 리전은 regional endpoint + SigV4 필수
# (default boto3는 https://<bucket>.s3.amazonaws.com 로 생성해 SignatureDoesNotMatch 발생)
s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
) if S3_BUCKET else None
