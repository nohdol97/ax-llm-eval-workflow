# 기능 명세

## 1. 단일 테스트

### 1.1 목적
프롬프트 개발 초기 단계에서 특정 케이스를 빠르게 검증한다.
코드를 작성하지 않고도 프롬프트의 동작을 즉시 확인할 수 있어야 한다.

### 1.2 기능 상세

#### 프롬프트 로드
- Langfuse에 저장된 프롬프트를 이름/버전/라벨로 조회하여 로드
- 에디터에서 프롬프트를 직접 입력하거나 수정 가능
- 프롬프트 내 `{{variable}}` 형태의 변수를 자동 감지하여 입력 폼 생성

#### 모델/파라미터 설정
- LiteLLM Proxy에 등록된 모델 목록을 드롭다운으로 제공
- 프로바이더별 모델 그룹핑 (Azure OpenAI / Gemini / Bedrock / Claude / OpenAI)
- 파라미터 조절: temperature, top_p, max_tokens, frequency_penalty, presence_penalty
- System Prompt 별도 입력 영역

#### 멀티모달 입력
- 이미지 파일 업로드 (drag & drop, 클릭 선택)
- 지원 포맷: PNG, JPEG, WebP, GIF
- 이미지 미리보기 및 삭제
- 여러 이미지 동시 첨부 가능 (최대 10장, 개별 이미지 최대 20MB — API_DESIGN.md §3.1 검증 규칙과 일치)
- base64 인코딩 후 LLM API messages에 포함

#### 스트리밍 응답
- SSE (Server-Sent Events)로 실시간 토큰 스트리밍
- 응답 생성 중 중단 버튼
- 응답 완료 후 메타데이터 표시: 지연 시간, 토큰 수 (input/output), 예상 비용

#### Context Engineering
- Prompt Variables에 값을 바인딩하여 동적 컨텍스트 삽입
- 변수 타입: text, json, file (파일 내용을 변수에 바인딩)
- 변수 프리셋 저장/로드 기능

### 1.3 사용자 시나리오

```
1. 사용자가 Langfuse에서 "sentiment-analysis" 프롬프트 v3를 로드
2. 변수 {{input_text}}에 분석할 텍스트 입력
3. 변수 {{analysis_rules}}에 분석 규칙 JSON 바인딩
4. 모델: GPT-4o, temperature: 0.1 설정
5. "실행" 클릭 → 스트리밍으로 분석 결과 수신
6. 결과 확인 후 temperature를 0.3으로 바꿔 재실행
7. 두 결과를 나란히 비교
```

---

## 2. 배치 실험

### 2.1 목적
Golden Dataset 기반으로 프롬프트/모델의 성능을 체계적으로 평가한다.
모델 간 비교, 프롬프트 버전 간 비교를 정량적으로 수행한다.

### 2.2 기능 상세

#### 실험 설정
- 실험 이름, 설명 입력
- Langfuse 프롬프트 선택 (이름 + 버전, **복수 버전 선택 가능** → 버전별 병렬 실행)
- Langfuse 데이터셋 선택
- 모델 선택 (복수 선택 가능 → 모델별 병렬 실행)
- 파라미터 설정 (모델별 개별 설정 가능)
- 총 Run 수 = 프롬프트 버전 수 × 모델 수 (API_DESIGN.md §4.1 `prompt_configs[]` × `model_configs[]`)

#### 평가 함수 선택
- 내장 평가 함수 목록에서 선택 (체크박스)
- LLM-as-Judge 설정: Judge 모델, 평가 프롬프트 입력
- Custom Evaluator:
  - `user` 역할: admin 승인된 목록에서 선택 (API_DESIGN.md §4.1의 `type: "approved"`, §9.1 거버넌스 파이프라인 참조)
  - `admin` 역할: 승인 없이 인라인 `custom_code`도 직접 포함 가능 (EVALUATION.md §4.3 "admin 역할만 실행 가능")
- 평가 함수별 가중치 설정 (합계 1.0, 상세 규칙 EVALUATION.md §5.4 / API_DESIGN.md §4.1)

#### 실행 및 모니터링
- 실행 시작 → 진행률 바 (완료/전체 아이템 수)
- 아이템별 상태: 대기 → 실행 중 → 완료/실패
- 실패 아이템 재시도 옵션
- 실험 일시 정지/재개/중단
- 동시 실행 제한 (concurrency) 설정

#### 결과 저장
- 모든 결과는 Langfuse에 trace + score + dataset run으로 기록
- 실험 메타데이터 (설정, 실행 시간, 총 비용)는 Langfuse trace의 metadata로 저장

