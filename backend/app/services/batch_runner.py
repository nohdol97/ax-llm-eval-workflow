"""배치 실험 실행 엔진(BatchExperimentRunner).

본 모듈은 본 프로젝트의 가장 복잡한 부분 — 프롬프트 N개 × 모델 M개의 조합으로 Run을
생성하고, 각 Run에서 데이터셋 아이템 K개를 ``asyncio.Semaphore``로 동시 실행하는
배치 실행 오케스트레이터다.

흐름 요약 (IMPLEMENTATION.md §1, API_DESIGN.md §4):
1. ``create_experiment``: ExperimentCreate 검증 → Redis 초기 상태 저장 → 백그라운드 실행
2. ``run_experiment``: Run 조합 생성 → 각 Run에 대해 데이터셋 아이템 순회 → LLM 호출
   → Langfuse trace 기록
3. ``stream_progress``: Redis Hash polling 기반 SSE 진행률 스트리밍

설계 원칙
---------
- 평가(Score)는 Phase 5에서 추가. 현 단계(Phase 4)는 LLM 호출 + Langfuse trace만 기록.
- 동시성: 실험 단위 ``Semaphore(concurrency)``,
  워크스페이스 단위 카운터(``ax:concurrency:experiments``).
- 재시도: 아이템 실패 시 최대 2회 재시도. 실패율 >50%면 자동 일시정지.
- PII 차단: 프롬프트/모델 출력 원본은 INFO 로그 금지 (해시/길이만 기록).
- 알림: 완료/실패 시 ``create_notification`` best-effort 호출.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.core.errors import LabsError, LangfuseError, LiteLLMError
from app.core.logging import get_logger
from app.models.experiment import (
    AUTO_PAUSE_FAILURE_RATE,
    EXPERIMENT_TTL_ACTIVE_SEC,
    EXPERIMENT_TTL_TERMINAL_SEC,
    ITEM_RETRY_MAX_ATTEMPTS,
    WORKSPACE_MAX_CONCURRENT_EXPERIMENTS,
    ExperimentCreate,
    ExperimentInitResponse,
    ExperimentStatus,
    RunInitSummary,
)
from app.services.context_engine import ContextEngine
from app.services.langfuse_client import LangfuseClient
from app.services.litellm_client import LiteLLMClient
from app.services.notification_service import create_notification
from app.services.redis_client import RedisClient
from app.services.sse import (
    SSE_HEARTBEAT_INTERVAL_SEC,
    format_retry_directive,
    format_sse_event,
    heartbeat,
)

logger = get_logger(__name__)


# ---------- Redis 키 헬퍼 ----------
def _exp_key(experiment_id: str) -> str:
    """실험 Hash 키 (prefix 미포함 — RedisClient가 자동 부착)."""
    return f"experiment:{experiment_id}"


def _exp_runs_key(experiment_id: str) -> str:
    """실험에 속한 Run 이름 Set 키."""
    return f"experiment:{experiment_id}:runs"


def _exp_events_key(experiment_id: str) -> str:
    """SSE 이벤트 큐 키 (Sorted Set, score=event_id)."""
    return f"exp_events:{experiment_id}"


def _run_key(experiment_id: str, run_name: str) -> str:
    """Run Hash 키."""
    return f"run:{experiment_id}:{run_name}"


def _run_failed_items_key(experiment_id: str, run_name: str) -> str:
    """Run의 실패 아이템 Set 키."""
    return f"run:{experiment_id}:{run_name}:failed_items"


def _project_experiments_key(project_id: str) -> str:
    """프로젝트 단위 실험 인덱스 Sorted Set 키."""
    return f"project:{project_id}:experiments"


def _concurrency_counter_key() -> str:
    """워크스페이스 단위 동시 실행 카운터 키."""
    return "concurrency:experiments"


def _exp_event_counter_key(experiment_id: str) -> str:
    """실험 이벤트 단조 증가 카운터 키."""
    return f"experiment:{experiment_id}:event_counter"


# ---------- 헬퍼 ----------
def _now_iso() -> str:
    """현재 UTC 시각 ISO 8601 (Z suffix)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    """현재 UTC 시각."""
    return datetime.now(UTC)


def _short_run_name(prompt_name: str, version: int | str | None, model: str) -> str:
    """Run 이름 생성 — `<prompt>_v<n>_<model>_<YYYYMMDD>` 형식.

    - 모델 슬래시(`openai/gpt-4o`)는 하이픈으로 정규화
    - 버전 None이면 `latest`로 표시
    """
    date = datetime.now(UTC).strftime("%Y%m%d")
    safe_model = model.replace("/", "-").replace(" ", "-")
    ver = f"v{version}" if version is not None else "vlatest"
    return f"{prompt_name}_{ver}_{safe_model}_{date}"


def _underlying(redis: Any) -> Any:
    """RedisClient.underlying 또는 MockRedisClient._client에서 raw redis 추출."""
    if hasattr(redis, "underlying"):
        return redis.underlying
    if hasattr(redis, "_client"):
        return redis._client
    return redis


