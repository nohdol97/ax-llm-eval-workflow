# Custom Evaluator 샌드박스 컨테이너
# 사용자 정의 평가 코드를 격리된 환경에서 실행
#
# 보안 제약:
#   - 네트워크 없음 (--network=none)
#   - 볼륨 마운트 없음
#   - non-root 사용자 (nobody)
#   - 5초 타임아웃
#   - 128MB 메모리 제한
#
# 허용 모듈 (stdlib만):
#   json, re, math, collections, difflib, statistics, unicodedata

FROM python:3.12-slim

# 불필요한 패키지 제거로 이미지 최소화
RUN apt-get update \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# pip 캐시 제거
RUN pip cache purge 2>/dev/null || true

COPY runner.py /runner.py

# non-root 사용자로 실행
USER nobody

ENTRYPOINT ["python", "/runner.py"]
