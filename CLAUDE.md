# ax-llm-eval-workflow

## 프로젝트 개요
Langfuse v3 기반 LLM 프롬프트 실험/평가 워크플로우 플랫폼.
GenAI Labs 컨셉을 구현하여 프롬프트 관리, 배치 실험, Custom Evaluation, Context Engineering을 하나의 워크플로우로 제공한다.

## 기술 스택
- **Backend**: Python 3.12+, FastAPI
- **Frontend**: Next.js 15 (App Router), TypeScript, Tailwind CSS
- **데이터 레이어**: Langfuse v3 (ClickHouse + PostgreSQL + Redis)
- **LLM Gateway**: LiteLLM Proxy (멀티 프로바이더 통합)
- **테스트**: pytest (backend), vitest (frontend)
- **컨테이너**: Docker, Docker Compose
- **CI/CD**: GitHub Actions

## 디렉토리 구조 가이드
```
ax-llm-eval-workflow/
├── docs/                  # 설계 문서, 아키텍처 다이어그램
├── backend/               # FastAPI 백엔드
│   ├── app/
│   │   ├── api/           # API 라우터
│   │   ├── core/          # 설정, 의존성
│   │   ├── services/      # 비즈니스 로직
│   │   ├── models/        # Pydantic 모델
│   │   └── evaluators/    # Custom Evaluation 엔진
│   └── tests/
├── frontend/              # Next.js 프론트엔드
│   ├── src/
│   │   ├── app/           # App Router 페이지
│   │   ├── components/    # UI 컴포넌트
│   │   ├── hooks/         # React hooks
│   │   └── lib/           # 유틸리티, API 클라이언트
│   └── tests/
├── docker/                # Docker 설정
├── scripts/               # 자동화 스크립트
└── .github/workflows/     # CI/CD
```

## 개발 컨벤션
- 모든 API 엔드포인트는 `/api/v1/` 접두사 사용
- Langfuse SDK 호출은 `services/langfuse_client.py`에서 중앙 관리
- 환경별 설정은 `.env.development`, `.env.production`으로 분리
- 시크릿(API 키, 토큰)은 절대 커밋 금지
- 커밋 메시지는 Conventional Commits 형식

## Langfuse 연동 규칙
- Langfuse v3 API를 직접 호출 (SDK 우선, REST API 보조)
- Trace/Generation 데이터는 Langfuse에만 저장 (자체 DB 중복 금지)
- ClickHouse 직접 쿼리는 분석/대시보드 용도로만 사용
- 프롬프트 원본은 Langfuse Prompt Management에서 관리

## 테스트
- 테스트 코드 작성은 Codex에 위임 (/codex:rescue)
- 인프라 검증: Docker 빌드 테스트
- API 검증: FastAPI TestClient 기반 통합 테스트
- Frontend 검증: vitest + Playwright E2E
