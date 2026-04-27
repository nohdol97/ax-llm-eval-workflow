"""실험 lifecycle 제어 — Redis Lua 기반 원자적 상태 전이.

본 모듈은 다음 액션을 제공한다 (BUILD_ORDER §4-4, §4-5):

- ``pause``        : running → paused
- ``resume``       : paused → running
- ``cancel``       : pending|queued|running|paused|degraded → cancelled
- ``retry_failed`` : completed|failed|degraded → running (실패 아이템만 재큐)
- ``delete``       : admin only. running/paused 상태에서 불가.

상태 전이는 모두 Redis Lua 스크립트로 원자적으로 수행된다 (race condition 방지).
비합법 전이는 ``ExperimentStateConflictError`` (409 STATE_CONFLICT)로 실패한다.
``started_by`` ≠ user_id (admin 외) 시 ``ExperimentForbiddenError`` (403).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final, cast

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.models.experiment import ExperimentStatus
from app.services.redis_client import RedisClient

logger = get_logger(__name__)

# ---------- 정책 상수 ----------
ACTIVE_TTL_SEC: Final[int] = 86400
TERMINAL_TTL_SEC: Final[int] = 3600


# ---------- 도메인 예외 ----------
class ExperimentNotFoundError(LabsError):
    """실험 미존재 — 404."""

    code = "EXPERIMENT_NOT_FOUND"
    status_code = 404
    title = "Experiment not found"


class ExperimentStateConflictError(LabsError):
    """비합법 상태 전이 — 409 STATE_CONFLICT."""

    code = "STATE_CONFLICT"
    status_code = 409
    title = "Experiment state conflict"


class ExperimentForbiddenError(LabsError):
    """본인 외 사용자(비-admin)의 제어 시도 — 403."""

    code = "FORBIDDEN"
    status_code = 403
    title = "Forbidden"


class ExperimentETagMismatchError(LabsError):
    """ETag/If-Match 불일치 — 412 Precondition Failed."""

    code = "ETAG_MISMATCH"
    status_code = 412
    title = "ETag mismatch"


# ---------- Lua 스크립트 ----------
# 공통 시그니처:
#   KEYS[1] = ax:experiment:{id}            (Hash)
#   KEYS[2] = ax:experiment:{id}:runs       (Set, Run 이름 목록)
#   ARGV[1] = current_timestamp (ISO 8601 with 'Z')
#   ARGV[2] = experiment_id      (Run/Failed 키 prefix용)
# 반환:
#   - 성공: new_status (string)
#   - 실패: redis.error_reply("STATE_CONFLICT:{current}") 또는
#           redis.error_reply("EXPERIMENT_NOT_FOUND")

_PAUSE_SCRIPT: Final[str] = """
-- pause: running → paused
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end
if current ~= 'running' then
    return redis.error_reply('STATE_CONFLICT:' .. current)
end
redis.call('HSET', KEYS[1], 'status', 'paused', 'updated_at', ARGV[1], 'paused_at', ARGV[1])
redis.call('EXPIRE', KEYS[1], 86400)
redis.call('EXPIRE', KEYS[2], 86400)
return 'paused'
"""

_RESUME_SCRIPT: Final[str] = """
-- resume: paused → running
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end
if current ~= 'paused' then
    return redis.error_reply('STATE_CONFLICT:' .. current)
end
redis.call('HSET', KEYS[1], 'status', 'running', 'updated_at', ARGV[1])
redis.call('HDEL', KEYS[1], 'paused_at')
redis.call('EXPIRE', KEYS[1], 86400)
redis.call('EXPIRE', KEYS[2], 86400)
return 'running'
"""

_CANCEL_SCRIPT: Final[str] = """
-- cancel: pending|queued|running|paused|degraded → cancelled
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end
if current ~= 'pending' and current ~= 'queued' and current ~= 'running'
    and current ~= 'paused' and current ~= 'degraded' then
    return redis.error_reply('STATE_CONFLICT:' .. current)
end
redis.call('HSET', KEYS[1],
    'status', 'cancelled',
    'updated_at', ARGV[1],
    'completed_at', ARGV[1])
-- 종료 상태 — TTL 1시간으로 단축 (자기 자신 + Run Set + Run/Failed Hash)
redis.call('EXPIRE', KEYS[1], 3600)
redis.call('EXPIRE', KEYS[2], 3600)
local run_names = redis.call('SMEMBERS', KEYS[2])
for _, run_name in ipairs(run_names) do
    local run_key = 'ax:run:' .. ARGV[2] .. ':' .. run_name
    local failed_key = run_key .. ':failed_items'
    redis.call('EXPIRE', run_key, 3600)
    redis.call('EXPIRE', failed_key, 3600)
