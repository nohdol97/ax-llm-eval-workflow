"""Custom Evaluator 거버넌스 서비스 (FEATURES.md §9.1, API_DESIGN.md §8/§14).

Custom Evaluator 제출/승인/반려/폐기(거버넌스) 워크플로우의 비즈니스 로직.

저장 스키마 (Redis)
-------------------
- Hash ``ax:evaluator_submission:{id}`` — 제출 본체
- Sorted Set ``ax:evaluator_submissions:by_user:{user_id}`` — 사용자별 최신순
- Sorted Set ``ax:evaluator_submissions:all`` — 전체 최신순 (admin 페이지네이션)
- Sorted Set ``ax:evaluator_submissions:status:{status}`` — 상태 필터 인덱스

상태 전이
---------
- ``pending → approved`` (admin)
- ``pending → rejected`` (admin, 사유 필수)
- ``approved → deprecated`` (admin)
- ``rejected → 종결`` (재제출은 새 ID)

보안
----
- admin 자동 승인은 라우터 권한과 별개로 본 서비스 ``submit(is_admin=True)``에서 처리
- 본인 외 제출 조회는 404로 통일 (정보 노출 차단)
- 코드 본문은 INFO 로그 금지 (PII 차단)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.evaluators.custom_code import (
    DEFAULT_SANDBOX_IMAGE,
    DEFAULT_TIMEOUT_SEC,
    validate_code,
)
from app.models.evaluator import (
    CODE_HASH_LENGTH,
    Submission,
    SubmissionListResponse,
    SubmissionStatus,
    TestCase,
)
from app.models.notification import NotificationType
from app.services.notification_service import create_notification
from app.services.redis_client import RedisClient

logger = get_logger(__name__)


# ---------- 도메인 예외 ----------
class SubmissionNotFoundError(LabsError):
    """제출 미존재 — 본인 외 접근 시에도 동일 404 응답을 위해 사용."""

    code = "submission_not_found"
    status_code = 404
    title = "Evaluator submission not found"


class SubmissionStateConflictError(LabsError):
    """잘못된 상태 전이 시도 (예: rejected → approved)."""

    code = "submission_state_conflict"
    status_code = 409
    title = "Submission state conflict"


class SubmissionInvalidCodeError(LabsError):
    """사전 검증 실패 — 모든 test_case 통과해야 pending 진입 가능."""

    code = "invalid_code"
    status_code = 422
    title = "Invalid evaluator code"


# ---------- 키 헬퍼 ----------
_KEY_PREFIX = "ax:"


def _full(redis: Any, key: str) -> str:
    """``ax:`` prefix 자동 부착 (underlying 직접 호출용)."""
    if key.startswith(_KEY_PREFIX):
        return key
    return f"{_KEY_PREFIX}{key}"


def _submission_key(submission_id: str) -> str:
    return f"evaluator_submission:{submission_id}"


def _by_user_key(user_id: str) -> str:
    return f"evaluator_submissions:by_user:{user_id}"


def _all_key() -> str:
    return "evaluator_submissions:all"


def _status_key(status: SubmissionStatus) -> str:
    return f"evaluator_submissions:status:{status}"


def _underlying(redis: Any) -> Any:
    """RedisClient.underlying 또는 MockRedisClient._client에서 raw redis 추출."""
    if hasattr(redis, "underlying"):
        return redis.underlying
    if hasattr(redis, "_client"):
        return redis._client
    return redis


# ---------- 직렬화 ----------
def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str:
    """UTC ISO 8601 + Z. None → 빈 문자열."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


