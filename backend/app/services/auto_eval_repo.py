"""AutoEvalPolicy / AutoEvalRun Redis 영속화 (Phase 8-B-1).

본 모듈은 ``docs/AGENT_EVAL.md`` §8.2 Redis 키 스키마 명세를 그대로 구현한다.

Redis 키:
    ax:auto_eval_policy:{id}                  Hash + JSON payload
    ax:auto_eval_policies:active              Sorted Set (score=next_run_at_ts)
    ax:auto_eval_policies:by_project:{pid}    Sorted Set (score=created_at_ts)
    ax:auto_eval_run:{id}                     Hash + JSON payload
    ax:auto_eval_runs:by_policy:{pid}         Sorted Set (score=started_at_ts)
    ax:auto_eval_cost:{policy_id}:{date}      Float counter (TTL 48h)

주요 동작:
    - 정책 CRUD + ``next_run_at`` 기반 schedule index 관리
    - run 영속화 + 정책별 시계열 조회
    - ``record_cost`` / ``get_daily_cost`` — 일일 비용 추적
    - ``fetch_due_policies`` — scheduler가 polling 시 호출 (ZRANGEBYSCORE)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.models.auto_eval import (
    AutoEvalPolicy,
    AutoEvalPolicyCreate,
    AutoEvalPolicyUpdate,
    AutoEvalRun,
    AutoEvalRunStatus,
    AutoEvalSchedule,
    PolicyStatus,
)
from app.services.redis_client import RedisClient

logger = get_logger(__name__)


class AutoEvalPolicyNotFoundError(LabsError):
    """정책 미존재."""

    code = "auto_eval_policy_not_found"
    status_code = 404
    title = "Auto-Eval policy not found"


class AutoEvalRunNotFoundError(LabsError):
    """run 미존재."""

    code = "auto_eval_run_not_found"
    status_code = 404
    title = "Auto-Eval run not found"


def _get_underlying(redis: RedisClient | Any) -> Any:
    """``RedisClient.underlying`` 또는 mock의 ``_client`` 반환.

    ZSet/Hash 등 prefix 자동 부착이 없는 경로는 underlying 인스턴스를 사용해야
    키 충돌 없이 동작한다 (Phase 2 notification_service 동일 패턴).
    """
    if hasattr(redis, "underlying"):
        return redis.underlying
    if hasattr(redis, "_client"):
        return redis._client
    return redis


def _to_datetime(value: Any) -> datetime | None:
    """ISO 문자열 또는 datetime → UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _serialize_dt(value: datetime | None) -> str | None:
    """datetime → ISO Z 문자열."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class AutoEvalRepo:
    """AutoEvalPolicy + AutoEvalRun 영속화."""

    KEY_POLICY = "ax:auto_eval_policy:{id}"
    KEY_POLICIES_ACTIVE = "ax:auto_eval_policies:active"
    KEY_POLICIES_BY_PROJECT = "ax:auto_eval_policies:by_project:{project_id}"
    KEY_RUN = "ax:auto_eval_run:{id}"
    KEY_RUNS_BY_POLICY = "ax:auto_eval_runs:by_policy:{policy_id}"
    KEY_COST_DAILY = "ax:auto_eval_cost:{policy_id}:{date}"

    POLICY_TTL_SEC: int | None = None  # active 정책 영속
    RUN_TTL_SEC: int = 90 * 86400  # 90일
    COST_TTL_SEC: int = 48 * 3600  # 48시간

    def __init__(self, redis: RedisClient | Any) -> None:
        self._redis = redis
        self._u = _get_underlying(redis)

    # ------------------------------------------------------------------ #
    # 직렬화
    # ------------------------------------------------------------------ #
    @staticmethod
    def _policy_to_payload(policy: AutoEvalPolicy) -> str:
        """정책 → JSON 문자열 (Hash 단일 필드 ``data`` 저장용)."""
        return policy.model_dump_json()

    @staticmethod
    def _payload_to_policy(payload: str | bytes | None) -> AutoEvalPolicy | None:
        """JSON 문자열 → 정책 객체."""
        if not payload:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            return AutoEvalPolicy.model_validate(data)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _run_to_payload(run: AutoEvalRun) -> str:
        return run.model_dump_json()

    @staticmethod
    def _payload_to_run(payload: str | bytes | None) -> AutoEvalRun | None:
        if not payload:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            return AutoEvalRun.model_validate(data)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # 키 헬퍼
    # ------------------------------------------------------------------ #
    @classmethod
    def _policy_key(cls, policy_id: str) -> str:
        return cls.KEY_POLICY.format(id=policy_id)

    @classmethod
    def _policies_by_project_key(cls, project_id: str) -> str:
        return cls.KEY_POLICIES_BY_PROJECT.format(project_id=project_id)

    @classmethod
    def _run_key(cls, run_id: str) -> str:
        return cls.KEY_RUN.format(id=run_id)

    @classmethod
    def _runs_by_policy_key(cls, policy_id: str) -> str:
        return cls.KEY_RUNS_BY_POLICY.format(policy_id=policy_id)

    @classmethod
    def _cost_key(cls, policy_id: str, day: date) -> str:
        return cls.KEY_COST_DAILY.format(policy_id=policy_id, date=day.isoformat())

    # ------------------------------------------------------------------ #
    # 내부 — 저장
    # ------------------------------------------------------------------ #
    async def _save_policy(self, policy: AutoEvalPolicy) -> None:
        """정책 본체 + 모든 인덱스 갱신.

        - status=active 이고 next_run_at 이 있으면 active ZSet 등록
        - 그 외는 active ZSet 에서 제거
        - by_project ZSet 은 항상 등록 (created_at score)
        """
        policy_key = self._policy_key(policy.id)
        active_key = self.KEY_POLICIES_ACTIVE
        project_key = self._policies_by_project_key(policy.project_id)

        await self._u.set(policy_key, self._policy_to_payload(policy))

        # active ZSet
        if policy.status == "active" and policy.next_run_at is not None:
            score = policy.next_run_at.timestamp()
            await self._u.zadd(active_key, {policy.id: score})
        else:
            await self._u.zrem(active_key, policy.id)

        # by_project ZSet — 모든 상태 포함
        created_score = policy.created_at.timestamp()
        await self._u.zadd(project_key, {policy.id: created_score})

    # ------------------------------------------------------------------ #
    # Policy CRUD
    # ------------------------------------------------------------------ #
    async def create_policy(
        self,
        payload: AutoEvalPolicyCreate,
        owner: str,
        *,
        now: datetime | None = None,
    ) -> AutoEvalPolicy:
        """정책 생성. ID는 ``policy_<uuid12>`` 자동 발급, next_run_at 자동 계산."""
        ts = now or datetime.now(UTC)
        policy_id = f"policy_{uuid.uuid4().hex[:12]}"

        next_run = (
            self._compute_next_run(payload.schedule, ts) if payload.status == "active" else None
        )

        policy = AutoEvalPolicy(
            id=policy_id,
            name=payload.name,
            description=payload.description,
            project_id=payload.project_id,
            trace_filter=payload.trace_filter,
            expected_dataset_name=payload.expected_dataset_name,
            evaluators=payload.evaluators,
            schedule=payload.schedule,
            alert_thresholds=payload.alert_thresholds,
            notification_targets=payload.notification_targets,
            daily_cost_limit_usd=payload.daily_cost_limit_usd,
            status=payload.status,
            owner=owner,
            created_at=ts,
            updated_at=ts,
            next_run_at=next_run,
        )
        await self._save_policy(policy)
        logger.info(
            "auto_eval_policy_created",
            policy_id=policy_id,
            project_id=payload.project_id,
            owner=owner,
            schedule_type=payload.schedule.type,
        )
        return policy

    async def get_policy(self, policy_id: str) -> AutoEvalPolicy:
        """정책 조회. 미존재 시 :class:`AutoEvalPolicyNotFoundError`."""
        raw = await self._u.get(self._policy_key(policy_id))
        policy = self._payload_to_policy(raw)
        if policy is None:
            raise AutoEvalPolicyNotFoundError(detail=f"정책을 찾을 수 없습니다: id={policy_id!r}")
        return policy

    async def list_policies(
        self,
        project_id: str | None = None,
        status: PolicyStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AutoEvalPolicy], int]:
        """정책 목록 — project_id/status 필터, 페이지네이션 (created_at desc)."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))

        if project_id:
            ids_raw = await self._u.zrevrange(self._policies_by_project_key(project_id), 0, -1)
        else:
            # 전체 — scan_iter 로 모든 policy 키
            ids_raw = []
            async for key in self._u.scan_iter(match="ax:auto_eval_policy:*"):
                k = key.decode("utf-8") if isinstance(key, bytes) else key
                # ax:auto_eval_policy:<id>
                ids_raw.append(k.split(":", 2)[2])

        ids: list[str] = [
            (rid.decode("utf-8") if isinstance(rid, bytes) else str(rid)) for rid in ids_raw
        ]

        policies: list[AutoEvalPolicy] = []
        for pid in ids:
            raw = await self._u.get(self._policy_key(pid))
            policy = self._payload_to_policy(raw)
            if policy is None:
                continue
            if status is not None and policy.status != status:
                continue
            policies.append(policy)

        # project_id 가 None 이면 created_at desc 정렬
        if not project_id:
            policies.sort(key=lambda p: p.created_at, reverse=True)

        total = len(policies)
        start = (page - 1) * page_size
        end = start + page_size
        return policies[start:end], total

    async def update_policy(
        self,
        policy_id: str,
        updates: AutoEvalPolicyUpdate,
        *,
        now: datetime | None = None,
    ) -> AutoEvalPolicy:
        """정책 수정. schedule 변경 시 next_run_at 재계산 + 인덱스 갱신."""
        current = await self.get_policy(policy_id)
        ts = now or datetime.now(UTC)

        data = current.model_dump()
        update_dict = updates.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            data[field] = value
        data["updated_at"] = ts

        # schedule 또는 status 변경 시 next_run_at 재계산
        schedule_changed = "schedule" in update_dict
        status_changed = "status" in update_dict

        if schedule_changed or status_changed:
            new_status = data.get("status", current.status)
            new_schedule_data = data.get("schedule")
            if new_status == "active" and new_schedule_data is not None:
                # dict 면 모델로 복원
                if isinstance(new_schedule_data, dict):
                    schedule_obj = AutoEvalSchedule.model_validate(new_schedule_data)
                else:
                    schedule_obj = new_schedule_data
                data["next_run_at"] = self._compute_next_run(schedule_obj, ts)
            else:
                data["next_run_at"] = None

        updated = AutoEvalPolicy.model_validate(data)
        await self._save_policy(updated)
        logger.info(
            "auto_eval_policy_updated",
            policy_id=policy_id,
            fields=list(update_dict.keys()),
        )
        return updated

    async def delete_policy(self, policy_id: str) -> None:
        """정책 삭제 + 모든 인덱스 정리.

        runs 도 동일 정책 ID 기반 ZSet 인덱스를 정리하나 run 본체는 TTL로 자연 만료.
        """
        policy = await self.get_policy(policy_id)
        pipe = self._u.pipeline()
        pipe.delete(self._policy_key(policy_id))
        pipe.zrem(self.KEY_POLICIES_ACTIVE, policy_id)
        pipe.zrem(self._policies_by_project_key(policy.project_id), policy_id)
        pipe.delete(self._runs_by_policy_key(policy_id))
        await pipe.execute()
        logger.info("auto_eval_policy_deleted", policy_id=policy_id)

    async def pause_policy(self, policy_id: str) -> AutoEvalPolicy:
        """status=paused + active ZSet 제거."""
        return await self.update_policy(policy_id, AutoEvalPolicyUpdate(status="paused"))

    async def resume_policy(self, policy_id: str) -> AutoEvalPolicy:
        """status=active + next_run_at 재계산."""
        return await self.update_policy(policy_id, AutoEvalPolicyUpdate(status="active"))

    # ------------------------------------------------------------------ #
    # Run CRUD
    # ------------------------------------------------------------------ #
    async def create_run(self, run: AutoEvalRun) -> str:
        """run 본체 + by_policy ZSet 등록 + TTL."""
        run_key = self._run_key(run.id)
        index_key = self._runs_by_policy_key(run.policy_id)

        pipe = self._u.pipeline()
        pipe.set(run_key, self._run_to_payload(run), ex=self.RUN_TTL_SEC)
        pipe.zadd(index_key, {run.id: run.started_at.timestamp()})
        pipe.expire(index_key, self.RUN_TTL_SEC)
        await pipe.execute()
        return run.id

    async def get_run(self, run_id: str) -> AutoEvalRun:
        """run 조회. 미존재 시 :class:`AutoEvalRunNotFoundError`."""
        raw = await self._u.get(self._run_key(run_id))
        run = self._payload_to_run(raw)
        if run is None:
            raise AutoEvalRunNotFoundError(detail=f"실행 기록을 찾을 수 없습니다: id={run_id!r}")
        return run

    async def update_run(self, run: AutoEvalRun) -> None:
        """run 본체 갱신 (TTL 보존)."""
        await self._u.set(self._run_key(run.id), self._run_to_payload(run), ex=self.RUN_TTL_SEC)

    async def list_runs(
        self,
        policy_id: str,
        status: AutoEvalRunStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AutoEvalRun], int]:
        """정책별 run 목록 (started_at desc, 페이지네이션)."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))

        ids_raw = await self._u.zrevrange(self._runs_by_policy_key(policy_id), 0, -1)
        ids = [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in ids_raw]

        runs: list[AutoEvalRun] = []
        for rid in ids:
            raw = await self._u.get(self._run_key(rid))
            run = self._payload_to_run(raw)
            if run is None:
                continue
            if status is not None and run.status != status:
                continue
            runs.append(run)

        total = len(runs)
        start = (page - 1) * page_size
        end = start + page_size
        return runs[start:end], total

    async def get_latest_completed_run(self, policy_id: str) -> AutoEvalRun | None:
        """직전 ``status=completed`` run — 회귀 baseline 용."""
        ids_raw = await self._u.zrevrange(self._runs_by_policy_key(policy_id), 0, -1)
        for raw_id in ids_raw:
            rid = raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
            raw = await self._u.get(self._run_key(rid))
            run = self._payload_to_run(raw)
            if run is None:
                continue
            if run.status == "completed":
                return run
        return None

    # ------------------------------------------------------------------ #
    # Schedule
    # ------------------------------------------------------------------ #
    async def fetch_due_policies(self, now: datetime) -> list[str]:
        """``next_run_at <= now`` 인 active 정책 ID 목록 (오름차순)."""
        ts = now.timestamp() if now.tzinfo else now.replace(tzinfo=UTC).timestamp()
        raw = await self._u.zrangebyscore(self.KEY_POLICIES_ACTIVE, 0, ts)
        return [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in raw]

    async def reschedule(
        self, policy: AutoEvalPolicy, *, now: datetime | None = None
    ) -> AutoEvalPolicy:
        """run 완료 후 ``next_run_at`` 재계산 + ``last_run_at`` 갱신.

        status != active 이면 next_run_at = None (active ZSet 제거).
        """
        ts = now or datetime.now(UTC)
        if policy.status == "active":
            policy.next_run_at = self._compute_next_run(policy.schedule, ts)
        else:
            policy.next_run_at = None
        policy.last_run_at = ts
        policy.updated_at = ts
        await self._save_policy(policy)
        return policy

    @staticmethod
    def _compute_next_run(schedule: AutoEvalSchedule, base: datetime) -> datetime:
        """다음 실행 시각 계산.

        - cron: ``croniter`` 로 base 기준 다음 발화 시각
        - interval: ``base + interval_seconds``
        - event: 365일 뒤 (placeholder — cron 폴링이 트리거하지 않도록)
        """
        # base 가 naive 면 UTC 부여
        if base.tzinfo is None:
            base = base.replace(tzinfo=UTC)

        if schedule.type == "cron":
            from croniter import croniter

            it = croniter(schedule.cron_expression, base)
            next_dt = it.get_next(datetime)
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=UTC)
            return next_dt
        if schedule.type == "interval":
            assert schedule.interval_seconds is not None  # validator 보장
            return base + timedelta(seconds=schedule.interval_seconds)
        if schedule.type == "event":
            return base + timedelta(days=365)
        raise ValueError(f"unknown schedule type: {schedule.type}")

    # ------------------------------------------------------------------ #
    # Cost tracking
    # ------------------------------------------------------------------ #
    async def record_cost(
        self,
        policy_id: str,
        cost_usd: float,
        *,
        day: date | None = None,
    ) -> float:
        """일일 비용 누적 + TTL 갱신. 누적값 반환."""
        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        d = day or datetime.now(UTC).date()
        key = self._cost_key(policy_id, d)
        new_total = await self._u.incrbyfloat(key, cost_usd)
        await self._u.expire(key, self.COST_TTL_SEC)
        return float(new_total)

    async def get_daily_cost(self, policy_id: str, day: date | None = None) -> float:
        """특정 날짜 (기본 오늘) 누적 비용. 없으면 0.0."""
        d = day or datetime.now(UTC).date()
        key = self._cost_key(policy_id, d)
        raw = await self._u.get(key)
        if raw is None:
            return 0.0
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    async def get_cost_usage(
        self,
        policy_id: str,
        from_date: date,
        to_date: date,
    ) -> dict[str, Any]:
        """기간별 비용 합계 + 일자별 분해.

        반환 dict 구조 (CostUsage 모델과 호환):
            {
                "policy_id": str,
                "date_range": "YYYY-MM-DD:YYYY-MM-DD",
                "daily_breakdown": [{"date", "cost_usd", "runs_count"}],
                "total_cost_usd": float,
                "daily_limit_usd": None,  # caller가 채움
            }
        """
        if from_date > to_date:
            raise ValueError("from_date must be <= to_date")

        breakdown: list[dict[str, Any]] = []
        total = 0.0
        cur = from_date
        # runs_count 는 TTL 윈도우 외에는 0 — runs 시계열이 있다면 zrangebyscore 로 가져옴
        runs_index_key = self._runs_by_policy_key(policy_id)
        while cur <= to_date:
            cost = await self.get_daily_cost(policy_id, cur)
            day_start = datetime.combine(cur, datetime.min.time(), tzinfo=UTC)
            day_end = day_start + timedelta(days=1)
            # run 카운트 — ZRANGEBYSCORE
            try:
                run_ids = await self._u.zrangebyscore(
                    runs_index_key, day_start.timestamp(), day_end.timestamp()
                )
                runs_count = len(run_ids) if run_ids else 0
            except Exception:  # noqa: BLE001
                runs_count = 0
            breakdown.append(
                {
                    "date": cur.isoformat(),
                    "cost_usd": float(cost),
                    "runs_count": int(runs_count),
                }
            )
            total += cost
            cur = cur + timedelta(days=1)

        return {
            "policy_id": policy_id,
            "date_range": f"{from_date.isoformat()}:{to_date.isoformat()}",
            "daily_breakdown": breakdown,
            "total_cost_usd": float(total),
            "daily_limit_usd": None,
        }


__all__ = [
    "AutoEvalPolicyNotFoundError",
    "AutoEvalRepo",
    "AutoEvalRunNotFoundError",
]
