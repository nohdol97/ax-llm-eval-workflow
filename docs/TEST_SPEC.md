# 테스트 명세 (PART 1: Phase 0-3)

ax-llm-eval-workflow 프로젝트의 테스트 전략, 인프라, 그리고 Phase 1~3에 해당하는 상세 테스트 케이스를 정의한다.

---

## Phase 0: 테스트 인프라

### 0.1 Backend 테스트 구조 (pytest)

```
backend/
├── tests/
│   ├── conftest.py                 # 글로벌 fixture, 테스트 설정
│   ├── fixtures/                   # 공용 fixture 모듈
│   │   ├── __init__.py
│   │   ├── auth.py                 # JWT 생성/검증 fixture
│   │   ├── langfuse.py             # Mock Langfuse Client fixture
│   │   ├── redis.py                # Mock/실제 Redis fixture
│   │   ├── litellm.py              # Mock LiteLLM fixture
│   │   └── clickhouse.py           # Mock ClickHouse fixture
│   ├── utils/                      # 테스트 유틸리티
│   │   ├── __init__.py
│   │   ├── factories.py            # 테스트 데이터 팩토리
│   │   └── assertions.py           # 커스텀 assertion 헬퍼
│   ├── unit/                       # 단위 테스트
│   │   ├── test_security.py        # JWT 미들웨어
│   │   ├── test_langfuse_client.py # Langfuse Client Wrapper
│   │   ├── test_redis_client.py    # Redis Client Wrapper
│   │   └── test_config.py          # Config 모듈
│   ├── integration/                # 통합 테스트
│   │   ├── test_prompts_api.py     # 프롬프트 API
│   │   ├── test_datasets_api.py    # 데이터셋 API
│   │   ├── test_models_api.py      # 모델 API
│   │   ├── test_projects_api.py    # 프로젝트 API
│   │   ├── test_search_api.py      # 검색 API
│   │   └── test_health_api.py      # 헬스체크 API
│   └── infra/                      # 인프라 연결 테스트
│       ├── test_redis_connection.py
│       ├── test_clickhouse_connection.py
│       ├── test_langfuse_connection.py
│       ├── test_litellm_connection.py
│       └── test_postgres_connection.py
```

#### conftest.py 핵심 fixture

```python
# -- 앱 인스턴스 --
@pytest.fixture
def app() -> FastAPI:
    """테스트용 FastAPI 앱 인스턴스 (의존성 오버라이드 적용)."""

@pytest.fixture
def client(app) -> TestClient:
    """FastAPI TestClient."""

@pytest.fixture
def async_client(app) -> AsyncClient:
    """httpx AsyncClient (비동기 테스트용)."""

# -- 인증 --
@pytest.fixture
def jwt_admin() -> str:
    """admin 역할의 유효한 JWT 토큰."""

@pytest.fixture
def jwt_user() -> str:
    """user 역할의 유효한 JWT 토큰."""

@pytest.fixture
def jwt_viewer() -> str:
    """viewer 역할의 유효한 JWT 토큰."""

@pytest.fixture
def jwt_expired() -> str:
    """만료된 JWT 토큰."""

@pytest.fixture
def auth_headers_admin(jwt_admin) -> dict:
    """admin Authorization 헤더."""
    return {"Authorization": f"Bearer {jwt_admin}"}

# -- 서비스 Mock --
@pytest.fixture
def mock_langfuse() -> MockLangfuseClient:
    """Langfuse Client mock 객체."""

@pytest.fixture
def mock_redis() -> MockRedisClient:
    """Redis Client mock 객체."""

@pytest.fixture
def mock_litellm() -> MockLiteLLMProxy:
    """LiteLLM Proxy mock 객체."""

@pytest.fixture
def mock_clickhouse() -> MockClickHouseClient:
    """ClickHouse Client mock 객체."""
```

**Fixture scope 정책**:
- `app`, `client`: `function` (테스트 간 독립성 보장)
- `mock_*`: `function` (매 테스트마다 초기화)
- `jwt_*`, `auth_headers_*`: `session` (변경 불필요)
- `real_*` (infra): `session` (연결 재사용)

**Fixture 격리 원칙 (테스트 간 오염 방지)**:
- 모든 mock fixture는 `function` scope로 강제하여 상태 누출 금지. `session`/`module` scope mock 사용 금지.
- `app` fixture는 매 테스트마다 새 FastAPI 인스턴스를 생성하고 `dependency_overrides`를 깨끗한 dict로 초기화한다. 테스트 종료 시 `app.dependency_overrides.clear()`를 자동 호출 (`yield` 후 cleanup).
- 전역 싱글톤(예: `langfuse_client`, `redis_client` 모듈 변수)은 fixture에서 `monkeypatch.setattr`로 교체하여 테스트 간 누출 차단.
- 환경변수 변경은 `monkeypatch.setenv` 사용 필수 (직접 `os.environ` 수정 금지).
- `tmp_path`/`tmp_path_factory`만 파일 I/O 테스트에 허용. 절대경로 하드코딩 금지.
- 시간 의존 테스트는 `freezegun.freeze_time` 또는 `time-machine` 사용.
- 무작위성 의존 테스트는 `random.seed(0)`/`numpy.random.seed(0)` fixture로 고정.
- 병렬 실행(`pytest -n auto`) 시 안전성 보장: 공유 자원(파일, 포트, Redis DB 번호)은 `worker_id`(`pytest-xdist`)로 네임스페이스 분리.

**실제 인프라 cleanup 전략 (`tests/infra/`)**:
- **Redis** (`real_redis`): 전용 DB 번호 사용 (CI: `LABS_REDIS_DB=15`, 로컬: `15`). `function` scope `redis_clean` fixture가 매 테스트 직전 `FLUSHDB` 실행. 다른 테스트가 사용하는 prod/dev DB(0~14) 접근 금지. 키 prefix는 `test:{worker_id}:{test_id}:`로 강제.
- **PostgreSQL** (`real_postgres`): 트랜잭션 롤백 패턴 사용 — `function` scope `pg_session` fixture가 `BEGIN` → 테스트 → `ROLLBACK`으로 격리. 스키마 변경 테스트는 별도 `pg_schema` fixture에서 `CREATE SCHEMA test_{uuid}` → 테스트 → `DROP SCHEMA ... CASCADE`.
- **ClickHouse** (`real_clickhouse`): Labs는 읽기 전용이므로 cleanup 불필요. 단, seed 데이터는 `session` scope에서 별도 테스트 DB(`labs_test`)에 1회 적재 후 세션 종료 시 `DROP DATABASE labs_test SYNC`. 테스트는 절대 prod DB(`langfuse`) 접근 금지 (환경변수로 enforce).
- **Langfuse** (`real_langfuse`): 테스트 전용 프로젝트(`labs-test`) 사용. `function` fixture가 생성한 trace/dataset은 테스트 종료 시 `tags=["test", worker_id]`로 마킹 후 nightly cleanup 잡이 24시간 경과분 삭제.
- 모든 cleanup 실패는 `pytest.fail`이 아닌 `warnings.warn`으로 처리하여 후속 테스트 실행을 막지 않는다 (단, CI는 `-W error::ResourceWarning`으로 게이트).

**스냅샷 테스트 정책 (Frontend `vitest` + `toMatchSnapshot`, Backend `syrupy`)**:
- 스냅샷 대상: 직렬화 가능한 안정 출력만 (UI 렌더 결과, JSON 응답 schema, 에러 응답 구조). 시간/UUID/난수 포함 출력은 금지 또는 `serializer`로 정규화.
- 스냅샷 갱신은 **로컬에서만** `vitest -u` / `pytest --snapshot-update` 허용. CI에서는 `--ci`/`--snapshot-warn-unused` 플래그로 갱신 금지.
- 갱신 시 PR 설명에 **갱신 이유**(의도된 변경 vs 버그)와 diff 스크린샷 첨부 필수. 리뷰어는 스냅샷 diff를 라인 단위로 확인.
- 스냅샷 파일 크기 상한: 단일 스냅샷 200줄, 디렉토리 합계 5000줄. 초과 시 해당 테스트는 명시적 assertion으로 분해.
- Stale 스냅샷(사용되지 않음)은 CI가 실패시킨다 (`--snapshot-warn-unused` → `--ci` 모드에서 error).
- 보안 민감 데이터(JWT, API key, PII)는 스냅샷에 포함 금지 — pre-commit hook이 정규식으로 차단.

**Flaky 테스트 정책**:
- **정의**: 동일 커밋/환경에서 3회 실행 중 1회 이상 결과가 달라지는 테스트.
- **격리 절차**: flaky가 감지되면 즉시 `@pytest.mark.flaky` 마킹 + GitHub Issue 자동 생성 (`flaky-test` 라벨). 7일 내 미해결 시 `@pytest.mark.skip(reason="flaky-{issue}")` 처리하고 담당자 지정.
- **자동 재시도 금지**: `pytest-rerunfailures`로 무조건 재시도하는 것은 **원칙적으로 금지** (근본 원인 은폐). 단, 외부 네트워크 의존(`@pytest.mark.infra`) 테스트에 한해 최대 2회 재시도(`--reruns 2 --only-rerun ConnectionError`) 허용.
- **CI 게이트**: 메인 브랜치 머지 전 동일 테스트 스위트를 3회 연속 실행(quarantine 단계)하여 flaky 신규 유입 차단.
- **루트 원인 분류**: 시간 의존 / 순서 의존 / 동시성 / 외부 의존 / 비결정성 / 리소스 누출 — 분류별로 fix 패턴 문서(`docs/FLAKY_PLAYBOOK.md`) 참조. 수정 시 회귀 테스트로 100회 반복(`pytest --count=100`) 통과 증명.
- **메트릭**: flaky 비율 ≥ 1% 시 신규 기능 머지 동결 (DevOps 알림).

**테스트 데이터 빌더 패턴 (`factories.py` / `factories.ts`)**:
- 모든 도메인 객체(`User`, `Project`, `Experiment`, `PromptVersion`, `DatasetItem`, `Trace`, `Score`, `EvaluationResult`)는 빌더/팩토리로 생성. 테스트 본문에서 dict literal로 직접 생성 금지.
- Backend: `factory_boy` + `pydantic` 통합. 베이스 팩토리는 `BaseFactory`를 상속하고 `Meta.model`에 Pydantic 모델 지정. 무작위 값은 `Faker` provider로 생성하되 `Faker.seed_instance(0)`로 결정성 보장.
- Frontend: 경량 빌더 함수(`buildExperiment(overrides)`)로 구현. `@faker-js/faker`는 `faker.seed(0)` 고정. TanStack Query mock 데이터도 동일 빌더 사용.
- **빌더 원칙**:
  - 빌더는 항상 valid한 객체를 반환 (domain invariant 위반 금지).
  - `overrides` 인자로 부분 필드만 지정 가능, 나머지는 기본값.
  - 중첩 객체는 sub-builder 호출 (`build_trace(observations=[build_observation()])`).
  - 빌더는 외부 I/O(DB, 네트워크) 호출 금지 — 순수 객체 생성만 담당. 영속화는 별도 `persist_*` helper로 분리.
  - 빌더 자체에 대한 단위 테스트 작성: 기본값 valid, override 정상 적용, schema 검증 통과.
- **금지**: 테스트 간 빌더 인스턴스 공유, 빌더에 mutable class 변수 사용, 빌더 내부에서 시간/난수 직접 호출(주입받기).

#### pytest 설정 (pyproject.toml)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_functions = "test_*"
asyncio_mode = "auto"
markers = [
    "unit: 단위 테스트",
    "integration: 통합 테스트 (TestClient 사용)",
    "infra: 인프라 연결 테스트 (실제 서비스 필요)",
    "contract: OpenAPI 컨트랙트 테스트 (schemathesis)",
    "performance: NFR 성능 테스트 (nightly)",
    "slow: 실행 시간이 긴 테스트",
    "e2e: E2E 테스트 (Playwright, 전체 스택 필요)",
]
filterwarnings = ["ignore::DeprecationWarning"]
```

**추가 의존성**: `pytest-xdist` — 병렬 테스트 실행 지원 (`pytest -n auto`)

### 0.2 Frontend 테스트 구조 (vitest)

```
frontend/
├── vitest.config.ts               # vitest 설정
├── tests/
│   ├── setup.ts                   # 글로벌 setup (MSW 등)
│   ├── mocks/                     # API mock 핸들러
│   │   ├── handlers.ts
│   │   └── server.ts              # MSW setupServer
│   ├── utils/                     # 테스트 유틸
│   │   ├── render.tsx             # 커스텀 render (providers 포함)
│   │   └── factories.ts           # 테스트 데이터 팩토리
│   ├── components/                # 컴포넌트 단위 테스트
│   ├── hooks/                     # 커스텀 hook 테스트
│   └── pages/                     # 페이지 통합 테스트
```

#### vitest.config.ts

```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      exclude: ['tests/**', 'node_modules/**'],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 70,
        statements: 80,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
```

### 0.3 공통 테스트 유틸리티

#### Mock Langfuse Client

```python
class MockLangfuseClient:
    """Langfuse Python SDK v3 호출을 가로채는 mock 클라이언트.

    v3는 OpenTelemetry 기반으로 재설계되어 v2의 `trace()`/`generation()`/`score()`가
    제거되었다. v3에서는 `start_as_current_observation()` 컨텍스트 매니저와
    `update_trace()`, `create_score()`를 사용한다 (LANGFUSE.md §2.4 참조).
    """

    def __init__(self):
        self.prompts: dict[str, list] = {}       # name -> [versions]
        self.datasets: dict[str, list] = {}      # name -> [items]
        self.observations: list[dict] = []       # spans/generations 기록
        self.scores: list[dict] = []
        self._connected = True

    def get_prompt(self, name: str, version: int = None, label: str = None):
        """프롬프트 조회 mock."""

    def create_dataset(self, name: str, **kwargs):
        """데이터셋 생성 mock."""

    def create_dataset_item(self, dataset_name: str, **kwargs):
        """데이터셋 아이템 생성 mock."""

    def start_as_current_observation(
        self,
        *,
        name: str,
        as_type: str = "span",        # "span" | "generation" | "event"
        input: Any = None,
        metadata: dict = None,
        model: str = None,
        model_parameters: dict = None,
        prompt: Any = None,
    ):
        """v3 observation 컨텍스트 매니저 mock.

        Returns a context manager that yields MockSpan (root_span 또는 nested).
        MockSpan 주요 메서드:
          - update(output=..., usage_details=..., cost_details=..., metadata=...)
          - update_trace(user_id=..., session_id=..., tags=..., input=..., output=...)
          - start_as_current_observation(...)  # nested observation
          - score(name=..., value=..., data_type=...)
          - trace_id -> str (OpenTelemetry trace_id)
          - id -> str (observation id)
        """

    def create_score(
        self,
        *,
        name: str,
        value: float | str,
        trace_id: str,
        observation_id: str = None,
        data_type: str = "NUMERIC",
        comment: str = None,
    ):
        """v3 Score 기록 mock (trace 단위로 분리된 호출)."""

    def get_dataset(self, name: str):
        """데이터셋 조회 mock -> MockDataset (with .items list and .run() method)."""

    def flush(self):
        """flush mock (no-op)."""

    def auth_check(self) -> bool:
        """v3 인증 검증 mock (SDK 초기화 시 호출)."""

    def simulate_connection_failure(self):
        """연결 실패 시뮬레이션."""
        self._connected = False

    def simulate_not_found(self, resource_type: str, name: str):
        """리소스 미존재 시뮬레이션."""
