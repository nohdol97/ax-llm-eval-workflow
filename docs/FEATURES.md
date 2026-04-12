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
- 여러 이미지 동시 첨부 가능
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
- Langfuse 프롬프트 선택 (이름 + 버전)
- Langfuse 데이터셋 선택
- 모델 선택 (복수 선택 가능 → 모델별 병렬 실행)
- 파라미터 설정 (모델별 개별 설정 가능)

#### 평가 함수 선택
- 내장 평가 함수 목록에서 선택 (체크박스)
- LLM-as-Judge 설정: Judge 모델, 평가 프롬프트 입력
- Custom Evaluator 코드 입력 (Python)
- 평가 함수별 가중치 설정

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
- 반환값: 0.0 ~ 1.0 사이 float
- Python 표준 라이브러리 7개만 사용 가능 (json, re, math, collections, difflib, statistics, unicodedata)
- 샌드박스 환경에서 실행 (보안)

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
- 최대 5개 (섹션 28.2 시리즈 색상 수 기준)
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