def _parse_iso(raw: str | None) -> datetime | None:
    """ISO 8601(+Z) 파싱. 빈/잘못된 입력 → None."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hash_code(code: str) -> str:
    """``sha256(code).hexdigest()[:N]`` — N=CODE_HASH_LENGTH (기본 16)."""
    return sha256(code.encode("utf-8")).hexdigest()[:CODE_HASH_LENGTH]


def _to_storage(submission: Submission) -> dict[str, str]:
    """Submission → Redis Hash 필드 dict."""
    return {
        "submission_id": submission.submission_id,
        "name": submission.name,
        "description": submission.description,
        "code": submission.code,
        "code_hash": submission.code_hash,
        "status": submission.status,
        "submitted_by": submission.submitted_by,
        "submitted_at": _iso(submission.submitted_at),
        "approved_by": submission.approved_by or "",
        "approved_at": _iso(submission.approved_at),
        "rejected_by": submission.rejected_by or "",
        "rejected_at": _iso(submission.rejected_at),
        "rejection_reason": submission.rejection_reason or "",
        "deprecated_at": _iso(submission.deprecated_at),
    }


def _hget(raw: dict[Any, Any], key: str) -> str | None:
    """Redis Hash → str (bytes/str 모두 수용). 빈 문자열은 None으로 정규화."""
    if not isinstance(raw, dict):
        return None
    value = raw.get(key)
    if value is None and isinstance(key, str):
        value = raw.get(key.encode("utf-8"))
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
    text = str(value)
    if text == "":
        return None
    return text


def _from_storage(raw: dict[Any, Any]) -> Submission | None:
    """Redis Hash → Submission. 결손 필드는 None으로 정규화."""
    if not raw:
        return None

    submission_id = _hget(raw, "submission_id")
    name = _hget(raw, "name")
    description = _hget(raw, "description")
    code = _hget(raw, "code")
    code_hash = _hget(raw, "code_hash")
    status_raw = _hget(raw, "status")
    submitted_by = _hget(raw, "submitted_by")
    submitted_at_raw = _hget(raw, "submitted_at")

    if not (submission_id and name and code and submitted_by and submitted_at_raw):
        return None
    if status_raw not in ("pending", "approved", "rejected", "deprecated"):
        return None

    submitted_at = _parse_iso(submitted_at_raw)
    if submitted_at is None:
        return None

    return Submission(
        submission_id=submission_id,
        name=name,
        description=description or "",
        code=code,
        code_hash=code_hash or _hash_code(code),
        status=status_raw,  # type: ignore[arg-type]
        submitted_by=submitted_by,
        submitted_at=submitted_at,
        approved_by=_hget(raw, "approved_by"),
        approved_at=_parse_iso(_hget(raw, "approved_at")),
        rejected_by=_hget(raw, "rejected_by"),
        rejected_at=_parse_iso(_hget(raw, "rejected_at")),
        rejection_reason=_hget(raw, "rejection_reason"),
        deprecated_at=_parse_iso(_hget(raw, "deprecated_at")),
    )


# ---------- 서비스 ----------
class EvaluatorGovernanceService:
    """Custom Evaluator 거버넌스 서비스.

    Args:
        redis: Redis 클라이언트 (실제 ``RedisClient`` 또는 ``MockRedisClient``).
        sandbox_image: 사전 검증에 사용할 Docker 이미지.
        validate_timeout_sec: 사전 검증 evaluator 단일 실행 타임아웃 (초).
        validator: ``validate_code`` callable 주입 (테스트용 — 기본은 모듈 함수).
    """

    def __init__(
        self,
        redis: RedisClient | Any,
        *,
        sandbox_image: str = DEFAULT_SANDBOX_IMAGE,
        validate_timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        validator: Any = None,
    ) -> None:
        self._redis = redis
        self._sandbox_image = sandbox_image
        self._validate_timeout_sec = validate_timeout_sec
        self._validator = validator or validate_code

    # ------------------------------------------------------------------ #
    # 사전 검증 (validate)
    # ------------------------------------------------------------------ #
    async def validate(
        self,
        code: str,
        test_cases: list[TestCase],
    ) -> list[dict[str, Any]]:
        """평가 코드 + test_cases를 샌드박스에서 실행.

        Returns:
            ``[{result: float} | {error: str}]`` — 입력 순서 보존.
        """
        cases_payload = [
            {
                "output": case.output,
                "expected": case.expected,
                "metadata": case.metadata,
            }
            for case in test_cases
        ]
        return await self._validator(
            code=code,
            test_cases=cases_payload,
            sandbox_image=self._sandbox_image,
            timeout_sec=self._validate_timeout_sec,
        )

    # ------------------------------------------------------------------ #
    # 제출
    # ------------------------------------------------------------------ #
    async def submit(
        self,
        *,
        user_id: str,
        is_admin: bool,
        name: str,
        description: str,
        code: str,
        test_cases: list[TestCase] | None,
        now: datetime | None = None,
    ) -> Submission:
        """평가 코드 제출.

        - test_cases가 비어있지 않으면 사전 검증 실행 → 모두 ``result`` 보유 시에만 통과.
          하나라도 ``error``가 있으면 ``SubmissionInvalidCodeError`` (422).
        - admin이면 status=approved + approved_by/approved_at 즉시 설정 (자동 승인).
        - 일반 사용자는 status=pending.
        """
        if not user_id:
            raise LabsError(detail="user_id가 비어 있습니다.")
        if not name.strip():
            raise LabsError(detail="name이 비어 있습니다.")
        if not code.strip():
            raise LabsError(detail="code가 비어 있습니다.")

        # 1) 사전 검증 (test_cases 제공 시)
        if test_cases:
            results = await self.validate(code=code, test_cases=test_cases)
            failures = [
                idx
                for idx, item in enumerate(results)
                if not isinstance(item, dict) or "error" in item
            ]
            if failures:
                # 코드 본문은 노출하지 않음 — 인덱스만 보고
                raise SubmissionInvalidCodeError(
                    detail=f"사전 검증 실패: {len(failures)}/{len(results)}개 test_case 실패",
                    extras={"failed_indexes": failures, "test_results": results},
                )

        timestamp = now or _now_utc()
        submission_id = str(uuid.uuid4())
        code_hash = _hash_code(code)

        if is_admin:
            status: SubmissionStatus = "approved"
            approved_by: str | None = user_id
            approved_at: datetime | None = timestamp
        else:
            status = "pending"
            approved_by = None
            approved_at = None

        submission = Submission(
            submission_id=submission_id,
            name=name,
            description=description,
            code=code,
            code_hash=code_hash,
            status=status,
            submitted_by=user_id,
            submitted_at=timestamp,
            approved_by=approved_by,
            approved_at=approved_at,
        )

        await self._persist_new(submission)

        logger.info(
            "evaluator_submission_created",
            submission_id=submission_id,
            user_id=user_id,
            status=status,
            code_hash=code_hash,
        )
        return submission

    async def _persist_new(self, submission: Submission) -> None:
        """제출을 Redis에 영속화 — Hash + by_user/all/status 인덱스에 추가."""
        underlying = _underlying(self._redis)
        score = submission.submitted_at.timestamp() * 1000.0
        sub_key = _full(self._redis, _submission_key(submission.submission_id))
        by_user_key = _full(self._redis, _by_user_key(submission.submitted_by))
        all_key = _full(self._redis, _all_key())
        status_key = _full(self._redis, _status_key(submission.status))

        pipe = underlying.pipeline()
        pipe.hset(sub_key, mapping=_to_storage(submission))
        pipe.zadd(by_user_key, {submission.submission_id: score})
        pipe.zadd(all_key, {submission.submission_id: score})
        pipe.zadd(status_key, {submission.submission_id: score})
        await pipe.execute()

    # ------------------------------------------------------------------ #
    # 단건 조회
    # ------------------------------------------------------------------ #
    async def get_submission(
        self,
        submission_id: str,
        *,
        user_id: str,
        is_admin: bool,
    ) -> Submission:
        """본인 또는 admin만 접근 가능.

        다른 사용자 것은 ``SubmissionNotFoundError``로 응답하여 정보 노출 방지.
        """
        submission = await self._read(submission_id)
        if submission is None:
            raise SubmissionNotFoundError(
                detail=f"submission_id={submission_id!r} not found"
            )
        if not is_admin and submission.submitted_by != user_id:
            raise SubmissionNotFoundError(
                detail=f"submission_id={submission_id!r} not found"
            )
        return submission

    async def _read(self, submission_id: str) -> Submission | None:
        underlying = _underlying(self._redis)
        raw = await underlying.hgetall(
            _full(self._redis, _submission_key(submission_id))
        )
        return _from_storage(raw)

    # ------------------------------------------------------------------ #
    # 목록 조회
    # ------------------------------------------------------------------ #
    async def list_submissions(
        self,
        *,
        user_id: str,
        is_admin: bool,
        status_filter: SubmissionStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SubmissionListResponse:
        """본인 제출 목록 또는 (admin) 전체 — 최신순."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        underlying = _underlying(self._redis)

        if is_admin:
            if status_filter is None:
                index_key = _full(self._redis, _all_key())
            else:
                index_key = _full(self._redis, _status_key(status_filter))
        else:
            index_key = _full(self._redis, _by_user_key(user_id))

        # 인덱스 → 최신순 ID 목록
        try:
            raw_ids = await underlying.zrevrange(index_key, 0, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "submission_index_fetch_failed",
                index_key=index_key,
                error=str(exc),
            )
            raw_ids = []

        ids: list[str] = [
            rid.decode("utf-8") if isinstance(rid, bytes) else str(rid)
            for rid in raw_ids
        ]

        # admin + status 필터 미지정이면 결과를 그대로 사용
        # 비-admin인데 status 필터 지정된 경우 본인 ID 목록에서 추가 필터링
        items: list[Submission] = []
        for sid in ids:
            sub = await self._read(sid)
            if sub is None:
                continue
            # 비-admin: 본인 것만
            if not is_admin and sub.submitted_by != user_id:
                continue
            # 상태 필터
            if status_filter is not None and sub.status != status_filter:
                continue
            items.append(sub)

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]

        return SubmissionListResponse(
            items=page_items,
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------ #
    # 승인
    # ------------------------------------------------------------------ #
    async def approve(
        self,
        submission_id: str,
        *,
        admin_id: str,
        note: str | None = None,
        now: datetime | None = None,
    ) -> Submission:
        """admin 승인 — pending → approved. 알림 best-effort 생성."""
        sub = await self._read(submission_id)
        if sub is None:
            raise SubmissionNotFoundError(
                detail=f"submission_id={submission_id!r} not found"
            )
        if sub.status != "pending":
            raise SubmissionStateConflictError(
                detail=f"승인 가능 상태가 아닙니다 (현재: {sub.status})"
            )

        timestamp = now or _now_utc()
        sub.status = "approved"
        sub.approved_by = admin_id
        sub.approved_at = timestamp
        await self._update_status_indexed(sub, prev_status="pending")

        # 알림 — 제출자에게 (best-effort)
        await self._notify(
            user_id=sub.submitted_by,
            type_="evaluator_approved",
            title="평가자 승인됨",
            body=(note or f"'{sub.name}'이(가) 승인되었습니다."),
            link=f"/evaluators/{submission_id}",
            resource_id=submission_id,
        )
        logger.info(
            "evaluator_submission_approved",
            submission_id=submission_id,
            admin_id=admin_id,
        )
        return sub

    # ------------------------------------------------------------------ #
    # 반려
    # ------------------------------------------------------------------ #
    async def reject(
        self,
        submission_id: str,
        *,
        admin_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> Submission:
        """admin 반려 — pending → rejected. 사유 필수."""
        if not reason or not reason.strip():
            raise LabsError(detail="rejection_reason 필수")

        sub = await self._read(submission_id)
        if sub is None:
            raise SubmissionNotFoundError(
                detail=f"submission_id={submission_id!r} not found"
            )
        if sub.status != "pending":
            raise SubmissionStateConflictError(
                detail=f"반려 가능 상태가 아닙니다 (현재: {sub.status})"
            )

        timestamp = now or _now_utc()
        sub.status = "rejected"
        sub.rejected_by = admin_id
        sub.rejected_at = timestamp
        sub.rejection_reason = reason
        await self._update_status_indexed(sub, prev_status="pending")

        await self._notify(
            user_id=sub.submitted_by,
            type_="evaluator_rejected",
            title="평가자 반려됨",
            body=f"'{sub.name}' 반려: {reason[:200]}",
            link=f"/evaluators/{submission_id}",
            resource_id=submission_id,
        )
        logger.info(
            "evaluator_submission_rejected",
            submission_id=submission_id,
            admin_id=admin_id,
        )
        return sub

    # ------------------------------------------------------------------ #
    # 폐기
    # ------------------------------------------------------------------ #
    async def deprecate(
        self,
        submission_id: str,
        *,
        admin_id: str,
        now: datetime | None = None,
    ) -> Submission:
        """admin 폐기 — approved → deprecated. 신규 사용 차단."""
        sub = await self._read(submission_id)
        if sub is None:
            raise SubmissionNotFoundError(
                detail=f"submission_id={submission_id!r} not found"
            )
        if sub.status != "approved":
            raise SubmissionStateConflictError(
                detail=f"폐기 가능 상태가 아닙니다 (현재: {sub.status})"
            )

        timestamp = now or _now_utc()
        sub.status = "deprecated"
        sub.deprecated_at = timestamp
        await self._update_status_indexed(sub, prev_status="approved")

        await self._notify(
            user_id=sub.submitted_by,
            type_="evaluator_deprecated",
            title="평가자 폐기됨",
            body=f"'{sub.name}'이(가) 폐기되었습니다 — 신규 사용이 차단됩니다.",
            link=f"/evaluators/{submission_id}",
            resource_id=f"{submission_id}:deprecated",
        )
        logger.info(
            "evaluator_submission_deprecated",
            submission_id=submission_id,
            admin_id=admin_id,
        )
        return sub

    # ------------------------------------------------------------------ #
    # 승인된 evaluator 목록 (모든 사용자 조회 가능)
    # ------------------------------------------------------------------ #
    async def list_approved(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> SubmissionListResponse:
        """승인 상태(``approved``) evaluator만 노출.

        모든 사용자가 위저드 Step 3에서 사용. ``code`` 본문은 라우터에서 마스킹.
        """
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        underlying = _underlying(self._redis)

        try:
            raw_ids = await underlying.zrevrange(
                _full(self._redis, _status_key("approved")), 0, -1
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "list_approved_index_fetch_failed",
                error=str(exc),
            )
            raw_ids = []

        ids: list[str] = [
            rid.decode("utf-8") if isinstance(rid, bytes) else str(rid)
            for rid in raw_ids
        ]
        items: list[Submission] = []
        for sid in ids:
            sub = await self._read(sid)
            if sub is None:
                continue
            if sub.status != "approved":
                # 인덱스에 잔존하는 stale 항목 무시
                continue
            items.append(sub)

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return SubmissionListResponse(
            items=items[start:end],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------ #
    # 인덱스 갱신
    # ------------------------------------------------------------------ #
    async def _update_status_indexed(
        self,
        submission: Submission,
        *,
        prev_status: SubmissionStatus,
    ) -> None:
        """Hash 갱신 + 상태 인덱스 이동 (prev_status → submission.status)."""
        underlying = _underlying(self._redis)
        sub_key = _full(self._redis, _submission_key(submission.submission_id))
        score = submission.submitted_at.timestamp() * 1000.0

        prev_status_key = _full(self._redis, _status_key(prev_status))
        new_status_key = _full(self._redis, _status_key(submission.status))

        pipe = underlying.pipeline()
        pipe.hset(sub_key, mapping=_to_storage(submission))
        pipe.zrem(prev_status_key, submission.submission_id)
        pipe.zadd(new_status_key, {submission.submission_id: score})
        await pipe.execute()

    # ------------------------------------------------------------------ #
    # 알림 — best-effort (실패해도 거버넌스 결과는 유지)
    # ------------------------------------------------------------------ #
    async def _notify(
        self,
        *,
        user_id: str,
        type_: NotificationType,
        title: str,
        body: str,
        link: str | None,
        resource_id: str,
    ) -> None:
        if not user_id:
            return
        try:
            await create_notification(
                user_id=user_id,
                type_=type_,
                title=title,
                body=body,
                link=link,
                redis=self._redis,
                resource_id=resource_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "evaluator_notification_failed",
                type=type_,
                user_id=user_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------ #
    # 직렬화 유틸 (라우터/테스트에서 노출 제어용)
    # ------------------------------------------------------------------ #
    @staticmethod
    def to_response(
        submission: Submission,
        *,
        include_code: bool,
    ) -> dict[str, Any]:
        """Submission → 응답 dict — 옵션으로 ``code`` 마스킹."""
        data = json.loads(submission.model_dump_json())
        if not include_code:
            data["code"] = ""
        return data


__all__ = [
    "EvaluatorGovernanceService",
    "SubmissionInvalidCodeError",
    "SubmissionNotFoundError",
    "SubmissionStateConflictError",
]
