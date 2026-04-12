# 평가 시스템 설계

## 1. 개요

평가 시스템은 LLM 응답의 품질을 정량적으로 측정하는 핵심 모듈이다.
세 가지 평가 방식을 지원하며, 이를 조합하여 다차원적인 품질 평가를 수행한다.

```
평가 방식:
├── Built-in Evaluator    — 내장 평가 함수 (규칙 기반, 빠름)
├── LLM-as-Judge          — LLM이 다른 LLM의 출력을 평가
└── Custom Code Evaluator — 사용자 정의 Python 평가 함수
```

---

## 2. Built-in Evaluator

### 2.1 텍스트 매칭

#### exact_match
- 출력과 기대값의 정확 일치 여부
- 대소문자 무시 옵션, 공백 정규화 옵션
- 반환: 0 또는 1

#### contains
- 출력에 특정 키워드가 포함되는지 확인
- 복수 키워드: AND/OR 조건 선택
- 반환: 0 또는 1

#### regex_match
- 출력이 정규표현식 패턴에 매칭되는지 확인
- 반환: 0 또는 1

#### levenshtein_similarity
- 편집 거리 기반 문자열 유사도
- 정규화: 0.0 ~ 1.0 (1.0이 완전 일치)

### 2.2 구조 검증

#### json_validity
- 출력이 유효한 JSON인지 검증
- 반환: 0 또는 1

#### json_schema_match
- 출력이 지정된 JSON Schema를 따르는지 검증
- 스키마는 평가 함수 설정에서 지정
- 반환: 0 또는 1

#### json_key_presence
- 출력 JSON에 필수 키가 모두 존재하는지 확인
- 필수 키 목록을 설정에서 지정
- 반환: 0.0 ~ 1.0 (존재 비율)

### 2.3 의미 유사도

#### cosine_similarity
- 출력과 기대값의 임베딩 벡터 코사인 유사도
- 임베딩 모델: text-embedding-3-small (기본), text-embedding-3-large 선택 가능
- LiteLLM 통해 임베딩 요청
- 반환: 0.0 ~ 1.0
- **주의**: 다른 Built-in과 달리 외부 API 호출이 필요하여 지연(~100-500ms)과 추가 비용($0.02/1M tokens)이 발생한다. 배치 실험에서 사용 시 아이템당 2회 임베딩 호출(output + expected)이 필요하므로 비용 영향을 사전에 확인해야 한다.

#### bleu
- BLEU 스코어 (기계 번역 평가 지표)
- n-gram 기반 정밀도 (1-gram ~ 4-gram)
- Backend 자체 구현 (nltk 의존성 없음, 표준 라이브러리로 구현)
- 반환: 0.0 ~ 1.0

#### rouge
- ROUGE-L 스코어 (요약 평가 지표)
- 최장 공통 부분 수열 기반
- Backend 자체 구현 (rouge-score 의존성 없음, LCS 알고리즘 직접 구현)
- 반환: 0.0 ~ 1.0

### 2.4 성능/비용

#### latency_check
- 응답 지연 시간이 임계값 이내인지 확인
- 임계값: 설정에서 지정 (ms 단위)
- 반환: 0 또는 1

#### token_budget_check
- 출력 토큰 수가 예산 이내인지 확인
- 예산: 설정에서 지정
- 반환: 0 또는 1

#### cost_check
- 호출 비용이 임계값 이내인지 확인
- 반환: 0 또는 1

---

## 3. LLM-as-Judge

### 3.1 작동 방식

```
평가 대상 LLM 출력
    │
    ▼
Judge 프롬프트 조립
    │  - 평가 기준 (rubric)
    │  - 입력 (input)
    │  - 출력 (output)
    │  - 기대 출력 (expected, optional)
    │
    ▼
Judge LLM 호출 (GPT-4o 권장)
    │
    ▼
응답 파싱 → score (0-10), reasoning (평가 근거)
    │
    ▼
score를 0.0-1.0으로 정규화 (score / 10) 하여 Langfuse에 기록
(모든 평가 함수의 스코어는 0.0~1.0으로 통일)
```

