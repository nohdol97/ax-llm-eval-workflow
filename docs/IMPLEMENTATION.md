# 구현 상세

## 1. Redis 스키마

### 1.1 개요

Labs Backend는 자체 RDBMS를 두지 않고, 실험 상태/진행률을 Redis에 저장한다.
완료된 실험의 최종 결과는 Langfuse trace metadata로 영속화하며, Redis 데이터는 TTL 기반으로 자동 만료된다.
분석/비교 쿼리(avg_score, latency percentile 등)는 ClickHouse에서 직접 수행한다.

### 1.2 키 네이밍 규칙

모든 키는 `ax:` 접두사를 사용하여 Langfuse 내장 Redis와 네임스페이스 충돌을 방지한다.

### 1.3 Experiment Hash

**키**: `ax:experiment:{experiment_id}`
**타입**: Hash
**TTL**: 활성(running/paused) 24시간, 완료(completed/failed/cancelled) 후 1시간

| 필드 | 타입 | 설명 | 기록 시점 |
|------|------|------|-----------|
| `name` | string | 실험 이름 | 생성 시 |
| `description` | string | 실험 설명 | 생성 시 |
| `status` | string | `running` \| `paused` \| `completed` \| `failed` \| `cancelled` | 생성 시, Lua script으로 전이 |
| `config` | string (JSON) | 실험 생성 요청 전체 JSON (prompt_configs, model_configs, evaluators, concurrency 등) | 생성 시 |
| `total_items` | int | 전체 평가 아이템 수 (dataset_items x prompts x models) | 생성 시 |
| `completed_items` | int | 완료된 아이템 수 | `HINCRBY` 로 아이템 완료마다 +1 |
| `failed_items` | int | 실패한 아이템 수 | `HINCRBY` 로 아이템 실패마다 +1 |
| `total_cost_usd` | float | 누적 비용 (USD) | `HINCRBYFLOAT` 로 아이템 완료마다 누적 |
| `created_at` | string (ISO 8601) | 실험 생성 시각 | 생성 시 |
| `updated_at` | string (ISO 8601) | 최종 상태 변경 시각 | 상태 변경마다 갱신 |
| `completed_at` | string (ISO 8601) | 실험 완료 시각 | 완료/실패/취소 시 |
| `started_by` | string | 실험 시작 사용자 ID (JWT sub claim) | 생성 시 |
| `total_runs` | int | 총 Run 수 (prompt_configs x model_configs) | 생성 시 |
| `project_id` | string | 프로젝트 ID | 생성 시 |
| `total_duration_sec` | float | 실험 총 소요 시간 (초) | 완료 시 계산 (completed_at - created_at) |
| `error_message` | string | 실험 레벨 에러 메시지 (전체 실패 시) | 실패 시 |

### 1.4 Run Hash

**키**: `ax:run:{experiment_id}:{run_name}`
**타입**: Hash
**TTL**: 소속 Experiment와 동일

| 필드 | 타입 | 설명 | 기록 시점 |
|------|------|------|-----------|
| `status` | string | `running` \| `completed` \| `failed` | 생성 시, 상태 변경 시 |
| `model` | string | 모델 ID (예: `gpt-4o`) | 생성 시 |
| `prompt_name` | string | Langfuse 프롬프트 이름 | 생성 시 |
| `prompt_version` | int | 프롬프트 버전 | 생성 시 |
| `completed_items` | int | Run 내 완료 아이템 수 | `HINCRBY` |
| `failed_items` | int | Run 내 실패 아이템 수 | `HINCRBY` |
| `total_items` | int | Run 내 전체 아이템 수 | 생성 시 |
| `total_cost_usd` | float | Run 누적 비용 | `HINCRBYFLOAT` |
| `total_latency_ms` | float | 지연 합계 (평균 계산용) | `HINCRBYFLOAT` |
| `total_score_sum` | float | 스코어 합계 (평균 계산용) | `HINCRBYFLOAT` |
| `scored_count` | int | 스코어가 기록된 아이템 수 (null score 제외) | `HINCRBY` |

**평균값 계산 전략**: `avg_score`와 `avg_latency_ms`는 Redis Hash 필드로 저장하지 않는다. API 응답 시 `total_score_sum / scored_count`, `total_latency_ms / completed_items`로 계산하여 반환한다. 이렇게 하면 concurrent HINCRBY/HINCRBYFLOAT 환경에서 정합성 문제가 발생하지 않는다.

### 1.5 보조 키

#### Run 이름 집합

**키**: `ax:experiment:{experiment_id}:runs`
**타입**: Set
**값**: Run 이름 문자열 (`sentiment-analysis_v3_gpt-4o_20260411` 등)
**용도**: 실험에 속한 모든 Run을 열거할 때 사용
**TTL**: 소속 Experiment와 동일

#### 실패 아이템 집합

**키**: `ax:run:{experiment_id}:{run_name}:failed_items`
**타입**: Set
**값**: 실패한 dataset item ID
**용도**: `retry-failed` 엔드포인트에서 재실행 대상 아이템 식별
**TTL**: 소속 Experiment와 동일

#### 프로젝트별 실험 인덱스

