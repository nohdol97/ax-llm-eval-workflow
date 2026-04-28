"""Auto-Eval Scheduler — backend lifespan 백그라운드 worker (Phase 8-B-1).

본 모듈은 ``docs/AGENT_EVAL.md`` §9.1 명세를 그대로 구현한다.

동작:
    - 5초 주기로 ``ax:auto_eval_policies:active`` ZSet 을 ``ZRANGEBYSCORE``로 스캔
    - ``next_run_at <= now`` 인 정책을 :class:`AutoEvalEngine.run_policy` 로 실행
    - 워크스페이스당 동시 실행 한도 5 (Redis 카운터 ``ax:auto_eval:concurrency``)
    - SIGTERM 수신 시 graceful shutdown — timeout 만큼 진행 중 task 대기 후 cancel

사용법 (FastAPI lifespan):

.. code-block:: python

    scheduler = AutoEvalScheduler(repo, engine, redis)
    await scheduler.start()
    yield
    await scheduler.stop()
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.services.auto_eval_engine import AutoEvalEngine, AutoEvalEngineError
from app.services.auto_eval_repo import AutoEvalRepo
from app.services.redis_client import RedisClient

logger = get_logger(__name__)


class AutoEvalScheduler:
    """Auto-Eval polling scheduler (asyncio).

    Args:
        repo: 정책/run 영속화 (due 조회).
        engine: 정책 1회 실행 엔진.
        redis: 동시성 카운터용 Redis client.
    """

    POLL_INTERVAL_SEC: float = 5.0
    MAX_CONCURRENT: int = 5
    CONCURRENCY_KEY: str = "ax:auto_eval:concurrency"

    def __init__(
        self,
        repo: AutoEvalRepo,
        engine: AutoEvalEngine,
        redis: RedisClient | Any,
    ) -> None:
        self._repo = repo
        self._engine = engine
        self._redis = redis
        self._stop_event = asyncio.Event()
        self._running_tasks: set[asyncio.Task[None]] = set()
        self._loop_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """백그라운드 polling 시작 — idempotent."""
        if self._loop_task is not None and not self._loop_task.done():
            logger.debug("auto_eval_scheduler_already_running")
            return
        self._stop_event.clear()
        # 시작 시 카운터 정리 (안전한 fresh start)
        try:
            await self._reset_counter()
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_eval_scheduler_counter_reset_failed", error=str(exc))
        self._loop_task = asyncio.create_task(self._poll_loop())
        logger.info("auto_eval_scheduler_started")

    async def stop(self, timeout_sec: float = 30.0) -> None:
        """graceful shutdown.

        진행 중 run 을 ``timeout_sec`` 만큼 대기, 초과 시 cancel.
        """
        self._stop_event.set()
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(self._loop_task, timeout=timeout_sec)
            except TimeoutError:
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    logger.debug("auto_eval_scheduler_loop_cancelled")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("auto_eval_scheduler_loop_cancel_error", error=str(exc))
            self._loop_task = None

        # 진행 중 task 회수
        if self._running_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._running_tasks, return_exceptions=True),
                    timeout=timeout_sec,
                )
            except TimeoutError:
                for t in list(self._running_tasks):
                    if not t.done():
                        t.cancel()
        self._running_tasks.clear()
        logger.info("auto_eval_scheduler_stopped")

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    async def _poll_loop(self) -> None:
        """polling 루프 — stop 까지 ``_tick`` 반복."""
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.exception("auto_eval_scheduler_tick_failed", error=str(exc))
            try:
                # stop event 가 set 되면 즉시 깨어남 (대기 중 종료 빠름)
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.POLL_INTERVAL_SEC)
            except TimeoutError:
                continue  # 정상 — 다음 polling

    async def _tick(self) -> None:
        """단일 polling 주기 — due 정책 fetch + 실행 task spawn."""
        now = datetime.now(UTC)
        try:
            policy_ids = await self._repo.fetch_due_policies(now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_eval_scheduler_fetch_failed", error=str(exc))
            return

        for policy_id in policy_ids:
            current = await self._get_concurrency()
            if current >= self.MAX_CONCURRENT:
                logger.warning(
                    "auto_eval_concurrency_limit_reached",
                    current=current,
                    limit=self.MAX_CONCURRENT,
                )
                break

            # 카운터 선증가 — 한도 race 방지
            try:
                await self._incr_counter()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "auto_eval_concurrency_incr_failed",
                    policy_id=policy_id,
                    error=str(exc),
                )
                continue

            task = asyncio.create_task(self._run_with_concurrency(policy_id))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _run_with_concurrency(self, policy_id: str) -> None:
        """단일 정책 실행 + 카운터 감소 (예외 무관 보장)."""
        try:
            await self._engine.run_policy(policy_id)
        except AutoEvalEngineError as exc:
            logger.warning(
                "auto_eval_engine_error",
                policy_id=policy_id,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "auto_eval_scheduler_run_unexpected",
                policy_id=policy_id,
                error=str(exc),
            )
        finally:
            try:
                await self._decr_counter()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "auto_eval_concurrency_decr_failed",
                    policy_id=policy_id,
                    error=str(exc),
                )

    # ------------------------------------------------------------------ #
    # Concurrency counter (Redis)
    # ------------------------------------------------------------------ #
    async def _get_concurrency(self) -> int:
        """현재 동시 실행 카운터."""
        u = self._underlying()
        raw = await u.get(self.CONCURRENCY_KEY)
        if raw is None:
            return 0
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def _incr_counter(self) -> int:
        u = self._underlying()
        return int(await u.incr(self.CONCURRENCY_KEY))

    async def _decr_counter(self) -> int:
        u = self._underlying()
        new_val = int(await u.decr(self.CONCURRENCY_KEY))
        # 음수 보호 — 비정상 누수 시 0 으로 강제
        if new_val < 0:
            await u.set(self.CONCURRENCY_KEY, 0)
            return 0
        return new_val

    async def _reset_counter(self) -> None:
        """프로세스 재시작 시 카운터 0 으로 초기화."""
        u = self._underlying()
        await u.set(self.CONCURRENCY_KEY, 0)

    def _underlying(self) -> Any:
        """RedisClient.underlying / Mock _client 추출."""
        if hasattr(self._redis, "underlying"):
            return self._redis.underlying
        if hasattr(self._redis, "_client"):
            return self._redis._client
        return self._redis


__all__ = ["AutoEvalScheduler"]
