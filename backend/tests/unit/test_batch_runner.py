"""BatchExperimentRunner 단위 테스트.

검증 범위:
- 실험 생성 → Redis Hash 초기 상태 + Run Set + Run Hash 등록
- Run 조합 (2 prompts × 3 models = 6 runs)
- asyncio.Semaphore 동시 실행 한도
- LLM 호출 → Langfuse trace 기록
- 실패 아이템 재시도 (최대 2회)
- 실패율 >50% → 자동 paused
- 완료 시 알림 hook 호출
- SSE 이벤트 시퀀스 (progress → run_complete → experiment_complete)
- Last-Event-ID 처리
- 워크스페이스 동시 한도 → queued

본 테스트는 Mock Redis(fakeredis), Mock LiteLLM, Mock Langfuse fixture를 사용한다.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.models.experiment import (
    EvaluatorConfig,
    ExperimentCreate,
    ModelConfig,
    PromptConfig,
)
from app.services.batch_runner import (
    BatchExperimentRunner,
    _exp_events_key,
    _exp_key,
    _exp_runs_key,
    _full_key,
    _run_key,
    _underlying,
)
from app.services.context_engine import ContextEngine
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 공통 헬퍼 ----------
def _make_request(
    *,
    project_id: str = "proj-1",
    name: str = "test-experiment",
    prompt_count: int = 2,
    model_count: int = 3,
    dataset_name: str = "ds-1",
    concurrency: int = 5,
    evaluators: list[EvaluatorConfig] | None = None,
) -> ExperimentCreate:
    """테스트용 ExperimentCreate 생성 — 기본 2 prompts × 3 models = 6 runs."""
    prompts = [PromptConfig(name=f"prompt-{i + 1}", version=i + 1) for i in range(prompt_count)]
    models = [
        ModelConfig(model=f"model-{chr(ord('a') + i)}", parameters={"temperature": 0.1})
        for i in range(model_count)
    ]
    return ExperimentCreate(
        project_id=project_id,
        name=name,
        prompt_configs=prompts,
        dataset_name=dataset_name,
        model_configs=models,
        evaluators=evaluators or [],
        concurrency=concurrency,
        metadata={},
    )


def _seed_dataset(
    langfuse: MockLangfuseClient,
    *,
    name: str = "ds-1",
    item_count: int = 4,
) -> None:
    """Mock Langfuse에 데이터셋 + 아이템 시드."""
    langfuse._seed(
        prompts=[
            {"name": "prompt-1", "body": "Hello {{topic}}", "version": 1},
            {"name": "prompt-2", "body": "Tell me about {{topic}}", "version": 2},
        ],
        datasets=[
            {
                "name": name,
                "items": [
                    {
                        "input": {"topic": f"item-{i}"},
                        "expected_output": f"out-{i}",
                    }
                    for i in range(item_count)
                ],
            }
        ],
    )


@pytest.fixture
def runner(
    langfuse_client: MockLangfuseClient,
    litellm_client: MockLiteLLMProxy,
    redis_client: MockRedisClient,
) -> BatchExperimentRunner:
    """BatchExperimentRunner 인스턴스 — 매 테스트 fresh fixture."""
    return BatchExperimentRunner(
        langfuse=langfuse_client,
        litellm=litellm_client,
        redis=redis_client,
        context_engine=ContextEngine(),
    )


# ---------- 1. 실험 생성 ----------
@pytest.mark.unit
class TestCreateExperiment:
    """create_experiment — Redis 상태 초기화 + 백그라운드 실행."""

    async def test_creates_redis_hash_with_initial_status(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=2)
        # 백그라운드 태스크가 즉시 실행되지 않도록 run_experiment를 패치
        with patch.object(BatchExperimentRunner, "run_experiment", new_callable=AsyncMock):
            request = _make_request(prompt_count=2, model_count=3)
            response = await runner.create_experiment(request=request, user_id="u-1")

        assert response.experiment_id
        assert response.status == "running"
        assert response.total_runs == 6  # 2 × 3
        assert response.total_items == 12  # 6 runs × 2 items
        assert len(response.runs) == 6

        # Redis Hash 검증
        underlying = _underlying(redis_client)
        raw = await underlying.hgetall(_full_key(redis_client, _exp_key(response.experiment_id)))
        assert raw["status"] == "running"
        assert int(raw["total_runs"]) == 6
        assert int(raw["total_items"]) == 12
        assert raw["owner_user_id"] == "u-1"
        assert raw["project_id"] == "proj-1"
        assert "config" in raw
        # config는 JSON 직렬화된 ExperimentCreate
        config = json.loads(raw["config"])
        assert config["name"] == "test-experiment"

    async def test_creates_run_set_and_hashes(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=3)
        with patch.object(BatchExperimentRunner, "run_experiment", new_callable=AsyncMock):
            request = _make_request(prompt_count=2, model_count=2)
            response = await runner.create_experiment(request=request, user_id="u-1")

        underlying = _underlying(redis_client)
        runs_set = await underlying.smembers(
            _full_key(redis_client, _exp_runs_key(response.experiment_id))
        )
        assert len(runs_set) == 4  # 2 × 2

        # 각 Run Hash 존재 확인
        for run in response.runs:
            run_raw = await underlying.hgetall(
                _full_key(redis_client, _run_key(response.experiment_id, run.run_name))
            )
            assert run_raw["status"] == "running"
            assert run_raw["model"] == run.model
            assert int(run_raw["total_items"]) == 3

    async def test_run_combinations_are_correct(
        self,
        runner: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=1)
        with patch.object(BatchExperimentRunner, "run_experiment", new_callable=AsyncMock):
            request = _make_request(prompt_count=2, model_count=3)
            response = await runner.create_experiment(request=request, user_id="u-1")
        # 모든 (prompt, model) 조합 존재
        combos = {(r.prompt_name, r.model) for r in response.runs}
        expected = {
            (f"prompt-{i + 1}", f"model-{chr(ord('a') + j)}") for i in range(2) for j in range(3)
        }
        assert combos == expected

    async def test_workspace_concurrency_limit_yields_queued(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """워크스페이스 동시 한도(5) 초과 시 status=queued."""
        _seed_dataset(langfuse_client, item_count=1)
        # 카운터를 미리 5로 채워 한도 초과 상태 시뮬레이션
        # MockRedisClient는 prefix 자동 적용 없음 — 코드 호출 키와 동일하게 설정
        await redis_client.set("concurrency:experiments", "5")

        with patch.object(
            BatchExperimentRunner, "run_experiment", new_callable=AsyncMock
        ) as mocked:
            request = _make_request(prompt_count=1, model_count=1)
            response = await runner.create_experiment(request=request, user_id="u-1")

        assert response.status == "queued"
        # run_experiment는 호출되지 않아야 함
        mocked.assert_not_called()

    async def test_invalid_combinations_raise_validation(self) -> None:
        """prompt_configs 또는 model_configs가 비면 Pydantic ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                prompt_configs=[],
                dataset_name="d",
                model_configs=[ModelConfig(model="m")],
            )
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                prompt_configs=[PromptConfig(name="p")],
                dataset_name="d",
                model_configs=[],
            )

    async def test_concurrency_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                prompt_configs=[PromptConfig(name="p")],
                dataset_name="d",
                model_configs=[ModelConfig(model="m")],
                concurrency=0,
            )
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                prompt_configs=[PromptConfig(name="p")],
                dataset_name="d",
                model_configs=[ModelConfig(model="m")],
                concurrency=21,
            )