**키**: `ax:project:{project_id}:experiments`
**타입**: Sorted Set
**Score**: `created_at` 타임스탬프 (Unix epoch, float)
**Member**: `experiment_id`
**용도**: `GET /api/v1/experiments?project_id=xxx` 목록 조회에 사용. 최신순 정렬, 페이지네이션 지원 (`ZREVRANGEBYSCORE` + `LIMIT`)
**TTL**: 없음 (Lazy cleanup으로 관리)

### 1.6 상태 전이 (Lua Script)

상태 전이는 반드시 Lua script으로 원자적으로 수행하여 race condition을 방지한다.

```lua
-- transition_status.lua
-- KEYS[1] = ax:experiment:{id}
-- ARGV[1] = expected_current_status (쉼표 구분, 예: "running,paused")
-- ARGV[2] = new_status
-- ARGV[3] = current_timestamp (ISO 8601)
-- ARGV[4] = error_message (optional, 빈 문자열이면 무시)

local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end

-- 허용된 현재 상태인지 검증
local allowed = false
for s in string.gmatch(ARGV[1], '([^,]+)') do
    if current == s then
        allowed = true
        break
    end
end

if not allowed then
    return redis.error_reply('STATE_CONFLICT:' .. current)
end

redis.call('HSET', KEYS[1], 'status', ARGV[2])
redis.call('HSET', KEYS[1], 'updated_at', ARGV[3])

-- 종료 상태면 completed_at 기록 + TTL 1시간으로 단축
if ARGV[2] == 'completed' or ARGV[2] == 'failed' or ARGV[2] == 'cancelled' then
    redis.call('HSET', KEYS[1], 'completed_at', ARGV[3])
    if ARGV[4] ~= '' then
        redis.call('HSET', KEYS[1], 'error_message', ARGV[4])
    end
    -- TTL 1시간 (종료 후 정리)
    redis.call('EXPIRE', KEYS[1], 3600)
end

return ARGV[2]
```

**상태 전이 규칙**:

| 현재 상태 | 허용 전이 | API 엔드포인트 |
|-----------|-----------|----------------|
| `running` | `paused`, `completed`, `failed`, `cancelled` | pause, cancel, (내부 완료/실패) |
| `paused` | `running`, `cancelled` | resume, cancel |
| `completed` | `running` (retry-failed만) | retry-failed |
| `failed` | `running` (retry-failed만) | retry-failed |
| `cancelled` | (전이 불가) | 409 Conflict 반환 |

### 1.7 TTL 전략

| 상태 | TTL | 사유 |
|------|-----|------|
| `running` / `paused` | 24시간 (86400초) | 활성 실험은 충분한 시간 확보 |
| `completed` / `failed` / `cancelled` | 1시간 (3600초) | 완료 후 Langfuse에 영속화 완료. Redis는 최근 결과 캐시 역할만 |

**TTL 갱신**: 활성 상태에서는 상태 변경, 아이템 완료 등 주요 이벤트마다 TTL을 24시간으로 재설정한다.

**종료 시 TTL 단축**: Lua script에서 상태가 종료 상태로 전이되면 TTL을 1시간으로 단축한다. 관련된 모든 키(Run Hash, Run Set, Failed Items Set)도 동일하게 단축한다.

### 1.8 Lazy Cleanup

`ax:project:{project_id}:experiments` Sorted Set은 TTL이 없으므로, 실험 목록 조회 시 다음 로직을 적용한다:

1. `ZREVRANGEBYSCORE`로 페이지 분량만큼 experiment_id를 가져온다.
2. 각 experiment_id에 대해 `EXISTS ax:experiment:{id}`를 확인한다.
3. Hash가 존재하지 않으면 `ZREM ax:project:{project_id}:experiments {id}`로 Sorted Set에서 제거한다.
4. 제거된 만큼 추가로 조회하여 요청된 page_size를 채운다.

이렇게 하면 별도 cleanup 배치 작업 없이도 만료된 실험이 자연스럽게 정리된다.

### 1.9 API 응답 필드 매핑 검증

아래 표는 API_DESIGN.md의 각 응답 필드가 Redis 스키마의 어떤 데이터에서 서빙되는지 매핑한다.

#### POST /api/v1/experiments 응답

| API 필드 | Redis 소스 |
|----------|-----------|
| `experiment_id` | Hash 키에서 추출 |
| `status` | `ax:experiment:{id}` → `status` |
| `total_runs` | `ax:experiment:{id}` → `total_runs` |
| `total_items` | `ax:experiment:{id}` → `total_items` |
| `runs[].run_name` | `ax:experiment:{id}:runs` Set 멤버 |
| `runs[].prompt_version` | `ax:run:{id}:{name}` → `prompt_version` |
| `runs[].model` | `ax:run:{id}:{name}` → `model` |
| `runs[].status` | `ax:run:{id}:{name}` → `status` |

#### GET /api/v1/experiments/{id} 응답