### 3.2 기본 제공 Judge 프롬프트

#### 정확성 (Accuracy)
```
당신은 AI 출력의 정확성을 평가하는 전문 평가자입니다.

## 평가 기준
- 사실적 정확성: 출력 내용이 사실에 부합하는가
- 완전성: 요청된 모든 정보가 포함되었는가
- 기대 출력 일치도: 기대 출력과 의미적으로 일치하는가

## 입력
{input}

## AI 출력
{output}

## 기대 출력
{expected}

## 지시사항
0-10 점수와 평가 근거를 JSON으로 반환하세요.
{"score": <0-10>, "reasoning": "<평가 근거>"}
```

#### 관련성 (Relevance)
- 출력이 입력 질문/요청과 관련이 있는가
- 불필요한 정보가 포함되지 않았는가

#### 일관성 (Consistency)
- 출력 내에서 자기모순이 없는가
- 동일 입력에 대해 이전 출력과 일관되는가

#### 유해성 (Harmfulness)
- 유해하거나 부적절한 내용이 포함되지 않았는가
- 편향이나 차별적 표현이 없는가

#### 자연스러움 (Fluency)
- 문법적으로 올바른가
- 자연스럽고 읽기 쉬운가

### 3.3 커스텀 Judge 프롬프트

사용자가 도메인에 맞는 평가 프롬프트를 직접 작성할 수 있다.

**중요**: 커스텀 Judge 프롬프트에서도 반드시 **0-10 범위**의 점수를 반환하도록 지시해야 한다. Labs는 스코어를 `score / 10`으로 정규화하므로, 다른 범위(0-5, 0-100 등)를 사용하면 스코어가 왜곡된다.

```
필수 요소:
- {input}: 입력 데이터 (자동 치환)
- {output}: LLM 출력 (자동 치환)
- {expected}: 기대 출력 (자동 치환, optional)
- 반환 포맷 지시: JSON {"score": <0-10>, "reasoning": "..."}
- 반드시 0-10 정수 스코어를 사용 (정규화: score / 10 = 0.0~1.0)

선택 요소:
- 평가 rubric (구체적 채점 기준)
- 도메인 특화 규칙
- few-shot 예시
```

### 3.4 Judge 설정

| 설정 | 기본값 | 설명 |
|------|--------|------|
| judge_model | gpt-4o | 평가에 사용할 모델 |
| temperature | 0.0 | 평가 일관성을 위해 0 권장 |
| max_tokens | 500 | Judge 응답 길이 제한 |
| retry_count | 2 | 파싱 실패 시 재시도 횟수 |

### 3.5 주의사항

- Judge 호출도 비용이 발생함 → 비용 추정 UI 제공
- Judge 모델과 평가 대상 모델이 같으면 편향 가능성 → 경고 표시
- Judge 응답 파싱 실패 시 재시도 (초기 시도 1회 + 재시도 최대 2회 = 총 최대 3회 호출), 3회 모두 실패 시 score=null로 기록
- Judge 비용은 실험 비용에 별도 집계

---

## 4. Custom Code Evaluator

### 4.1 함수 규약

```python
def evaluate(output: str, expected: str, metadata: dict) -> float:
    """
    Args:
        output: LLM이 생성한 출력 텍스트
        expected: 데이터셋의 기대 출력 (없으면 빈 문자열)
        metadata: 데이터셋 아이템의 메타데이터 dict

    Returns:
        0.0 ~ 1.0 사이의 float 스코어
        (범위 밖의 값은 자동 클램핑)
    """
    pass
```

### 4.2 사용 가능 라이브러리

샌드박스에 사전 설치된 패키지만 import 가능:

| 패키지 | 용도 |
|--------|------|
| json | JSON 파싱/생성 |
| re | 정규표현식 |
| math | 수학 함수 |
| collections | 카운터, 딕셔너리 유틸 |
| difflib | 텍스트 비교 |
| statistics | 통계 함수 |
| unicodedata | 유니코드 처리 |

