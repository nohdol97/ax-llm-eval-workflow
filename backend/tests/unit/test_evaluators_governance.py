"""Custom Evaluator 거버넌스 라우터 + 서비스 단위 테스트.

검증 범위
- 사전 검증 실패 → 422 INVALID_CODE
- 제출 (user → pending, admin → 자동 approved + 알림 미발송)
- 목록 조회 (본인 / admin 전체 / 상태 필터)
- 승인 → 알림 생성, 응답 ETag 부여, 상태 인덱스 이동
- 반려 (사유 필수, pending 외 상태에서 409)
- deprecate (approved 외 상태에서 409)
- 본인 외 단건 조회 → 404 (정보 노출 방지)
- 내장 evaluator 카탈로그 (13종)
- 승인된 evaluator 카탈로그
- score config 상태 (admin)
- 비-admin이 admin 전용 엔드포인트 호출 → 403
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.evaluators import get_governance_service
from app.core.deps import get_langfuse_client, get_redis_client
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.models.evaluator import TestCase
from app.services.evaluator_governance import (
    EvaluatorGovernanceService,
    SubmissionInvalidCodeError,
    SubmissionNotFoundError,
    SubmissionStateConflictError,
)
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 공통 fixture ----------
@pytest.fixture
def admin_user() -> User:
    return User(id="admin-1", email="a@x.com", role="admin")


@pytest.fixture
def regular_user() -> User:
    return User(id="user-1", email="u@x.com", role="user")


@pytest.fixture
def viewer_user() -> User:
    return User(id="viewer-1", email="v@x.com", role="viewer")


def _fake_validator(
    *, code: str, test_cases: list[dict[str, Any]], **kwargs: Any
) -> Any:
    """validate_code 대체 — 첫 케이스가 'BAD'면 error, 나머지는 result 0.5."""

    async def _runner() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for case in test_cases:
            output = case.get("output")
            if isinstance(output, str) and output.startswith("BAD"):
                results.append({"error": "intentional failure"})
            else:
                results.append({"result": 0.5})
        return results

    return _runner()


@pytest.fixture
def governance(redis_client: MockRedisClient) -> EvaluatorGovernanceService:
    """샌드박스 미사용 — validator를 가짜로 주입."""
    return EvaluatorGovernanceService(
        redis=redis_client, validator=_fake_validator
    )


@pytest.fixture
def app_for_user(
    redis_client: MockRedisClient,
    regular_user: User,
    governance: EvaluatorGovernanceService,
    langfuse_client: MockLangfuseClient,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_current_user] = lambda: regular_user
    app.dependency_overrides[get_governance_service] = lambda: governance
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
    return TestClient(app)


@pytest.fixture
def app_for_admin(
    redis_client: MockRedisClient,
    admin_user: User,
    governance: EvaluatorGovernanceService,
    langfuse_client: MockLangfuseClient,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_governance_service] = lambda: governance
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
    return TestClient(app)


@pytest.fixture
def app_for_viewer(
    redis_client: MockRedisClient,
    viewer_user: User,
    governance: EvaluatorGovernanceService,
    langfuse_client: MockLangfuseClient,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    app.dependency_overrides[get_governance_service] = lambda: governance
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
    return TestClient(app)


# ============================================================ #
# 1) 서비스 레벨 — submit / list / approve / reject / deprecate
# ============================================================ #
@pytest.mark.unit
class TestSubmissionService:
    """``EvaluatorGovernanceService``의 핵심 메서드."""

    async def test_user_submit_creates_pending(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="my_eval",
            description="test",
            code="def evaluate(o,e,m): return 1.0",
            test_cases=None,
        )
        assert sub.status == "pending"
        assert sub.submitted_by == "user-1"
        assert sub.approved_by is None
        assert len(sub.code_hash) == 16

    async def test_admin_submit_auto_approves(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="admin-1",
            is_admin=True,
            name="auto",
            description="t",
            code="def evaluate(o,e,m): return 1.0",
            test_cases=None,
        )
        assert sub.status == "approved"
        assert sub.approved_by == "admin-1"
        assert sub.approved_at is not None

    async def test_submit_with_failing_test_cases_raises_invalid_code(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        with pytest.raises(SubmissionInvalidCodeError):
            await governance.submit(
                user_id="user-1",
                is_admin=False,
                name="bad",
                description="t",
                code="def evaluate(o,e,m): return 0.0",
                test_cases=[
                    TestCase(output="BAD-1"),
                    TestCase(output="ok"),
                ],
            )

    async def test_submit_with_passing_test_cases_creates_pending(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="ok",
            description="t",
            code="def evaluate(o,e,m): return 0.5",
            test_cases=[TestCase(output="x"), TestCase(output="y")],
        )
        assert sub.status == "pending"

    async def test_get_submission_owner_succeeds(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        loaded = await governance.get_submission(
            sub.submission_id, user_id="user-1", is_admin=False
        )
        assert loaded.submission_id == sub.submission_id

    async def test_get_submission_other_user_returns_404(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        with pytest.raises(SubmissionNotFoundError):
            await governance.get_submission(
                sub.submission_id, user_id="other", is_admin=False
            )

    async def test_admin_can_get_other_users_submission(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        loaded = await governance.get_submission(
            sub.submission_id, user_id="admin-1", is_admin=True
        )
        assert loaded.submission_id == sub.submission_id

    async def test_list_user_only_returns_own(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="a",
            description="d",
            code="x",
            test_cases=None,
        )
        await governance.submit(
            user_id="user-2",
            is_admin=False,
            name="b",
            description="d",
            code="y",
            test_cases=None,
        )
        result = await governance.list_submissions(
            user_id="user-1", is_admin=False
        )
        assert result.total == 1
        assert result.items[0].submitted_by == "user-1"

    async def test_list_admin_returns_all(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="a",
            description="d",
            code="x",
            test_cases=None,
        )
        await governance.submit(
            user_id="user-2",
            is_admin=False,
            name="b",
            description="d",
            code="y",
            test_cases=None,
        )
        result = await governance.list_submissions(
            user_id="admin-1", is_admin=True
        )
        assert result.total == 2

    async def test_list_status_filter(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="pending-1",
            description="d",
            code="x",
            test_cases=None,
        )
        approved = await governance.submit(
            user_id="user-1",
            is_admin=True,
            name="approved-1",
            description="d",
            code="z",
            test_cases=None,
        )
        # admin이 모든 사용자를 보지만, status=approved 필터 적용
        result = await governance.list_submissions(
            user_id="admin-1", is_admin=True, status_filter="approved"
        )
        assert result.total == 1
        assert result.items[0].submission_id == approved.submission_id

    async def test_approve_pending_succeeds_and_creates_notification(
        self,
        governance: EvaluatorGovernanceService,
        redis_client: MockRedisClient,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        approved = await governance.approve(
            sub.submission_id, admin_id="admin-1", note="lgtm"
        )
        assert approved.status == "approved"
        assert approved.approved_by == "admin-1"
        # 알림 — 제출자에게 evaluator_approved
        underlying = redis_client._client
        index_key = "ax:notification:user-1:index"
        ids = await underlying.zrevrange(index_key, 0, -1)
        assert len(ids) >= 1

    async def test_approve_non_pending_raises_state_conflict(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=True,  # auto-approve
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        with pytest.raises(SubmissionStateConflictError):
            await governance.approve(sub.submission_id, admin_id="admin-2")

    async def test_reject_with_reason_succeeds(
        self,
        governance: EvaluatorGovernanceService,
        redis_client: MockRedisClient,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        rejected = await governance.reject(
            sub.submission_id, admin_id="admin-1", reason="중복"
        )
        assert rejected.status == "rejected"
        assert rejected.rejection_reason == "중복"
        # 알림 발송 확인
        underlying = redis_client._client
        ids = await underlying.zrevrange("ax:notification:user-1:index", 0, -1)
        assert len(ids) >= 1

    async def test_deprecate_approved_succeeds(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="admin-1",
            is_admin=True,  # auto-approved
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        deprecated = await governance.deprecate(
            sub.submission_id, admin_id="admin-1"
        )
        assert deprecated.status == "deprecated"
        assert deprecated.deprecated_at is not None

    async def test_deprecate_non_approved_raises_state_conflict(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        sub = await governance.submit(
            user_id="user-1",
            is_admin=False,  # pending
            name="n",
            description="d",
            code="x",
            test_cases=None,
        )
        with pytest.raises(SubmissionStateConflictError):
            await governance.deprecate(sub.submission_id, admin_id="admin-1")

    async def test_list_approved_returns_only_approved(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        # 1 pending + 1 approved
        await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="p",
            description="d",
            code="x1",
            test_cases=None,
        )
        await governance.submit(
            user_id="admin-1",
            is_admin=True,
            name="a",
            description="d",
            code="x2",
            test_cases=None,
        )
        result = await governance.list_approved()
        assert result.total == 1
        assert all(s.status == "approved" for s in result.items)

    async def test_code_hash_is_deterministic(
        self,
        governance: EvaluatorGovernanceService,
    ) -> None:
        a = await governance.submit(
            user_id="user-1",
            is_admin=False,
            name="a",
            description="d",
            code="def evaluate(o,e,m): return 1.0",
            test_cases=None,
        )
        b = await governance.submit(
            user_id="user-2",
            is_admin=False,
            name="b",
            description="d",
            code="def evaluate(o,e,m): return 1.0",
            test_cases=None,
        )
        assert a.code_hash == b.code_hash


# ============================================================ #
# 2) 라우터 — built-in / validate / submissions
# ============================================================ #
@pytest.mark.unit
class TestBuiltInEndpoint:
    """``GET /api/v1/evaluators/built-in``."""

    def test_returns_thirteen_evaluators(self, app_for_viewer: TestClient) -> None:
        resp = app_for_viewer.get("/api/v1/evaluators/built-in")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 13
        names = {ev["name"] for ev in body}
        assert "exact_match" in names
        assert "cosine_similarity" in names
        assert "llm_judge_quality" not in names  # built-in 카탈로그가 아님

    def test_cache_control_header(self, app_for_viewer: TestClient) -> None:
        resp = app_for_viewer.get("/api/v1/evaluators/built-in")
        cc = resp.headers.get("Cache-Control") or resp.headers.get("cache-control")
        assert cc is not None
        assert "max-age=300" in cc


@pytest.mark.unit
class TestValidateEndpoint:
    """``POST /api/v1/evaluators/validate`` — 사전 검증."""

    def test_validate_returns_per_case_results(
        self, app_for_user: TestClient
    ) -> None:
        resp = app_for_user.post(
            "/api/v1/evaluators/validate",
            json={
                "code": "def evaluate(o,e,m): return 0.5",
                "test_cases": [
                    {"output": "x"},
                    {"output": "BAD-2"},
                ],
            },
        )
        assert resp.status_code == 200
        results = resp.json()["test_results"]
        assert results[0].get("result") == 0.5
        assert results[1].get("error")

    def test_viewer_cannot_call_validate(
        self, app_for_viewer: TestClient
    ) -> None:
        resp = app_for_viewer.post(
            "/api/v1/evaluators/validate",
            json={"code": "x", "test_cases": []},
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestSubmissionEndpoint:
    """제출 / 목록 / 단건 조회."""

    def test_user_creates_pending_submission(
        self, app_for_user: TestClient
    ) -> None:
        resp = app_for_user.post(
            "/api/v1/evaluators/submissions",
            json={
                "name": "my",
                "description": "test",
                "code": "def evaluate(o,e,m): return 1.0",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["submitted_by"] == "user-1"
        assert "etag" in {k.lower() for k in resp.headers}

    def test_admin_auto_approves_submission(
        self, app_for_admin: TestClient
    ) -> None:
        resp = app_for_admin.post(
            "/api/v1/evaluators/submissions",
            json={
                "name": "auto",
                "description": "t",
                "code": "def evaluate(o,e,m): return 1.0",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "approved"
        assert body["approved_by"] == "admin-1"

    def test_submission_with_invalid_code_returns_422(
        self, app_for_user: TestClient
    ) -> None:
        resp = app_for_user.post(
            "/api/v1/evaluators/submissions",
            json={
                "name": "bad",
                "description": "t",
                "code": "def evaluate(o,e,m): return 0.0",
                "test_cases": [{"output": "BAD-1"}],
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        # Problem Details: code=invalid_code (LabsError 핸들러)
        assert body.get("code") == "invalid_code"

    def test_viewer_cannot_submit(self, app_for_viewer: TestClient) -> None:
        resp = app_for_viewer.post(
            "/api/v1/evaluators/submissions",
            json={"name": "x", "description": "t", "code": "y"},
        )
        assert resp.status_code == 403

    def test_user_list_only_returns_own(
        self,
        app_for_user: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        # 다른 사용자 것을 직접 서비스로 생성 → 본인 응답에 포함되면 안 됨
        # (라우터는 user-1로 인증)
        # 본인 제출
        app_for_user.post(
            "/api/v1/evaluators/submissions",
            json={"name": "mine", "description": "d", "code": "z"},
        )
        # 다른 사용자 — 서비스 직접 호출
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="other",
                is_admin=False,
                name="other-eval",
                description="d",
                code="zz",
                test_cases=None,
            )
        )
        resp = app_for_user.get("/api/v1/evaluators/submissions")
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["submitted_by"] == "user-1"

    def test_get_other_user_submission_returns_404(
        self,
        app_for_user: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="other",
                is_admin=False,
                name="x",
                description="d",
                code="y",
                test_cases=None,
            )
        )
        resp = app_for_user.get(
            f"/api/v1/evaluators/submissions/{sub.submission_id}"
        )
        assert resp.status_code == 404


# ============================================================ #
# 3) 라우터 — approve / reject / deprecate (admin only)
# ============================================================ #
@pytest.mark.unit
class TestAdminGovernanceEndpoints:
    """admin 거버넌스 액션 — ETag/If-Match, 알림, 권한."""

    def test_user_cannot_approve(
        self,
        app_for_user: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="x",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        resp = app_for_user.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/approve",
            json={},
        )
        assert resp.status_code == 403

    def test_admin_approve_changes_status_and_returns_etag(
        self,
        app_for_admin: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="x",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        resp = app_for_admin.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/approve",
            json={"note": "ok"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["approved_by"] == "admin-1"
        assert "etag" in {k.lower() for k in resp.headers}

    def test_admin_approve_with_stale_if_match_returns_412(
        self,
        app_for_admin: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="x",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        resp = app_for_admin.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/approve",
            headers={"If-Match": '"stale-etag"'},
            json={},
        )
        assert resp.status_code == 412

    def test_admin_reject_requires_reason(
        self,
        app_for_admin: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="x",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        resp = app_for_admin.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/reject",
            json={},
        )
        # reason 필드 누락 → 422
        assert resp.status_code == 422

    def test_admin_reject_with_reason_succeeds(
        self,
        app_for_admin: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="x",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        resp = app_for_admin.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/reject",
            json={"reason": "보안 위험"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["rejection_reason"] == "보안 위험"

    def test_admin_deprecate_only_after_approved(
        self,
        app_for_admin: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        # pending 상태에서 시도 → 409
        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="x",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        r1 = app_for_admin.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/deprecate"
        )
        assert r1.status_code == 409

    def test_admin_deprecate_after_approval(
        self,
        app_for_admin: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        sub = asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="admin-1",
                is_admin=True,
                name="auto",
                description="d",
                code="z",
                test_cases=None,
            )
        )
        resp = app_for_admin.post(
            f"/api/v1/evaluators/submissions/{sub.submission_id}/deprecate"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deprecated"


# ============================================================ #
# 4) 라우터 — approved 카탈로그 + score-configs
# ============================================================ #
@pytest.mark.unit
class TestApprovedCatalogEndpoint:
    """``GET /api/v1/evaluators/approved`` — 모든 사용자 사용."""

    def test_returns_only_approved(
        self,
        app_for_user: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        # pending + approved 1개씩
        asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="user-1",
                is_admin=False,
                name="p",
                description="d",
                code="z1",
                test_cases=None,
            )
        )
        asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="admin-1",
                is_admin=True,
                name="a",
                description="d",
                code="z2",
                test_cases=None,
            )
        )
        resp = app_for_user.get("/api/v1/evaluators/approved")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "approved"

    def test_other_user_code_is_masked(
        self,
        app_for_user: TestClient,
        governance: EvaluatorGovernanceService,
    ) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            governance.submit(
                user_id="other",
                is_admin=True,  # auto-approve
                name="x",
                description="d",
                code="SECRET-CODE-BODY",
                test_cases=None,
            )
        )
        resp = app_for_user.get("/api/v1/evaluators/approved")
        body = resp.json()
        assert body["items"][0]["code"] == ""
        assert body["items"][0]["code_hash"]


@pytest.mark.unit
class TestScoreConfigsEndpoint:
    """``GET /api/v1/evaluators/score-configs`` — admin only."""

    def test_user_forbidden(self, app_for_user: TestClient) -> None:
        resp = app_for_user.get("/api/v1/evaluators/score-configs")
        assert resp.status_code == 403

    def test_admin_returns_catalog(self, app_for_admin: TestClient) -> None:
        resp = app_for_admin.get("/api/v1/evaluators/score-configs")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 13
        # SDK가 list_score_configs 미지원 → 모두 registered
        statuses = {item["status"] for item in body}
        assert "registered" in statuses