def _full_key(redis: Any, key: str) -> str:
    """``ax:`` prefix 포함 전체 키 — underlying 호출용.

    RedisClient.set/get 등은 prefix를 자동 적용하지만, hset/zadd/hincrby 등은
    underlying을 직접 호출하므로 prefix를 수동으로 부착해야 한다.
    """
    if key.startswith("ax:"):
        return key
    return f"ax:{key}"


# ---------- Lua 스크립트 (상태 전이) ----------
TRANSITION_STATUS_LUA = """
-- KEYS[1] = ax:experiment:{id}
-- KEYS[2] = ax:experiment:{id}:runs
-- ARGV[1] = expected_current_status (콤마 구분)
-- ARGV[2] = new_status
-- ARGV[3] = current_timestamp (ISO 8601)
-- ARGV[4] = error_message (옵션)
-- ARGV[5] = experiment_id

if #ARGV < 3 then
    return redis.error_reply('INVALID_ARGS')
end

local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end

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

if ARGV[2] == 'completed' or ARGV[2] == 'failed' or ARGV[2] == 'cancelled' then
    redis.call('HSET', KEYS[1], 'completed_at', ARGV[3])
    if ARGV[4] and ARGV[4] ~= '' then
        redis.call('HSET', KEYS[1], 'error_message', ARGV[4])
    end
    redis.call('EXPIRE', KEYS[1], 3600)
    redis.call('EXPIRE', KEYS[2], 3600)
end

if ARGV[2] == 'running' or ARGV[2] == 'paused' then
    redis.call('EXPIRE', KEYS[1], 86400)
    redis.call('EXPIRE', KEYS[2], 86400)
end

return ARGV[2]
"""
"""상태 전이 Lua — IMPLEMENTATION.md §1.6 동일 기능 (요약본)."""