**차단된 함수**: exec, eval, open, compile, __import__ (허용 모듈 외), globals, locals, getattr, setattr, delattr, type, breakpoint, exit, quit 등은 보안상 사용할 수 없다. `__import__`는 허용된 7개 모듈에 한해서만 사용 가능하다.

### 4.3 실행 환경

```
보안 제약:
├── 파일 시스템 접근 금지
├── 네트워크 접근 금지
├── OS 명령 실행 금지
├── 실행 시간 제한: 5초
├── 메모리 제한: 128MB
└── 허용된 패키지 외 import 금지

구현 방식:
- Docker 컨테이너 격리 (ax-eval-sandbox 이미지)
- stdin/stdout JSON 파이프 통신
  - 배치: 장수 컨테이너 (docker run -i), 줄 단위 JSON
  - 단일: docker run --rm
- 컨테이너 제약:
  - --network=none, --memory=128m, --cpus=0.5
  - --user=nobody, --read-only
- runner.py가 매 아이템마다 fresh namespace에서 exec()
- admin 역할만 실행 가능
- 예외 발생 시 catch하여 score=null + 에러 메시지 기록
```

### 4.4 예시: 도메인 특화 평가 함수

#### 분류 F1 Score
```python
def evaluate(output, expected, metadata):
    import json
    try:
        pred = json.loads(output)
        truth = json.loads(expected)
        pred_categories = set(pred.get("categories", []))
        true_categories = set(truth.get("categories", []))
        
        if not true_categories:
            return 1.0 if not pred_categories else 0.0
        
        tp = len(pred_categories & true_categories)
        precision = tp / len(pred_categories) if pred_categories else 0
        recall = tp / len(true_categories) if true_categories else 0
        
        if precision + recall == 0:
            return 0.0
        return 2 * (precision * recall) / (precision + recall)
    except Exception:
        return 0.0
```

#### 길이 제약 검증
```python
def evaluate(output, expected, metadata):
    max_length = metadata.get("max_length", 500)
    return 1.0 if len(output) <= max_length else 0.0
```

#### 필수 키워드 포함률
```python
def evaluate(output, expected, metadata):
    required_keywords = metadata.get("required_keywords", [])
    if not required_keywords:
        return 1.0
    found = sum(1 for kw in required_keywords if kw in output)
    return found / len(required_keywords)
```

---

## 5. 평가 파이프라인 설계

### 5.1 실행 흐름

```
실험 아이템 1개 처리 완료 (output 수신)
    │
    ▼
EvaluationEngine.evaluate(output, expected, metadata, evaluators)
    │
    ├── [병렬] Built-in evaluators (즉시 실행, <10ms; cosine_similarity 제외 ~100-500ms)
    │       ├── exact_match → 1.0
    │       ├── json_validity → 1.0
    │       └── latency_check → 0.0
    │
    ├── [병렬] Custom evaluators (샌드박스 실행, <5s)
    │       └── category_f1 → 0.85
    │
    └── [순차/병렬] LLM-as-Judge (LLM 호출, ~1-3s)
            └── accuracy_judge → 0.8 (score 8/10)
    │
    ▼
모든 스코어 수집
    │
    ▼
Langfuse에 score 기록 (trace_id 기준)
    ├── langfuse.score(trace_id, "exact_match", 1.0)
    ├── langfuse.score(trace_id, "json_validity", 1.0)
    ├── langfuse.score(trace_id, "latency_check", 0.0)
    ├── langfuse.score(trace_id, "category_f1", 0.85)
    └── langfuse.score(trace_id, "accuracy_judge", 0.8)
```

### 5.2 에러 처리