# ---------- 2. 실험 본체 실행 ----------
@pytest.mark.unit
class TestRunExperiment:
    """run_experiment — 실제 LLM 호출 + Langfuse trace + Redis 갱신."""

    async def test_runs_through_all_items_and_records_traces(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=2)
        request = _make_request(prompt_count=1, model_count=1)
        response = await runner.create_experiment(request=request, user_id="u-1")
        # 백그라운드 태스크 완료 대기
        for task in list(runner._tasks.values()):
            await task

        # LiteLLM 호출 검증 — 1 run × 2 items
        calls = litellm_client._get_calls()
        assert len(calls) == 2
        # Langfuse trace 기록 검증
        traces = langfuse_client._get_traces()
        assert len(traces) >= 2

        # 최종 상태 completed
        underlying = _underlying(redis_client)
        raw = await underlying.hgetall(_full_key(redis_client, _exp_key(response.experiment_id)))
        assert raw["status"] == "completed"
        assert int(raw["completed_items"]) == 2
        assert int(raw["failed_items"]) == 0

    async def test_failed_items_retry_and_mark_failed_after_exhausted(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=2)
        # 모든 호출에서 실패
        litellm_client.set_failure(RuntimeError("LLM down"))

        request = _make_request(prompt_count=1, model_count=1, concurrency=2)
        response = await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task

        # 재시도 횟수 검증 — 2 items × (1 + 2 retries) = 6 호출
        calls = litellm_client._get_calls()
        assert len(calls) == 6  # ITEM_RETRY_MAX_ATTEMPTS=2, +1 첫 호출

        underlying = _underlying(redis_client)
        run_set = await underlying.smembers(
            _full_key(redis_client, _exp_runs_key(response.experiment_id))
        )
        assert run_set, "Run Set이 비어 있음"
        # 실패율 100% > 50% → 자동 paused
        exp_raw = await underlying.hgetall(
            _full_key(redis_client, _exp_key(response.experiment_id))
        )
        assert exp_raw["status"] == "paused"
        assert "자동 일시정지" in exp_raw.get("error_message", "")

    async def test_concurrency_limits_concurrent_calls(
        self,
        runner: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """asyncio.Semaphore가 동시 호출 수를 제한하는지 검증."""
        _seed_dataset(langfuse_client, item_count=10)
        # 호출 시 약간의 지연 → 동시 진행 측정 가능
        litellm_client.set_latency(20)

        # 동시 호출 카운터 감시
        in_flight = 0
        peak = 0
        original_completion = litellm_client.completion

        async def tracking_completion(*args: Any, **kwargs: Any) -> Any:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                return await original_completion(*args, **kwargs)
            finally:
                in_flight -= 1

        litellm_client.completion = tracking_completion  # type: ignore[method-assign]

        request = _make_request(prompt_count=1, model_count=1, concurrency=3)
        await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task

        # peak이 concurrency=3을 초과하지 않아야 함
        assert peak <= 3
        assert peak > 0

    async def test_paused_status_skips_remaining_runs(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """status=paused이면 새 Run을 시작하지 않음."""
        _seed_dataset(langfuse_client, item_count=1)
        request = _make_request(prompt_count=2, model_count=1)
        response = await runner.create_experiment(request=request, user_id="u-1")

        # 첫 Run 시작 전에 status를 paused로 변경
        underlying = _underlying(redis_client)
        await underlying.hset(
            _full_key(redis_client, _exp_key(response.experiment_id)),
            mapping={"status": "paused"},
        )

        for task in list(runner._tasks.values()):
            await task

        raw = await underlying.hgetall(_full_key(redis_client, _exp_key(response.experiment_id)))
        assert raw["status"] == "paused"


# ---------- 3. 알림 발송 ----------
@pytest.mark.unit
class TestNotificationHooks:
    """완료/실패 시 create_notification 호출 검증."""

    async def test_notification_sent_on_complete(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=1)
        request = _make_request(prompt_count=1, model_count=1)
        await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task

        # 알림 Hash 존재 확인
        underlying = _underlying(redis_client)
        index_key = "ax:notification:u-1:index"
        ids = await underlying.zrange(index_key, 0, -1)
        assert len(ids) >= 1


# ---------- 4. SSE 이벤트 시퀀스 ----------
@pytest.mark.unit
class TestSSEStreaming:
    """stream_progress — Last-Event-ID 처리 + 이벤트 시퀀스."""

    async def test_publish_event_writes_to_sorted_set(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
    ) -> None:
        underlying = _underlying(redis_client)
        # 가짜 실험 Hash 생성 (publish_event 자체만 검증)
        await underlying.hset(
            _full_key(redis_client, _exp_key("exp-x")),
            mapping={"status": "running"},
        )
        await runner._publish_event("exp-x", "progress", {"completed": 1})

        members = await underlying.zrange(_full_key(redis_client, _exp_events_key("exp-x")), 0, -1)
        assert len(members) == 1

    async def test_stream_emits_progress_then_complete(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=1)
        request = _make_request(prompt_count=1, model_count=1)
        response = await runner.create_experiment(request=request, user_id="u-1")
        # 실행 완료 대기
        for task in list(runner._tasks.values()):
            await task

        # 스트림 → 모든 이벤트 누적 후 종료
        events: list[str] = []
        async for chunk in runner.stream_progress(
            response.experiment_id,
            last_event_id=0,
            poll_interval=0.01,
            timeout_sec=2.0,
        ):
            events.append(chunk)
            if "experiment_complete" in chunk:
                break

        joined = "".join(events)
        assert "progress" in joined or "run_complete" in joined
        assert "experiment_complete" in joined

    async def test_stream_with_last_event_id_skips_old_events(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
    ) -> None:
        """Last-Event-ID 이후 이벤트만 재전송."""
        underlying = _underlying(redis_client)
        await underlying.hset(
            _full_key(redis_client, _exp_key("exp-y")),
            mapping={"status": "completed"},
        )
        # 3개 이벤트 미리 publish
        for i in range(3):
            await runner._publish_event("exp-y", "progress", {"i": i})

        # last_event_id=2면 3번째 이벤트(id=3)만 받아야 함
        # (단, 종료 상태이므로 final 이벤트가 추가될 수 있음)
        events_received: list[str] = []
        async for chunk in runner.stream_progress(
            "exp-y", last_event_id=2, poll_interval=0.01, timeout_sec=1.0
        ):
            events_received.append(chunk)
            if len(events_received) > 3:  # safety
                break

        joined = "".join(events_received)
        # id=1, id=2 이벤트의 페이로드 ({"i": 0}, {"i": 1})는 포함되지 않아야 함
        assert '"i": 0' not in joined
        assert '"i": 1' not in joined

    async def test_stream_404_for_nonexistent_experiment(
        self, runner: BatchExperimentRunner
    ) -> None:
        events: list[str] = []
        async for chunk in runner.stream_progress(
            "nonexistent", last_event_id=0, poll_interval=0.01, timeout_sec=1.0
        ):
            events.append(chunk)
            if "EXPERIMENT_NOT_FOUND" in chunk:
                break
        assert any("EXPERIMENT_NOT_FOUND" in e for e in events)