### 2.3 사용자 시나리오

```
1. 사용자가 "감성 분석 정확도 실험 v3 vs v4" 실험 생성
2. 프롬프트: "sentiment-analysis" v3, v4 두 버전 선택
3. 데이터셋: "sentiment-analysis-golden-100" 선택
4. 모델: GPT-4o, GPT-4.1, Gemini 2.5 Pro 선택
5. 평가 함수: exact_match, llm_judge (일관성 평가)
6. 실행 → 총 600건 (100 아이템 × 2 프롬프트 × 3 모델)
7. 진행률과 실시간 스코어 확인
8. 완료 후 비교 분석으로 이동
```

---

## 3. 실험 비교/분석

### 3.1 목적
여러 실험 Run의 결과를 나란히 비교하여 최적의 프롬프트/모델 조합을 찾는다.

### 3.2 기능 상세

#### 실험 간 요약 비교
- 비교할 Run 선택 (2개 이상)
- 요약 테이블: 모델, 프롬프트 버전, avg latency, total cost, avg score, token count
- 막대/레이더 차트로 시각화

#### 아이템별 상세 비교
- 동일 데이터셋 아이템에 대한 Run별 출력 나란히 보기
- 스코어 차이가 큰 아이템 하이라이트 (outlier 감지)
- 개별 아이템의 input → output → expected → score 상세 보기

#### 스코어 분석
- 평가 함수별 스코어 분포 히스토그램
- Run 간 스코어 상관관계 산점도
- 스코어 기준 정렬 및 필터링

#### 비용/성능 분석
- Run별 총 비용 비교
- 토큰당 비용 효율 비교
- 지연 시간 분포 (P50, P90, P99)
- 비용 대비 스코어 효율 매트릭스

### 3.3 데이터 소스

모든 분석 데이터는 Langfuse ClickHouse에서 직접 쿼리하여 조회한다.

```
ClickHouse 쿼리 대상:
├── traces: 실험 실행 기록, 메타데이터
├── observations (generations): 모델 호출 기록, 토큰, 비용, 지연
├── scores: 평가 점수
└── dataset_run_items: 데이터셋 아이템과 trace 연결
```

---

## 4. 데이터셋 관리

### 4.1 목적
실험용 데이터셋을 준비하고 Langfuse에 업로드한다.

### 4.2 기능 상세

#### 파일 업로드
- 지원 포맷: CSV, JSON, JSONL
- 파일 크기 제한: 50MB
- 행 수 제한: 최대 10,000행 (초과 시 분할 업로드 권장)
- 인코딩 자동 감지 (UTF-8, EUC-KR 등)

#### 컬럼 매핑
- 파일의 컬럼/필드를 Langfuse 데이터셋 스키마에 매핑
  - input: 프롬프트 변수에 바인딩될 입력 데이터
  - expected_output: 기대 출력 (평가 기준)
  - metadata: 추가 메타데이터 (카테고리, 난이도 등)
- 매핑 미리보기: 첫 5건 미리보기로 확인

#### 데이터셋 목록
- Langfuse에 저장된 데이터셋 목록 조회
- 데이터셋별 아이템 수, 생성일, 최근 사용 실험
- 데이터셋 아이템 브라우징 (페이지네이션)

---

## 5. Custom Evaluation

### 5.1 목적
도메인별 평가 기준을 코드 레벨에서 자유롭게 정의한다.

### 5.2 평가 함수 유형

#### 내장 평가 함수 (Built-in)

| 함수 | 설명 | 반환값 |
|------|------|--------|
| `exact_match` | 출력과 기대값의 정확 일치 | 0 또는 1 |
| `contains` | 출력에 기대 키워드 포함 여부 | 0 또는 1 |
| `regex_match` | 출력이 정규표현식 패턴에 매칭 | 0 또는 1 |
| `json_validity` | 출력이 유효한 JSON인지 검증 | 0 또는 1 |
| `json_schema_match` | 출력이 지정 JSON 스키마를 따르는지 | 0 또는 1 |
| `json_key_presence` | 출력 JSON에 필수 키 존재 비율 | 0.0 ~ 1.0 |
| `levenshtein_similarity` | 편집 거리 기반 유사도 | 0.0 ~ 1.0 |
| `cosine_similarity` | 임베딩 기반 의미 유사도 | 0.0 ~ 1.0 |
| `bleu` | BLEU 스코어 | 0.0 ~ 1.0 |
| `rouge` | ROUGE-L 스코어 | 0.0 ~ 1.0 |
| `latency_check` | 응답 시간이 임계값 이내인지 | 0 또는 1 |
| `token_budget_check` | 출력 토큰 수가 예산 이내인지 | 0 또는 1 |
| `cost_check` | 호출 비용이 임계값 이내인지 | 0 또는 1 |