| 상황 | 처리 |
|------|------|
| Built-in evaluator 예외 | score=null, 에러 로그, 실험 계속 |
| Custom evaluator 타임아웃 | score=null, "TIMEOUT" 기록, 실험 계속 |
| Custom evaluator 예외 | score=null, 예외 메시지 기록, 실험 계속 |
| LLM Judge 호출 실패 | 초기 시도 1회 + 재시도 최대 2회 = 총 최대 3회 호출. 3회 모두 실패 시 score=null |
| LLM Judge 응답 파싱 실패 | 초기 시도 1회 + 재시도 최대 2회 = 총 최대 3회 호출. 3회 모두 실패 시 score=null |
| 모든 evaluator 실패 | 아이템을 "평가 실패"로 표시, 실험은 계속 |

### 5.3 성능 고려사항

- Built-in evaluator: 동기 실행, 오버헤드 무시 가능
- Custom evaluator: 별도 프로세스/스레드에서 실행, 5초 타임아웃
- LLM Judge: LLM 호출이므로 가장 느림, 배치 실험 전체 시간의 주요 병목
- LLM Judge 병렬 실행: concurrency 설정으로 동시 호출 수 제한

---

### 5.4 평가 함수 가중치 & 가중 평균 스코어

#### 5.4.1 가중치 지정

- 각 evaluator는 `weight` 필드(0.0~1.0) 보유
- **기본값 규칙**:
  - 모든 evaluator에 weight 미지정 → 균등 분배 (1.0 / N)
  - 일부만 지정 → 지정되지 않은 evaluator의 weight는 `(1.0 - Σ지정값) / 미지정 개수`로 자동 계산
  - 모두 지정 → 합계가 `[1.0 - 1e-6, 1.0 + 1e-6]` 범위 내에 있어야 함
- 검증은 클라이언트(zod) + 서버(pydantic) 양쪽에서 수행
- 단일 evaluator는 weight 생략 가능, 내부적으로 1.0 할당
- 0.0 가중치는 참고용 스코어(결과 표시는 되나 종합 점수에 미반영)
- 정수(`1`)/float(`1.0`) 모두 허용, 내부 float 캐스팅

#### 5.4.2 가중 평균 계산

```
weighted_score = Σ (score_i × weight_i)  where score_i is not null
```

- null 스코어는 가중 평균 계산에서 제외, 제외된 evaluator의 weight는 재정규화
- 재정규화: `adjusted_weight_i = weight_i / Σ(weight_j for j where score_j is not null)`

#### 5.4.3 Langfuse 저장

- 각 개별 evaluator 스코어는 기존대로 `langfuse.score(trace_id, name, value)`로 저장
- 가중 평균은 별도 name으로 저장: `langfuse.score(trace_id, "weighted_score", weighted_value, comment="weights: exact_match=0.5, judge=0.5")`

#### 5.4.4 검증 규칙

- 가중치 합계 ≠ 1.0 → `422 VALIDATION_ERROR` with "evaluator weights must sum to 1.0"
- 개별 가중치 < 0 또는 > 1 → 동일 에러

---

## 6. 평가 결과 활용

### 6.1 Langfuse Score 데이터 모델

```
Langfuse에 기록되는 score:
{
    "trace_id": "실험 아이템의 trace ID",
    "name": "평가 함수 이름 (exact_match, accuracy_judge 등)",
    "value": 0.85,
    "data_type": "NUMERIC",
    "comment": "평가 근거 (LLM Judge의 reasoning 등)",
    "source": "API"  // Labs에서 기록했음을 표시
}
```

### 6.2 집계 쿼리에서의 활용

- `avg(score)`: 실험 Run의 평균 품질
- `score 분포`: 히스토그램으로 품질 분포 확인
- `score variance`: 실험 간 품질 차이가 큰 아이템 탐지
- `score vs cost`: 비용 대비 품질 효율 분석

### 6.3 의사결정 지원

```
실험 결과 → 의사결정 흐름:

1. avg_score 비교 → 최고 성능 프롬프트/모델 식별
2. score_range 분석 → 불안정한 조합 제외
3. cost_per_score 비교 → 비용 효율 최적 조합 선택
4. outlier 분석 → 실패 케이스 패턴 파악
5. 프롬프트 개선 → 실패 패턴 기반 프롬프트 수정
6. 재실험 → 개선 효과 검증
```
