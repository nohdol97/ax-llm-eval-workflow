# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────
# ax-llm-eval-workflow Frontend Dockerfile (Next.js 15)
#
# 멀티스테이지 빌드:
#   1) deps     : 프로덕션 의존성만 설치 (캐시 키 분리)
#   2) builder  : 모든 의존성 설치 + next build
#   3) runtime  : Next.js standalone 출력만 복사 — 최소 이미지
#
# 전제: frontend/next.config.ts 에 `output: 'standalone'` 설정.
#       (본 작업에서 설정 추가 — Edit으로 next.config.ts 갱신)
#
# 보안:
#   - non-root 사용자 (nextjs:1001)
#   - HEALTHCHECK 정의 (/api/health 라우트 가정)
#
# 참조: BUILD_ORDER.md Phase 1 작업 1-1
# ─────────────────────────────────────────────────────────────────────

# ── Stage 1: deps (프로덕션 의존성만) ─────────────────────────────
FROM node:20-alpine AS deps

# alpine에 libc6-compat 추가 (Next.js SWC 바이너리 호환)
RUN apk add --no-cache libc6-compat

WORKDIR /app

# package manifest만 복사 → 캐시 효율
COPY package.json package-lock.json* ./

# 프로덕션 의존성만 설치 (devDependencies 제외)
RUN npm ci --omit=dev --no-audit --no-fund


# ── Stage 2: builder (전체 의존성 + 빌드) ─────────────────────────
FROM node:20-alpine AS builder

RUN apk add --no-cache libc6-compat

WORKDIR /app

# devDependencies 포함 전체 설치
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# 소스 복사
COPY . .

# Next.js 빌드 — standalone 출력 활성화 필요 (next.config.ts 참조)
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


# ── Stage 3: runtime (standalone) ─────────────────────────────────
FROM node:20-alpine AS runtime

WORKDIR /app

# non-root 사용자 (UID 1001)
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 --ingroup nodejs nextjs

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3100 \
    HOSTNAME=0.0.0.0

# Next.js standalone 출력만 복사 (이미지 크기 최소화)
# - .next/standalone : server.js + 최소 node_modules
# - .next/static     : 정적 자산
# - public           : 정적 파일
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3100

# Next.js standalone은 server.js 단일 진입점 사용
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://localhost:3100/api/health || exit 1

ENTRYPOINT ["node", "server.js"]