#### LLM-as-Judge

- Judge 모델 선택 (GPT-4o 권장)
- 평가 프롬프트 커스터마이징
- 출력: 0~10 정수 스코어 + 평가 근거 텍스트
- 기본 제공 평가 기준: 정확성, 관련성, 일관성, 유해성, 자연스러움

#### Custom Code Evaluator

- Python 함수로 평가 로직 작성
- 함수 시그니처: `def evaluate(output: str, expected: str, metadata: dict) -> float`
- 반환값: 0.0 ~ 1.0 사이 float (범위 밖은 자동 클램핑)
- Python 표준 라이브러리 7개만 사용 가능 (json, re, math, collections, difflib, statistics, unicodedata)
- Docker 컨테이너 샌드박스 실행: `--network=none`, 메모리 128MB, CPU 0.5, 타임아웃 5초
- **거버넌스**: 실험에서 사용하려면 admin 승인 필요 (§9.1 참조). 미승인 코드는 `POST /evaluators/validate`로 검증만 가능하며, 배치 실험에는 `approved` 타입으로 승인된 submission만 사용 가능 (API_DESIGN.md §14 참조)

### 5.3 평가 파이프라인

```
모델 응답 수신
    │
    ▼
평가 함수 병렬 실행
    ├── built-in evaluator 1 → score_1
    ├── built-in evaluator 2 → score_2
    ├── llm-as-judge → score_3
    └── custom evaluator → score_4
    │
    ▼
스코어를 Langfuse에 기록
    └── langfuse.score(trace_id, name, value)
```

---

## 6. Context Engineering

### 6.1 목적
프롬프트의 성능을 극대화하기 위해 동적 컨텍스트를 체계적으로 관리한다.

### 6.2 기능 상세

#### Prompt Variables
- 프롬프트 내 `{{variable_name}}` 패턴을 자동 파싱
- 변수별 타입 지정: text, json, file, list
- 데이터셋의 컬럼을 변수에 자동 매핑

#### 컨텍스트 구성 전략

| 전략 | 설명 | 사용 사례 |
|------|------|-----------|
| Static Context | 고정된 규칙, 가이드라인을 프롬프트에 삽입 | 분류 규칙, 출력 포맷 정의 |
| Dynamic Context | 입력에 따라 다른 컨텍스트를 선택적으로 삽입 | RAG 결과, 사용자 이력 |
| Few-shot Examples | 유사 사례를 동적으로 선택하여 삽입 | 분류, 추출 태스크 |
| Metadata Context | 데이터셋 메타데이터를 활용한 조건부 컨텍스트 | 도메인별 규칙 분기 |

#### 변수 프리셋
- 자주 사용하는 변수 조합을 프리셋으로 저장
- 프리셋 이름, 설명, 변수 값 세트
- 실험 설정 시 프리셋 선택으로 빠르게 적용

---

## 7. 멀티 LLM 지원

### 7.1 지원 프로바이더

| 프로바이더 | 모델 예시 | 연동 방식 |
|-----------|-----------|-----------|
| Azure OpenAI | GPT-4o, GPT-4.1 | LiteLLM → Azure API |
| Google Gemini | Gemini 2.5 Pro, 2.5 Flash | LiteLLM → Vertex AI / AI Studio |
| AWS Bedrock | Claude 4.5, Llama 3.3 | LiteLLM → Bedrock API |
| Anthropic | Claude 4.5 Sonnet, Claude 4.6 Opus | LiteLLM → Anthropic API |
| OpenAI | GPT-5.4, o3, o4-mini | LiteLLM → OpenAI API |

### 7.2 모델 관리
- LiteLLM Proxy의 모델 목록을 API로 조회하여 UI에 표시
- 모델별 비용 정보 자동 반영 (LiteLLM의 cost tracking)
- 새 모델 추가는 LiteLLM 설정에서만 관리 (Labs에서는 조회만)

---

## 8. 활용 시나리오 정리

### 개발 단계
- 신규 프롬프트 초안 작성 → 단일 테스트로 빠른 검증
- 변수/컨텍스트 조합 탐색 → 변수 프리셋 활용
- 멀티모달 프롬프트 개발 → 이미지 입력 테스트

