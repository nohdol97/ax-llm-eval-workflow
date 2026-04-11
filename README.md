# ax-llm-eval-workflow

Langfuse v3 기반 LLM 프롬프트 실험/평가 워크플로우

## 개요

LLM 서비스 운영에서 프롬프트 관리, 실험 실행, 성능 평가, 결과 분석까지의 전 과정을 하나의 워크플로우로 제공합니다.

### 핵심 기능

- **단일 테스트**: 프롬프트 개발 초기, 특정 케이스 빠른 검증 (멀티모달 입력, 스트리밍 응답 지원)
- **배치 실험**: Golden Dataset 기반 성능 평가 및 모델 비교
- **Custom Evaluation**: 코드 레벨에서 평가 지표를 자유롭게 정의
- **Context Engineering**: Prompt Variables를 활용한 동적 컨텍스트 삽입
- **실험 비교/분석**: 응답 시간, 비용, 스코어, 토큰 수 기반 실험 간 비교

### 왜 직접 구축하는가

- Langfuse Playground는 멀티모달 입력 미지원
- Context Engineering을 위한 코드 레벨 유연성 필요
- LLM 제공사의 신기능을 빠르게 검토/실험할 수 있는 환경 필요
- 프롬프트 > 데이터셋 > 실험 > 평가 > 분석이 하나의 흐름으로 연결되어야 함

## 아키텍처

```
┌─────────────────────────────────────┐
│         Frontend (Next.js)          │
│  단일테스트 | 배치실험 | 데이터셋    │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│        Backend (FastAPI)            │
│  Experiment Runner | Eval Engine    │
│  Context Engine | Dataset Manager   │
└───┬──────────┬──────────────┬───────┘
    │          │              │
┌───▼───┐ ┌───▼────┐  ┌─────▼──────┐
│LiteLLM│ │Langfuse│  │ClickHouse  │
│Proxy  │ │  v3    │  │ (직접쿼리) │
└───────┘ └────────┘  └────────────┘
```

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| Frontend | Next.js 15, TypeScript, Tailwind CSS v4, shadcn/ui |
| Backend | Python 3.12+, FastAPI |
| LLM Gateway | LiteLLM Proxy |
| 데이터 레이어 | Langfuse v3 (ClickHouse + PostgreSQL + Redis) |
| 상태 저장 | Redis (실험 상태/진행률) |
| 인증 | 사내 Auth 서비스 JWT |
| 샌드박스 | Docker 컨테이너 격리 (Custom Evaluator) |
| 컨테이너 | Docker, Docker Compose |
| CI/CD | GitHub Actions |

## 프로젝트 구조

```
ax-llm-eval-workflow/
├── docs/                  # 설계 문서
│   ├── ARCHITECTURE.md    # 시스템 아키텍처 상세
│   ├── FEATURES.md        # 기능 명세
│   ├── LANGFUSE.md        # Langfuse 연동 전략
│   ├── API_DESIGN.md      # API 설계
│   ├── EVALUATION.md      # 평가 시스템 설계
│   └── UI_UX_DESIGN.md    # UI/UX 설계
├── backend/               # FastAPI 백엔드
├── frontend/              # Next.js 프론트엔드
├── docker/                # Docker 설정
├── scripts/               # 자동화 스크립트
└── .github/workflows/     # CI/CD
```

## 문서 가이드

| 문서 | 내용 |
|------|------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 시스템 아키텍처, 데이터 흐름, 인프라 구성 |
| [FEATURES.md](docs/FEATURES.md) | 기능별 상세 명세, 활용 시나리오 |
| [LANGFUSE.md](docs/LANGFUSE.md) | Langfuse v3 연동 전략, API 매핑, ClickHouse 활용 |
| [API_DESIGN.md](docs/API_DESIGN.md) | REST API 설계, 엔드포인트, 요청/응답 스키마 |
| [EVALUATION.md](docs/EVALUATION.md) | 평가 시스템 설계, Custom Evaluator, 스코어링 |
| [UI_UX_DESIGN.md](docs/UI_UX_DESIGN.md) | UI/UX 설계, 디자인 토큰, 페이지별 레이아웃, 인터랙션 |

## 라이선스

MIT
