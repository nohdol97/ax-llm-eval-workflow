# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────
# ax-llm-eval-workflow Backend Dockerfile
#
# 멀티스테이지 빌드:
#   1) builder  : 의존성 설치 + wheel 빌드
#   2) runtime  : non-root, 최소 런타임
#
# 보안:
#   - non-root 사용자 (labs:1000) 로 실행
#   - 시스템 패키지 최소화 (curl 만 — healthcheck 용)
#   - HEALTHCHECK 정의
#
# 참조: BUILD_ORDER.md Phase 1 작업 1-1 / docs/adr/ADR-011
# ─────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 빌드 도구 (wheel 빌드용). 런타임에는 포함되지 않는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 의존성 정의를 먼저 복사 → 캐시 효율
COPY pyproject.toml ./
COPY README.md ./

# 소스 복사 후 editable 설치 → /usr/local/lib/python3.12/site-packages
COPY app ./app

RUN pip install --upgrade pip wheel \
    && pip install --prefix=/install .


# ── Stage 2: runtime ─────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/usr/local/bin:${PATH}"

# 런타임 시스템 패키지 — curl(healthcheck), ca-certificates(TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/*

# non-root 사용자 (UID/GID 1000)
RUN groupadd --system --gid 1000 labs \
    && useradd --system --uid 1000 --gid labs --create-home --home-dir /home/labs labs

WORKDIR /app

# builder에서 설치된 site-packages + 콘솔 스크립트 복사
COPY --from=builder /install /usr/local

# 애플리케이션 소스
COPY --chown=labs:labs app ./app
COPY --chown=labs:labs pyproject.toml ./

USER labs

EXPOSE 8000

# 컨테이너 자체 헬스체크 — compose의 healthcheck와 별개로 동작.
# /api/v1/health 는 사내 의존 서비스 도달성도 함께 확인한다.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# 운영 진입점 — 4 worker, host 0.0.0.0, port 8000
# 개발 환경(override)은 --reload + 단일 워커로 command를 덮어쓴다.
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