### 검증 단계
- Golden Dataset 구축 → 데이터셋 업로드
- 프롬프트 A/B 테스트 → 배치 실험 (복수 프롬프트 버전)
- 모델 비교 평가 → 배치 실험 (복수 모델)
- 커스텀 품질 기준 적용 → Custom Evaluator 작성

### 분석 단계
- 실험 결과 종합 비교 → 실험 비교 대시보드
- 비용 효율 분석 → 비용 대비 스코어 매트릭스
- 실패 케이스 심층 분석 → 아이템별 상세 비교
- 프롬프트 버전 이력 추적 → Langfuse 프롬프트 버전 관리

---

## 9. 협업/거버넌스 기능

### 9.1 Custom Evaluator 거버넌스 파이프라인
도메인 특화 평가 함수를 팀 단위로 공유/재사용하기 위한 승인 기반 워크플로우.

**플로우**:
1. `user` 역할 사용자가 Custom Evaluator 코드 작성 → 제출 (`POST /evaluators/submissions`)
2. `admin` 역할이 검토 큐에서 코드/테스트 결과/보안 확인 → 승인 또는 반려
3. 승인 시 전체 사용자가 실험 생성 시 사용 가능 (`GET /evaluators/approved`)
4. 반려 시 제출자에게 알림 발송 (사유 포함)
5. **Deprecated 정책**: 승인 후 보안 이슈/품질 회귀 발견 시 admin이 `approved → deprecated`로 전환. Deprecated submission은 신규 실험 선택 목록에서 즉시 제외되며, 진행 중 실험은 시작 시점의 snapshot(코드+해시) 기반으로 완료한다. 신규 사용 시도는 `ax_unauthorized_evaluator_attempts_total`로 기록되고 `evaluator_deprecated` 알림이 소유자/구독자에게 발송된다 (API_DESIGN.md §14, §14.3 참조).

**권한 매트릭스**:
| 역할 | 검증 실행 | 제출 | 자기 제출 조회 | 전체 승인/반려 |
|------|---------|------|-------------|-------------|
| viewer | ✕ | ✕ | ✕ | ✕ |
| user | ○ | ○ | ○ | ✕ |
| admin | ○ | ○ (자동 승인) | ○ (전체) | ○ |

### 9.2 알림 수신함 (Notification Inbox)
비동기 이벤트를 복기할 수 있는 영구 알림 저장소.

**트리거 이벤트**:
- `experiment_complete`: 배치 실험 완료
- `experiment_failed`: 배치 실험 실패
- `evaluator_approved`: 내 제출이 승인됨
- `evaluator_rejected`: 내 제출이 반려됨 (사유 포함)

**보관**: 사용자별 Redis 저장, TTL 30일

**통지 수단**:
- Top Bar 종 아이콘 배지 (unread 수)
- 브라우저 알림 API (탭 비활성 시, 최초 1회 권한 요청)
- Side Nav 실험 아이콘 배지 (실행 중 건수)

### 9.3 실패 아이템 → 새 데이터셋 파생
실험에서 실패한 아이템만 선별하여 새 Golden Dataset으로 파생.

- 결과 비교 페이지에서 "실패만 필터" → 아이템 선택 → "새 데이터셋으로 저장"
- Langfuse trace에서 input/expected/metadata 자동 추출
- 파생 데이터셋으로 프롬프트 수정 후 재실험 → 실패 케이스 반복 개선 루프

### 9.4 비교 장바구니 (Compare Basket)
실험 목록에서 비교 대상을 페이지 간 유지하며 누적 선택.

- `localStorage` 기반 전역 상태 (새로고침 후에도 보존)
- Top Bar 배지 + 드롭다운으로 현재 담긴 실험 조회
- 최대 5개 (차트 가독성 및 비교 UI 한도, BUILD_ORDER.md §비교 장바구니 드롭다운 참조)
- 동일 프로젝트 실험만 비교 가능

### 9.5 실험 템플릿
자주 사용하는 실험 설정을 템플릿으로 저장하여 재사용.

- 프롬프트/데이터셋/모델/평가 함수/파라미터 전체 설정
- `localStorage` 기반 (사용자별, 기기별)
- 실험 생성 위저드에서 "템플릿에서 시작" 선택 가능
- 기존 실험 → "같은 설정으로 재실행"은 템플릿과 별개 (서버 `config_snapshot` 기반)

---

## 10. 평가 함수 가중치

배치 실험에서 여러 평가 함수를 조합하여 종합 점수(weighted_score) 산출.