| API 필드 | Redis 소스 |
|----------|-----------|
| `experiment_id` | Hash 키 |
| `name` | `ax:experiment:{id}` → `name` |
| `status` | `ax:experiment:{id}` → `status` |
| `progress.completed` | `ax:experiment:{id}` → `completed_items` |
| `progress.failed` | `ax:experiment:{id}` → `failed_items` |
| `progress.total` | `ax:experiment:{id}` → `total_items` |
| `runs[].run_name` | `ax:experiment:{id}:runs` Set |
| `runs[].status` | `ax:run:{id}:{name}` → `status` |
| `runs[].summary.avg_score` | `ax:run:{id}:{name}` → `total_score_sum / scored_count` |
| `created_at` | `ax:experiment:{id}` → `created_at` |
| `completed_at` | `ax:experiment:{id}` → `completed_at` |

#### GET /api/v1/experiments (목록) 응답

| API 필드 | Redis 소스 |
|----------|-----------|
| `experiments[].experiment_id` | `ax:project:{pid}:experiments` Sorted Set 멤버 |
| `experiments[].name` | `ax:experiment:{id}` → `name` |
| `experiments[].status` | `ax:experiment:{id}` → `status` |
| `experiments[].total_runs` | `ax:experiment:{id}` → `total_runs` |
| `experiments[].created_at` | `ax:experiment:{id}` → `created_at` |
| `total` | `ZCARD ax:project:{pid}:experiments` (lazy cleanup 후 보정) |
| `page` | 요청 파라미터 |

#### SSE 이벤트 (GET /api/v1/experiments/{id}/stream)

| SSE 이벤트 | Redis 소스 |
|------------|-----------|
| `progress.run_name` | Run Hash 키 |
| `progress.completed` | `ax:run:{id}:{name}` → `completed_items` |
| `progress.total` | `ax:run:{id}:{name}` → `total_items` |
| `run_complete.summary.avg_score` | `total_score_sum / scored_count` |
| `run_complete.summary.total_cost` | `ax:run:{id}:{name}` → `total_cost_usd` |
| `run_complete.summary.avg_latency` | `total_latency_ms / completed_items` |
| `experiment_complete.total_duration_sec` | `ax:experiment:{id}` → `total_duration_sec` |
| `experiment_complete.total_cost_usd` | `ax:experiment:{id}` → `total_cost_usd` |

**분석 API (비교, 분포, 아이템별 상세)**: Redis가 아닌 ClickHouse 직접 쿼리에서 서빙한다. 상세 쿼리 패턴은 LANGFUSE.md 섹션 3을 참조한다.

---

## 2. Docker 샌드박스 통신 프로토콜

### 2.1 개요

Custom Code Evaluator는 사용자가 작성한 임의의 Python 코드를 실행하므로, 보안을 위해 Docker 컨테이너에서 격리 실행한다. 컨테이너와의 통신은 `docker run -i` + stdin/stdout JSON 파이프를 사용한다.

### 2.2 컨테이너 이미지: ax-eval-sandbox

#### Dockerfile

```dockerfile
FROM python:3.12-slim

# 보안: non-root 사용자 (nobody)
# docker run --user=nobody와 일치하도록 Dockerfile에서도 nobody 사용

# 허용된 패키지만 설치 (EVALUATION.md 섹션 4.2 기준)
# json, re, math, collections, difflib, statistics, unicodedata는 표준 라이브러리이므로 별도 설치 불필요

# runner.py 복사
COPY runner.py /app/runner.py

# 파일 시스템 최소화
RUN chmod 444 /app/runner.py && \
    rm -rf /var/cache/apt /var/lib/apt/lists/* /tmp/*

USER nobody
WORKDIR /app

ENTRYPOINT ["python", "-u", "/app/runner.py"]
```

**설명**:
- `python:3.12-slim` 기반으로 최소한의 이미지 크기 유지
- 허용된 7개 패키지(json, re, math, collections, difflib, statistics, unicodedata)는 모두 Python 표준 라이브러리이므로 별도 pip install이 불필요하다
- `runner.py`는 읽기 전용(444)으로 설정하여 컨테이너 내부에서 변경 불가
- `python -u`로 stdout 버퍼링을 비활성화하여 JSON 라인이 즉시 전송되도록 보장

### 2.3 Docker 실행 제약

```bash
docker run \
  --rm \
  -i \
  --network=none \
  --memory=128m \
  --cpus=0.5 \
  --user=nobody \
  --read-only \
  --tmpfs /tmp:size=10m,noexec \
  --security-opt=no-new-privileges \
  --pids-limit=50 \
  ax-eval-sandbox
```

| 제약 | 설정 | 사유 |
|------|------|------|
| 네트워크 | `--network=none` | 외부 통신 완전 차단, 데이터 유출 방지 |
| 메모리 | `--memory=128m` | OOM 방지, 과도한 메모리 사용 차단 |
| CPU | `--cpus=0.5` | CPU 독점 방지 |
| 사용자 | `--user=nobody` | root 권한 차단 |
| 파일 시스템 | `--read-only` | 파일 시스템 변경 방지 |
| 임시 디렉토리 | `--tmpfs /tmp:size=10m,noexec` | 제한된 임시 파일만 허용, 실행 파일 생성 불가 |
| 권한 상승 | `--security-opt=no-new-privileges` | setuid/setgid 차단 |
| 프로세스 수 | `--pids-limit=50` | fork bomb 방지 |

### 2.4 통신 프로토콜: Line-Delimited JSON