```

#### Mock Redis Client

```python
class MockRedisClient:
    """Redis 명령을 인메모리 dict로 시뮬레이션."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        self._ttls: dict[str, float] = {}
        self._connected = True

    def hset(self, key: str, mapping: dict): ...
    def hget(self, key: str, field: str): ...
    def hgetall(self, key: str) -> dict: ...
    def hincrby(self, key: str, field: str, amount: int): ...
    def hincrbyfloat(self, key: str, field: str, amount: float): ...
    def expire(self, key: str, seconds: int): ...
    def ttl(self, key: str) -> int: ...
    def exists(self, key: str) -> bool: ...
    def sadd(self, key: str, *members): ...
    def smembers(self, key: str) -> set: ...
    def zrevrangebyscore(self, key: str, max: float, min: float, start: int, num: int): ...
    def eval(self, script: str, numkeys: int, *keys_and_args): ...
    def zadd(self, key: str, mapping: dict) -> int: ...
    def zrem(self, key: str, *members) -> int: ...
    def delete(self, *keys) -> int: ...
    def srem(self, key: str, *members) -> int: ...
    def ping(self) -> True: ...
    def pipeline(self) -> "MockPipeline":
        """MockPipeline (execute() runs batched commands)."""
        ...

    def simulate_connection_failure(self): ...
```

#### Mock LiteLLM Proxy

```python
class MockLiteLLMProxy:
    """LiteLLM Proxy HTTP 응답을 시뮬레이션."""

    def __init__(self):
        self.models = [
            {"id": "gpt-4o", "provider": "azure", "supports_vision": True},
            {"id": "gemini-2.5-pro", "provider": "google", "supports_vision": True},
        ]

    def get_model_info(self) -> dict:
        """GET /model/info 응답."""

    def health(self) -> dict:
        """GET /health 응답."""

    async def acompletion(self, model: str, messages: list, stream: bool = False, **params):
        """비동기 LLM 호출 mock.
        - stream=False -> MockCompletion (usage, choices 포함)
        - stream=True -> AsyncGenerator[MockChunk] (yields chunks)
        """

    def completion_cost(self, response) -> float:
        """응답 기반 비용 계산 mock."""

    # -- Streaming fixture --
    # stream=True일 때 yields: [{"content":"감성"},{"content":" 분석"},{"content":" 완료"}]

    # -- Error simulation --
    def raise_timeout(self):
        """asyncio.TimeoutError 발생 시뮬레이션."""

    def raise_rate_limit(self):
        """RateLimitError (429) 발생 시뮬레이션."""

    def raise_api_error(self):
        """APIError (500) 발생 시뮬레이션."""
```

#### Test JWT Generator

```python
import jwt
from datetime import datetime, timedelta

def create_test_jwt(
    sub: str = "user_001",
    role: str = "user",
    groups: list[str] = None,
    exp_delta: timedelta = timedelta(hours=1),
    issuer: str = "https://auth.example.com",
    audience: str = "ax-llm-eval-workflow",
    secret: str = "test-secret-key-for-testing",
    algorithm: str = "HS256",
    extra_claims: dict = None,
) -> str:
    """테스트용 JWT 토큰 생성.

    Args:
        sub: 사용자 ID
        role: 역할 (admin, user, viewer)
        groups: 그룹 목록
        exp_delta: 만료 시간 델타 (음수이면 만료된 토큰)
        issuer: JWT issuer
        audience: JWT audience
        secret: 서명용 비밀 키 (테스트에서는 HS256, 프로덕션은 RS256/JWKS)
        algorithm: 서명 알고리즘
        extra_claims: 추가 claim

    Returns:
        JWT 문자열
    """
    now = datetime.utcnow()
    payload = {
        "sub": sub,
        "role": role,
        "groups": groups or [],
        "iat": now,
        "exp": now + exp_delta,
        "iss": issuer,
        "aud": audience,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm=algorithm)
```

#### 0.3.5 인프라 테스트 Fixture

실제 서비스 연결을 위한 fixture. 이 fixture들은 `tests/infra/conftest.py`에 별도 정의한다.

```python
@pytest.fixture(scope="session")
def real_redis():
    """실제 Redis 연결.
    REDIS_URL 환경변수에서 읽음, 미설정 시 redis://localhost:6379/1 사용.
    """

@pytest.fixture(scope="session")
def real_clickhouse():
    """실제 ClickHouse 연결.
    CLICKHOUSE_HOST, CLICKHOUSE_PORT 환경변수에서 읽음.
    """

@pytest.fixture(scope="session")
def real_langfuse():
    """실제 Langfuse 연결.
    LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 환경변수에서 읽음.
    """

@pytest.fixture(scope="session")
def real_litellm():
    """실제 LiteLLM Proxy 연결.
    LITELLM_BASE_URL 환경변수에서 읽음.
    """

@pytest.fixture(scope="session")
def real_postgres():
    """실제 PostgreSQL 연결.
    DATABASE_URL 환경변수에서 읽음.
    """
```

#### 0.3.6 MockClickHouseClient

```python
class MockClickHouseClient:
    """ClickHouse 쿼리를 인메모리로 시뮬레이션."""

    def __init__(self):
        self._query_results: dict[str, list[dict]] = {}
        self._executed_queries: list[str] = []
        self._connected = True

    def query(self, sql: str, params: dict = None) -> list[dict]:
        """SQL 쿼리 실행 mock."""

    def set_query_result(self, sql_pattern: str, result: list[dict]) -> None:
        """특정 SQL 패턴에 대한 반환값 사전 정의."""

    def get_executed_queries(self) -> list[str]:
        """실행된 쿼리 목록 반환 (SQL injection 검증용 spy)."""

    def simulate_timeout(self):
        """쿼리 타임아웃 시뮬레이션."""

    def simulate_connection_error(self):
        """연결 에러 시뮬레이션."""
```

### 0.4 CI 파이프라인 (GitHub Actions)

```yaml
# .github/workflows/test.yml
name: Test Suite

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  backend-unit:
    name: Backend Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && pytest tests/unit/ -m "not slow" --tb=short -q

  backend-integration:
    name: Backend Integration Tests
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
        options: --health-cmd "redis-cli ping" --health-interval 10s
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && pytest tests/integration/ --tb=short -q
        env:
          REDIS_URL: redis://localhost:6379/1

  backend-infra:
    name: Backend Infra Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker/docker-compose.yml up -d postgres clickhouse redis langfuse litellm
      - run: docker compose -f docker/docker-compose.yml exec -T clickhouse bash /scripts/setup-clickhouse-readonly.sh
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && pytest tests/infra/ --tb=short -q
      - run: docker compose -f docker/docker-compose.yml down -v

  frontend-unit:
    name: Frontend Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: cd frontend && npm ci
      - run: cd frontend && npx vitest run --coverage

  lint:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && ruff check . && mypy app/
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: cd frontend && npm ci && npm run lint && npx tsc --noEmit
```

### 0.5 TDD 개발 사이클 실행 가이드

#### 단일 테스트 실행
```bash
# 특정 테스트 함수 실행
pytest tests/unit/test_jwt.py::test_should_return_401_when_jwt_missing -v

# 패턴 매칭으로 실행
pytest -k "should_return_401" -v

# 마지막 실패한 테스트만 재실행
pytest --lf

# 첫 실패 시 즉시 중단
pytest -x
```

#### Frontend 단일 테스트
```bash
npx vitest run tests/components/ScoreBadge.test.tsx
```

#### 커버리지 측정
```bash
# Backend 커버리지 (80% 라인 커버리지 목표)
pytest --cov=app --cov-fail-under=80
```

Backend 커버리지 목표 80% (lines)는 `pyproject.toml`에 설정한다:

```toml
[tool.coverage.report]
fail_under = 80
```

#### 병렬 실행
```bash
# pytest-xdist를 사용한 병렬 실행
pytest -n auto
```

---

## Phase 1: 인프라 테스트

인프라 서비스(Redis, ClickHouse, Langfuse, LiteLLM, PostgreSQL)의 연결, 헬스체크, 인증을 검증한다.
이 테스트들은 `@pytest.mark.infra` 마커를 사용하며, 실제 Docker 서비스가 기동된 환경에서 실행한다.

### 1.1 Redis 연결 테스트

**파일**: `tests/infra/test_redis_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_successfully_when_valid_credentials` | REDIS_URL 환경변수 (password 포함) | `PONG` 응답, 연결 성공 | 실제 Redis 인스턴스 | - |
| 2 | `test_should_fail_connection_when_wrong_password` | 잘못된 password를 포함한 REDIS_URL | `AuthenticationError` 예외 발생 | 실제 Redis 인스턴스 | 빈 문자열 password |
| 3 | `test_should_fail_connection_when_host_unreachable` | 존재하지 않는 host (`redis://invalid-host:6379`) | `ConnectionError` 예외, 타임아웃 이내 발생 | 없음 | DNS 미해석, 포트 미응답 |
| 4 | `test_should_respond_to_healthcheck_ping_when_connected` | 정상 연결된 Redis 클라이언트 | `ping()` 결과 `True` | 실제 Redis 인스턴스 | - |
| 5 | `test_should_use_correct_db_number_when_labs_redis_db_set` | `LABS_REDIS_DB=1` 환경변수 | DB 1에 데이터 저장 확인 (DB 0과 격리) | 실제 Redis 인스턴스 | DB 번호 0과의 키 충돌 없음 |
| 6 | `test_should_authenticate_with_password_when_requirepass_set` | `--requirepass` 설정된 Redis + 올바른 password | 인증 성공, SET/GET 동작 | 실제 Redis 인스턴스 | password 없이 접근 시 `NOAUTH` 에러 |

### 1.2 ClickHouse 연결 테스트

**파일**: `tests/infra/test_clickhouse_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_successfully_when_valid_readonly_credentials` | `CLICKHOUSE_READONLY_USER`, `CLICKHOUSE_READONLY_PASSWORD` | `SELECT 1` 결과 `1` 반환 | 실제 ClickHouse + 읽기 전용 계정 | - |
| 2 | `test_should_fail_connection_when_wrong_password` | 잘못된 비밀번호 | 인증 실패 예외 | 실제 ClickHouse | 빈 문자열 password |
| 3 | `test_should_respond_to_healthcheck_when_connected` | HTTP GET `http://clickhouse:8123/ping` | 응답 `Ok.\n` (200) | 실제 ClickHouse | - |
| 4 | `test_should_reject_write_when_readonly_account` | `labs_readonly` 계정으로 `INSERT INTO traces VALUES (...)` | 권한 오류 예외 발생 (`READONLY`) | 실제 ClickHouse + 읽기 전용 계정 | `CREATE TABLE`, `DROP TABLE`, `ALTER TABLE` 모두 거부 확인 |
| 5 | `test_should_allow_select_on_langfuse_db_when_readonly_account` | `labs_readonly` 계정으로 `SELECT * FROM system.tables LIMIT 1` | 정상 결과 반환 | 실제 ClickHouse + 읽기 전용 계정 | `langfuse` 데이터베이스 이외의 DB 접근 거부 확인 |
| 6 | `test_should_fail_connection_when_host_unreachable` | 잘못된 host | 연결 실패 예외, 적절한 타임아웃 | 없음 | - |

### 1.3 Langfuse 연결 테스트

**파일**: `tests/infra/test_langfuse_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_ok_when_langfuse_health_endpoint_called` | HTTP GET `http://langfuse:3000/api/public/health` | 200 응답, `status: "OK"` | 실제 Langfuse 인스턴스 | - |
| 2 | `test_should_connect_sdk_when_valid_api_keys` | 유효한 `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | SDK 초기화 성공, `auth_check()` 통과 | 실제 Langfuse 인스턴스 | - |
| 3 | `test_should_fail_sdk_when_invalid_api_keys` | 잘못된 API 키 | 인증 실패 예외 | 실제 Langfuse 인스턴스 | 빈 문자열 키, 형식이 다른 키 |
| 4 | `test_should_fail_connection_when_langfuse_host_unreachable` | 잘못된 `LANGFUSE_HOST` | 연결 실패 예외 | 없음 | - |

### 1.4 LiteLLM Proxy 연결 테스트

**파일**: `tests/infra/test_litellm_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_ok_when_litellm_health_called` | HTTP GET `http://litellm:4000/health` | 200 응답 | 실제 LiteLLM Proxy | - |
| 2 | `test_should_return_model_list_when_model_info_called` | HTTP GET `http://litellm:4000/model/info` + Master Key 헤더 | 200 응답, `data` 배열에 모델 목록 존재 | 실제 LiteLLM Proxy | 모델이 0개인 경우 빈 배열 |
| 3 | `test_should_reject_when_invalid_master_key` | 잘못된 Master Key로 `/model/info` 호출 | 401 또는 403 응답 | 실제 LiteLLM Proxy | Master Key 없이 호출 |
| 4 | `test_should_fail_connection_when_litellm_unreachable` | 잘못된 `LITELLM_BASE_URL` | 연결 실패 예외 | 없음 | - |

### 1.5 PostgreSQL 연결 테스트

**파일**: `tests/infra/test_postgres_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_successfully_when_valid_credentials` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `SELECT 1` 결과 반환 | 실제 PostgreSQL | - |
| 2 | `test_should_respond_to_healthcheck_when_connected` | `pg_isready` 커맨드 | 정상 응답 | 실제 PostgreSQL | - |
| 3 | `test_should_fail_connection_when_wrong_credentials` | 잘못된 password | 인증 실패 예외 | 실제 PostgreSQL | - |

---

## Phase 2: Backend 기초 테스트

### 2.1 JWT 미들웨어 테스트

**파일**: `tests/unit/test_security.py`

#### 2.1.1 유효한 JWT 처리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_200_when_valid_jwt_with_admin_role` | `Authorization: Bearer <valid_admin_jwt>` | 200, 요청 처리 성공. `request.state.user.role == "admin"` | `jwt_admin`, mock JWKS 엔드포인트 | - |
| 2 | `test_should_return_200_when_valid_jwt_with_user_role` | `Authorization: Bearer <valid_user_jwt>` | 200, `request.state.user.role == "user"` | `jwt_user`, mock JWKS 엔드포인트 | - |
| 3 | `test_should_return_200_when_valid_jwt_with_viewer_role` | `Authorization: Bearer <valid_viewer_jwt>` | 200, `request.state.user.role == "viewer"` | `jwt_viewer`, mock JWKS 엔드포인트 | - |
| 4 | `test_should_extract_user_id_from_sub_claim_when_valid_jwt` | `sub: "user_001"` 포함 JWT | `request.state.user.sub == "user_001"` | `create_test_jwt(sub="user_001")` | - |

#### 2.1.2 만료/잘못된 JWT 처리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 5 | `test_should_return_401_when_jwt_expired` | 만료 시간이 과거인 JWT (`exp_delta=timedelta(hours=-1)`) | 401, `{"error": {"code": "AUTH_REQUIRED", "message": ...}}` | `jwt_expired` | 방금 만료된 토큰 (1초 전) |
| 6 | `test_should_return_401_when_jwt_signature_invalid` | 다른 secret으로 서명된 JWT | 401, `{"error": {"code": "AUTH_REQUIRED"}}` | `create_test_jwt(secret="wrong-secret")` | - |
| 7 | `test_should_return_401_when_jwt_issuer_mismatch` | 잘못된 issuer (`iss: "https://evil.com"`) | 401 | `create_test_jwt(issuer="https://evil.com")` | - |
| 8 | `test_should_return_401_when_jwt_audience_mismatch` | 잘못된 audience (`aud: "other-app"`) | 401 | `create_test_jwt(audience="other-app")` | - |
| 9 | `test_should_return_401_when_no_authorization_header` | 헤더 없음 | 401, `{"error": {"code": "AUTH_REQUIRED", "message": "인증 토큰이 필요합니다"}}` | 없음 | - |
| 10 | `test_should_return_401_when_authorization_header_malformed` | `Authorization: InvalidFormat abc123` | 401 | 없음 | `Authorization: Bearer` (토큰 누락), `Authorization: Basic abc` (잘못된 스킴) |
| 11 | `test_should_return_401_when_authorization_header_empty_bearer` | `Authorization: Bearer ` (공백만) | 401 | 없음 | - |
| 12 | `test_should_return_401_when_jwt_payload_missing_required_claims` | `sub` 또는 `role` claim이 없는 JWT | 401 | `create_test_jwt`에서 claim 제거 | `exp` 누락, `iat` 누락 |

#### 2.1.3 RBAC 권한 검증

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 13 | `test_should_return_403_when_viewer_calls_admin_endpoint` | viewer JWT로 `PATCH /api/v1/prompts/{name}/versions/{v}/labels` 호출 | 403, `{"error": {"code": "FORBIDDEN"}}` | `jwt_viewer` | - |
| 14 | `test_should_return_403_when_user_calls_admin_endpoint` | user JWT로 `DELETE /api/v1/datasets/{name}` 호출 | 403 | `jwt_user` | - |
| 15 | `test_should_return_403_when_viewer_calls_user_endpoint` | viewer JWT로 `POST /api/v1/prompts` 호출 | 403 | `jwt_viewer` | - |
| 16 | `test_should_allow_admin_to_access_all_endpoints` | admin JWT로 admin/user/viewer 엔드포인트 각각 호출 | 모든 요청 200 (또는 적절한 성공 응답) | `jwt_admin` | - |
| 17 | `test_should_allow_user_to_access_user_and_viewer_endpoints` | user JWT로 user/viewer 엔드포인트 호출 | 성공 응답 | `jwt_user` | - |
| 18 | `test_should_return_403_when_role_claim_is_unknown` | `role: "superadmin"` (존재하지 않는 역할) | 403 | `create_test_jwt(role="superadmin")` | `role: ""` (빈 문자열), `role: null` |

### 2.2 Langfuse Client Wrapper 테스트

**파일**: `tests/unit/test_langfuse_client.py`

#### 2.2.1 프로젝트 전환

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_correct_client_when_valid_project_id` | `project_id="proj_1"` (PROJECTS_CONFIG에 등록된 프로젝트) | 해당 프로젝트의 public_key/secret_key로 초기화된 Langfuse 클라이언트 반환 | PROJECTS_CONFIG fixture | - |
| 2 | `test_should_switch_client_when_different_project_id` | `project_id="proj_1"` 호출 후 `project_id="proj_2"` 호출 | 각각 다른 API 키로 초기화된 클라이언트 반환 | PROJECTS_CONFIG fixture (2개 프로젝트) | - |
| 3 | `test_should_raise_error_when_project_id_not_found` | `project_id="non_existent"` | `PROJECT_NOT_FOUND` 에러 (404) | PROJECTS_CONFIG fixture | `project_id=""` (빈 문자열), `project_id=None` |
| 4 | `test_should_cache_client_when_same_project_id_called_repeatedly` | 동일 `project_id`로 3회 연속 호출 | 동일 클라이언트 인스턴스 반환 (매번 새로 생성하지 않음) | PROJECTS_CONFIG fixture | - |

#### 2.2.2 연결 실패 처리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 5 | `test_should_return_graceful_error_when_langfuse_unreachable` | Langfuse 서버가 응답하지 않는 상태에서 `get_prompt()` 호출 | `LANGFUSE_ERROR` 예외 (502), 에러 메시지에 연결 실패 원인 포함 | `mock_langfuse.simulate_connection_failure()` | 타임아웃, DNS 실패, 서버 500 |
| 6 | `test_should_return_graceful_error_when_langfuse_returns_500` | Langfuse SDK가 500 에러를 반환하도록 설정 | `LANGFUSE_ERROR` 예외 (502), 원본 에러 메시지 전달 | mock Langfuse (500 응답) | - |
| 7 | `test_should_retry_on_transient_failure_when_configured` | 첫 호출 실패, 두 번째 성공 | 최종 성공 결과 반환 | mock Langfuse (순차적 실패/성공) | 최대 재시도 횟수 초과 시 최종 실패 |

#### 2.2.3 핵심 메서드 동작

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 8 | `test_should_parse_variables_when_get_prompt_called` | `get_prompt("test-prompt")` (프롬프트에 `{{input_text}}`, `{{rules}}` 포함) | 결과에 `variables: ["input_text", "rules"]` 포함 | mock Langfuse | 변수 없는 프롬프트, 중복 변수, 중첩 중괄호 `{{{var}}}` |
| 9 | `test_should_call_flush_when_flush_invoked` | `flush()` 호출 | Langfuse SDK의 `flush()` 메서드가 호출됨 | mock Langfuse | - |
| 10 | `test_should_start_observation_with_correct_metadata_when_called` | `start_as_current_observation(name=..., as_type="span", metadata={...})` 호출 (v3 SDK) | 컨텍스트 진입 시 MockSpan 반환, metadata에 `source: "ax-llm-eval-workflow"` 자동 병합, 컨텍스트 종료 시 observation 자동 기록 | mock Langfuse | metadata가 None인 경우, nested `start_as_current_observation(as_type="generation")` 호출 시 parent span에 연결 |
| 11 | `test_should_call_update_trace_when_root_span_metadata_set` | root span에서 `update_trace(user_id, session_id, tags)` 호출 | v3 trace 속성이 기록됨 (v2 `trace()` 인자가 아닌 별도 호출로 분리됨) | mock Langfuse | - |
| 12 | `test_should_call_create_score_when_score_recorded_on_trace` | `create_score(name, value, trace_id, observation_id)` 호출 (v3) | score가 해당 trace에 귀속되어 기록됨 (v2 `langfuse.score()` API 아님) | mock Langfuse | `data_type="CATEGORICAL"`로 문자열 value 기록 |

> 참고: `CLICKHOUSE_ERROR` 에러 매핑은 Phase 6 ClickHouse 쿼리 테스트(TEST_SPEC_PART2 §6.1)에서, `SANDBOX_VIOLATION`은 Custom Evaluator 샌드박스 테스트(TEST_SPEC_PART2 §5.3)에서 다룬다. 이 섹션에서는 Langfuse Client Wrapper 고유의 에러 매핑(§2.2.2)만 검증한다.

### 2.3 Redis Client 테스트

**파일**: `tests/unit/test_redis_client.py`

#### 2.3.1 연결 관리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_when_valid_redis_url` | 유효한 `REDIS_URL` | 연결 성공, `ping()` 응답 `True` | mock_redis 또는 실제 Redis | - |
| 2 | `test_should_raise_error_when_connection_failed` | 잘못된 `REDIS_URL` | `ConnectionError` 예외, 명확한 에러 메시지 | 없음 | 호스트 미응답, 인증 실패 |
| 3 | `test_should_use_connection_pool_when_initialized` | 클라이언트 초기화 | 연결 풀 사용 확인 (max_connections 설정) | mock_redis | - |

#### 2.3.2 HINCRBY 원자적 증가

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 4 | `test_should_increment_field_atomically_when_hincrby_called` | `HINCRBY ax:experiment:exp1 completed_items 1` | 필드 값 1 증가, 증가 후 값 반환 | mock_redis | - |
| 5 | `test_should_create_field_with_value_when_hincrby_on_nonexistent_field` | 존재하지 않는 필드에 `HINCRBY` | 필드 생성, 값 = increment | mock_redis | - |
| 6 | `test_should_increment_float_atomically_when_hincrbyfloat_called` | `HINCRBYFLOAT ax:experiment:exp1 total_cost_usd 0.023` | float 값 정확하게 누적 | mock_redis | 부동소수점 정밀도 문제 (0.1 + 0.2) |
| 7 | `test_should_handle_concurrent_increments_when_multiple_calls` | 동시에 10개 `HINCRBY` 호출 | 최종 값이 정확히 10 증가 | mock_redis (또는 실제 Redis) | - |

#### 2.3.3 Lua Script 실행 (상태 전이)

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 8 | `test_should_transition_status_when_valid_transition` | 현재 상태 `running`, 전이 요청 `running` -> `paused` | 상태가 `paused`로 변경, `updated_at` 갱신 | mock_redis | - |
| 9 | `test_should_reject_transition_when_invalid_current_status` | 현재 상태 `cancelled`, 전이 요청 `cancelled` -> `running` | `STATE_CONFLICT` 에러 반환 | mock_redis | - |
| 10 | `test_should_set_completed_at_when_terminal_status_reached` | `running` -> `completed` 전이 | `completed_at` 필드 설정, TTL 1시간으로 단축 | mock_redis | `failed`, `cancelled` 전이도 동일 동작 |
| 11 | `test_should_shorten_ttl_when_terminal_status` | `running` -> `completed` 전이 | TTL이 3600초(1시간)로 변경 | mock_redis | 기존 TTL이 86400초였는지 확인 |
| 12 | `test_should_set_error_message_when_failed_with_message` | `running` -> `failed`, `error_message="LLM timeout"` | `error_message` 필드에 값 저장 | mock_redis | 빈 문자열 error_message는 무시 |
| 13 | `test_should_return_experiment_not_found_when_key_missing` | 존재하지 않는 experiment_id | `EXPERIMENT_NOT_FOUND` 에러 | mock_redis | - |
| 14 | `test_should_support_multiple_allowed_statuses_when_comma_separated` | `expected_current_status="running,paused"`, 현재 `paused` | 전이 성공 | mock_redis | 현재 상태가 허용 목록에 없는 경우 거부 |

#### 2.3.4 TTL 설정/만료

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 15 | `test_should_set_ttl_when_experiment_created` | 실험 생성 시 Hash 저장 | TTL = 86400초 (24시간) | mock_redis | - |
| 16 | `test_should_refresh_ttl_when_item_completed` | 아이템 완료 이벤트 처리 | TTL이 86400초로 재설정 | mock_redis | - |
| 17 | `test_should_expire_key_when_ttl_reached` | TTL이 0이 된 키 조회 | `None` 반환 (키 만료) | mock_redis (TTL 시뮬레이션) | - |
| 18 | `test_should_apply_same_ttl_to_related_keys_when_terminal` | 실험 종료 시 Run Hash, Run Set, Failed Items Set | 모든 관련 키의 TTL이 3600초 | mock_redis | - |

#### 2.3.5 Redis 필드 저장 테스트

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 19 | `test_should_store_started_by_from_jwt_sub_when_experiment_created` | JWT `sub: "user_001"`로 실험 생성 | Redis Hash에 `started_by = "user_001"` 저장 | mock_redis, `jwt_user(sub="user_001")` | `sub` 값이 빈 문자열인 경우 |
| 20 | `test_should_cleanup_expired_experiment_from_sorted_set_when_listing` | `ax:project:{pid}:experiments` Sorted Set에 만료된 experiment_id 포함 | 목록 조회 시 만료된 키를 Sorted Set에서 제거 (lazy cleanup) | mock_redis (만료된 키 시뮬레이션) | 만료된 키가 여러 개인 경우 |

#### 2.3.6 상태 전이 추가 검증

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 21 | `test_should_return_409_when_resume_called_on_running_experiment` | `status="running"` 실험에 `POST /resume` | 409, `{"error": {"code": "STATE_CONFLICT"}}` | mock_redis | - |
| 22 | `test_should_return_409_when_cancel_called_on_completed_experiment` | `status="completed"` 실험에 `POST /cancel` | 409, `{"error": {"code": "STATE_CONFLICT"}}` | mock_redis | - |
| 23 | `test_should_return_409_when_cancel_called_on_failed_experiment` | `status="failed"` 실험에 `POST /cancel` | 409, `{"error": {"code": "STATE_CONFLICT"}}` | mock_redis | - |

#### 2.3.7 Run Hash 필드 테스트

| # | 테스트 이름 | 기대 결과 |
|---|------------|----------|
| 24 | `test_should_hincrby_run_completed_items_when_item_completes` | 아이템 완료 시 `HINCRBY run:{id} completed_items 1` 호출 확인 |
| 25 | `test_should_hincrby_run_failed_items_when_item_fails` | 아이템 실패 시 `HINCRBY run:{id} failed_items 1` 호출 확인 |
| 26 | `test_should_hincrbyfloat_run_total_cost_when_item_completes` | 아이템 완료 시 `HINCRBYFLOAT run:{id} total_cost {cost}` 호출 확인 |
| 27 | `test_should_hincrbyfloat_run_total_latency_when_item_completes` | 아이템 완료 시 `HINCRBYFLOAT run:{id} total_latency {latency}` 호출 확인 |
| 28 | `test_should_hincrbyfloat_run_total_score_sum_when_score_recorded` | 점수 기록 시 `HINCRBYFLOAT run:{id} total_score_sum {score}` 호출 확인 |
| 29 | `test_should_hincrby_run_scored_count_when_score_recorded` | 점수 기록 시 `HINCRBY run:{id} scored_count 1` 호출 확인 |
| 30 | `test_should_calculate_avg_score_from_sum_and_count_when_queried` | 조회 시 `total_score_sum / scored_count`로 평균 점수 계산 확인 |
| 31 | `test_should_store_total_duration_sec_when_experiment_completes` | 실험 완료 시 `HSET run:{id} total_duration_sec {seconds}` 호출 확인 |

---

## Phase 3: Core API 테스트

모든 API 테스트는 FastAPI TestClient를 사용한 통합 테스트이다.
공통 전제: 유효한 JWT (user 또는 admin)로 요청, `project_id` 파라미터 필수.

### 3.1 헬스체크 API

**파일**: `tests/integration/test_health_api.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_ok_when_all_services_healthy` | `GET /api/v1/health` (인증 불필요) | 200, `{"status": "ok", "version": "1.0.0"}` | mock_langfuse, mock_redis, mock_litellm, mock_clickhouse (모두 정상) | - |
| 2 | `test_should_return_ok_without_auth_when_health_called` | `GET /api/v1/health` (Authorization 헤더 없음) | 200 | 모든 서비스 mock | 인증이 필요한 다른 엔드포인트와 구별 |
| 3 | `test_should_include_service_statuses_when_detailed_health` | `GET /api/v1/health` | 응답에 각 서비스별 상태 포함 (langfuse, litellm, clickhouse, redis) | 일부 서비스 실패 mock | 개별 서비스 장애 시 해당 서비스만 `fail` 표시 |

### 3.2 프롬프트 API

**파일**: `tests/integration/test_prompts_api.py`

#### 3.2.1 GET /api/v1/prompts -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_prompt_list_when_prompts_exist` | `GET /api/v1/prompts?project_id=proj_1` | 200, `{"status": "success", "data": {"prompts": [...]}}`. 각 프롬프트에 `name`, `latest_version`, `labels`, `tags`, `created_at` 포함 | mock_langfuse (프롬프트 3개 등록) | - |
| 2 | `test_should_return_empty_list_when_no_prompts` | `GET /api/v1/prompts?project_id=proj_empty` | 200, `{"data": {"prompts": []}}` | mock_langfuse (프롬프트 0개) | - |
| 3 | `test_should_return_404_when_project_not_found` | `GET /api/v1/prompts?project_id=non_existent` | 404, `{"error": {"code": "PROJECT_NOT_FOUND"}}` | mock_langfuse | - |
| 4 | `test_should_return_422_when_project_id_missing` | `GET /api/v1/prompts` (project_id 누락) | 422, `{"error": {"code": "VALIDATION_ERROR"}}` | 없음 | - |
| 5 | `test_should_return_401_when_no_auth` | `GET /api/v1/prompts?project_id=proj_1` (JWT 없음) | 401 | 없음 | - |

#### 3.2.2 GET /api/v1/prompts/{name} -- 상세 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 6 | `test_should_return_prompt_detail_when_exists` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1` | 200, `name`, `version`, `type`, `prompt`, `config`, `labels`, `variables` 포함 | mock_langfuse (해당 프롬프트 등록) | - |
| 7 | `test_should_return_specific_version_when_version_param_set` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1&version=3` | 200, `version: 3` | mock_langfuse (v1, v2, v3 존재) | - |
| 8 | `test_should_return_labeled_version_when_label_param_set` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1&label=production` | 200, `labels` 배열에 `"production"` 포함 | mock_langfuse (production 라벨 설정) | - |
| 9 | `test_should_return_latest_version_when_no_version_or_label` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1` | 200, 가장 높은 version 반환 | mock_langfuse (v1~v5 존재) | - |
| 10 | `test_should_extract_variables_when_prompt_contains_template_vars` | 프롬프트 텍스트: `"{{input_text}}를 분석하세요. 규칙: {{rules}}"` | `variables: ["input_text", "rules"]` | mock_langfuse | - |
| 11 | `test_should_return_empty_variables_when_no_template_vars` | 프롬프트 텍스트: `"안녕하세요"` (변수 없음) | `variables: []` | mock_langfuse | - |
| 12 | `test_should_deduplicate_variables_when_same_var_used_multiple_times` | 프롬프트 텍스트: `"{{name}}의 {{name}} 분석"` | `variables: ["name"]` (중복 제거) | mock_langfuse | - |
| 13 | `test_should_return_404_when_prompt_not_found` | `GET /api/v1/prompts/non-existent?project_id=proj_1` | 404, `{"error": {"code": "PROMPT_NOT_FOUND"}}` | mock_langfuse | - |
| 14 | `test_should_return_404_when_version_not_found` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1&version=999` | 404 | mock_langfuse | version=0, version=-1 |
| 15 | `test_should_handle_chat_type_prompt_when_type_is_chat` | chat 타입 프롬프트 (messages 배열) | `type: "chat"`, `prompt`이 messages 배열 형태 | mock_langfuse | - |

#### 3.2.3 GET /api/v1/prompts/{name}/versions -- 버전 목록

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 16 | `test_should_return_version_list_when_versions_exist` | `GET /api/v1/prompts/sentiment-analysis/versions?project_id=proj_1` | 200, `{"data": {"versions": [{"version": 1, "labels": [...], "created_at": "...", "created_by": "..."}]}}` | mock_langfuse (3개 버전) | - |
| 17 | `test_should_return_empty_when_prompt_has_no_versions` | 존재하지 않는 프롬프트 이름 | 404, `PROMPT_NOT_FOUND` | mock_langfuse | - |

#### 3.2.4 POST /api/v1/prompts -- 생성

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 18 | `test_should_create_prompt_when_valid_request` | `POST` body: `{"project_id": "proj_1", "name": "new-prompt", "prompt": "...", "type": "text", "labels": ["staging"]}` | 200, `{"data": {"name": "new-prompt", "version": 1, "labels": ["staging"]}}` | mock_langfuse, `jwt_user` | - |
| 19 | `test_should_increment_version_when_prompt_name_exists` | 이미 존재하는 프롬프트 이름으로 `POST` | 새 버전 번호 반환 (기존 + 1) | mock_langfuse (기존 v2 존재) | - |
| 20 | `test_should_return_403_when_viewer_creates_prompt` | viewer JWT로 `POST /api/v1/prompts` | 403, `FORBIDDEN` | `jwt_viewer` | - |
| 21 | `test_should_return_422_when_missing_required_fields` | `POST` body에서 `name` 누락 | 422, `VALIDATION_ERROR` | `jwt_user` | `prompt` 누락, `type` 누락, `project_id` 누락 |
| 22 | `test_should_return_422_when_invalid_type` | `type: "invalid"` | 422 | `jwt_user` | `type` 필드가 `text` 또는 `chat` 외의 값 |
| 23 | `test_should_create_chat_prompt_when_type_is_chat` | `type: "chat"`, `prompt: [{"role": "system", "content": "..."}]` | 성공, chat 타입 프롬프트 생성 | mock_langfuse, `jwt_user` | - |

#### 3.2.5 PATCH /api/v1/prompts/{name}/versions/{version}/labels -- 라벨 승격

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 24 | `test_should_update_labels_when_admin_calls` | admin JWT, `PATCH` body: `{"project_id": "proj_1", "labels": ["production"]}` | 200, 라벨 업데이트 성공 | mock_langfuse, `jwt_admin` | - |
| 25 | `test_should_return_403_when_user_promotes_label` | user JWT로 `PATCH` 호출 | 403 | `jwt_user` | - |
| 26 | `test_should_return_403_when_viewer_promotes_label` | viewer JWT로 `PATCH` 호출 | 403 | `jwt_viewer` | - |
| 27 | `test_should_return_404_when_prompt_version_not_found` | 존재하지 않는 프롬프트/버전 | 404 | mock_langfuse, `jwt_admin` | - |
| 28 | `test_should_allow_multiple_labels_when_array_provided` | `labels: ["production", "reviewed"]` | 200, 여러 라벨 설정 | mock_langfuse, `jwt_admin` | - |
| 29 | `test_should_allow_empty_labels_when_removing_all` | `labels: []` | 200, 기존 라벨 제거 | mock_langfuse, `jwt_admin` | - |

### 3.3 데이터셋 API

**파일**: `tests/integration/test_datasets_api.py`

#### 3.3.1 GET /api/v1/datasets -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_dataset_list_when_datasets_exist` | `GET /api/v1/datasets?project_id=proj_1` | 200, `{"data": {"datasets": [{"name": "...", "item_count": 100, "created_at": "...", "metadata": {...}}]}}` | mock_langfuse (데이터셋 2개) | - |
| 2 | `test_should_return_empty_list_when_no_datasets` | `GET /api/v1/datasets?project_id=proj_empty` | 200, `{"data": {"datasets": []}}` | mock_langfuse (데이터셋 0개) | - |
| 3 | `test_should_return_422_when_project_id_missing` | `GET /api/v1/datasets` | 422 | 없음 | - |

#### 3.3.2 GET /api/v1/datasets/{name}/items -- 아이템 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 4 | `test_should_return_items_with_pagination_when_valid_request` | `GET /api/v1/datasets/golden-100/items?project_id=proj_1&page=1&page_size=20` | 200, `items` 배열 (최대 20개), `total: 100`, 각 아이템에 `id`, `input`, `expected_output`, `metadata` 포함 | mock_langfuse (100개 아이템) | - |
| 5 | `test_should_return_second_page_when_page_2_requested` | `page=2&page_size=20` | 200, 21~40번째 아이템 반환 | mock_langfuse | - |
| 6 | `test_should_return_empty_when_page_exceeds_total` | `page=999&page_size=20` | 200, `items: []`, `total: 100` | mock_langfuse | - |
| 7 | `test_should_return_404_when_dataset_not_found` | `GET /api/v1/datasets/non-existent/items?project_id=proj_1` | 404, `DATASET_NOT_FOUND` | mock_langfuse | - |
| 8 | `test_should_use_default_pagination_when_no_params` | `GET /api/v1/datasets/golden-100/items?project_id=proj_1` (page/page_size 미지정) | 200, 기본 page=1, page_size=20 적용 | mock_langfuse | - |

#### 3.3.3 POST /api/v1/datasets/upload -- 업로드

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 9 | `test_should_upload_csv_when_valid_file_and_mapping` | multipart: CSV 파일 (100행) + mapping JSON `{"input_columns": ["input"], "output_column": "expected", "metadata_columns": ["difficulty"]}` | 200, `{"data": {"dataset_name": "test-ds", "items_created": 100, "status": "completed"}}` | mock_langfuse, `jwt_user` | - |
| 10 | `test_should_upload_json_when_valid_json_file` | multipart: JSON 파일 (배열 형태) + mapping | 200, 아이템 생성 성공 | mock_langfuse, `jwt_user` | - |
| 11 | `test_should_upload_jsonl_when_valid_jsonl_file` | multipart: JSONL 파일 (줄 단위 JSON) + mapping | 200, 아이템 생성 성공 | mock_langfuse, `jwt_user` | - |
| 12 | `test_should_return_400_when_csv_mapping_column_not_found` | mapping의 `input_columns`에 존재하지 않는 컬럼명 | 400, `{"error": {"code": "MAPPING_ERROR"}}` | `jwt_user` | `output_column`이 없는 경우, `metadata_columns`에 없는 컬럼 |
| 13 | `test_should_return_413_when_file_exceeds_50mb` | 51MB 파일 업로드 | 413, `{"error": {"code": "FILE_TOO_LARGE"}}` | `jwt_user` | 정확히 50MB (경계값), 50MB - 1 byte (허용) |
| 14 | `test_should_return_400_when_rows_exceed_10000` | 10,001행 CSV 파일 | 400, 행 수 초과 에러 메시지 | `jwt_user` | 정확히 10,000행 (허용), 빈 행 포함 시 카운트 |
| 15 | `test_should_return_400_when_file_encoding_undetectable` | 잘못된 인코딩 바이너리 파일 | 400, `{"error": {"code": "FILE_ENCODING_ERROR"}}` | `jwt_user` | - |
| 16 | `test_should_detect_encoding_when_euckr_file` | EUC-KR 인코딩 CSV | 200, 한글 데이터 정상 파싱 | mock_langfuse, `jwt_user` | - |
| 17 | `test_should_return_400_when_file_format_invalid` | 확장자가 .txt인 파일, 또는 파싱 불가능한 내용 | 400, `{"error": {"code": "FILE_PARSE_ERROR"}}` | `jwt_user` | XML, Excel 파일 등 미지원 포맷 |
| 18 | `test_should_return_400_when_csv_has_no_rows` | 헤더만 있고 데이터 행이 없는 CSV | 400, 데이터 없음 에러 | `jwt_user` | - |
| 19 | `test_should_return_403_when_viewer_uploads` | viewer JWT로 업로드 시도 | 403 | `jwt_viewer` | - |
| 20 | `test_should_handle_unicode_data_when_csv_contains_special_chars` | CSV에 이모지, 한자, 특수문자 포함 | 200, 데이터 정상 저장 | mock_langfuse, `jwt_user` | null 바이트 포함 데이터 |
| 21 | `test_should_map_multiple_input_columns_when_mapping_has_array` | `input_columns: ["text", "context"]` | 200, input이 `{"text": "...", "context": "..."}` 형태로 저장 | mock_langfuse, `jwt_user` | - |
| 22 | `test_should_return_422_when_mapping_json_invalid` | mapping 파라미터가 유효하지 않은 JSON 문자열 | 422, `VALIDATION_ERROR` | `jwt_user` | - |
| 23 | `test_should_return_202_when_file_has_more_than_500_rows` | multipart: 501행 CSV + mapping | 202 Accepted, `{"data": {"upload_id": "uuid", "status": "processing", "stream_url": "/api/v1/datasets/upload/{upload_id}/stream"}}` (API_DESIGN §6.3 비동기 경로) | mock_langfuse, mock_redis (`ax:dataset_upload:{upload_id}` Hash 생성 확인, `owner_user_id` = JWT sub), `jwt_user` | 정확히 500행 (동기 200), 501행 (비동기 202) 경계값 |
| 24 | `test_should_return_200_when_file_has_500_rows_or_fewer` | multipart: 500행 CSV + mapping | 200, `{"data": {"status": "completed", "items_created": 500, "upload_id": "uuid"}}` (동기 경로) | mock_langfuse, `jwt_user` | - |
| 25 | `test_should_store_owner_user_id_in_upload_hash_when_async_upload` | 501행 CSV + `jwt_user(sub="user_001")` | Redis `ax:dataset_upload:{upload_id}` Hash에 `owner_user_id = "user_001"` 저장, TTL 3600초 | mock_redis, `jwt_user` | - |

#### 3.3.3-SSE GET /api/v1/datasets/upload/{upload_id}/stream -- SSE 구독 (API_DESIGN §6.3.1)

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 26 | `test_should_stream_progress_events_when_subscribed` | `GET /api/v1/datasets/upload/{upload_id}/stream`, 진행 중 업로드 | `text/event-stream` 응답, `event: progress` + `data: {"completed": N, "total": M, "failed": K}` 수신 | mock_redis (`ax:dataset_upload:{upload_id}`), `jwt_user` (owner) | - |
| 27 | `test_should_emit_done_event_when_upload_completes` | 업로드 완료 시점 | `event: done` + 최종 결과 페이로드 수신 후 연결 종료 | mock_redis, `jwt_user` | - |
| 28 | `test_should_emit_error_event_when_file_level_error_occurs` | 파일 파싱 실패 | `event: error` + `{"code": "FILE_PARSE_ERROR", "message": ...}` 수신 후 종료 | mock_redis, `jwt_user` | - |
| 29 | `test_should_return_403_when_non_owner_subscribes_to_stream` | owner가 아닌 user JWT로 SSE 구독 | 403, `{"error": {"code": "FORBIDDEN"}}` | mock_redis (`owner_user_id="user_001"`), `jwt_user(sub="user_002")` | admin은 우회 허용 → 성공 |
| 30 | `test_should_allow_admin_to_subscribe_to_any_upload_stream` | admin JWT로 타인 upload SSE 구독 | 200 스트림 시작 | mock_redis, `jwt_admin` | - |
| 31 | `test_should_emit_snapshot_immediately_on_reconnect` | 진행 중인 업로드에 재접속 | 현재 snapshot 즉시 1회 전송 후 live 이벤트 append | mock_redis (진행률 50%), `jwt_user` | - |
| 32 | `test_should_send_heartbeat_comment_every_15_seconds` | 긴 업로드 구독 | 15초마다 `: heartbeat\n\n` 주석 프레임 전송 | mock_redis, `jwt_user` | 60초 무응답 시 error 이벤트 후 종료 |
| 33 | `test_should_return_404_when_upload_id_not_found` | 존재하지 않거나 TTL 만료된 upload_id | 404, `{"error": {"code": "UPLOAD_NOT_FOUND"}}` 또는 SSE `error` 이벤트 | mock_redis (빈 상태), `jwt_user` | - |
| 34 | `test_should_include_monotonic_id_line_on_every_event` | 정상 스트림 구독 | 각 이벤트 프레임에 단조 증가 `id: N` 라인 포함 (API_DESIGN §3 SSE 포맷) | mock_redis, `jwt_user` | id 중복/역전 금지 검증 |
| 35 | `test_should_emit_initial_retry_directive_on_connect` | 최초 연결 | 첫 프레임에 `retry: 3000` 디렉티브 전송 | mock_redis, `jwt_user` | - |
| 36 | `test_should_replay_events_after_last_event_id_when_reconnect_header_provided` | `Last-Event-ID: 42` 헤더로 재접속 | id > 42 이벤트부터 재전송 후 live append (API_DESIGN §3) | mock_redis (이벤트 버퍼 id 1..100), `jwt_user` | `Last-Event-ID` = 0, 미래 id(999), 비숫자 값 → snapshot fallback |
| 37 | `test_should_fallback_to_snapshot_when_last_event_id_buffer_expired` | `Last-Event-ID` 값이 버퍼 TTL 만료됨 | snapshot 즉시 전송 후 live 이벤트 append, 경고 로그 | mock_redis (버퍼 만료), `jwt_user` | - |
| 38 | `test_should_emit_error_and_close_when_no_client_ack_for_60_seconds` | 클라이언트 응답 없음 60초 경과 | `event: error` + `{"code": "STREAM_TIMEOUT"}` 송출 후 연결 종료 | mock_redis, `jwt_user` | - |

#### 3.3.4 POST /api/v1/datasets/upload/preview -- 미리보기

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 39 | `test_should_return_preview_when_valid_file` | CSV 파일 + mapping | 200, `{"data": {"columns": [...], "preview": [5건], "total_rows": 100}}` | 없음 (파싱만, Langfuse 호출 없음) | - |
| 40 | `test_should_return_max_5_items_when_file_has_many_rows` | 1000행 CSV | 200, `preview` 배열 길이 = 5 | 없음 | - |
| 41 | `test_should_show_mapped_structure_when_mapping_applied` | mapping 적용 | 200, 각 preview 아이템이 `input`, `expected_output`, `metadata` 구조 | 없음 | - |
| 42 | `test_should_return_columns_when_file_parsed` | CSV 파일 | 200, `columns` 배열에 모든 컬럼명 포함 | 없음 | - |
| 43 | `test_should_return_400_when_preview_file_unparseable` | 잘못된 형식의 파일 | 400, `FILE_PARSE_ERROR` | 없음 | - |
| 44 | `test_should_not_require_auth_at_same_level_when_preview` | user JWT | 200, 인증 성공 (viewer도 미리보기 가능해야 함) | `jwt_viewer` | 미리보기는 데이터 저장 없으므로 viewer 허용 검토 |

#### 3.3.5 DELETE /api/v1/datasets/{name} -- 삭제

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 45 | `test_should_delete_dataset_when_admin_calls` | admin JWT, `DELETE /api/v1/datasets/test-ds?project_id=proj_1` | 200, 삭제 성공 | mock_langfuse, `jwt_admin` | - |
| 46 | `test_should_return_403_when_user_deletes_dataset` | user JWT | 403 | `jwt_user` | - |
| 47 | `test_should_return_403_when_viewer_deletes_dataset` | viewer JWT | 403 | `jwt_viewer` | - |
| 48 | `test_should_return_404_when_deleting_nonexistent_dataset` | admin JWT, 존재하지 않는 데이터셋 이름 | 404, `DATASET_NOT_FOUND` | mock_langfuse, `jwt_admin` | - |
| 49 | `test_should_return_422_when_project_id_missing_on_delete` | `DELETE /api/v1/datasets/test-ds` (project_id 누락) | 422 | `jwt_admin` | - |
| 50 | `test_should_return_409_when_dataset_referenced_by_active_experiment` | admin JWT, 활성(running/paused) 실험이 참조 중인 데이터셋 삭제 | 409, `{"error": {"code": "STATE_CONFLICT"}}` | mock_langfuse, mock_redis (활성 실험이 해당 데이터셋 참조), `jwt_admin` | completed/failed/cancelled 실험만 참조 중이면 삭제 허용 |

### 3.4 모델 API

**파일**: `tests/integration/test_models_api.py`

#### 3.4.1 GET /api/v1/models -- 모델 목록

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_model_list_when_litellm_available` | `GET /api/v1/models` | 200, `{"data": {"models": [{"id": "gpt-4o", "provider": "azure", "display_name": "...", "supports_vision": true, "supports_streaming": true, "max_tokens": 128000, "cost_per_1k_input": 0.0025, "cost_per_1k_output": 0.01}]}}` | mock_litellm (모델 5개) | - |
| 2 | `test_should_return_empty_list_when_no_models_configured` | LiteLLM에 모델 0개 | 200, `{"data": {"models": []}}` | mock_litellm (빈 목록) | - |
| 3 | `test_should_return_502_when_litellm_unreachable` | LiteLLM Proxy 연결 실패 | 502, `{"error": {"code": "LLM_ERROR"}}` | mock_litellm (연결 실패) | - |
| 4 | `test_should_include_vision_support_flag_when_model_supports_it` | vision 지원 모델 포함 | 각 모델의 `supports_vision` 필드 정확 | mock_litellm | - |
| 5 | `test_should_group_models_by_provider_when_multiple_providers` | Azure, Google, Anthropic 모델 혼합 | `provider` 필드별 정확한 분류 | mock_litellm | - |

### 3.5 프로젝트 API

**파일**: `tests/integration/test_projects_api.py`

#### 3.5.1 GET /api/v1/projects -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_project_list_when_projects_configured` | `GET /api/v1/projects` | 200, `{"data": {"projects": [{"id": "proj_1", "name": "서비스A", "created_at": "..."}]}}` | PROJECTS_CONFIG fixture (2개 프로젝트) | - |
| 2 | `test_should_return_empty_list_when_no_projects` | PROJECTS_CONFIG가 비어있음 | 200, `{"data": {"projects": []}}` | 빈 PROJECTS_CONFIG | - |

#### 3.5.2 POST /api/v1/projects/switch -- 전환

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 3 | `test_should_switch_project_when_valid_project_id` | `POST` body: `{"project_id": "proj_1"}` | 200, 프로젝트 전환 성공 응답. 해당 프로젝트의 Langfuse API Key 검증 통과 | PROJECTS_CONFIG fixture, mock_langfuse | - |
| 4 | `test_should_return_404_when_project_not_found` | `POST` body: `{"project_id": "non_existent"}` | 404, `{"error": {"code": "PROJECT_NOT_FOUND"}}` | PROJECTS_CONFIG fixture | - |
| 5 | `test_should_return_422_when_project_id_missing` | `POST` body: `{}` | 422, `VALIDATION_ERROR` | 없음 | - |
| 6 | `test_should_verify_langfuse_connectivity_when_switching` | 유효한 project_id이지만 해당 프로젝트의 Langfuse 연결 실패 | 502, `LANGFUSE_ERROR` 또는 연결 실패 메시지 | mock_langfuse (연결 실패 시뮬레이션) | - |
| 7 | `test_should_be_stateless_when_project_switched` | switch 후 다른 API 호출 시 project_id 파라미터 확인 | 이후 API 호출은 project_id 파라미터로 프로젝트를 지정해야 함 (서버에 상태 저장하지 않음) | PROJECTS_CONFIG fixture | - |

### 3.6 검색 API

**파일**: `tests/integration/test_search_api.py`

#### 3.6.1 GET /api/v1/search -- 통합 검색

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_results_across_all_types_when_query_matches` | `GET /api/v1/search?project_id=proj_1&q=sentiment` | 200, `{"data": {"results": {"prompts": [...], "datasets": [...], "experiments": [...]}}}` | mock_langfuse + mock_redis ("sentiment" 키워드 매칭 데이터) | - |
| 2 | `test_should_return_empty_results_when_no_match` | `GET /api/v1/search?project_id=proj_1&q=xyznonexistent` | 200, `{"data": {"results": {"prompts": [], "datasets": [], "experiments": []}}}` | mock_langfuse + mock_redis | - |
| 3 | `test_should_filter_by_type_when_type_param_set` | `GET /api/v1/search?project_id=proj_1&q=test&type=prompt` | 200, `prompts` 배열만 결과 포함, `datasets`와 `experiments`는 빈 배열 | mock_langfuse | - |
| 4 | `test_should_filter_dataset_only_when_type_is_dataset` | `q=golden&type=dataset` | 200, `datasets`만 결과 포함 | mock_langfuse | - |
| 5 | `test_should_filter_experiment_only_when_type_is_experiment` | `q=v3-vs-v4&type=experiment` | 200, `experiments`만 결과 포함 | mock_redis | - |
| 6 | `test_should_return_422_when_query_missing` | `GET /api/v1/search?project_id=proj_1` (q 누락) | 422, `VALIDATION_ERROR` | 없음 | - |
| 7 | `test_should_return_422_when_project_id_missing` | `GET /api/v1/search?q=test` | 422 | 없음 | - |
| 8 | `test_should_return_422_when_invalid_type_param` | `type=invalid` | 422, `VALIDATION_ERROR` | 없음 | `type=PROMPT` (대문자) |
| 9 | `test_should_include_match_context_when_results_found` | 매칭 결과 존재 | 각 결과 아이템에 `match_context` 필드 포함 | mock_langfuse | - |
| 10 | `test_should_handle_special_characters_in_query_when_searched` | `q=test%20"prompt"` (특수문자, 공백, 따옴표) | 200, 에러 없이 처리 | mock_langfuse | SQL injection 시도 문자열, HTML 태그, 한글 검색어 |
| 11 | `test_should_handle_empty_query_when_q_is_empty_string` | `q=` (빈 문자열) | 422, `VALIDATION_ERROR` 또는 빈 결과 | 없음 | 공백만 포함된 쿼리 `q=%20%20` |

### 3.7 평가 함수 API 테스트

**파일**: `tests/integration/test_evaluators_api.py`

#### 3.7.1 GET /api/v1/evaluators/built-in -- 빌트인 평가 함수 목록

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_builtin_evaluator_list_when_called` | `GET /api/v1/evaluators/built-in` | 200, `{"data": {"evaluators": [...]}}` | 없음 | - |
| 2 | `test_should_return_all_13_evaluators_when_list_requested` | `GET /api/v1/evaluators/built-in` | 200, evaluators 배열 길이 = 13, 각 항목에 `name`, `description`, `parameters` 포함 | 없음 | - |

#### 3.7.2 POST /api/v1/evaluators/validate -- 커스텀 코드 검증

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 3 | `test_should_validate_custom_code_successfully_when_valid_code_provided` | `POST` body: `{"code": "def evaluate(output, expected, metadata):\n    return 1.0"}` | 200, `{"data": {"valid": true}}` | `jwt_admin` | - |
| 4 | `test_should_return_INVALID_EVALUATOR_when_syntax_error_in_code` | `POST` body: `{"code": "def evaluate(output, expected, metadata)\n    return 1.0"}` (콜론 누락) | 400, `{"error": {"code": "INVALID_EVALUATOR"}}` | `jwt_admin` | 들여쓰기 에러, 미닫힌 괄호 |
| 5 | `test_should_return_INVALID_EVALUATOR_when_no_evaluate_function` | `POST` body: `{"code": "def my_func():\n    return 1.0"}` | 400, `{"error": {"code": "INVALID_EVALUATOR", "message": "..."}}` | `jwt_admin` | 빈 코드 문자열 |
| 6 | `test_should_return_test_results_for_each_case_when_test_cases_provided` | `POST` body: `{"code": "...", "test_cases": [{"output": "a", "expected": "a"}, {"output": "b", "expected": "a"}]}` | 200, `{"data": {"valid": true, "results": [{"score": 1.0}, {"score": 0.0}]}}` | `jwt_admin` | 빈 test_cases 배열 |

### 3.8 실험 삭제 API 테스트

**파일**: `tests/integration/test_experiments_api.py`

#### 3.8.1 DELETE /api/v1/experiments/{id} -- 실험 삭제

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_delete_experiment_when_admin_and_experiment_completed` | admin JWT, `DELETE /api/v1/experiments/{id}`, 실험 상태 `completed` | 200, 삭제 성공 | mock_redis (`status=completed`), `jwt_admin` | - |
| 2 | `test_should_return_403_when_non_admin_deletes_experiment` | user JWT, `DELETE /api/v1/experiments/{id}` | 403, `{"error": {"code": "FORBIDDEN"}}` | mock_redis, `jwt_user` | viewer JWT도 403 |
| 3 | `test_should_return_409_when_deleting_running_experiment` | admin JWT, 실험 상태 `running` | 409, `{"error": {"code": "STATE_CONFLICT"}}` | mock_redis (`status=running`), `jwt_admin` | - |
| 4 | `test_should_return_409_when_deleting_paused_experiment` | admin JWT, 실험 상태 `paused` | 409, `{"error": {"code": "STATE_CONFLICT"}}` | mock_redis (`status=paused`), `jwt_admin` | - |
| 5 | `test_should_return_404_when_deleting_nonexistent_experiment` | admin JWT, 존재하지 않는 `experiment_id` | 404, `{"error": {"code": "EXPERIMENT_NOT_FOUND"}}` | mock_redis (빈 상태), `jwt_admin` | - |

### 3.9 실험 목록 조회 API 테스트

**파일**: `tests/integration/test_experiments_api.py` (§3.8과 공유 — 테스트 ID는 3.8의 1-5 이후 연속 번호 6-10 사용)

#### 3.9.1 GET /api/v1/experiments -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 6 | `test_should_return_paginated_list_when_experiments_exist` | `GET /api/v1/experiments?project_id=proj_1&page=1&page_size=10` | 200, `{"data": {"experiments": [...], "total": N, "page": 1}}` | mock_redis (실험 15개), `jwt_user` | - |
| 7 | `test_should_filter_by_status_when_status_param_provided` | `GET /api/v1/experiments?project_id=proj_1&status=running` | 200, 모든 실험의 `status`가 `"running"` | mock_redis, `jwt_user` | `status=completed`, `status=failed` |
| 8 | `test_should_return_empty_when_no_experiments_in_project` | `GET /api/v1/experiments?project_id=proj_empty` | 200, `{"data": {"experiments": [], "total": 0}}` | mock_redis (빈 상태), `jwt_user` | - |
| 9 | `test_should_remove_expired_experiments_from_list_when_lazy_cleanup_triggered` | TTL이 만료된 실험이 Sorted Set에 남아있는 경우 | 200, 만료된 실험이 목록에서 제외되고 Sorted Set에서도 제거됨 | mock_redis (만료된 키 시뮬레이션), `jwt_user` | - |
| 10 | `test_should_return_correct_total_count_when_page_requested` | `page=2&page_size=5`, 총 실험 13개 | 200, `total: 13`, `experiments` 배열 길이 = 5 | mock_redis, `jwt_user` | 마지막 페이지 (page=3) → 3개 반환 |

---

### 3.10 실험 상태 상세 조회 테스트

**파일**: `tests/integration/test_experiments_api.py` (§3.8-3.9와 공유 — 테스트 ID 11-15 연속 사용)

#### 3.10.1 GET /api/v1/experiments/{id} -- 상세 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 11 | `test_should_return_full_experiment_state_when_experiment_exists` | `GET /api/v1/experiments/{id}`, 존재하는 실험 ID | 200, `{"data": {"id": "...", "status": "running", "progress": {"completed": 5, "total": 10}, "runs": [...], "created_at": "...", "updated_at": "..."}}` | mock_redis (실험 상태 + 진행률 + run 목록), `jwt_user` | - |
| 12 | `test_should_return_404_when_experiment_id_not_found` | `GET /api/v1/experiments/{id}`, 존재하지 않는 실험 ID | 404, `{"error": {"code": "EXPERIMENT_NOT_FOUND"}}` | mock_redis (빈 상태), `jwt_user` | TTL 만료된 실험 ID |
| 13 | `test_should_return_paused_status_when_experiment_is_paused` | `GET /api/v1/experiments/{id}`, `status="paused"` 실험 | 200, `status`가 `"paused"`, `progress`에 현재까지 완료된 항목 포함 | mock_redis (`status="paused"`), `jwt_user` | - |
| 14 | `test_should_include_run_summaries_when_runs_completed` | `GET /api/v1/experiments/{id}`, 완료된 run이 있는 실험 | 200, `runs` 배열에 각 run의 `{id, status, score, started_at, completed_at}` 포함 | mock_redis (실험 + 완료된 run 3개), `jwt_user` | run이 0개인 경우 → 빈 배열 |
| 15 | `test_should_include_error_message_when_experiment_failed` | `GET /api/v1/experiments/{id}`, `status="failed"` 실험 | 200, `status`가 `"failed"`, `error` 필드에 실패 원인 메시지 포함 | mock_redis (`status="failed"`, `error="LiteLLM timeout"`), `jwt_user` | - |

---

### 3.11 실험 제어 API 통합 테스트

**파일**: `tests/integration/test_experiments_api.py` (§3.8-3.10과 공유 — 테스트 ID 16-22 연속 사용)

> Phase 4.3의 시나리오 테스트와 달리, 이 섹션은 각 제어 엔드포인트를 독립적으로 검증하는 단위 수준 API 통합 테스트이다.

#### 3.11.1 POST /api/v1/experiments/{id}/pause

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 16 | `test_should_pause_experiment_when_admin_calls_pause_endpoint` | `POST /api/v1/experiments/{id}/pause`, `jwt_admin` | 200, Redis `status = "paused"`, `updated_at` 갱신 | mock_redis (`status="running"`), `jwt_admin` | - |
| 17 | `test_should_return_403_when_viewer_calls_pause` | `POST /api/v1/experiments/{id}/pause`, `jwt_viewer` | 403, `{"error": {"code": "FORBIDDEN"}}` | mock_redis (`status="running"`), `jwt_viewer` | - |
| 18 | `test_should_return_404_when_pause_nonexistent_experiment` | `POST /api/v1/experiments/{id}/pause`, 존재하지 않는 ID | 404, `{"error": {"code": "EXPERIMENT_NOT_FOUND"}}` | mock_redis (빈 상태), `jwt_admin` | - |

#### 3.11.2 POST /api/v1/experiments/{id}/resume

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 19 | `test_should_resume_experiment_when_admin_calls_resume_endpoint` | `POST /api/v1/experiments/{id}/resume`, `jwt_admin` | 200, Redis `status = "running"`, `updated_at` 갱신 | mock_redis (`status="paused"`), `jwt_admin` | - |

#### 3.11.3 POST /api/v1/experiments/{id}/cancel

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 20 | `test_should_cancel_experiment_when_user_calls_cancel_endpoint` | `POST /api/v1/experiments/{id}/cancel`, `jwt_user` | 200, Redis `status = "cancelled"`, `completed_at` 기록, TTL 3600초로 단축 | mock_redis (`status="running"`), `jwt_user` | - |

#### 3.11.4 POST /api/v1/experiments/{id}/retry-failed

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 21 | `test_should_retry_failed_items_when_called_on_completed_experiment` | `POST /api/v1/experiments/{id}/retry-failed`, `jwt_user` | 200, Redis `status = "running"`, `failed_items`만 재실행 대상, TTL 86400초로 재설정 | mock_redis (`status="completed"`, `failed_items=3`), `jwt_user` | - |
| 22 | `test_should_return_409_when_retry_on_experiment_with_zero_failures` | `POST /api/v1/experiments/{id}/retry-failed`, `failed_items=0` | 409, `{"error": {"code": "STATE_CONFLICT", "message": "재시도할 실패 아이템이 없습니다"}}` | mock_redis (`status="completed"`, `failed_items=0`), `jwt_user` | - |

---

### 3.12 공통 에러 케이스 테스트

**파일**: `tests/integration/test_common_errors.py`

> JWT 미들웨어가 모든 요청에 적용되므로 개별 엔드포인트별 401 테스트는 생략한다. 대신 아래 대표 테스트로 미들웨어 공통 적용을 검증한다.

| # | 테스트 이름 | 기대 결과 |
|---|------------|----------|
| 1 | `test_should_return_401_for_all_protected_endpoints_when_jwt_missing` | 대표 5개 엔드포인트(prompts, datasets, models, experiments, search)에 JWT 없이 요청 → 모두 401 |
| 2 | `test_should_return_502_LANGFUSE_ERROR_when_langfuse_unreachable` | 프롬프트 목록 조회 시 Langfuse 연결 불가 → 502 `LANGFUSE_ERROR` |
| 3 | `test_should_return_502_CLICKHOUSE_ERROR_when_clickhouse_unreachable` | 분석 비교 시 ClickHouse 연결 불가 → 502 `CLICKHOUSE_ERROR` |
| 4 | `test_should_return_422_when_project_id_missing_on_protected_endpoints` | 대표 3개 엔드포인트에 `project_id` 누락 → 422 |
| 5 | `test_should_return_413_when_file_too_large_on_preview_endpoint` | preview 엔드포인트에 제한 초과 파일 업로드 → 413 |

---

### 3.13 Idempotency-Key 테스트 (API_DESIGN §3)

**파일**: `tests/integration/test_idempotency.py`

> 대상 엔드포인트: `POST /api/v1/experiments`, `POST /api/v1/tests/single`, `POST /api/v1/datasets/upload`, `POST /api/v1/datasets/from-items`, `POST /api/v1/evaluators/submissions`. 키는 `ax:idem:{user_id}:{key}` (TTL 24h)에 캐싱된다.

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_cache_response_when_first_request_with_idempotency_key` | `POST /experiments` + `Idempotency-Key: <uuid4>`, `jwt_user(sub="user_001")` | 201 생성. Redis `ax:idem:user_001:<uuid4>` Hash 생성 (request_hash, response_body, status_code, TTL=86400) | mock_redis, mock_langfuse, `jwt_user` | - |
| 2 | `test_should_return_cached_response_when_duplicate_key_and_same_body` | 동일 key + 동일 body 재요청 | 최초 응답(201 + body) 그대로 반환. 실제 생성 로직 미호출 (mock spy) | mock_redis (Hash 존재), mock_langfuse (spy) | - |
| 3 | `test_should_return_409_IDEMPOTENCY_CONFLICT_when_same_key_different_body` | 동일 key + 다른 body 재요청 | 409, `{"error": {"code": "IDEMPOTENCY_CONFLICT"}}` | mock_redis (request_hash 충돌) | body 일부만 변경(공백, 순서) 정규화 후에도 충돌 |
| 4 | `test_should_isolate_keys_per_user_when_same_key_used_by_different_users` | user_001과 user_002가 동일 key로 요청 | 각각 독립 처리 (서로 캐시 간섭 없음) | mock_redis, `jwt_user(sub="user_001")`, `jwt_user(sub="user_002")` | - |
| 5 | `test_should_accept_request_normally_when_idempotency_key_absent` | `Idempotency-Key` 헤더 누락 | 정상 처리. Redis `ax:idem:*` 키 미생성 | mock_redis, mock_langfuse | - |
| 6 | `test_should_return_400_when_idempotency_key_exceeds_128_chars` | 129자 키 | 400, `VALIDATION_ERROR` | `jwt_user` | 0자(빈 문자열), 공백 키 |
| 7 | `test_should_apply_idempotency_on_tests_single_endpoint` | `POST /tests/single` + key 중복 | 최초 LLM 응답 캐싱 후 재사용 (LLM 미호출) | mock_litellm (spy), mock_redis, `jwt_user` | 스트리밍 엔드포인트 제외 확인 |
| 8 | `test_should_apply_idempotency_on_dataset_upload_endpoint` | `POST /datasets/upload` + key 중복 | upload_id 재사용, Langfuse 재업로드 없음 | mock_langfuse (spy), mock_redis, `jwt_user` | - |
| 9 | `test_should_apply_idempotency_on_datasets_from_items_endpoint` | `POST /datasets/from-items` + key 중복 | 최초 결과 반환, 중복 생성 없음 | mock_langfuse (spy), mock_redis, `jwt_user` | - |
| 10 | `test_should_apply_idempotency_on_evaluator_submissions_endpoint` | `POST /evaluators/submissions` + key 중복 | submission_id 재사용 | mock_redis (spy), `jwt_user` | - |
| 11 | `test_should_expire_idempotency_cache_after_24h_ttl` | TTL 86400초 경과 후 동일 key 재요청 | 새 요청으로 처리 (캐시 만료) | mock_redis (TTL 만료 시뮬레이션), `jwt_user` | - |
| 12 | `test_should_not_cache_response_when_request_fails_with_5xx` | 최초 요청이 502 LLM_ERROR | 키 미캐싱 → 동일 key 재요청 시 재시도 가능 | mock_litellm (실패), mock_redis, `jwt_user` | 4xx는 캐싱 (정책 검증) |
| 13 | `test_should_cache_4xx_validation_response_when_configured` | 최초 요청이 422 VALIDATION_ERROR | 정책에 따라 캐싱, 동일 요청 시 동일 422 반환 | mock_redis, `jwt_user` | - |
| 14 | `test_should_normalize_request_hash_when_comparing_bodies` | JSON 필드 순서만 다른 동일 요청 | 동일 요청으로 간주, 캐시 히트 | mock_redis, `jwt_user` | 공백/개행만 다른 경우 |

---

### 3.14 NFR 성능 테스트 (FEATURES §12.1)

**파일**: `tests/performance/test_nfr_performance.py`

> 마커 `@pytest.mark.performance`. CI에서는 nightly 잡으로 실행. FEATURES.md §12.1의 SLA를 자동 검증한다.

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_emit_first_token_within_1500ms_p95_on_single_test_stream` | `POST /api/v1/tests/single` (streaming) 100회 반복 측정 | 첫 SSE `token` 이벤트 수신까지 p95 < 1500ms, p99 < 2500ms | mock_litellm (실제 토큰 지연 시뮬레이션 50~200ms), `jwt_user` | cold start 첫 호출 제외, 워밍업 10회 |
| 2 | `test_should_not_block_event_loop_when_streaming_first_token` | 스트리밍 중 다른 동시 요청 | 다른 요청 응답 지연 < 100ms (비동기 처리 확인) | mock_litellm, `jwt_user` | 10개 동시 스트림 |
| 3 | `test_should_complete_batch_experiment_within_10min_p95_for_300_runs` | 100 아이템 × 3 모델 배치 실험 20회 측정 | 전체 완료 시간 p95 < 600s (10분) | mock_litellm (실제 지연 시뮬레이션), mock_redis, `jwt_user` | 실패율 5% 포함 시 재시도 경로 |
| 4 | `test_should_load_comparison_page_api_within_2s_p95` | `GET /api/v1/experiments/{id}/compare` 50회 반복 | p95 < 2000ms (ClickHouse 쿼리 포함) | mock_clickhouse (현실적 지연), mock_redis, `jwt_user` | 10K row 결과 집계 |
| 5 | `test_should_execute_custom_evaluator_single_run_within_5s_p95` | 빌트인 샌드박스에서 Custom Evaluator 단건 실행 100회 | p95 < 5000ms, 타임아웃 5s 강제 | Docker sandbox fixture, `jwt_admin` | CPU 바운드 코드, 무한 루프(타임아웃 발화) |
| 6 | `test_should_timeout_and_return_408_when_custom_evaluator_exceeds_5s` | 6초 sleep 포함 evaluator | 408 또는 `EVALUATOR_TIMEOUT`, 프로세스 kill 확인 | Docker sandbox, `jwt_admin` | - |
| 7 | `test_should_queue_additional_experiments_when_concurrent_limit_exceeded` | 워크스페이스에 이미 5개 running 상태, 6번째 실험 생성 | 6번째는 `status="queued"`로 대기, 5번째 중 하나 종료 시 자동 승격 | mock_redis (active 5), `jwt_user` | - |
| 8 | `test_should_return_429_when_concurrent_experiment_quota_hard_limit` | 하드 리밋 초과 시 | 429, `{"error": {"code": "RATE_LIMITED"}}` | mock_redis, `jwt_user` | - |
| 9 | `test_should_maintain_sse_heartbeat_under_load_when_100_concurrent_streams` | 100개 동시 SSE 구독 | 모든 연결에서 15초 heartbeat 유지, 드롭률 < 1% | mock_redis, 다수 `jwt_user` | - |
| 10 | `test_should_report_performance_regression_when_baseline_exceeded` | 직전 baseline 대비 p95 지연 > +20% | 성능 리그레션 알림 (CI fail) | baseline JSON fixture | - |

---

### 3.15 Evaluator Governance 권한 매트릭스 (FEATURES §9.1)

> 상세 테스트는 `TEST_SPEC_PART2.md` Evaluator Submissions 섹션 참조. 본 문서에서는 FEATURES.md §9.1 권한 매트릭스(viewer/user/admin × 검증/제출/자기 조회/승인-반려)의 모든 셀이 TEST_SPEC_PART2에서 최소 1회 커버되는지 추적한다.

**커버리지 체크리스트**:

| 역할 | 검증 실행 | 제출 | 자기 제출 조회 | 전체 승인/반려 |
|------|---------|------|-------------|-------------|
| viewer | `test_should_return_403_when_viewer_calls_validate` | `test_should_return_403_when_viewer_submits` | `test_should_return_403_when_viewer_lists_submissions` | `test_should_return_403_when_viewer_approves` |
| user | `test_should_validate_code_when_user_calls_validate` | `test_should_create_submission_when_user_submits_code` | `test_should_allow_user_to_view_own_submission_detail` | `test_should_return_403_when_user_approves` |
| admin | (user 케이스 상속) | `test_should_auto_approve_submission_when_admin_submits` | `test_should_return_all_submissions_when_admin_lists` | `test_should_approve_submission_when_admin_approves`, `test_should_reject_submission_when_admin_rejects` |

> 각 셀에 해당하는 테스트가 TEST_SPEC_PART2에 존재하지 않으면 추가 필요. 본 테이블은 완료 체크리스트로만 사용한다.

---

### 3.16 OpenAPI 컨트랙트 테스트 (API_DESIGN 전 영역)

**파일**: `tests/contract/test_openapi_contract.py`

> FastAPI가 자동 생성하는 `/openapi.json`이 `docs/API_DESIGN.md`에 명세된 모든 엔드포인트/스키마와 일치하는지 검증한다. `schemathesis`로 fuzz 기반 컨트랙트 테스트를 수행하여 응답이 스키마와 어긋날 경우 실패시킨다. 마커 `@pytest.mark.contract`. CI에서 unit/integration과 동등하게 실행된다.

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_export_openapi_schema_with_all_documented_endpoints` | `GET /openapi.json` | `paths`에 API_DESIGN.md §4~§13 모든 경로(prompts, datasets, models, experiments, tests, search, evaluators, notifications, analytics) 포함 | `client` | API_DESIGN의 경로/메서드 목록을 fixture(`api_design_routes.json`)로 추출 후 set 비교, 누락 시 fail |
| 2 | `test_should_define_error_envelope_schema_matching_api_design_section_2` | OpenAPI components에서 `ErrorResponse` 스키마 조회 | `{error: {code, message, details?, request_id?}}` 구조 일치, `code` enum이 API_DESIGN §2.2 에러 코드 표(AUTH_REQUIRED, FORBIDDEN, NOT_FOUND, VALIDATION_ERROR, IDEMPOTENCY_CONFLICT, RATE_LIMITED, LANGFUSE_ERROR, CLICKHOUSE_ERROR, LLM_ERROR, EVALUATOR_TIMEOUT 등) 전체 포함 | `client` | 신규 코드 추가 시 enum 동기화 누락 검출 |
| 3 | `test_should_validate_all_responses_against_schema_when_schemathesis_fuzz` | `schemathesis.from_asgi("/openapi.json", app).parametrize()` 전체 엔드포인트 fuzz | 모든 응답이 자체 스키마를 만족 (status code, content-type, body shape) | `app`, mock_langfuse, mock_redis, mock_clickhouse, mock_litellm, `jwt_admin` | 5xx 응답도 ErrorResponse 스키마 준수 |
| 4 | `test_should_require_security_scheme_on_all_protected_endpoints` | OpenAPI `security` 항목 검사 | `/api/v1/health`를 제외한 모든 경로에 `bearerAuth` (JWT) 보안 요구 명시 | `client` | 신규 엔드포인트가 보안 누락 시 fail |
| 5 | `test_should_match_request_body_schema_for_create_experiment` | API_DESIGN §6.1의 `CreateExperimentRequest` JSON Schema vs OpenAPI components | 필드명/필수성/타입 완전 일치 (`prompt_name`, `prompt_version`, `dataset_name`, `models[]`, `evaluator_ids[]`, `concurrency`, `timeout_sec` 등) | API_DESIGN에서 추출한 schema fixture | 추가 필드는 backward-compatible 여부 표시 |
| 6 | `test_should_define_idempotency_key_header_on_all_documented_endpoints` | API_DESIGN §3 Idempotency 적용 엔드포인트 5종 OpenAPI parameters | `Idempotency-Key` header parameter 정의 (string, maxLength 128) | `client` | 누락 시 fail |
| 7 | `test_should_describe_pagination_params_consistently` | 목록 엔드포인트(prompts, datasets, experiments, notifications) | `cursor`, `limit`(기본 20, max 100) 파라미터 동일 정의 | `client` | - |
| 8 | `test_should_freeze_openapi_schema_via_snapshot` | `/openapi.json` 직렬화 결과 | `syrupy` 스냅샷과 일치 (의도된 변경만 PR로 갱신) | `client`, syrupy | 비결정적 필드(version, generated_at) 제거 후 비교 |
| 9 | `test_should_reject_request_when_unknown_field_in_strict_mode` | `POST /experiments`에 OpenAPI에 없는 필드 포함 | 422 `VALIDATION_ERROR` (Pydantic `extra="forbid"`) | `client`, `jwt_user` | - |
| 10 | `test_should_match_response_schema_for_sse_event_documents` | API_DESIGN §6.4 SSE 이벤트 타입(`progress`, `result`, `error`, `heartbeat`)별 JSON 페이로드 | 각 이벤트 페이로드가 components.schemas의 대응 모델을 만족 | mock SSE producer | 알 수 없는 이벤트 타입은 클라이언트가 무시하도록 명세 검증 |
| 11 | `test_should_wrap_http_exception_with_error_envelope_via_global_handler` | 임의 라우터에서 `raise HTTPException(404, detail="missing")` | 응답 body가 `{"error": {"code": "NOT_FOUND", "message": "missing", "request_id": "<uuid>"}}` 형태, `Content-Type: application/json` | `client`, `jwt_user` | `HTTPException(409)` → `STATE_CONFLICT`/`IDEMPOTENCY_CONFLICT` 매핑, `detail`이 dict인 경우도 envelope로 정규화 |
| 12 | `test_should_wrap_request_validation_error_with_error_envelope` | 잘못된 JSON body로 `POST /experiments` 호출 | 422, `{"error": {"code": "VALIDATION_ERROR", "message": ..., "details": [{"loc": [...], "msg": ..., "type": ...}], "request_id": "<uuid>"}}` | `client`, `jwt_user` | FastAPI 기본 응답 포맷이 노출되지 않아야 함 (`detail` 키 없음) |
| 13 | `test_should_wrap_unhandled_exception_with_500_error_envelope` | 라우터 내부에서 `raise RuntimeError("boom")` (테스트 전용 라우터/monkeypatch) | 500, `{"error": {"code": "INTERNAL_ERROR", "message": "Internal Server Error", "request_id": "<uuid>"}}`. 예외 traceback은 응답에 노출 금지, OBSERVABILITY 로그에만 기록 | `client`, `jwt_user`, log capture | 민감 정보 redaction 검증, `request_id`는 응답 header `X-Request-ID`와 일치 |
| 14 | `test_should_register_exception_handlers_on_app_startup` | `app.exception_handlers` 검사 | `HTTPException`, `RequestValidationError`, `Exception` 3종 핸들러 모두 등록되어 있음 | `app` | 누락 시 즉시 fail (회귀 방지) |
| 15 | `test_should_match_response_schema_for_get_evaluators_score_configs` | `GET /api/v1/evaluators/score-configs` (admin) | 200, 응답 body가 OpenAPI components의 `ScoreConfigList` 스키마와 완전 일치 (`items[].name`, `data_type`(NUMERIC/CATEGORICAL/BOOLEAN), `min`/`max`/`categories`, `description`, `created_at`), `paths`에 본 엔드포인트 등록 + `bearerAuth` 보안 요구 | `client`, `jwt_admin`, mock score_registry (NUMERIC+CATEGORICAL+BOOLEAN 각 1건 시드) | 빈 결과 → `items: []`, viewer/user → 403, NUMERIC 타입에 `categories` 필드 부재 검증, schemathesis fuzz로 임의 query에 대한 응답 스키마 일치 |
| 16 | `test_should_match_total_cost_usd_when_summing_experiment_results` | 실험 5건(각 LiteLLM usage 응답 mock) 종료 후 `GET /experiments/{id}/results` 응답의 `total_cost_usd`와 개별 result `cost_usd` 합계 비교 | `total_cost_usd == sum(results[].cost_usd)` (decimal 비교, 절대오차 ≤ 1e-9), Redis `HINCRBYFLOAT total_cost_usd` 누적값과도 일치, ClickHouse 집계 쿼리 결과와 3-way 일치 | `client`, `jwt_user`, mock_litellm (usage fixture), mock_redis, mock_clickhouse | 부동소수점 누적 오차(0.1+0.2 등 15건), 일부 result 실패(`cost_usd=null`)는 합계에서 제외, 통화 단위는 USD 고정, 음수/NaN 발생 시 즉시 fail |

---

### 3.17 Notification Inbox 테스트 (API_DESIGN §13)

**파일**: `tests/integration/test_notifications.py`

> §13 Notification Inbox는 본 시스템의 유일한 서버→사용자 알림 채널이다(외부 웹훅 없음, API_DESIGN §1 단언). Redis `ax:notification:{user_id}:*` (TTL 30일) 저장. 본 절은 inbox CRUD + 발송 트리거를 검증한다.

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_only_own_notifications_when_user_lists_inbox` | `GET /api/v1/notifications`, `jwt_user(sub="user_001")`, Redis에 user_001/user_002 알림 혼재 | 200, user_001 알림만 반환. user_002 알림 미노출 | mock_redis (사전 시드), `jwt_user` | 빈 inbox → `notifications: []`, `unread_count: 0` |
| 2 | `test_should_paginate_notifications_with_cursor_and_limit` | `?limit=20&cursor=<id>` | 최대 20건, `next_cursor` 포함 | mock_redis (50건 시드), `jwt_user` | `limit=0` → 422, `limit=101` → 422 |
| 3 | `test_should_filter_notifications_by_unread_when_query_param_set` | `?unread=true` | `read_at IS NULL` 알림만 | mock_redis, `jwt_user` | - |
| 4 | `test_should_mark_notification_read_when_patch_called` | `PATCH /api/v1/notifications/{id}/read`, 본인 알림 | 200, Redis Hash `read_at` 갱신, `unread_count` 감소 | mock_redis, `jwt_user` | 이미 읽음 상태 재호출 → 멱등(200) |
| 5 | `test_should_return_404_when_notification_id_not_found` | 존재하지 않는 id | 404 `NOT_FOUND` | mock_redis, `jwt_user` | - |
| 6 | `test_should_return_404_when_marking_other_user_notification_as_read` | user_002 알림 id를 user_001이 PATCH | 404 (정보 노출 회피, API_DESIGN §1 예외 정책) | mock_redis, `jwt_user` | admin은 동일하게 403이 아닌 404 |
| 7 | `test_should_mark_all_as_read_when_post_mark_all_read` | `POST /api/v1/notifications/mark-all-read` | 200, 본인 미읽음 전체 `read_at` 일괄 갱신, `updated_count` 반환 | mock_redis (10건 unread), `jwt_user` | 0건 → 200, `updated_count=0` |
| 8 | `test_should_return_401_when_no_jwt_on_notifications_endpoint` | JWT 없이 호출 | 401 `AUTH_REQUIRED` | 없음 | - |
| 9 | `test_should_create_notification_when_evaluator_submission_approved` | admin이 evaluator 제출 승인 (`POST /evaluators/submissions/{id}/approve`) | 제출자 inbox에 `type="evaluator_submission_approved"` 알림 1건 생성, `payload`에 submission_id 포함 | mock_redis (notification spy), `jwt_admin` | 알림 발송 실패 시 승인 트랜잭션은 성공, 경고 로그(에러 전파 금지) |
| 10 | `test_should_create_notification_when_experiment_completed` | 배치 실험 종료 이벤트 | 실험 owner inbox에 `type="experiment_completed"` 알림, `status`(success/partial_failure) 포함 | mock_redis, mock_experiment_runner | owner가 viewer 권한으로 강등된 경우에도 알림 생성 |
| 11 | `test_should_create_budget_warning_notification_when_80pct_usage_reached` | 프로젝트 비용 80% 도달 이벤트 (API_DESIGN §12) | admin들 inbox에 `type="budget_warning"` 알림 broadcast | mock_redis, `jwt_admin` | 100% 도달 시 `budget_exceeded` 별도 타입 |
| 12 | `test_should_apply_ttl_30days_when_creating_notification` | 신규 알림 생성 | Redis 키 TTL ≈ 2592000초 (±60초) | mock_redis (TTL inspect), freezegun | TTL 만료 시뮬레이션 → 자동 소멸 |
| 13 | `test_should_not_emit_outbound_webhook_when_notification_created` | 알림 생성 경로 전체 | 외부 HTTP 클라이언트(requests/httpx) 호출 0회 (API_DESIGN §1: 외부 아웃바운드 웹훅 미제공) | httpx mock spy | Slack/Telegram 모듈 import 되지 않음 검증 |
| 14 | `test_should_stream_new_notifications_via_sse_when_subscribed` | `GET /api/v1/notifications/stream` (SSE) | 새 알림 생성 즉시 `data: {...}` 이벤트 수신, heartbeat 15초 | mock_redis pubsub, `jwt_user`, freezegun (heartbeat 결정성, §3.19) | 연결 종료 시 cleanup |

---

### 3.18 감사 로그(Audit Log) 테스트 (FEATURES §11 / OBSERVABILITY)

**파일**: `tests/integration/test_audit_log.py`

> 권한 변경, 데이터 변형, 승인/반려, 삭제 등 민감 액션은 감사 로그에 기록되어야 한다. 저장소는 `audit_log` PostgreSQL 테이블(불변, append-only). 본 절은 액션별 기록 정확성과 무결성을 검증한다. 본 절의 모든 테스트는 §3.19 매트릭스에 따라 `freezegun.freeze_time`을 의무 적용한다 (`created_at` 결정성 + 체인 해시 재현). 마커 `@pytest.mark.integration`.

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_record_audit_when_admin_promotes_prompt_label` | `PATCH /prompts/{name}/versions/{v}/labels` (admin) | `audit_log` 1행 삽입: `actor_id`, `actor_role="admin"`, `action="prompt.label.promote"`, `resource_type="prompt"`, `resource_id`, `before`(이전 label), `after`(신규 label), `request_id`, `created_at`(UTC), `ip` | real_postgres (`pg_session`), `jwt_admin` | label 동일값으로 promote → 변경 없음, audit 미기록 |
| 2 | `test_should_record_audit_when_dataset_deleted` | `DELETE /datasets/{name}` (admin) | `action="dataset.delete"`, `before`에 dataset 메타 snapshot 포함 | real_postgres, `jwt_admin` | dry-run 모드에서는 미기록 |
| 3 | `test_should_record_audit_when_experiment_canceled` | `POST /experiments/{id}/cancel` | `action="experiment.cancel"`, `reason` 포함 | real_postgres, `jwt_user` (owner) | admin이 타인 실험 강제 취소 시 `actor_role="admin"`, `target_owner_id` 기록 |
| 4 | `test_should_record_audit_when_evaluator_submission_approved_or_rejected` | 승인/반려 각 1회 | 2행 삽입, `action="evaluator.submission.approve"` / `evaluator.submission.reject`, `rejection_reason` 포함 | real_postgres, `jwt_admin` | - |
| 5 | `test_should_record_audit_when_authentication_fails_with_invalid_jwt` | 만료 JWT로 보호 엔드포인트 호출 | `action="auth.failure"`, `actor_id="anonymous"`, `details.reason="jwt_expired"`, IP 기록 | real_postgres | 미들웨어에서 비동기 기록(요청 처리 차단 금지) |
| 6 | `test_should_be_append_only_when_attempting_to_update_audit_row` | DB 직접 `UPDATE audit_log SET ...` 시도 | 트리거/권한으로 거부 (`READ_ONLY_VIOLATION` 또는 권한 오류) | real_postgres (audit 전용 role) | DELETE도 동일하게 거부 |
| 7 | `test_should_compute_chain_hash_for_tamper_evidence` | 연속 3건 기록 후 각 행의 `hash` 컬럼 검증 | `hash_n = sha256(hash_{n-1} || row_payload)`. 임의 행 변조 시 체인 검증 실패 | real_postgres | 첫 행은 `hash_0 = sha256(genesis)` |
| 8 | `test_should_redact_sensitive_fields_when_recording_audit` | 프롬프트 변경 audit | `before`/`after`에 raw 프롬프트 텍스트 미포함 (해시만), API key/JWT 절대 미기록 | real_postgres | OBSERVABILITY 로그 정책과 일관 |
| 9 | `test_should_query_audit_by_actor_and_resource_when_admin_calls_endpoint` | `GET /api/v1/audit?actor_id=&resource_type=&from=&to=` (admin) | 200, 필터링된 결과, cursor 페이지네이션 | real_postgres (시드), `jwt_admin` | viewer/user → 403 |
| 10 | `test_should_return_403_when_non_admin_queries_audit_log` | user/viewer가 `GET /api/v1/audit` | 403 `FORBIDDEN` | `jwt_user`, `jwt_viewer` | - |
| 11 | `test_should_record_audit_with_request_id_for_correlation` | 임의 admin 액션 | `request_id` 컬럼이 응답 header `X-Request-ID`와 일치 | real_postgres, `jwt_admin` | OBSERVABILITY 추적 ID 일관성 |
| 12 | `test_should_record_audit_when_role_change_event_received` | 외부 Auth 서비스로부터 role 변경 webhook 수신 (있다면) | `action="user.role.change"` 기록 | real_postgres | 본 시스템이 role을 변경하지 않더라도 캐시 invalidate 시점 기록 |

---

### 3.18a 비즈니스 메트릭 발생 검증 & score_registry 부팅 등록

**파일**: `tests/unit/test_business_metrics.py`, `tests/unit/test_score_registry_bootstrap.py`

> OBSERVABILITY/EVALUATION 문서에 정의된 비즈니스 메트릭(WVPI, evaluator_approval_duration, regression_detection 등)이 실제 코드 경로에서 누락 없이 emit 되는지, 그리고 score_registry가 부팅 시 idempotent하게 등록되는지를 강제한다.

| # | 테스트 이름 | 시나리오 | 기대 결과 | 의존성 | 비고 |
|---|------------|---------|----------|--------|------|
| 1 | `test_should_emit_wvpi_metric_when_experiment_completed` | 실험 완료 콜백 호출 | `labs_wvpi{experiment_id,project_id}` Histogram observe 1회, 0~1 범위 | mock prometheus registry | WVPI = weighted value-per-input |
| 2 | `test_should_not_emit_wvpi_when_experiment_failed` | 실험 실패 종료 | WVPI 미발생, `labs_experiment_failed_total` +1 | mock registry | 실패는 별도 카운터 |
| 3 | `test_should_emit_evaluator_approval_duration_when_admin_approves` | 제출 → 승인 시간차 측정 | `labs_evaluator_approval_duration_seconds` Histogram observe, label `decision="approve"` | freeze_time, real_postgres | 거부 시 `decision="reject"` |
| 4 | `test_should_emit_regression_detection_when_baseline_diff_exceeds_threshold` | nightly baseline 비교 | `labs_regression_detected_total{metric,severity}` +1, severity ∈ {minor,major,critical} | mock baseline store | threshold 미달 시 미발생 |
| 5 | `test_should_emit_regression_detection_zero_when_within_threshold` | 차이 < threshold | 카운터 미증가, `labs_regression_check_total{result="pass"}` +1 | - | false-negative 방지 |
| 6 | `test_should_register_all_score_definitions_on_app_startup` | FastAPI lifespan 시작 | score_registry에 정의된 모든 score name이 Langfuse에 등록 (POST `/api/public/score-configs`) | mock langfuse client | 등록 호출 수 = 정의 수 |
| 7 | `test_should_be_idempotent_when_score_registry_bootstrap_runs_twice` | lifespan 2회 실행 (재시작 시뮬레이션) | 동일 score 재등록 시도 시 409/이미존재 응답을 swallow, 예외 미발생, 최종 등록 수 불변 | mock langfuse | hot reload 안전성 |
| 8 | `test_should_skip_registration_when_score_definition_unchanged` | 동일 schema hash | API 호출 생략 (캐시 적중), `labs_score_registry_skipped_total` +1 | mock | 부팅 시간 단축 |
| 9 | `test_should_update_score_definition_when_schema_hash_changed` | data_type/range 변경 | `PATCH` 호출 발생, audit log `action="score.definition.update"` 기록 | real_postgres | 마이그레이션 안전 |
| 10 | `test_should_fail_fast_when_score_registry_bootstrap_fails_critical` | Langfuse 503 응답 | lifespan 예외 전파, 앱 기동 중단, `/healthz` ready=false | mock | non-critical은 warning만 |
| 11 | `test_should_emit_business_metrics_with_required_labels` | 위 메트릭 전수 점검 | 각 메트릭이 OBSERVABILITY.md에 정의된 필수 label 키 모두 보유 | meta test (registry introspection) | label 누락 즉시 실패 |

---

### 3.19 시간 의존 테스트 (freezegun 적용 범위)

**파일**: `tests/unit/test_time_dependent.py` 외 분산 (본 절은 적용 매트릭스)

> §0.1 fixture 정책에서 `freezegun.freeze_time` 사용을 의무화했다. 본 절은 어떤 테스트들이 시간 고정을 반드시 사용해야 하는지 매트릭스로 정의하여, freezegun 누락으로 인한 flaky를 방지한다. 모든 시간 비교는 UTC 기준, 시간대 변환은 `zoneinfo`를 사용한다.

| 영역 | 대상 테스트 | 시간 고정 이유 | 사용 기법 |
|------|-----------|-------------|----------|
| JWT 만료 | `test_should_return_401_when_jwt_expired`, `test_should_accept_jwt_with_valid_exp`, `test_should_refresh_token_within_leeway_window` | `exp`/`iat`/`nbf` claim과 현재 시각 비교의 결정성 | `freeze_time("2026-04-12T00:00:00Z")`, leeway ±60s 경계 |
| Idempotency TTL | `test_should_expire_idempotency_cache_after_24h_ttl` | TTL 86400초 경과 시뮬레이션 | `freeze_time` + `tick()` 또는 `move_to(+86401s)` |
| Redis 키 TTL | Notification TTL 30일, 실험 진행률 TTL 24시간 | 만료 동작 검증 | `freeze_time(...)` + mock_redis가 시간 인지 |
| 감사 로그 `created_at` | `test_should_record_audit_when_*` 전 케이스 | `created_at` 결정성 + 체인 해시 재현 | `freeze_time` per test |
| 스케줄러/cron | nightly 잡, 성능 baseline 비교 잡 | 스케줄 발화 시점 검증 | `freeze_time` + `croniter` |
| Rate limit 윈도우 | `test_should_return_429_*`, sliding window 리셋 | 윈도우 경계 (예: 60s) 정확 검증 | `freeze_time` + `tick(seconds=N)` |
| 비용 누적 윈도우 | 일일/월간 비용 집계, budget warning 트리거 | 일/월 경계(00:00 UTC) 발화 검증 | `freeze_time("...T23:59:59Z")` → `tick(2)` |
| SSE heartbeat | 15초 heartbeat 주기 | 시간 흐름 결정성 | `freeze_time` + `asyncio` 가짜 루프 또는 `pytest-asyncio` + `time-machine` |
| Langfuse trace timestamp | trace start/end 시간 fixture | 스냅샷 안정성 | `freeze_time` per test |
| 프롬프트 버전 `created_at` 정렬 | `test_should_sort_prompt_versions_desc_by_created_at` | 정렬 결정성 | `freeze_time` 다회 `move_to` |

**규칙**:
- 위 매트릭스의 영역에 해당하는 테스트가 freezegun을 사용하지 않으면 CI 정적 검사(`tests/meta/test_freezegun_coverage.py`)가 실패시킨다. 해당 메타 테스트는 AST 파싱으로 `datetime.now()`/`time.time()` 직접 호출 + freezegun decorator 부재를 탐지한다.
- `time.sleep()` 직접 호출 금지 (테스트에서). 대기 필요 시 `freeze_time().tick()` 또는 `asyncio.sleep`을 mock으로 가속한다.
- 타임존: 모든 freeze 값은 `Z`(UTC) 표기 필수. 로컬 타임존 의존 테스트는 `monkeypatch.setenv("TZ", "UTC")` 추가.

---

## 테스트 실행 가이드

### 전체 테스트 실행

```bash
# Backend 단위 테스트
cd backend && pytest tests/unit/ -v

# Backend 통합 테스트
cd backend && pytest tests/integration/ -v

# Backend 인프라 테스트 (Docker 서비스 필요)
cd backend && pytest tests/infra/ -v -m infra

# Frontend 테스트
cd frontend && npx vitest run

# 전체 (단위 + 통합)
cd backend && pytest tests/unit/ tests/integration/ -v --tb=short
```

### 마커별 실행

```bash
# 단위 테스트만
pytest -m unit

# 통합 테스트만
pytest -m integration

# 인프라 테스트만 (느림, 실제 서비스 필요)
pytest -m infra

# 빠른 테스트만 (infra/performance 제외)
pytest -m "not infra and not slow and not performance"

# 성능 NFR 테스트 (nightly)
pytest -m performance --benchmark-only
```

### 커버리지 리포트

```bash
# Backend
cd backend && pytest --cov=app --cov-report=html --cov-report=term-missing

# Frontend
cd frontend && npx vitest run --coverage
```