- 각 evaluator에 `weight` (0.0~1.0) 지정, 합계 1.0
- 가중 평균: `Σ(score_i × weight_i)`, null 스코어는 제외하고 재정규화
- `weighted_score`라는 이름으로 Langfuse score에 별도 기록
- KPI 카드의 "Best Score"는 기본적으로 weighted_score 기준

상세 계산 규칙은 EVALUATION.md §5.4 참조.

---

## 11. 기능 우선순위 (MVP / Post-MVP)

본 프로젝트의 단계별 범위를 BUILD_ORDER.md와 일치시켜 정의한다.

| 우선순위 | 기능 | 비고 |
|---------|------|------|
| P0 (MVP) | 데이터셋 업로드 (§4), 단일 테스트 (§1), 배치 실험 (§2), 내장 평가 함수 (§5.2), LLM-as-Judge (§5.2), 기본 실험 비교 (§3.2 실험 간 요약 비교 / 아이템별 상세 비교) | BUILD_ORDER.md Phase 3 / 3-2(데이터셋 API) → Phase 4 / 4-1~4-4, 4-7(실행 엔진/단일·배치) → Phase 5 / 5-1~5-2, 5-4(내장·Judge·파이프라인) → Phase 6 / 6-1~6-3(비교 분석) |
| P1 | 스코어 분석 (§3.2 스코어 분석), 비용/성능 분석 (§3.2 비용/성능 분석), 변수 프리셋 (§6.2), 실패 아이템 파생 데이터셋 (§9.3), 알림 수신함 (§9.2) | BUILD_ORDER.md Phase 3 / 3-2(`POST /datasets/from-items`) → Phase 4 / 4-6(알림 생성) → Phase 6 / 6-3(scores/latency/cost distribution) → Phase 7(변수 프리셋 localStorage, 알림 Top Bar 드롭다운) |
| P2 | 평가 가중치 (§10), Custom Evaluator 거버넌스 (§5.2, §9.1), 비교 장바구니 (§9.4), 실험 템플릿 (§9.5) | BUILD_ORDER.md Phase 4 / 4-7(config_snapshot) → Phase 5 / 5-3, 5-5, 5-6(Custom Code·weighted_score·거버넌스 API) → Phase 7(장바구니/템플릿/거버넌스 페이지) |

---

## 12. 비기능 요구사항 (NFR)

각 항목은 OBSERVABILITY.md §2.2 메트릭 또는 §2.4 recording rule로 측정한다.

### 12.1 성능
| NFR | 임계값 | 측정 메트릭 |
|-----|-------|-----------|
| 단일 테스트 첫 토큰 지연 | p95 < 1.5s | `ax_llm_first_token_latency_seconds` (p95) |
| 배치 실험 처리량 (100×3 run) | p95 < 10분 | `ax_experiment_batch_duration_seconds` (p95) |
| 비교 페이지 초기 로딩 | p95 < 3s | `ax:http_request_duration_seconds:p95_5m{route="/api/v1/compare"}` (OBSERVABILITY §9.1 SLO 정합) |
| Custom Evaluator 단건 실행 | p95 < 5s | `ax_sandbox_container_duration_seconds` (p95), `ax:sandbox:duration_p99_5m` 보조 |
| 동시 실험 수 | 워크스페이스당 ≤ 5 | `ax_experiments_in_progress{project_id}` (gauge) |

### 12.2 보안
| NFR | 측정 메트릭 |
|-----|-----------|
| JWT 인증 / RBAC 강제 | `ax_auth_failures_total`, `ax_http_requests_total{status="401\|403"}` |
| Sandbox 격리(`--network=none`, 128MB/0.5/5s), 미승인 실험 차단 | `ax_sandbox_violations_total`, `ax_unauthorized_evaluator_attempts_total` |
| LLM Provider 키 LiteLLM 단독 보관 | 정적 검사 (Backend 코드에 키 참조 0건) |
| ClickHouse parameterized query / 읽기 전용 | 정적 검사 + DB 권한 감사 |
| 프롬프트/출력 원본 로그 금지 | 로그 PII 스캐너 (CI 단계) |

### 12.3 가용성 / 신뢰성
| NFR | 임계값 | 측정 메트릭 |
|-----|-------|-----------|
| 실험 상태 Redis TTL 24h + Langfuse 영속화 | 손실 0건 | `ax_experiment_persistence_failures_total` |
| Backend 재시작 후 체크포인트 재개 | 재개 성공률 ≥ 99% | `ax_experiment_resume_total{outcome}` |
| LiteLLM Proxy 장애 시 명시적 에러 + 재시도 | 사용자 인지 가능 에러율 100% | `ax_litellm_errors_total{kind}` |
| Labs Backend/Frontend 가용성 | ≥ 99.9% (월) | `up{job="labs-backend\|labs-frontend"}`, OBSERVABILITY §9.1 SLO |