# ---------- BatchExperimentRunner ----------
class BatchExperimentRunner:
    """배치 실험 실행 엔진.

    Args:
        langfuse: Langfuse SDK 래퍼 (Trace/Generation 기록)
        litellm: LiteLLM Proxy 클라이언트 (모델 호출)
        redis: Redis 클라이언트 (상태/진행률/이벤트 큐)
        context_engine: 변수 바인딩 엔진
    """

    def __init__(
        self,
        langfuse: LangfuseClient | Any,
        litellm: LiteLLMClient | Any,
        redis: RedisClient | Any,
        context_engine: ContextEngine,
        evaluation_pipeline: Any | None = None,
        governance: Any | None = None,
    ) -> None:
        self._langfuse = langfuse
        self._litellm = litellm
        self._redis = redis
        self._context_engine = context_engine
        # Phase 5: evaluator 통합 (선택적). 미주입 시 lazy 생성.
        self._eval_pipeline = evaluation_pipeline
        self._governance = governance
        # 백그라운드 태스크 핸들 (graceful shutdown 용)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ---------- 1) 실험 생성 ----------
    async def create_experiment(
        self,
        request: ExperimentCreate,
        user_id: str,
    ) -> ExperimentInitResponse:
        """실험 생성 + Redis 초기 상태 저장 + 백그라운드 실행 시작.

        반환: ``ExperimentInitResponse`` (총 Run 수, 총 아이템 수, Run 목록).
        ``status`` 는 워크스페이스 동시 실행 한도에 따라 ``running`` 또는 ``queued``.

        Args:
            request: 검증된 ExperimentCreate
            user_id: 실험 시작자 (JWT sub)
        """
        # 0) Phase 5 — evaluator 가중치 사전 검증 (생성 시점에 1회)
        if request.evaluators:
            from app.evaluators.score_calculator import validate_weights

            try:
                validate_weights(request.evaluators)
            except ValueError as exc:
                raise LabsError(
                    detail=f"evaluator 가중치 검증 실패: {exc}"
                ) from exc

        # 1) 데이터셋 아이템 fetch (총 아이템 수 산출에 필요)
        try:
            from app.services.dataset_service import (
                list_dataset_items_via_client,
            )

            items_raw = list_dataset_items_via_client(
                self._langfuse, request.dataset_name
            )
        except Exception as exc:
            raise LabsError(
                detail=f"데이터셋 조회 실패: {request.dataset_name!r} ({exc})"
            ) from exc

        item_count = len(items_raw)

        # 2) Run 조합 생성 (prompt × model)
        runs: list[RunInitSummary] = []
        for prompt_cfg in request.prompt_configs:
            for model_cfg in request.model_configs:
                run_name = _short_run_name(
                    prompt_cfg.name, prompt_cfg.version, model_cfg.model
                )
                runs.append(
                    RunInitSummary(
                        run_name=run_name,
                        prompt_name=prompt_cfg.name,
                        prompt_version=prompt_cfg.version,
                        model=model_cfg.model,
                        status="running",
                    )
                )

        total_runs = len(runs)
        total_items = total_runs * item_count

        # 3) 워크스페이스 동시 실행 한도 검사
        active_count = await self._redis.incr(_concurrency_counter_key())
        # 한도 초과 시 즉시 decrement → queued 상태로 전이
        if active_count > WORKSPACE_MAX_CONCURRENT_EXPERIMENTS:
            await self._redis.incr(_concurrency_counter_key(), -1)
            initial_status: ExperimentStatus = "queued"
        else:
            initial_status = "running"

        # 4) Redis 초기 상태 기록
        experiment_id = str(uuid.uuid4())
        started_at = _now()
        config_snapshot = request.model_dump(mode="json")

        underlying = _underlying(self._redis)
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))
        runs_full_key = _full_key(self._redis, _exp_runs_key(experiment_id))

        await underlying.hset(
            exp_full_key,
            mapping={
                "name": request.name,
                "description": request.description or "",
                "status": initial_status,
                "config": json.dumps(config_snapshot, ensure_ascii=False),
                "total_items": total_items,
                "completed_items": 0,
                "failed_items": 0,
                "total_cost_usd": 0.0,
                "created_at": started_at.isoformat().replace("+00:00", "Z"),
                "updated_at": started_at.isoformat().replace("+00:00", "Z"),
                "started_by": user_id,
                "owner_user_id": user_id,
                "project_id": request.project_id,
                "total_runs": total_runs,
            },
        )
        await underlying.expire(exp_full_key, EXPERIMENT_TTL_ACTIVE_SEC)

        # Run 이름 Set 등록 + 각 Run Hash 초기화
        if runs:
            await underlying.sadd(
                runs_full_key, *[r.run_name for r in runs]
            )
            await underlying.expire(runs_full_key, EXPERIMENT_TTL_ACTIVE_SEC)

            for r in runs:
                run_full_key = _full_key(
                    self._redis, _run_key(experiment_id, r.run_name)
                )
                await underlying.hset(
                    run_full_key,
                    mapping={
                        "status": "running",
                        "model": r.model,
                        "prompt_name": r.prompt_name,
                        "prompt_version": (
                            str(r.prompt_version)
                            if r.prompt_version is not None
                            else "0"
                        ),
                        "completed_items": 0,
                        "failed_items": 0,
                        "total_items": item_count,
                        "total_cost_usd": 0.0,
                        "total_latency_ms": 0.0,
                        "total_score_sum": 0.0,
                        "scored_count": 0,
                    },
                )
                await underlying.expire(run_full_key, EXPERIMENT_TTL_ACTIVE_SEC)

        # 프로젝트 인덱스에 등록 (Sorted Set, score=created_at_ms)
        proj_full_key = _full_key(
            self._redis, _project_experiments_key(request.project_id)
        )
        score = started_at.timestamp()
        await underlying.zadd(proj_full_key, {experiment_id: score})

        # 5) 백그라운드 실행 시작 (queued면 시작하지 않음)
        if initial_status == "running":
            task = asyncio.create_task(
                self.run_experiment(experiment_id),
                name=f"experiment-{experiment_id}",
            )
            self._tasks[experiment_id] = task
            # 완료 시 핸들 정리
            task.add_done_callback(
                lambda t: self._tasks.pop(experiment_id, None)
            )

        logger.info(
            "experiment_created",
            extra={
                "experiment_id": experiment_id,
                "user_id": user_id,
                "project_id": request.project_id,
                "total_runs": total_runs,
                "total_items": total_items,
                "status": initial_status,
            },
        )

        return ExperimentInitResponse(
            experiment_id=experiment_id,
            status=initial_status,
            total_runs=total_runs,
            total_items=total_items,
            runs=runs,
            started_at=started_at,
        )

    # ---------- 2) 실험 본체 실행 ----------
    async def run_experiment(
        self,
        experiment_id: str,
        *,
        resume: bool = False,
    ) -> None:
        """실험 본체 — 백그라운드 태스크.

        흐름:
        1. Redis에서 config 로드
        2. 데이터셋 아이템 fetch
        3. 각 Run에 대해 ``asyncio.Semaphore(concurrency)`` 한도 내에서 아이템 순회
        4. 각 아이템: 변수 바인딩 → LLM 호출 → Langfuse trace + generation 기록
        5. 진행률 Redis 갱신 + SSE 이벤트 publish
        6. 완료 시 status=completed, 알림 best-effort 발송
        7. 실패율 >50%면 자동 paused
        """
        underlying = _underlying(self._redis)
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))

        try:
            raw = await underlying.hgetall(exp_full_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "experiment_load_failed",
                extra={"experiment_id": experiment_id, "error": str(exc)},
            )
            return

        if not raw:
            logger.warning(
                "experiment_not_found_at_run",
                extra={"experiment_id": experiment_id},
            )
            return

        config_raw = _hget_str(raw, "config")
        owner = _hget_str(raw, "owner_user_id") or _hget_str(raw, "started_by") or ""
        project_id = _hget_str(raw, "project_id") or ""
        if not config_raw:
            await self._fail_experiment(
                experiment_id, owner, "config snapshot 없음"
            )
            return

        try:
            config = json.loads(config_raw)
            request = ExperimentCreate.model_validate(config)
        except Exception as exc:  # noqa: BLE001
            await self._fail_experiment(
                experiment_id, owner, f"config 파싱 실패: {exc}"
            )
            return

        # 데이터셋 아이템 fetch
        try:
            from app.services.dataset_service import (
                list_dataset_items_via_client,
            )

            items_raw = list_dataset_items_via_client(
                self._langfuse, request.dataset_name
            )
        except Exception as exc:  # noqa: BLE001
            await self._fail_experiment(
                experiment_id, owner, f"데이터셋 조회 실패: {exc}"
            )
            return

        # Run 조합 (재구성 — Redis Set은 unordered이므로 request에서 다시 생성)
        runs: list[tuple[str, Any, Any]] = []  # (run_name, prompt_cfg, model_cfg)
        for prompt_cfg in request.prompt_configs:
            for model_cfg in request.model_configs:
                run_name = _short_run_name(
                    prompt_cfg.name, prompt_cfg.version, model_cfg.model
                )
                runs.append((run_name, prompt_cfg, model_cfg))

        # 동시성 제한
        sem = asyncio.Semaphore(request.concurrency)
        started_monotonic = time.monotonic()

        total_completed = 0
        total_failed = 0
        total_cost = 0.0

        try:
            for run_name, prompt_cfg, model_cfg in runs:
                # 일시정지/취소 체크
                status_now = await self._get_status(experiment_id)
                if status_now in ("paused", "cancelled"):
                    logger.info(
                        "experiment_run_skipped_due_to_status",
                        extra={
                            "experiment_id": experiment_id,
                            "status": status_now,
                            "run_name": run_name,
                        },
                    )
                    return

                # 프롬프트 fetch (Langfuse)
                try:
                    prompt_obj = self._langfuse.get_prompt(
                        name=prompt_cfg.name,
                        version=prompt_cfg.version,
                        label=prompt_cfg.label,
                    )
                    prompt_body = (
                        getattr(prompt_obj, "body", None)
                        or getattr(prompt_obj, "prompt", None)
                        or ""
                    )
                except (LangfuseError, Exception) as exc:  # noqa: BLE001
                    logger.warning(
                        "prompt_fetch_failed",
                        extra={
                            "experiment_id": experiment_id,
                            "run_name": run_name,
                            "error": str(exc),
                        },
                    )
                    await self._mark_run_failed(experiment_id, run_name)
                    continue

                # Run 단위 실행
                run_completed, run_failed, run_cost = await self._run_single_run(
                    experiment_id=experiment_id,
                    run_name=run_name,
                    prompt_name=prompt_cfg.name,
                    prompt_version=prompt_cfg.version,
                    prompt_body=prompt_body,
                    model=model_cfg.model,
                    parameters=model_cfg.parameters,
                    items=items_raw,
                    variable_mapping=request.dataset_variable_mapping,
                    system_prompt=request.system_prompt,
                    semaphore=sem,
                    user_id=owner,
                    project_id=project_id,
                    evaluators=request.evaluators,
                )
                total_completed += run_completed
                total_failed += run_failed
                total_cost += run_cost

                # Run 완료 마킹 + run_complete 이벤트
                await self._mark_run_completed(experiment_id, run_name)
                await self._publish_event(
                    experiment_id,
                    "run_complete",
                    {
                        "run_name": run_name,
                        "summary": await self._run_summary(
                            experiment_id, run_name
                        ),
                    },
                )

                # 실패율 >50% 자동 일시정지
                processed = run_completed + run_failed
                if (
                    processed > 0
                    and run_failed / processed > AUTO_PAUSE_FAILURE_RATE
                ):
                    await self._auto_pause(experiment_id, owner, run_name)
                    return

            # 전체 완료
            duration_sec = time.monotonic() - started_monotonic
            await self._complete_experiment(
                experiment_id=experiment_id,
                owner=owner,
                duration_sec=duration_sec,
                total_cost=total_cost,
                total_items=total_completed + total_failed,
                completed_items=total_completed,
                failed_items=total_failed,
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "experiment_run_failed",
                extra={"experiment_id": experiment_id},
            )
            await self._fail_experiment(experiment_id, owner, str(exc))
        finally:
            # 워크스페이스 동시 실행 카운터 감소
            try:
                count = await self._redis.incr(_concurrency_counter_key(), -1)
                if count < 0:
                    # 안전망: 음수 방지
                    await self._redis.set(_concurrency_counter_key(), 0)
            except Exception:  # noqa: BLE001, S110  # pragma: no cover
                pass

            # Langfuse buffer flush (best-effort)
            try:
                self._langfuse.flush()
            except Exception:  # noqa: BLE001, S110  # pragma: no cover
                pass

    # ---------- 3) 단일 Run 실행 ----------
    async def _run_single_run(
        self,
        *,
        experiment_id: str,
        run_name: str,
        prompt_name: str,
        prompt_version: int | None,
        prompt_body: Any,
        model: str,
        parameters: dict[str, Any],
        items: list[dict[str, Any]],
        variable_mapping: dict[str, str] | None,
        system_prompt: str | None,
        semaphore: asyncio.Semaphore,
        user_id: str,
        project_id: str,
        evaluators: list[Any] | None = None,
    ) -> tuple[int, int, float]:
        """단일 Run 내에서 모든 아이템을 동시 실행.

        Returns:
            (completed, failed, total_cost_usd)
        """
        completed = 0
        failed = 0
        total_cost = 0.0

        # 아이템 단위 처리 코루틴
        async def _process_item(item: dict[str, Any]) -> tuple[bool, float]:
            """단일 아이템 처리 — 성공 여부 + 비용 반환."""
            async with semaphore:
                # 일시정지 체크
                status_now = await self._get_status(experiment_id)
                if status_now in ("paused", "cancelled"):
                    return False, 0.0

                item_id = str(item.get("id") or "")
                attempt = 0
                last_exc: Exception | None = None

                while attempt < ITEM_RETRY_MAX_ATTEMPTS + 1:
                    attempt += 1
                    try:
                        cost = await self._execute_item(
                            experiment_id=experiment_id,
                            run_name=run_name,
                            prompt_name=prompt_name,
                            prompt_version=prompt_version,
                            prompt_body=prompt_body,
                            model=model,
                            parameters=parameters,
                            item=item,
                            variable_mapping=variable_mapping,
                            system_prompt=system_prompt,
                            user_id=user_id,
                            project_id=project_id,
                            evaluators=evaluators,
                        )
                        return True, cost
                    except (LiteLLMError, Exception) as exc:  # noqa: BLE001
                        last_exc = exc
                        if attempt > ITEM_RETRY_MAX_ATTEMPTS:
                            break
                        await asyncio.sleep(0.1 * attempt)

                # 재시도 모두 실패
                logger.warning(
                    "item_failed",
                    extra={
                        "experiment_id": experiment_id,
                        "run_name": run_name,
                        "item_id": item_id,
                        "attempts": attempt,
                        "error_type": type(last_exc).__name__
                        if last_exc
                        else "unknown",
                    },
                )
                # 실패 아이템 Set에 등록
                if item_id:
                    try:
                        await _underlying(self._redis).sadd(
                            _full_key(
                                self._redis,
                                _run_failed_items_key(experiment_id, run_name),
                            ),
                            item_id,
                        )
                    except Exception:  # noqa: BLE001, S110  # pragma: no cover
                        pass
                # 에러 이벤트 발행
                await self._publish_event(
                    experiment_id,
                    "error",
                    {
                        "code": type(last_exc).__name__
                        if last_exc
                        else "ItemError",
                        "message": str(last_exc) if last_exc else "unknown",
                        "item_id": item_id,
                        "run_name": run_name,
                    },
                )
                return False, 0.0

        # 모든 아이템을 동시 실행 (Semaphore가 한도 보장)
        results = await asyncio.gather(
            *[_process_item(it) for it in items],
            return_exceptions=False,
        )

        for ok, cost in results:
            if ok:
                completed += 1
                total_cost += cost
                # 진행률 progress 이벤트는 _execute_item 내부에서 발행
            else:
                failed += 1

        return completed, failed, total_cost

    # ---------- 4) 단일 아이템 실행 ----------
    async def _execute_item(
        self,
        *,
        experiment_id: str,
        run_name: str,
        prompt_name: str,
        prompt_version: int | None,
        prompt_body: Any,
        model: str,
        parameters: dict[str, Any],
        item: dict[str, Any],
        variable_mapping: dict[str, str] | None,
        system_prompt: str | None,
        user_id: str,
        project_id: str,
        evaluators: list[Any] | None = None,
    ) -> float:
        """단일 아이템 처리 — LLM 호출 + Langfuse trace 기록 + Redis 갱신.

        Returns:
            아이템 처리 비용 (USD)

        Raises:
            예외 발생 시 호출자(_run_single_run)가 retry 처리.
        """
        item_id = str(item.get("id") or "")
        item_input = item.get("input") or {}
        if not isinstance(item_input, dict):
            item_input = {"input": item_input}

        # 변수 바인딩
        compiled = self._context_engine.bind_dataset_item(
            prompt=prompt_body,
            item_input=item_input,
            variable_mapping=variable_mapping,
            strict=False,
        )

        # 메시지 구성 (text → user 메시지로, chat list 그대로)
        if isinstance(compiled, str):
            messages: list[dict[str, Any]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": compiled})
        elif isinstance(compiled, list):
            messages = [m for m in compiled if isinstance(m, dict)]
            if system_prompt and not any(
                m.get("role") == "system" for m in messages
            ):
                messages.insert(0, {"role": "system", "content": system_prompt})
        else:
            messages = [{"role": "user", "content": str(compiled)}]

        # LLM 호출
        start = time.perf_counter()
        response = await self._litellm.completion(
            model=model,
            messages=messages,
            stream=False,
            **parameters,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0

        if not isinstance(response, dict):
            raise LiteLLMError(detail="completion 응답 형식 오류 (dict 아님)")

        # 응답에서 출력/usage/비용 추출
        output_text = ""
        try:
            choices = response.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                output_text = str(msg.get("content") or "")
        except Exception:  # noqa: BLE001
            output_text = ""

        usage = response.get("usage") or {}
        cost = response.get("_litellm_cost")
        try:
            cost_value = float(cost) if cost is not None else 0.0
        except (TypeError, ValueError):
            cost_value = 0.0

        # Langfuse trace + generation 기록 (best-effort)
        trace_id: str | None = None
        try:
            trace_id = self._langfuse.create_trace(
                name=f"experiment.{run_name}",
                user_id=user_id or None,
                session_id=experiment_id,
                metadata={
                    "experiment_id": experiment_id,
                    "run_name": run_name,
                    "project_id": project_id,
                    "item_id": item_id,
                    "prompt_name": prompt_name,
                    "prompt_version": prompt_version,
                },
                tags=["labs", "experiment", run_name],
            )
            self._langfuse.create_generation(
                trace_id=trace_id,
                name="completion",
                model=model,
                input=messages,
                output=output_text,
                usage=usage,
                metadata={"latency_ms": latency_ms, "cost_usd": cost_value},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "langfuse_trace_failed",
                extra={
                    "experiment_id": experiment_id,
                    "run_name": run_name,
                    "error_type": type(exc).__name__,
                },
            )

        # Phase 5 — evaluator pipeline 호출 (있을 때만)
        weighted_score: float | None = None
        if evaluators:
            try:
                expected = item.get("expected_output")
                scores = await self._evaluate_item(
                    evaluators=evaluators,
                    output=output_text,
                    expected=expected,
                    metadata={
                        "latency_ms": latency_ms,
                        "cost_usd": cost_value,
                        "item_id": item_id,
                        "model": model,
                    },
                    trace_id=trace_id,
                )
                # weighted_score를 Run 통계에 반영
                ws = scores.get("weighted_score")
                if isinstance(ws, (int, float)):
                    weighted_score = float(ws)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "evaluator_run_failed",
                    extra={
                        "experiment_id": experiment_id,
                        "run_name": run_name,
                        "error": str(exc),
                    },
                )

        # Redis 진행률 갱신
        underlying = _underlying(self._redis)
        run_full_key = _full_key(self._redis, _run_key(experiment_id, run_name))
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))

        try:
            pipe = underlying.pipeline()
            pipe.hincrby(run_full_key, "completed_items", 1)
            pipe.hincrbyfloat(run_full_key, "total_cost_usd", cost_value)
            pipe.hincrbyfloat(run_full_key, "total_latency_ms", latency_ms)
            pipe.hincrby(exp_full_key, "completed_items", 1)
            pipe.hincrbyfloat(exp_full_key, "total_cost_usd", cost_value)
            if weighted_score is not None:
                pipe.hincrbyfloat(
                    run_full_key, "total_score_sum", float(weighted_score)
                )
                pipe.hincrby(run_full_key, "scored_count", 1)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "progress_update_failed",
                extra={
                    "experiment_id": experiment_id,
                    "run_name": run_name,
                    "error": str(exc),
                },
            )

        # progress 이벤트 발행
        try:
            current = await underlying.hgetall(run_full_key)
            comp = int(_hget_str(current, "completed_items") or "0")
            failed_n = int(_hget_str(current, "failed_items") or "0")
            total = int(_hget_str(current, "total_items") or "0")
            await self._publish_event(
                experiment_id,
                "progress",
                {
                    "run_name": run_name,
                    "completed": comp,
                    "failed": failed_n,
                    "total": total,
                    "current_item": {
                        "id": item_id,
                        "status": "completed",
                        "latency_ms": latency_ms,
                    },
                },
            )
        except Exception:  # noqa: BLE001, S110  # pragma: no cover
            pass

        return cost_value

    # ---------- 4.5) Evaluator pipeline (Phase 5) ----------
    async def _evaluate_item(
        self,
        *,
        evaluators: list[Any],
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, float | None]:
        """``EvaluationPipeline.evaluate_item`` 호출. 미설정 시 lazy 생성."""
        pipeline = self._eval_pipeline
        if pipeline is None:
            from app.evaluators.pipeline import EvaluationPipeline

            pipeline = EvaluationPipeline(
                langfuse=self._langfuse,
                litellm_client=self._litellm,
            )
            self._eval_pipeline = pipeline

        result = await pipeline.evaluate_item(
            evaluators=evaluators,
            output=output,
            expected=expected,
            metadata=metadata,
            trace_id=trace_id,
        )
        return result  # type: ignore[no-any-return]

    # ---------- 5) Redis 헬퍼 ----------
    async def _get_status(self, experiment_id: str) -> str:
        """현재 실험 상태 조회 (없으면 빈 문자열)."""
        underlying = _underlying(self._redis)
        try:
            value = await underlying.hget(
                _full_key(self._redis, _exp_key(experiment_id)), "status"
            )
        except Exception:  # noqa: BLE001
            return ""
        if value is None:
            return ""
        return value if isinstance(value, str) else value.decode("utf-8")

    async def _mark_run_failed(self, experiment_id: str, run_name: str) -> None:
        """Run 단위 status=failed."""
        underlying = _underlying(self._redis)
        try:
            await underlying.hset(
                _full_key(self._redis, _run_key(experiment_id, run_name)),
                mapping={"status": "failed"},
            )
        except Exception:  # noqa: BLE001, S110  # pragma: no cover
            pass

    async def _mark_run_completed(self, experiment_id: str, run_name: str) -> None:
        """Run 단위 status=completed."""
        underlying = _underlying(self._redis)
        try:
            await underlying.hset(
                _full_key(self._redis, _run_key(experiment_id, run_name)),
                mapping={"status": "completed"},
            )
        except Exception:  # noqa: BLE001, S110  # pragma: no cover
            pass

    async def _run_summary(
        self, experiment_id: str, run_name: str
    ) -> dict[str, Any]:
        """Run 요약 dict — run_complete 이벤트 페이로드용."""
        underlying = _underlying(self._redis)
        try:
            raw = await underlying.hgetall(
                _full_key(self._redis, _run_key(experiment_id, run_name))
            )
        except Exception:  # noqa: BLE001
            return {}
        comp = int(_hget_str(raw, "completed_items") or "0")
        latency_sum = float(_hget_str(raw, "total_latency_ms") or "0")
        score_sum = float(_hget_str(raw, "total_score_sum") or "0")
        scored = int(_hget_str(raw, "scored_count") or "0")
        cost = float(_hget_str(raw, "total_cost_usd") or "0")
        avg_latency = (latency_sum / comp) if comp > 0 else None
        avg_score = (score_sum / scored) if scored > 0 else None
        return {
            "items_completed": comp,
            "total_cost": cost,
            "avg_latency_ms": avg_latency,
            "avg_score": avg_score,
        }

    async def _publish_event(
        self,
        experiment_id: str,
        event: str,
        data: dict[str, Any],
    ) -> int:
        """SSE 이벤트를 Redis Sorted Set에 추가 + counter 증가.

        Returns:
            이벤트 ID (단조 증가)
        """
        underlying = _underlying(self._redis)
        try:
            event_id = await self._redis.incr(_exp_event_counter_key(experiment_id))
            payload = json.dumps(
                {"event": event, "data": data, "id": event_id},
                ensure_ascii=False,
                default=str,
            )
            await underlying.zadd(
                _full_key(self._redis, _exp_events_key(experiment_id)),
                {f"{event_id}:{payload}": float(event_id)},
            )
            await underlying.expire(
                _full_key(self._redis, _exp_events_key(experiment_id)),
                EXPERIMENT_TTL_ACTIVE_SEC,
            )
            await underlying.expire(
                _full_key(self._redis, _exp_event_counter_key(experiment_id)),
                EXPERIMENT_TTL_ACTIVE_SEC,
            )
            return int(event_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "publish_event_failed",
                extra={
                    "experiment_id": experiment_id,
                    "event": event,
                    "error": str(exc),
                },
            )
            return -1

    # ---------- 6) 종료 처리 ----------
    async def _complete_experiment(
        self,
        *,
        experiment_id: str,
        owner: str,
        duration_sec: float,
        total_cost: float,
        total_items: int,
        completed_items: int,
        failed_items: int,
    ) -> None:
        """실험 정상 종료."""
        underlying = _underlying(self._redis)
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))
        completed_at = _now_iso()
        try:
            await underlying.hset(
                exp_full_key,
                mapping={
                    "status": "completed",
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                    "total_duration_sec": duration_sec,
                },
            )
            await underlying.expire(exp_full_key, EXPERIMENT_TTL_TERMINAL_SEC)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "experiment_complete_update_failed",
                extra={"experiment_id": experiment_id, "error": str(exc)},
            )

        await self._publish_event(
            experiment_id,
            "experiment_complete",
            {
                "experiment_id": experiment_id,
                "total_duration_sec": duration_sec,
                "total_cost_usd": total_cost,
                "total_items": total_items,
                "completed_items": completed_items,
                "failed_items": failed_items,
            },
        )

        # 알림 best-effort
        if owner:
            try:
                await create_notification(
                    user_id=owner,
                    type_="experiment_complete",
                    title="실험 완료",
                    body=f"실험이 완료되었습니다 ({completed_items}/{total_items}).",
                    link=f"/experiments/{experiment_id}",
                    redis=self._redis,
                    resource_id=experiment_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "notification_create_failed_on_complete",
                    extra={"experiment_id": experiment_id, "error": str(exc)},
                )

    async def _fail_experiment(
        self, experiment_id: str, owner: str, error_message: str
    ) -> None:
        """실험 인프라 레벨 실패 — status=failed."""
        underlying = _underlying(self._redis)
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))
        completed_at = _now_iso()
        try:
            await underlying.hset(
                exp_full_key,
                mapping={
                    "status": "failed",
                    "error_message": error_message[:500],
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                },
            )
            await underlying.expire(exp_full_key, EXPERIMENT_TTL_TERMINAL_SEC)
        except Exception:  # noqa: BLE001, S110  # pragma: no cover
            pass

        await self._publish_event(
            experiment_id,
            "error",
            {"code": "EXPERIMENT_FAILED", "message": error_message[:500]},
        )

        if owner:
            try:
                await create_notification(
                    user_id=owner,
                    type_="experiment_failed",
                    title="실험 실패",
                    body=error_message[:200],
                    link=f"/experiments/{experiment_id}",
                    redis=self._redis,
                    resource_id=experiment_id,
                )
            except Exception:  # noqa: BLE001, S110  # pragma: no cover
                pass

    async def _auto_pause(
        self, experiment_id: str, owner: str, run_name: str
    ) -> None:
        """실패율 >50% 자동 일시정지."""
        underlying = _underlying(self._redis)
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))
        try:
            await underlying.hset(
                exp_full_key,
                mapping={
                    "status": "paused",
                    "updated_at": _now_iso(),
                    "error_message": f"자동 일시정지: {run_name} 실패율 >50%",
                },
            )
        except Exception:  # noqa: BLE001, S110  # pragma: no cover
            pass

        await self._publish_event(
            experiment_id,
            "error",
            {
                "code": "AUTO_PAUSED_HIGH_FAILURE_RATE",
                "message": f"{run_name} 실패율이 50%를 초과하여 일시정지되었습니다.",
                "run_name": run_name,
            },
        )

        if owner:
            try:
                await create_notification(
                    user_id=owner,
                    type_="experiment_failed",
                    title="실험 자동 일시정지",
                    body=f"실패율이 50%를 초과하여 일시정지되었습니다: {run_name}",
                    link=f"/experiments/{experiment_id}",
                    redis=self._redis,
                    resource_id=f"{experiment_id}:auto_pause",
                )
            except Exception:  # noqa: BLE001, S110  # pragma: no cover
                pass

    # ---------- 7) SSE 스트리밍 ----------
    async def stream_progress(
        self,
        experiment_id: str,
        last_event_id: int | None = None,
        *,
        poll_interval: float = 0.1,
        timeout_sec: float = 1800.0,
    ) -> AsyncIterator[str]:
        """SSE 진행률 스트리밍.

        Redis Sorted Set ``ax:exp_events:{id}``에서 ``last_event_id`` 이후의
        이벤트를 polling 기반으로 가져온다 (15초 간격 heartbeat 포함).

        이벤트 종류 (API_DESIGN.md §4.2):
        - ``progress``: 아이템 처리 진행
        - ``run_complete``: 단일 Run 완료
        - ``experiment_complete``: 실험 정상 종료
        - ``error``: 아이템/실험 레벨 에러

        ``Last-Event-ID`` 처리: 해당 id 이후 이벤트만 재전송.
        """
        yield format_retry_directive()

        underlying = _underlying(self._redis)
        events_full_key = _full_key(self._redis, _exp_events_key(experiment_id))
        exp_full_key = _full_key(self._redis, _exp_key(experiment_id))

        cursor = float(last_event_id or 0)
        last_emit_at = time.monotonic()
        started_at = time.monotonic()

        # 실험 존재 확인
        exists = await underlying.exists(exp_full_key)
        if not exists:
            yield format_sse_event(
                "error",
                {
                    "code": "EXPERIMENT_NOT_FOUND",
                    "message": f"experiment_id={experiment_id} not found",
                },
                event_id=int(cursor) + 1,
            )
            return

        while True:
            if time.monotonic() - started_at > timeout_sec:
                yield format_sse_event(
                    "error",
                    {"code": "STREAM_TIMEOUT", "message": "stream timed out"},
                    event_id=int(cursor) + 1,
                )
                return

            # cursor 이후의 이벤트 fetch
            try:
                raw_events = await underlying.zrangebyscore(
                    events_full_key,
                    min=f"({cursor}",  # exclusive
                    max="+inf",
                )
            except Exception:  # noqa: BLE001
                raw_events = []

            terminal_event_seen = False
            for raw in raw_events:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                # member 형식: "{event_id}:{json_payload}"
                _idx, _, payload = raw.partition(":")
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                event = str(parsed.get("event") or "message")
                data = parsed.get("data") or {}
                event_id = int(parsed.get("id") or _idx or 0)
                yield format_sse_event(event, data, event_id=event_id)
                cursor = float(event_id)
                last_emit_at = time.monotonic()
                if event in ("experiment_complete",):
                    terminal_event_seen = True

            if terminal_event_seen:
                return

            # 종료 상태이면서 추가 이벤트 없음 → 종료
            current_status = await self._get_status(experiment_id)
            if current_status in ("completed", "failed", "cancelled"):
                # 종료 이벤트가 누락된 경우 final 메시지 송출 후 종료
                yield format_sse_event(
                    "experiment_complete",
                    {
                        "experiment_id": experiment_id,
                        "status": current_status,
                    },
                    event_id=int(cursor) + 1,
                )
                return

            # heartbeat
            if time.monotonic() - last_emit_at >= SSE_HEARTBEAT_INTERVAL_SEC:
                yield heartbeat()
                last_emit_at = time.monotonic()

            await asyncio.sleep(poll_interval)


# ---------- Hash 직렬화 헬퍼 ----------
def _hget_str(raw: dict[Any, Any], key: str) -> str:
    """Redis Hash → str 변환 (bytes/str 모두 수용)."""
    if not isinstance(raw, dict):
        return ""
    value = raw.get(key)
    if value is None and isinstance(key, str):
        value = raw.get(key.encode("utf-8"))
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:  # noqa: BLE001
            return ""
    return str(value)