end
return 'cancelled'
"""

_RETRY_FAILED_SCRIPT: Final[str] = """
-- retry_failed: completed|failed|degraded → running
-- 실패 아이템만 재큐 (Set 멤버를 살려두고 status만 전환)
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end
if current ~= 'completed' and current ~= 'failed' and current ~= 'degraded' then
    return redis.error_reply('STATE_CONFLICT:' .. current)
end
redis.call('HSET', KEYS[1],
    'status', 'running',
    'updated_at', ARGV[1])
redis.call('HDEL', KEYS[1], 'completed_at', 'error_message')
-- 활성 TTL 24h 복원
redis.call('EXPIRE', KEYS[1], 86400)
redis.call('EXPIRE', KEYS[2], 86400)
local run_names = redis.call('SMEMBERS', KEYS[2])
for _, run_name in ipairs(run_names) do
    local run_key = 'ax:run:' .. ARGV[2] .. ':' .. run_name
    local failed_key = run_key .. ':failed_items'
    redis.call('EXPIRE', run_key, 86400)
    redis.call('EXPIRE', failed_key, 86400)
end
return 'running'
"""

_DELETE_GUARD_SCRIPT: Final[str] = """
-- delete guard: running/paused 상태가 아니면 삭제용 status 마킹
-- KEYS[1] = ax:experiment:{id}
-- 반환: 현재 상태 또는 STATE_CONFLICT
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then
    return redis.error_reply('EXPERIMENT_NOT_FOUND')
end
if current == 'running' or current == 'paused' then
    return redis.error_reply('STATE_CONFLICT:' .. current)
end
return current
"""


# ---------- 헬퍼 ----------
def _now_iso() -> str:
    """ISO 8601 ``Z`` suffix UTC 타임스탬프."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{datetime.now(UTC).microsecond:06d}Z"
    )


def _parse_state_conflict(detail: str) -> str:
    """``STATE_CONFLICT:{current}`` 메시지에서 현재 상태 추출."""
    if ":" in detail:
        return detail.split(":", 1)[1]
    return ""