### 12.4 접근성 / 관측성
| NFR | 측정 메트릭 |
|-----|-----------|
| **지원 환경**: v1은 사내 데스크톱 브라우저 전용 (최소 1280px, 권장 1440px+), 모바일/태블릿 미지원 | UI_UX_DESIGN.md "뷰포트 정책" 및 §7 참조 |
| UI WCAG 2.1 AA 준수 | aXe CI 리포트 (위반 0건), UI_UX_DESIGN.md 참조 |
| 배치 실험 → Langfuse trace/score/dataset run 완전 기록 | `ax_langfuse_persistence_success_ratio` |
| 구조화 JSON 로그 + 요청 ID 전파 | 로그 스키마 검증 (CI), OBSERVABILITY.md 참조 |

---

## 13. 역할별 기능 매트릭스

역할: `viewer` (조회 전용), `user` (실험 실행), `admin` (거버넌스/관리).

| 기능 | viewer | user | admin |
|------|:------:|:----:|:-----:|
| 단일 테스트 실행 (§1) | ✕ | ○ | ○ |
| 배치 실험 생성/실행 (§2) | ✕ | ○ | ○ |
| 실험 결과 비교/분석 조회 (§3) | ○ | ○ | ○ |
| 데이터셋 업로드 (§4) | ✕ | ○ | ○ |
| 데이터셋 목록 조회 (§4.2) | ○ | ○ | ○ |
| 내장 / LLM-as-Judge 평가 사용 (§5.2) | ✕ | ○ | ○ |
| Custom Evaluator 제출 (§5.2, §9.1) | ✕ | ○ | ○ (자동 승인) |
| Custom Evaluator 검증 실행 (validate) | ✕ | ○ | ○ |
| 인라인 `custom_code` 직접 실행 (§5.2) | ✕ | ✕ | ○ |
| Evaluator 승인/반려 (§9.1) | ✕ | ✕ | ○ |
| 알림 수신함 조회 (§9.2) | ○ (본인) | ○ (본인) | ○ (본인) |
| 실패 아이템 → 새 데이터셋 파생 (§9.3) | ✕ | ○ | ○ |
| 비교 장바구니 / 실험 템플릿 (§9.4, §9.5) | ○ (조회) | ○ | ○ |
| 프로젝트 예산/사용량 경고 수신 (API §9) | ✕ | ✕ | ○ |

API 레벨 권한은 API_DESIGN.md §권한 섹션이 정본(Source of Truth)이며, 본 매트릭스는 기능 단위 요약이다.

---

## 14. 에러 및 엣지 플로우

### 14.1 단일 테스트 (§1)
- **파일 업로드 실패**: 포맷 미지원/용량 초과(20MB, 10장) → 업로드 전 클라이언트 검증, 서버 재검증 실패 시 `400 INVALID_FILE` 반환 및 입력 유지
- **LiteLLM 4xx (잘못된 파라미터)**: 에러 메시지 인라인 표시, 파라미터 조정 가이드 링크 제공
- **LiteLLM 5xx / 타임아웃(30s)**: "재시도" 버튼 노출, 요청 ID를 토스트에 표시
- **스트리밍 중단**: 사용자가 중단 버튼 클릭 시 생성된 부분 토큰은 유지하고 "partial" 배지 표시
- **네트워크 끊김 (SSE)**: 최대 1회 자동 재연결 시도, 실패 시 부분 응답 보존 + 재시도 유도

### 14.2 배치 실험 (§2)
- **아이템 단건 실패**: 전체 실험은 계속 진행, 실패 아이템은 최대 2회 자동 재시도 후 `failed` 표시
- **실험 전체 실패율 > 50%**: 자동 일시 정지, admin/소유자에게 알림
- **동시 실행 한도 초과**: 대기 큐로 전환, 예상 시작 시각 표시
- **Langfuse 저장 실패**: 로컬 Redis 버퍼 후 재시도 (최대 3회), 최종 실패 시 실험 상태 `degraded` + 경고 배지
- **사용자 세션 종료**: 실험은 백그라운드에서 계속, 완료 시 알림 수신함에 기록