Backend와 컨테이너 간 통신은 stdin/stdout을 통한 line-delimited JSON(NDJSON)으로 수행한다.

#### 요청 메시지 (Backend → Container, stdin)

```json
{"id": "item_001", "code": "def evaluate(output, expected, metadata):\n    return 1.0 if output == expected else 0.0", "output": "positive", "expected": "positive", "metadata": {"difficulty": "easy"}}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `id` | string | O | 데이터셋 아이템 ID (결과 매칭용) |
| `code` | string | O | 평가 함수 Python 코드 (evaluate 함수 정의 포함) |
| `output` | string | O | LLM이 생성한 출력 텍스트 |
| `expected` | string | O | 기대 출력 (없으면 빈 문자열) |
| `metadata` | object | O | 데이터셋 아이템 메타데이터 |

#### 응답 메시지 (Container → Backend, stdout)

**성공**:
```json
{"id": "item_001", "status": "success", "score": 1.0}
```

**에러**:
```json
{"id": "item_002", "status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "NameError: name 'undefined_var' is not defined"}
```

**타임아웃**:
```json
{"id": "item_003", "status": "error", "error_code": "EVALUATOR_TIMEOUT", "error_message": "Execution exceeded 5s timeout"}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `id` | string | O | 요청의 id와 동일 (매칭용) |
| `status` | string | O | `success` \| `error` |
| `score` | float | 성공 시 | 0.0~1.0 사이 값 (범위 밖이면 runner.py에서 클램핑) |
| `error_code` | string | 에러 시 | `EVALUATOR_ERROR` \| `EVALUATOR_TIMEOUT` \| `EVALUATOR_IMPORT` |
| `error_message` | string | 에러 시 | 에러 상세 메시지 (stderr에서 캡처) |

#### 종료 신호

Backend가 모든 아이템을 전송하면 stdin을 닫는다(EOF). runner.py는 EOF를 감지하면 정상 종료(exit 0)한다.

### 2.5 runner.py 프로토콜 명세

```python
#!/usr/bin/env python3
"""
ax-eval-sandbox runner.py
Line-delimited JSON을 stdin으로 받아 evaluate() 함수를 실행하고 결과를 stdout으로 출력한다.
각 아이템은 독립된 네임스페이스에서 실행되어 상태가 격리된다.
"""
import sys
import json
import signal
import traceback

# 허용된 모듈 (Python 표준 라이브러리)
ALLOWED_MODULES = frozenset([
    'json', 're', 'math', 'collections', 'difflib', 'statistics', 'unicodedata'
])

TIMEOUT_SECONDS = 5


class EvalTimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise EvalTimeoutError(f"Execution exceeded {TIMEOUT_SECONDS}s timeout")


def execute_item(item: dict) -> dict:
    item_id = item['id']

    # 1. 독립 네임스페이스 생성 (아이템 간 상태 격리)
    # 보안: 위험한 내장 함수를 제거한 제한된 builtins 제공
    safe_builtins = {k: v for k, v in __builtins__.__dict__.items()
                     if k not in ('__import__', 'exec', 'eval', 'open', 'compile',
                                  'globals', 'locals', 'getattr', 'setattr', 'delattr',
                                  'type', 'breakpoint', 'exit', 'quit')}

    # 2. 허용된 모듈만 import 가능한 커스텀 __import__ 함수
    def restricted_import(name, *args, **kwargs):
        if name not in ALLOWED_MODULES:
            raise ImportError(f"Module '{name}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_MODULES))}")
        return __builtins__.__dict__['__import__'](name, *args, **kwargs)

    safe_builtins['__import__'] = restricted_import
    namespace = {'__builtins__': safe_builtins}

    code = item['code']

    # 3. 사용자 코드 exec (evaluate 함수 정의)
    try:
        exec(code, namespace)
    except Exception as e:
        return {
            'id': item_id,
            'status': 'error',
            'error_code': 'EVALUATOR_ERROR',
            'error_message': f"Code compilation failed: {str(e)}"
        }

    if 'evaluate' not in namespace:
        return {
            'id': item_id,
            'status': 'error',
            'error_code': 'EVALUATOR_ERROR',
            'error_message': "Function 'evaluate' not defined in code"
        }

    # 4. 타임아웃 설정 + evaluate() 실행
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)

    try:
        score = namespace['evaluate'](
            item['output'],
            item['expected'],
            item['metadata']
        )
    except EvalTimeoutError:
        return {
            'id': item_id,
            'status': 'error',
            'error_code': 'EVALUATOR_TIMEOUT',
            'error_message': f"Execution exceeded {TIMEOUT_SECONDS}s timeout"
        }
    except ImportError as e:
        return {
            'id': item_id,
            'status': 'error',
            'error_code': 'EVALUATOR_IMPORT',
            'error_message': str(e)
        }
    except Exception as e:
        return {
            'id': item_id,
            'status': 'error',
            'error_code': 'EVALUATOR_ERROR',
            'error_message': f"{type(e).__name__}: {str(e)}"
        }
    finally:
        signal.alarm(0)  # 타이머 해제

    # 5. 스코어 검증 및 클램핑
    try:
        score = float(score)
    except (TypeError, ValueError):
        return {
            'id': item_id,
            'status': 'error',
            'error_code': 'EVALUATOR_ERROR',
            'error_message': f"evaluate() returned non-numeric value: {repr(score)}"
        }

    score = max(0.0, min(1.0, score))  # 0.0~1.0 클램핑

    return {
        'id': item_id,
        'status': 'success',
        'score': score
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            # 파싱 불가능한 입력은 에러 응답 (id 알 수 없으므로 "unknown")
            result = {
                'id': 'unknown',
                'status': 'error',
                'error_code': 'EVALUATOR_ERROR',
                'error_message': f"Invalid JSON input: {str(e)}"
            }
            print(json.dumps(result), flush=True)
            continue

        result = execute_item(item)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
```

### 2.6 컨테이너 라이프사이클

| 이벤트 | 동작 |
|--------|------|
| 실험 시작 (POST /experiments) | custom_code 타입 evaluator가 있으면 컨테이너 1개 생성, stdin 파이프 확보 |
| 아이템 평가 | stdin으로 JSON 라인 전송 → stdout에서 결과 JSON 라인 수신 |
| 실험 완료 | stdin 닫기(EOF) → 컨테이너 정상 종료 → `--rm`으로 자동 삭제 |
| 실험 취소 (cancel) | `docker kill` → 컨테이너 강제 종료 및 삭제 |
| 실험 일시정지 (pause) | 컨테이너 유지, stdin 전송 중단 |
| 실험 재개 (resume) | stdin 전송 재개 |

**아이템별 격리**: 컨테이너는 실험 단위로 1개만 생성하지만, runner.py 내부에서 각 아이템마다 `exec()`에 독립 네임스페이스(`namespace = {}`)를 사용하므로 아이템 간 상태가 격리된다.

### 2.7 Backend 연동 패턴

```
Backend (Python asyncio)
    │
    ├── asyncio.create_subprocess_exec(
    │       'docker', 'run', '--rm', '-i', '--network=none', ...
    │       stdin=PIPE, stdout=PIPE, stderr=PIPE
    │   )
    │
    ├── 아이템 처리 루프:
    │   │
    │   ├── process.stdin.write(json_line + '\n')
    │   │
    │   ├── line = await process.stdout.readline()
    │   │   └── JSON 파싱 → 결과 처리
    │   │
    │   └── 5초 내 응답 없음 → EVALUATOR_TIMEOUT 기록, 다음 아이템 진행
    │
    ├── 모든 아이템 완료:
    │   └── process.stdin.close() → EOF → 컨테이너 종료
    │
    └── 실험 취소:
        └── process.kill() → 컨테이너 강제 종료
```

**에러 복구**: 컨테이너가 예기치 않게 종료되면(OOM 등), 남은 아이템은 `EVALUATOR_ERROR`로 기록하고 실험은 계속 진행한다. custom evaluator 스코어만 null이 되고, 다른 evaluator(built-in, LLM judge)의 스코어는 정상 기록된다.

---

## 3. 멀티 프로젝트 API Key 관리

### 3.1 설계 결정: Static Config 방식

Labs는 SaaS가 아닌 사내 인프라 도구이므로, 프로젝트를 사용자가 동적으로 생성하지 않는다. 프로젝트 추가/삭제는 관리자가 수행하며, 설정 변경 후 재시작(또는 Secret Manager 기반 hot-reload)으로 적용한다.

### 3.2 프로젝트 설정 구조

환경변수 또는 Secret Manager에 JSON 형태로 저장한다:

```json
{
  "projects": [
    {
      "id": "proj_sentiment",
      "name": "감성 분석 서비스",
      "langfuse_public_key": "pk-lf-...",
      "langfuse_secret_key": "sk-lf-...",
      "langfuse_host": "https://langfuse.internal.company.com"
    },
    {
      "id": "proj_summarize",
      "name": "문서 요약 서비스",
      "langfuse_public_key": "pk-lf-...",
      "langfuse_secret_key": "sk-lf-...",
      "langfuse_host": "https://langfuse.internal.company.com"
    }
  ]
}
```

### 3.3 Backend 초기화 흐름

```
서버 시작
    │
    ├── 환경변수 PROJECTS_CONFIG 로드 (JSON 문자열)
    │   또는 Secret Manager에서 조회
    │
    ├── 프로젝트별 Langfuse 클라이언트 인스턴스 생성
    │   project_clients = {
    │       "proj_sentiment": Langfuse(pk=..., sk=..., host=...),
    │       "proj_summarize": Langfuse(pk=..., sk=..., host=...),
    │   }
    │
    └── 메모리에 캐싱 (서버 수명 동안 유지)
```

### 3.4 요청별 프로젝트 전환

모든 API 요청에 `project_id`를 파라미터 또는 쿼리로 포함한다.

```python
# 의사 코드
def get_langfuse_client(project_id: str) -> Langfuse:
    client = project_clients.get(project_id)
    if client is None:
        raise HTTPException(404, detail="PROJECT_NOT_FOUND")
    return client
```

API 핸들러에서:
```python
@router.get("/api/v1/prompts")
async def list_prompts(project_id: str):
    langfuse = get_langfuse_client(project_id)
    prompts = langfuse.get_prompts()
    return {"prompts": prompts}
```

### 3.5 프론트엔드 프로젝트 선택

1. 앱 로드 시 `GET /api/v1/projects` 호출 → 프로젝트 목록 수신
2. 헤더 영역에 프로젝트 드롭다운 표시
3. 사용자가 프로젝트 선택 → `project_id`를 React Context/상태에 저장
4. 이후 모든 API 호출에 `project_id` 포함

### 3.6 프로젝트 추가 절차

1. Secret Manager (또는 환경변수)에 새 프로젝트 설정 추가
2. Backend 재시작 (또는 hot-reload 엔드포인트 호출)
3. Langfuse에서 해당 프로젝트의 API Key 생성 필요

**Hot-reload 옵션** (운영 환경):
- Secret Manager 사용 시 주기적으로 설정을 re-fetch하여 새 프로젝트 감지
- 또는 admin 전용 `POST /api/v1/admin/reload-config` 엔드포인트 제공
- reload 시 기존 클라이언트 인스턴스는 유지, 새로운/변경된 것만 업데이트

### 3.7 LiteLLM Proxy 호출 시 인증

Backend에서 LiteLLM Proxy 호출 시 `LITELLM_MASTER_KEY`를 `Authorization: Bearer {key}` 헤더로 전달한다. litellm Python SDK 사용 시:

```python
import os
import litellm

litellm.api_key = os.environ['LITELLM_MASTER_KEY']
litellm.api_base = os.environ['LITELLM_BASE_URL']
```

---

## 4. Auth 연동 상세

### 4.1 설계 결정

Labs는 JWT를 발급하지 않는다. 사내 Auth 서비스에서 발급된 JWT를 수신하여 서명 검증만 수행한다.

### 4.2 Backend JWT 검증

#### 환경변수

| 변수 | 필수 | 설명 | 예시 |
|------|------|------|------|
| `AUTH_JWKS_URL` | O | JWKS 엔드포인트 URL | `https://auth.company.com/.well-known/jwks.json` |
| `AUTH_JWT_AUDIENCE` | O | JWT aud 클레임 검증 값 | `ax-llm-eval-workflow` |
| `AUTH_JWT_ISSUER` | O | JWT iss 클레임 검증 값 | `https://auth.company.com` |
| `JWT_ALGORITHM` | X | 서명 알고리즘 (기본: RS256) | `RS256` |
| `JWT_CLAIM_USER_ID` | X | 사용자 ID 클레임 경로 (기본: `sub`) | `sub` |
| `JWT_CLAIM_ROLE` | X | 역할 클레임 경로 (기본: `role`) | `role` |
| `JWT_CLAIM_GROUPS` | X | 그룹 클레임 경로 (기본: `groups`) | `groups` |

#### 검증 흐름

```
요청 수신
    │
    ├── Authorization 헤더 추출
    │   └── 없으면 → 401 AUTH_REQUIRED
    │
    ├── "Bearer " 접두사 제거 → JWT 토큰
    │
    ├── JWKS에서 공개키 조회 (캐싱, 5분 TTL)
    │   └── JWT kid 헤더로 키 매칭
    │   └── 키 없으면 JWKS 강제 갱신 1회 시도
    │
    ├── JWT 서명 검증 + 클레임 검증
    │   ├── exp: 만료 시간 확인
    │   ├── iss: AUTH_JWT_ISSUER와 일치 확인
    │   ├── aud: AUTH_JWT_AUDIENCE 포함 확인
    │   └── 실패 시 → 401 AUTH_REQUIRED
    │
    ├── 클레임 추출 (설정 가능한 경로)
    │   ├── user_id = token[JWT_CLAIM_USER_ID]
    │   ├── role = token[JWT_CLAIM_ROLE]
    │   └── groups = token[JWT_CLAIM_GROUPS]
    │
    └── CurrentUser 객체 생성 → 요청 컨텍스트에 주입
```

### 4.3 RBAC 권한 매핑

| 역할 | 권한 | API 접근 범위 |
|------|------|--------------|
| `admin` | 전체 접근 | 모든 API + Custom Code Evaluator 실행 + 프롬프트 라벨 승격 + 실험/데이터셋 삭제 |
| `user` | 일반 사용 | 실험 생성/실행, 데이터셋 업로드, 프롬프트 생성/수정, 결과 조회/비교 |
| `viewer` | 읽기 전용 | 프롬프트/데이터셋/실험 결과 조회, 비교 분석 조회 |

**권한 검증 포인트**:

| API 엔드포인트 | 최소 권한 |
|----------------|-----------|
| `GET /api/v1/prompts` | viewer |
| `POST /api/v1/prompts` | user |
| `PATCH /api/v1/prompts/{name}/versions/{v}/labels` | admin |
| `POST /api/v1/tests/single` | user |
| `POST /api/v1/experiments` | user |
| `POST /api/v1/experiments/{id}/pause\|resume\|cancel` | user (본인 실험만) |
| `POST /api/v1/experiments/{id}/retry-failed` | user (본인 실험만) |
| `DELETE /api/v1/experiments/{id}` | admin |
| `POST /api/v1/datasets/upload` | user |
| `DELETE /api/v1/datasets/{name}` | admin |
| `GET /api/v1/analysis/*` | viewer |
| `POST /api/v1/evaluators/validate` | admin |
| `GET /api/v1/projects` | viewer |

**본인 실험 제한**: user 역할의 실험 제어(pause/resume/cancel/retry)는 `ax:experiment:{id}` → `started_by`와 JWT의 user_id가 일치할 때만 허용한다. admin은 모든 실험을 제어할 수 있다.

### 4.4 Frontend 인증 흐름

```
앱 로드
    │
    ├── 메모리에서 JWT 확인
    │   ├── 존재 + 유효 → API 호출 시 Authorization: Bearer {token} 헤더 포함
    │   └── 없음 → AUTH_LOGIN_URL로 리다이렉트
    │
    ├── Auth 서비스 로그인 완료
    │   └── callback URL로 JWT 수신 (URL fragment 또는 response body)
    │   └── JWT를 메모리 변수에 저장 (localStorage 사용 금지 — XSS 방어)
    │
    ├── API 호출 시
    │   ├── 200 → 정상 처리
    │   └── 401 → JWT 만료/무효 → AUTH_LOGIN_URL로 리다이렉트
    │
    └── 탭/브라우저 닫기
        └── 메모리 JWT 소멸 → 다시 로그인 필요
```

**보안 고려사항**:
- JWT는 메모리에만 저장한다. localStorage/sessionStorage는 XSS 공격에 취약하므로 사용하지 않는다.
- 새로고침 시 JWT가 소멸되므로 Auth 서비스의 세션이 유효하면 silent re-authentication (iframe/popup 방식)으로 JWT를 재취득한다. 이 동작은 Auth 서비스의 구현에 의존한다.
- Labs에서는 refresh token을 처리하지 않는다. 토큰 갱신 책임은 Auth 서비스에 있다.
- CORS 설정에서 `credentials: true`를 설정하여 쿠키 기반 세션이 필요한 경우에도 대응한다.

---

## 5. 환경변수 통합 목록

### 5.1 Backend (FastAPI)

| 변수명 | 필수 | 설명 | 예시 | 비고 |
|--------|------|------|------|------|
| `PROJECTS_CONFIG` | O | 프로젝트별 Langfuse API Key 설정 (JSON) | `'{"projects":[{"id":"proj_1","name":"서비스A","langfuse_public_key":"pk-lf-...","langfuse_secret_key":"sk-lf-...","langfuse_host":"https://langfuse.internal"}]}'` | Secret Manager 사용 시 대체 가능 |
| `REDIS_URL` | O | Labs 상태 저장용 Redis 연결 URL | `redis://:password@redis:6379/1` | |
| `LITELLM_BASE_URL` | O | LiteLLM Proxy 내부 URL | `http://litellm:4000` | |
| `LITELLM_MASTER_KEY` | O | LiteLLM Proxy Master Key | `sk-litellm-master-...` | 최소 32자 |
| `CLICKHOUSE_HOST` | O | ClickHouse 서버 호스트 | `clickhouse` | |
| `CLICKHOUSE_PORT` | X | ClickHouse HTTP 포트 (기본: 8123) | `8123` | |
| `CLICKHOUSE_DB` | X | ClickHouse 데이터베이스 (기본: langfuse) | `langfuse` | |
| `CLICKHOUSE_READONLY_USER` | O | ClickHouse 읽기 전용 계정 (Labs 분석 쿼리 전용) | `labs_readonly` | |
| `CLICKHOUSE_READONLY_PASSWORD` | O | ClickHouse 읽기 전용 비밀번호 | `readonly_password` | |
| `AUTH_JWKS_URL` | O | JWKS 엔드포인트 URL | `https://auth.company.com/.well-known/jwks.json` | |
| `AUTH_JWT_AUDIENCE` | O | JWT aud 클레임 검증 값 | `ax-llm-eval-workflow` | |
| `AUTH_JWT_ISSUER` | O | JWT iss 클레임 검증 값 | `https://auth.company.com` | |
| `JWT_ALGORITHM` | X | JWT 서명 알고리즘 (기본: RS256) | `RS256` | |
| `JWT_CLAIM_USER_ID` | X | 사용자 ID 클레임 경로 (기본: sub) | `sub` | |
| `JWT_CLAIM_ROLE` | X | 역할 클레임 경로 (기본: role) | `role` | |
| `JWT_CLAIM_GROUPS` | X | 그룹 클레임 경로 (기본: groups) | `groups` | |
| `CORS_ALLOWED_ORIGINS` | O | CORS 허용 오리진 (쉼표 구분) | `http://localhost:3000,https://labs.company.com` | 와일드카드 `*` 금지 |
| `DOCKER_SOCKET` | X | Docker 소켓 경로 (기본: /var/run/docker.sock) | `/var/run/docker.sock` | Custom Evaluator용 |
| `EVAL_SANDBOX_IMAGE` | X | 샌드박스 Docker 이미지 (기본: ax-eval-sandbox) | `ax-eval-sandbox:1.0.0` | |
| `EVAL_SANDBOX_TIMEOUT_SEC` | X | 샌드박스 아이템 타임아웃 (기본: 5) | `5` | |
| `EVAL_SANDBOX_MEMORY_LIMIT` | X | 샌드박스 메모리 제한 (기본: 128m) | `128m` | |
| `LOG_LEVEL` | X | 로그 레벨 (기본: INFO) | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `SECRET_MANAGER_PROVIDER` | X | Secret Manager 제공자 (gcp/aws/none) | `gcp` | none이면 환경변수만 사용 |
| `SECRET_MANAGER_PROJECT` | X | GCP Secret Manager 프로젝트 ID | `my-gcp-project` | SECRET_MANAGER_PROVIDER=gcp 시 필수 |

### 5.2 Frontend (Next.js)

| 변수명 | 필수 | 설명 | 예시 | 비고 |
|--------|------|------|------|------|
| `NEXT_PUBLIC_API_BASE_URL` | O | Backend API 기본 URL | `http://localhost:8000/api/v1` | 브라우저에서 접근 가능 |
| `NEXT_PUBLIC_AUTH_LOGIN_URL` | O | Auth 서비스 로그인 URL | `https://auth.company.com/login?redirect_uri=...` | |
| `NEXT_PUBLIC_AUTH_CALLBACK_URL` | O | Auth callback URL (Labs 프론트) | `https://labs.company.com/auth/callback` | |
| `NEXT_PUBLIC_APP_TITLE` | X | 앱 타이틀 (기본: GenAI Labs) | `GenAI Labs` | |

### 5.3 LiteLLM Proxy

| 변수명 | 필수 | 설명 | 예시 | 비고 |
|--------|------|------|------|------|
| `LITELLM_MASTER_KEY` | O | Proxy Master Key | `sk-litellm-master-...` | 최소 32자, 90일 로테이션 |
| `DATABASE_URL` | X | LiteLLM 내부 DB (설정 저장) | `postgresql://...` | Proxy 모드에서 사용 |
| `AZURE_API_KEY` | X | Azure OpenAI API Key | `abc123...` | 모델 설정에 따라 |
| `AZURE_API_BASE` | X | Azure OpenAI 엔드포인트 | `https://myorg.openai.azure.com` | |
| `AZURE_API_VERSION` | X | Azure API 버전 | `2024-06-01` | |
| `OPENAI_API_KEY` | X | OpenAI API Key | `sk-...` | |
| `GEMINI_API_KEY` | X | Google Gemini API Key | `AIza...` | Vertex AI 사용 시 서비스 계정 대체 |
| `ANTHROPIC_API_KEY` | X | Anthropic API Key | `sk-ant-...` | |
| `AWS_ACCESS_KEY_ID` | X | AWS Bedrock 접근 키 | `AKIA...` | |
| `AWS_SECRET_ACCESS_KEY` | X | AWS Bedrock 시크릿 키 | `wJal...` | |
| `AWS_REGION_NAME` | X | AWS 리전 | `us-east-1` | |

**참고**: LLM Provider API 키는 LiteLLM Proxy에서만 관리한다. Backend 코드에서 이 키에 직접 접근하지 않는다.

### 5.4 Docker Compose

| 변수명 | 필수 | 설명 | 예시 | 비고 |
|--------|------|------|------|------|
| `POSTGRES_USER` | O | PostgreSQL 사용자 (Langfuse용) | `langfuse` | |
| `POSTGRES_PASSWORD` | O | PostgreSQL 비밀번호 | `langfuse_password` | |
| `POSTGRES_DB` | X | PostgreSQL 데이터베이스 (기본: langfuse) | `langfuse` | |
| `CLICKHOUSE_PASSWORD` | O | ClickHouse 관리자 비밀번호 | `clickhouse_admin_pw` | |
| `LANGFUSE_SALT` | O | Langfuse 시크릿 키 솔트 | `random_salt_string` | |
| `LANGFUSE_NEXTAUTH_SECRET` | O | Langfuse NextAuth 시크릿 | `random_auth_secret` | |
| `NEXTAUTH_URL` | O | Langfuse Web UI URL (브라우저 접근용) | `http://localhost:${LANGFUSE_PORT:-3001}` | docker-compose.yml에 하드코딩됨 |
| `LANGFUSE_TELEMETRY_ENABLED` | X | Langfuse 텔레메트리 (기본: true) | `false` | 사내 배포 시 false 권장 |

### 5.5 환경변수 로딩 우선순위

```
1. 시스템 환경변수 (최우선)
2. Secret Manager (SECRET_MANAGER_PROVIDER 설정 시)
3. .env.production 또는 .env.development (로컬 개발)
4. 코드 내 기본값 (최하위)
```

### 5.6 환경별 설정 분리

| 항목 | 개발 (.env.development) | 운영 (.env.production) |
|------|------------------------|----------------------|
| `REDIS_URL` | `redis://:password@redis:6379/1` | Secret Manager |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | `https://labs.company.com` |
| `LOG_LEVEL` | `DEBUG` | `INFO` |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000/api/v1` | `https://api.labs.company.com/api/v1` |
| LLM Provider Keys | `.env` 파일 | Secret Manager |
| `SECRET_MANAGER_PROVIDER` | `none` | `gcp` 또는 `aws` |

**`.env` 파일은 반드시 `.gitignore`에 포함되어야 한다. 시크릿이 저장소에 커밋되면 안 된다.**