def _decode(value: Any) -> Any:
    """bytes → str 디코드 (decode_responses=True 환경에서는 no-op)."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _raw_redis(redis: Any) -> Any:
    """``RedisClient.underlying`` (실 클라) 또는 fakeredis(``_client``) 추출.

    Mock(``MockRedisClient``)은 ``underlying`` 속성이 없으므로 ``_client``로 폴백.
    """
    underlying = getattr(redis, "underlying", None)
    if underlying is not None:
        return underlying
    return getattr(redis, "_client", redis)


async def _raw_hgetall(redis: Any, full_key: str) -> dict[Any, Any]:
    """fakeredis/실 Redis 모두에서 동일하게 작동하는 HGETALL 헬퍼."""
    raw_client = _raw_redis(redis)
    result = await raw_client.hgetall(full_key)
    return cast(dict[Any, Any], result)


# ---------- 메인 클래스 ----------
class ExperimentControl:
    """실험 상태 전이 컨트롤러 (BUILD_ORDER §4-4, §4-5).

    내부 상태 전이는 Redis Lua 스크립트로 원자화된다.
    각 메서드는 권한 검증(소유자 또는 admin) 후 Lua를 호출한다.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    # ---------- 권한 검증 ----------
    async def _ensure_actor_authorized(
        self,
        experiment_id: str,
        user_id: str,
        *,
        require_admin: bool = False,
    ) -> dict[str, Any]:
        """실험 소유자 일치 또는 admin 여부 검증.

        Args:
            experiment_id: 실험 ID
            user_id: 호출자 user_id (admin 호출 시에는 ``"<admin>"`` 같은 sentinel 사용 가능
                — 본 클래스는 직접 RBAC 체크를 하지 않으며, ``require_admin``은 라우터 단에서
                ``require_role("admin")``으로 처리되었음을 가정).
            require_admin: True면 admin 가드는 라우터에서 처리되었다고 가정하고 본인검증을 생략.

        Returns:
            ``ax:experiment:{id}`` Hash dict.
        """
        meta = await self._read_meta(experiment_id)
        if not meta:
            raise ExperimentNotFoundError(detail=f"experiment_id={experiment_id!r} not found")
        if require_admin:
            return meta
        owner = str(meta.get("started_by", "") or meta.get("owner_user_id", ""))
        if owner and owner != user_id:
            raise ExperimentForbiddenError(
                detail=f"본인 실험만 제어 가능합니다 (owner={owner})."
            )
        return meta

    async def _read_meta(self, experiment_id: str) -> dict[str, Any]:
        """``ax:experiment:{id}`` Hash → dict (없으면 빈 dict).

        실 ``RedisClient.underlying`` 또는 Mock의 ``hgetall``(prefix 미적용 fakeredis 위임)
        양쪽에서 안전하게 동작하도록 직접 접근자를 우선한다.
        """
        full_key = f"ax:experiment:{experiment_id}"
        raw = await _raw_hgetall(self._redis, full_key)
        if not raw:
            return {}
        return {_decode(k): _decode(v) for k, v in raw.items()}

    # ---------- ETag ----------
    @staticmethod
    def compute_etag(meta: dict[str, Any]) -> str:
        """실험 Hash의 ETag — ``status`` + ``updated_at`` 조합 기반.

        업데이트 시점이 바뀌면 ETag도 변경되므로 낙관적 동시성 제어에 사용한다.
        """
        import hashlib

        status = str(meta.get("status", ""))
        updated_at = str(meta.get("updated_at", "") or meta.get("created_at", ""))
        seed = f"{status}|{updated_at}".encode()
        digest = hashlib.sha256(seed).hexdigest()[:16]
        return f'"{digest}"'

    @staticmethod
    def verify_if_match(if_match: str | None, etag: str) -> None:
        """``If-Match`` 헤더 검증 — 불일치 시 412.

        ``*``는 와일드카드로 통과시킨다 (RFC 9110 §13.1.1).
        헤더가 ``None``이면 검증 스킵 (라우터에서 ``Header(...)`` 필수 지정 권장).
        """
        if if_match is None:
            return
        if if_match.strip() in ("*", etag):
            return
        raise ExperimentETagMismatchError(
            detail=f"If-Match={if_match!r} != ETag={etag!r}"
        )

    # ---------- Lua 호출 ----------
    async def _invoke_transition(
        self,
        script: str,
        experiment_id: str,
    ) -> str:
        """Lua 스크립트 EVAL — 결과를 디코드하여 반환.

        실패 메시지 패턴:
        - ``EXPERIMENT_NOT_FOUND`` → :class:`ExperimentNotFoundError`
        - ``STATE_CONFLICT:{current}`` → :class:`ExperimentStateConflictError`
        """
        exp_key = f"ax:experiment:{experiment_id}"
        runs_key = f"ax:experiment:{experiment_id}:runs"
        try:
            # MockRedis(eval) / RedisClient(eval) 양쪽 호환:
            # - RedisClient.eval은 KEYS에 prefix 자동 적용 (이미 ax: 접두 → 통과)
            # - Mock의 eval은 그대로 fakeredis로 위임 (prefix 미적용)
            result = await self._redis.eval(
                script,
                2,
                exp_key,
                runs_key,
                _now_iso(),
                experiment_id,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "EXPERIMENT_NOT_FOUND" in message:
                raise ExperimentNotFoundError(
                    detail=f"experiment_id={experiment_id!r} not found"
                ) from exc
            if "STATE_CONFLICT" in message:
                current = _parse_state_conflict(message.split("STATE_CONFLICT", 1)[1])
                raise ExperimentStateConflictError(
                    detail=(
                        f"비합법 상태 전이: 현재={current!r}"
                        if current
                        else "비합법 상태 전이"
                    ),
                    extras={"current_status": current} if current else None,
                ) from exc
            raise
        decoded = _decode(result)
        return cast(str, decoded)

    # ---------- 공개 액션 ----------
    async def pause(self, experiment_id: str, user_id: str) -> ExperimentStatus:
        """``running`` → ``paused`` (소유자만 가능)."""
        await self._ensure_actor_authorized(experiment_id, user_id)
        new_status = await self._invoke_transition(_PAUSE_SCRIPT, experiment_id)
        logger.info(
            "experiment_paused",
            experiment_id=experiment_id,
            user_id=user_id,
        )
        return cast(ExperimentStatus, new_status)

    async def resume(self, experiment_id: str, user_id: str) -> ExperimentStatus:
        """``paused`` → ``running`` (소유자만 가능).

        백그라운드 태스크 재기동(BatchRunner.run_experiment(resume=True))은
        Agent 17의 ``batch_runner`` 후크에서 처리한다 — 본 클래스는 상태 전이만 책임.
        """
        await self._ensure_actor_authorized(experiment_id, user_id)
        new_status = await self._invoke_transition(_RESUME_SCRIPT, experiment_id)
        logger.info(
            "experiment_resumed",
            experiment_id=experiment_id,
            user_id=user_id,
        )
        return cast(ExperimentStatus, new_status)

    async def cancel(self, experiment_id: str, user_id: str) -> ExperimentStatus:
        """``pending|queued|running|paused|degraded`` → ``cancelled``.

        진행 중 태스크에 대한 cancellation 신호는 Redis Hash의 ``status`` 변경으로
        전달된다 (BatchRunner는 주기적으로 status를 polling하여 본인 실행을 중단).
        """
        await self._ensure_actor_authorized(experiment_id, user_id)
        new_status = await self._invoke_transition(_CANCEL_SCRIPT, experiment_id)
        logger.info(
            "experiment_cancelled",
            experiment_id=experiment_id,
            user_id=user_id,
        )
        return cast(ExperimentStatus, new_status)

    async def retry_failed(self, experiment_id: str, user_id: str) -> ExperimentStatus:
        """``completed|failed|degraded`` → ``running`` (실패 아이템만 재큐)."""
        await self._ensure_actor_authorized(experiment_id, user_id)
        new_status = await self._invoke_transition(_RETRY_FAILED_SCRIPT, experiment_id)
        logger.info(
            "experiment_retry_failed",
            experiment_id=experiment_id,
            user_id=user_id,
        )
        return cast(ExperimentStatus, new_status)

    async def delete(self, experiment_id: str, user_id: str) -> None:
        """실험 삭제 — admin only 라우터 가드 필요. running/paused는 409.

        삭제 대상 키:
        - ``ax:experiment:{id}`` (Hash)
        - ``ax:experiment:{id}:runs`` (Set)
        - ``ax:experiment:{id}:events`` (Stream)
        - ``ax:experiment:{id}:config_blob`` (대용량 config 분리 보관 시)
        - ``ax:run:{id}:*`` 및 ``ax:run:{id}:*:failed_items``
        - ``ax:project:{project_id}:experiments`` ZSet에서 멤버 ZREM

        Args:
            experiment_id: 삭제 대상 실험 ID
            user_id: 호출자 (감사 로그용 — admin 가드는 라우터에서 처리)
        """
        meta = await self._read_meta(experiment_id)
        if not meta:
            raise ExperimentNotFoundError(
                detail=f"experiment_id={experiment_id!r} not found"
            )

        # 가드 — running/paused 거부 (Lua는 read-only, 부작용 없음)
        try:
            await self._redis.eval(
                _DELETE_GUARD_SCRIPT,
                1,
                f"ax:experiment:{experiment_id}",
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "EXPERIMENT_NOT_FOUND" in message:
                raise ExperimentNotFoundError(
                    detail=f"experiment_id={experiment_id!r} not found"
                ) from exc
            if "STATE_CONFLICT" in message:
                current = _parse_state_conflict(message.split("STATE_CONFLICT", 1)[1])
                raise ExperimentStateConflictError(
                    detail=(
                        f"running/paused 상태에서는 삭제 불가 (현재={current!r}). "
                        "먼저 cancel하세요."
                    ),
                    extras={"current_status": current},
                ) from exc
            raise

        # Run Set의 멤버 수집 → Run/Failed 키 삭제
        runs_set_full = f"ax:experiment:{experiment_id}:runs"
        raw_client = _raw_redis(self._redis)
        run_names_raw = await raw_client.smembers(runs_set_full)
        run_names = [_decode(name) for name in run_names_raw]

        # 모든 후보 키 모음 (전체 prefix 포함 — raw_client 직접 호출)
        full_keys: list[str] = [
            f"ax:experiment:{experiment_id}",
            f"ax:experiment:{experiment_id}:runs",
            f"ax:experiment:{experiment_id}:events",
            f"ax:experiment:{experiment_id}:stream",
            f"ax:experiment:{experiment_id}:config_blob",
            f"ax:experiment:{experiment_id}:items",
        ]
        for run_name in run_names:
            full_keys.append(f"ax:run:{experiment_id}:{run_name}")
            full_keys.append(f"ax:run:{experiment_id}:{run_name}:failed_items")

        # raw_client.delete를 사용 — 이미 ax: prefix 포함, Mock/실제 양쪽 동작
        await raw_client.delete(*full_keys)

        # ZSet에서 ZREM (project_id가 있을 때만)
        project_id = str(meta.get("project_id", ""))
        if project_id:
            try:
                await raw_client.zrem(
                    f"ax:project:{project_id}:experiments", experiment_id
                )
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                logger.warning(
                    "experiment_zrem_failed",
                    experiment_id=experiment_id,
                    project_id=project_id,
                    error=str(exc),
                )

        logger.info(
            "experiment_deleted",
            experiment_id=experiment_id,
            user_id=user_id,
            project_id=project_id,
            runs=len(run_names),
        )