### 14.3 Custom Evaluator (§5.2, §9.1)
- **샌드박스 타임아웃**: 해당 아이템 스코어 null 처리, 가중 평균에서 제외 후 재정규화
- **메모리 초과(OOM)**: 컨테이너 강제 종료 + `evaluator_error` 로그, 사용자에게 검증 요청 권고
- **승인된 코드의 보안 이슈 사후 발견**: admin이 `deprecated`로 전환, 실행 중 실험은 snapshot 기반으로 완료 (API_DESIGN.md §14 참조)

### 14.4 데이터셋 업로드 (§4)
- **인코딩 자동 감지 실패**: 사용자에게 수동 선택 요청 (UTF-8 기본)
- **10,000행 초과**: 분할 업로드 가이드 모달, 첫 10,000행만 임포트 옵션 제공
- **컬럼 매핑 불일치**: 필수 필드(`input`) 누락 시 저장 차단, 실시간 미리보기에서 경고

### 14.5 실험 비교 (§3)
- **선택 Run 스키마 불일치** (다른 데이터셋/평가 함수): 비교 불가 항목은 "-"로 표시, 공통 지표만 정렬
- **ClickHouse 쿼리 타임아웃**: 캐시된 요약값으로 폴백 + "최신 아님" 배너 표시

---

## 15. 사용자 페르소나 · 핵심 시나리오 · KPI · 운영 책임

### 15.1 사용자 페르소나

| 페르소나 | 역할 매핑 | 주요 목표 | 주 사용 기능 | 성공 신호 |
|---------|---------|---------|-----------|----------|
| **Researcher** (프롬프트 엔지니어/AI 리서처) | `user` | 프롬프트/모델 조합 탐색, 품질 가설 검증 | §1 단일 테스트, §2 배치 실험, §3 비교, §6 변수 프리셋 | 주당 실험 ≥3건, 가설→검증 사이클 < 1일 |
| **Engineer** (서비스 개발자) | `user` | 프로덕션 투입 전 회귀 검증, 비용/지연 SLA 확인 | §2 배치 실험, §3.2 비용/성능 분석, §9.3 실패 데이터셋 파생 | 회귀 detect rate ≥ 90%, 배포 차단 의사결정 근거 확보 |
| **Admin** (플랫폼/거버넌스 운영자) | `admin` | Custom Evaluator 승인, 예산/권한 관리, 사고 대응 | §9.1 거버넌스, §13 권한 매트릭스, NFR §12 모니터링 | 승인 SLA p95 < 24h, 보안 사고 0건 |
| **Viewer** (PM/QA/도메인 전문가) | `viewer` | 결과 열람, 품질 의사결정 | §3 비교 분석, §9.2 알림 수신함 | 의사결정 회의 시 비교 링크 인용률 ≥ 80% |

### 15.2 핵심 사용 시나리오 (End-to-End)

#### S1. Researcher: 프롬프트 개선 루프 (1일 사이클)
1. Langfuse에서 대상 프롬프트 v_n 로드 (§1.2)
2. 단일 테스트로 5~10건 케이스 빠른 검증, 변수 프리셋 저장 (§1, §6)
3. Golden Dataset 선택 후 v_n vs v_n+1 배치 실험 생성, 가중치 평가 함수 구성 (§2, §10)
4. 진행률 모니터링 → 완료 알림 수신 (§9.2)
5. 비교 대시보드에서 weighted_score / 비용 / 지연 비교, outlier 분석 (§3)
6. 실패 아이템만 필터 → 새 데이터셋 파생 → 프롬프트 v_n+2로 재실험 (§9.3)
7. 최종 v_n+2를 Langfuse production 라벨로 승격

#### S2. Engineer: 배포 전 회귀 검증
1. 실험 템플릿 "release-regression" 로드 (§9.5)
2. 후보 모델/프롬프트 조합으로 배치 실험 실행 (§2)
3. NFR §12.1 SLO(p95 지연, 비용 임계값)와 비교 → 통과 시 PR 코멘트에 비교 링크 첨부
4. 실패 시 §3.2 아이템별 상세 비교로 회귀 원인 식별 → Researcher에게 핸드오프

#### S3. Admin: Custom Evaluator 승인 거버넌스
1. 알림 수신함에서 신규 제출 알림 확인 (§9.2)
2. 검토 큐에서 코드/검증 결과/보안 점검 → 승인 또는 반려 (§9.1)
3. 승인 시 전체 사용자에게 노출, 반려 시 사유 포함 알림
4. 사후 보안 이슈 발견 시 `deprecated` 전환, snapshot 기반 진행 중 실험만 완료 (§14.3)

### 15.3 North Star · KPI

**North Star Metric**: **주간 검증된 프롬프트 개선 수** (Weekly Validated Prompt Improvements, WVPI)
> 정의: 한 주 동안 배치 실험을 통해 weighted_score가 직전 production 버전 대비 통계적으로 유의하게(>0, p<0.05) 향상되어 production 라벨로 승격된 프롬프트 버전 수.

**보조 KPI** (산출 출처는 OBSERVABILITY.md §2.4 `ax_kpi.rules` recording rule을 1차로, 부재 시 §2.2 raw 메트릭을 사용):

| 카테고리 | 지표 | 목표 (출시 6개월) | 산출 출처 (OBSERVABILITY `ax_kpi.rules` / §2.2) |
|---------|------|----------------|-------------------------------|
| Adoption | WAU (주간 활성 user 역할) | ≥ 20명 | `ax:wau` (recording rule, §2.4) |
| Adoption | 워크스페이스당 주간 실험 수 | ≥ 30건 | `increase(ax_experiments_created_total[7d])` (§2.2) |
| Quality | 실험 성공률 (degraded/failed 제외) | ≥ 95% | `ax_experiments_completed_total{status}` 비율 (§2.2) |
| Quality | 회귀 detect rate (배포 전 회귀 발견율) | ≥ 90% | `ax_regression_detection_total{outcome}` (§2.2) |
| Velocity | 가설→검증 사이클 시간 (p50) | < 4시간 | `ax:experiment_cycle:p50_24h` (recording rule, §2.4) |
| Velocity | Custom Evaluator 승인 SLA (p95) | < 24시간 | `ax:evaluator_approval:p95_24h` (recording rule, §2.4) |
| Velocity | WVPI (North Star, 7일 합) | 트렌드 ↑ | `ax:wvpi:7d` (recording rule, §2.4) |
| Efficiency | 실험당 평균 비용 (USD) | < $2 | `ax_llm_cost_usd_per_experiment` (§2.2) |
| Reliability | NFR §12 SLO 충족률 | ≥ 99% | OBSERVABILITY §9.1 SLO recording rules |
| Governance | 미승인/Deprecated 코드 실험 진입 시도 | 0 | `ax_unauthorized_evaluator_attempts_total` (§2.2) |

**데이터 소스 책임 분담**:
- **Langfuse (원천)**: trace/score/dataset run 등 실험 결과 원본. WVPI 승격 판정의 기반이 되는 weighted_score는 Langfuse score에서 산출.
- **Prometheus (집계)**: 위 표의 모든 KPI 시계열은 Prometheus 메트릭으로 노출되며, Backend가 Langfuse 이벤트(완료/승인/승격 등)를 받아 메트릭으로 변환·기록한다. KPI 대시보드는 Prometheus를 단일 조회 지점으로 사용.

### 15.4 출시 후 운영 책임 (RACI)

| 영역 | Researcher | Engineer | Admin | Platform Team |
|------|:---------:|:--------:|:-----:|:------------:|
| 프롬프트 품질/실험 설계 | **R/A** | C | I | I |
| 배포 전 회귀 검증 | C | **R/A** | I | I |
| Custom Evaluator 승인/거버넌스 | I | I | **R/A** | C |
| 권한·예산·할당량 관리 | I | I | **R/A** | C |
| Labs Backend/Frontend 가용성 (NFR §12.3) | I | I | C | **R/A** |
| 인시던트 대응 (LiteLLM/Langfuse 장애) | I | I | C | **R/A** |
| 보안 사고 대응 (Evaluator/PII) | I | I | **R** | **A** |
| KPI 모니터링·리포팅 | C | C | **R** | **A** |

(R=Responsible, A=Accountable, C=Consulted, I=Informed)

**운영 의식 (Operating Cadence)**:
- **주간**: Platform Team이 KPI 대시보드 + SLO 충족률 리뷰, Admin이 거버넌스 큐 백로그 점검
- **월간**: WVPI 트렌드 리뷰, 비용/성능 회귀 감사, 페르소나별 사용 패턴 분석
- **분기**: NFR 임계값/우선순위(§11) 재조정, 페르소나 인터뷰 기반 로드맵 갱신

## 16. 향후 검토 (Deferred)

v1 범위에서는 제외하되, 사용자 피드백 누적 후 재검토할 후보 기능을 기록한다.

- **프롬프트 diff 뷰어**: Langfuse 프롬프트의 버전 간 변경사항을 시각화하는 UI. v1 범위 외. 향후 사용자 피드백 누적 후 설계. 위치: 프롬프트 로드 모달 또는 실험 비교 페이지 내.
