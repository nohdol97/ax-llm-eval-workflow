"""Auto-Eval API 라우터 단위 테스트 (Phase 8-B-2).

검증 항목 (``docs/AGENT_EVAL.md`` §13):
- POST   /policies                      201 + 본문/Idempotency-Key
- GET    /policies?project_id&status    페이지네이션 + 필터
- GET    /policies/{id}                 ETag 헤더
- PATCH  /policies/{id}                 owner check + If-Match 412
- DELETE /policies/{id}                 admin only + If-Match
- POST   /policies/{id}/pause           owner/admin
- POST   /policies/{id}/resume          owner/admin
- POST   /policies/{id}/run-now         active 아닐 시 409
- GET    /runs?policy_id                목록
- GET    /runs/{id}                     단건
- GET    /runs/{id}/items               placeholder
- GET    /policies/{id}/cost-usage      from > to → 422
- 401 (인증 미포함), 403 (다른 사용자 수정)

테스트는 ``app.dependency_overrides`` 로 ``get_auto_eval_repo`` / ``get_auto_eval_engine``
및 ``get_current_user`` 을 mock 으로 교체하여 실제 Redis / Engine 동작과 분리한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.deps import (
    get_auto_eval_engine,
    get_auto_eval_repo,
)
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.models.auto_eval import (
    AlertThreshold,
    AutoEvalPolicy,
    AutoEvalPolicyCreate,
    AutoEvalPolicyUpdate,
    AutoEvalRun,
    AutoEvalSchedule,
)
from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def _make_policy(
    *,
    policy_id: str = "policy_abc123",
    owner: str = "user-owner",
    name: str = "qa-agent v3",
    status: str = "active",
    daily_cost_limit_usd: float | None = None,
    alert_thresholds: list[AlertThreshold] | None = None,
) -> AutoEvalPolicy:
    """테스트용 ``AutoEvalPolicy`` 픽스처 빌더."""
    return AutoEvalPolicy(
        id=policy_id,
        name=name,
        description="QA 에이전트 회귀 모니터링",
        project_id="proj-1",
        trace_filter=TraceFilter(project_id="proj-1", name="qa-agent"),
        evaluators=[EvaluatorConfig(type="builtin", name="exact_match", config={}, weight=1.0)],
        schedule=AutoEvalSchedule(type="interval", interval_seconds=3600),
        alert_thresholds=alert_thresholds or [],
        notification_targets=[],
        daily_cost_limit_usd=daily_cost_limit_usd,
        status=status,  # type: ignore[arg-type]
        owner=owner,
        created_at=T0,
        updated_at=T0,
    )


class _MockRepo:
    """``AutoEvalRepo`` 의 인메모리 mock — 라우터 통합 테스트용.

    컨텍스트 명세의 시그니처 그대로 (``create_policy(payload, owner=...)`` 등).
    """

    def __init__(self) -> None:
        self.policies: dict[str, AutoEvalPolicy] = {}
        self.runs: dict[str, AutoEvalRun] = {}
        self._policy_seq = 0
        self._run_seq = 0

    # ---------- Policy ----------
    async def create_policy(self, payload: AutoEvalPolicyCreate, *, owner: str) -> AutoEvalPolicy:
        self._policy_seq += 1
        pid = f"policy_test{self._policy_seq:03d}"
        now = datetime.now(UTC)
        policy = AutoEvalPolicy(
            id=pid,
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
            status="active",
            owner=owner,
            created_at=now,
            updated_at=now,
        )
        self.policies[pid] = policy
        return policy

    async def get_policy(self, policy_id: str) -> AutoEvalPolicy:
        if policy_id not in self.policies:
            from app.core.errors import LabsError

            class _NotFoundError(LabsError):
                code = "auto_eval_policy_not_found"
                status_code = 404
                title = "Auto-Eval policy not found"

            raise _NotFoundError(detail=f"정책 미존재: {policy_id!r}")
        return self.policies[policy_id]

    async def list_policies(
        self,
        project_id: str,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AutoEvalPolicy], int]:
        items = [
            p
            for p in self.policies.values()
            if p.project_id == project_id and (status is None or p.status == status)
        ]
        items.sort(key=lambda p: p.created_at, reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    async def update_policy(self, policy_id: str, updates: AutoEvalPolicyUpdate) -> AutoEvalPolicy:
        existing = await self.get_policy(policy_id)
        update_dict = updates.model_dump(exclude_unset=True)
        merged = existing.model_dump()
        merged.update(update_dict)
        merged["updated_at"] = datetime.now(UTC)
        new_policy = AutoEvalPolicy.model_validate(merged)
        self.policies[policy_id] = new_policy
        return new_policy

    async def delete_policy(self, policy_id: str) -> None:
        await self.get_policy(policy_id)
        del self.policies[policy_id]

    async def pause_policy(self, policy_id: str) -> AutoEvalPolicy:
        return await self.update_policy(policy_id, AutoEvalPolicyUpdate(status="paused"))

    async def resume_policy(self, policy_id: str) -> AutoEvalPolicy:
        return await self.update_policy(policy_id, AutoEvalPolicyUpdate(status="active"))

    # ---------- Run ----------
    async def list_runs(
        self,
        policy_id: str,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AutoEvalRun], int]:
        items = [
            r
            for r in self.runs.values()
            if r.policy_id == policy_id and (status is None or r.status == status)
        ]
        items.sort(key=lambda r: r.started_at, reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return items[start : start + page_size], total

    async def get_run(self, run_id: str) -> AutoEvalRun:
        if run_id not in self.runs:
            from app.core.errors import LabsError

            class _NotFoundError(LabsError):
                code = "auto_eval_run_not_found"
                status_code = 404
                title = "Auto-Eval run not found"

            raise _NotFoundError(detail=f"run 미존재: {run_id!r}")
        return self.runs[run_id]

    # ---------- Cost ----------
    async def get_cost_usage(self, policy_id: str, from_date: Any, to_date: Any) -> dict[str, Any]:
        return {
            "policy_id": policy_id,
            "date_range": f"{from_date.isoformat()}:{to_date.isoformat()}",
            "daily_breakdown": [],
            "total_cost_usd": 0.0,
            "daily_limit_usd": None,
        }


class _MockEngine:
    """``AutoEvalEngine`` 의 mock — ``run_policy`` 만 노출."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_policy(self, policy_id: str) -> Any:
        self.calls.append(policy_id)
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_repo() -> _MockRepo:
    return _MockRepo()


@pytest.fixture
def mock_engine() -> _MockEngine:
    return _MockEngine()


@pytest.fixture
def admin_user() -> User:
    return User(id="user-admin-1", email="a@x.com", role="admin")


@pytest.fixture
def owner_user() -> User:
    return User(id="user-owner", email="o@x.com", role="user")


@pytest.fixture
def other_user() -> User:
    return User(id="user-other", email="b@x.com", role="user")


@pytest.fixture
def viewer_user() -> User:
    return User(id="user-viewer", email="v@x.com", role="viewer")


def _make_client(
    user: User,
    *,
    repo: _MockRepo,
    engine: _MockEngine,
) -> TestClient:
    """라우터 통합 테스트용 ``TestClient`` 생성 — repo/engine/user 주입."""
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_auto_eval_repo] = lambda: repo
    app.dependency_overrides[get_auto_eval_engine] = lambda: engine
    # require_role 도 user 를 그대로 통과시키기 위해 함께 override

    def _allow(_role: str) -> Any:
        def _dep() -> User:
            return user

        return _dep

    # FastAPI dependency override 시 ``require_role`` 의 inner 함수를 직접 잡기
    # 어렵기 때문에, 라우터에 등록된 ``Depends(require_role("user"))`` 인스턴스가
    # ``get_current_user`` 를 내부에서 호출하므로 위 override 만으로도 통과한다.
    return TestClient(app)


def _payload_template() -> dict[str, Any]:
    """POST /policies 에 사용할 최소 유효 payload."""
    return {
        "name": "qa-agent monitor",
        "description": "regression watchdog",
        "project_id": "proj-1",
        "trace_filter": {
            "project_id": "proj-1",
            "name": "qa-agent",
        },
        "evaluators": [
            {
                "type": "builtin",
                "name": "exact_match",
                "config": {},
                "weight": 1.0,
            }
        ],
        "schedule": {
            "type": "interval",
            "interval_seconds": 3600,
        },
        "alert_thresholds": [],
        "notification_targets": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestCreatePolicy:
    """POST /api/v1/auto-eval/policies."""

    def test_creates_with_user_role(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(
            "/api/v1/auto-eval/policies",
            json=_payload_template(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "qa-agent monitor"
        assert body["owner"] == owner_user.id
        # ETag 헤더 부착
        assert "etag" in {k.lower() for k in resp.headers}

    def test_accepts_idempotency_key(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        """Idempotency-Key 헤더 수용 (현재 placeholder)."""
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(
            "/api/v1/auto-eval/policies",
            json=_payload_template(),
            headers={"Idempotency-Key": "abc-123"},
        )
        assert resp.status_code == 201

    def test_validates_evaluators_required(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        """evaluators 빈 list 면 422 (모델 validator)."""
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        bad = _payload_template()
        bad["evaluators"] = []
        resp = client.post("/api/v1/auto-eval/policies", json=bad)
        assert resp.status_code == 422

    def test_unauthenticated_rejected(
        self,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        """인증 헤더 없는 요청 → 401."""
        app = create_app()
        # repo/engine 만 override, get_current_user 는 그대로 (실제 검증)
        app.dependency_overrides[get_auto_eval_repo] = lambda: mock_repo
        app.dependency_overrides[get_auto_eval_engine] = lambda: mock_engine
        client = TestClient(app)
        resp = client.post("/api/v1/auto-eval/policies", json=_payload_template())
        assert resp.status_code == 401


@pytest.mark.unit
class TestListPolicies:
    """GET /api/v1/auto-eval/policies."""

    async def test_pagination_and_status_filter(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        # 3개 active + 2개 paused 정책 시드
        for i in range(3):
            policy = _make_policy(policy_id=f"policy_a{i}", status="active")
            mock_repo.policies[policy.id] = policy
        for i in range(2):
            policy = _make_policy(policy_id=f"policy_p{i}", status="paused")
            mock_repo.policies[policy.id] = policy

        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        # 전체
        resp = client.get(
            "/api/v1/auto-eval/policies",
            params={"project_id": "proj-1", "page_size": 10},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 5

        # active 만
        resp2 = client.get(
            "/api/v1/auto-eval/policies",
            params={"project_id": "proj-1", "status": "active"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["total"] == 3

        # 페이지네이션 — page=2, page_size=2 → 2개
        resp3 = client.get(
            "/api/v1/auto-eval/policies",
            params={"project_id": "proj-1", "page": 2, "page_size": 2},
        )
        assert resp3.json()["page"] == 2
        assert len(resp3.json()["items"]) == 2

    def test_project_id_required(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get("/api/v1/auto-eval/policies")
        assert resp.status_code == 422


@pytest.mark.unit
class TestGetPolicy:
    """GET /api/v1/auto-eval/policies/{id}."""

    def test_returns_etag_header(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy()
        mock_repo.policies[policy.id] = policy
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get(f"/api/v1/auto-eval/policies/{policy.id}")
        assert resp.status_code == 200
        # ETag 부착 + 따옴표
        etag = resp.headers.get("etag") or resp.headers.get("ETag")
        assert etag is not None
        assert etag.startswith('"') and etag.endswith('"')
        assert resp.json()["id"] == policy.id

    def test_unknown_returns_404(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get("/api/v1/auto-eval/policies/unknown")
        assert resp.status_code == 404


@pytest.mark.unit
class TestUpdatePolicy:
    """PATCH /api/v1/auto-eval/policies/{id}."""

    def test_owner_can_update(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id)
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.patch(
            f"/api/v1/auto-eval/policies/{policy.id}",
            json={"description": "수정됨"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "수정됨"

    def test_admin_can_update_any(
        self,
        admin_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner="someone-else")
        mock_repo.policies[policy.id] = policy
        client = _make_client(admin_user, repo=mock_repo, engine=mock_engine)
        resp = client.patch(
            f"/api/v1/auto-eval/policies/{policy.id}",
            json={"description": "admin update"},
        )
        assert resp.status_code == 200

    def test_non_owner_user_forbidden(
        self,
        other_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner="user-owner")
        mock_repo.policies[policy.id] = policy
        client = _make_client(other_user, repo=mock_repo, engine=mock_engine)
        resp = client.patch(
            f"/api/v1/auto-eval/policies/{policy.id}",
            json={"description": "권한 없음"},
        )
        assert resp.status_code == 403

    def test_if_match_mismatch_returns_412(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id)
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.patch(
            f"/api/v1/auto-eval/policies/{policy.id}",
            json={"description": "x"},
            headers={"If-Match": '"deadbeef00000000"'},
        )
        assert resp.status_code == 412

    def test_if_match_wildcard_passes(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id)
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.patch(
            f"/api/v1/auto-eval/policies/{policy.id}",
            json={"description": "x"},
            headers={"If-Match": "*"},
        )
        assert resp.status_code == 200


@pytest.mark.unit
class TestDeletePolicy:
    """DELETE /api/v1/auto-eval/policies/{id}."""

    def test_admin_can_delete(
        self,
        admin_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy()
        mock_repo.policies[policy.id] = policy
        client = _make_client(admin_user, repo=mock_repo, engine=mock_engine)
        resp = client.delete(f"/api/v1/auto-eval/policies/{policy.id}")
        assert resp.status_code == 204
        assert policy.id not in mock_repo.policies

    def test_user_role_forbidden(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id)
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.delete(f"/api/v1/auto-eval/policies/{policy.id}")
        assert resp.status_code == 403

    def test_if_match_mismatch_412(
        self,
        admin_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy()
        mock_repo.policies[policy.id] = policy
        client = _make_client(admin_user, repo=mock_repo, engine=mock_engine)
        resp = client.delete(
            f"/api/v1/auto-eval/policies/{policy.id}",
            headers={"If-Match": '"wrong0000000000"'},
        )
        assert resp.status_code == 412


@pytest.mark.unit
class TestPauseResume:
    """POST /api/v1/auto-eval/policies/{id}/{pause,resume}."""

    def test_owner_can_pause(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id)
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(f"/api/v1/auto-eval/policies/{policy.id}/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_owner_can_resume(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id, status="paused")
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(f"/api/v1/auto-eval/policies/{policy.id}/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_other_user_pause_forbidden(
        self,
        other_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner="user-owner")
        mock_repo.policies[policy.id] = policy
        client = _make_client(other_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(f"/api/v1/auto-eval/policies/{policy.id}/pause")
        assert resp.status_code == 403


@pytest.mark.unit
class TestRunNow:
    """POST /api/v1/auto-eval/policies/{id}/run-now."""

    def test_active_policy_returns_202(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id, status="active")
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(f"/api/v1/auto-eval/policies/{policy.id}/run-now")
        assert resp.status_code == 202
        assert resp.json()["status"] == "running"
        assert resp.json()["id"] == "pending"

    def test_paused_policy_returns_409(
        self,
        owner_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(owner=owner_user.id, status="paused")
        mock_repo.policies[policy.id] = policy
        client = _make_client(owner_user, repo=mock_repo, engine=mock_engine)
        resp = client.post(f"/api/v1/auto-eval/policies/{policy.id}/run-now")
        assert resp.status_code == 409


@pytest.mark.unit
class TestRuns:
    """GET /api/v1/auto-eval/runs."""

    def test_lists_runs_for_policy(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        for i in range(3):
            run = AutoEvalRun(
                id=f"run_{i:03d}",
                policy_id="policy_test001",
                started_at=T0 + timedelta(minutes=i),
                status="completed",
                avg_score=0.85,
                pass_rate=0.9,
                cost_usd=0.0,
            )
            mock_repo.runs[run.id] = run
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get(
            "/api/v1/auto-eval/runs",
            params={"policy_id": "policy_test001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3

    def test_run_detail(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        run = AutoEvalRun(
            id="run_xyz",
            policy_id="policy_test001",
            started_at=T0,
            status="completed",
            avg_score=0.85,
            pass_rate=0.9,
            cost_usd=0.0,
        )
        mock_repo.runs[run.id] = run
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get(f"/api/v1/auto-eval/runs/{run.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == run.id

    def test_run_items_placeholder(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get("/api/v1/auto-eval/runs/run_test/items")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0


@pytest.mark.unit
class TestCostUsage:
    """GET /api/v1/auto-eval/policies/{id}/cost-usage."""

    def test_returns_breakdown(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy(daily_cost_limit_usd=10.0)
        mock_repo.policies[policy.id] = policy
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get(
            f"/api/v1/auto-eval/policies/{policy.id}/cost-usage",
            params={"from_date": "2026-04-01", "to_date": "2026-04-03"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["policy_id"] == policy.id
        # daily_limit_usd 가 정책에서 채워짐
        assert body["daily_limit_usd"] == 10.0

    def test_from_after_to_returns_422(
        self,
        viewer_user: User,
        mock_repo: _MockRepo,
        mock_engine: _MockEngine,
    ) -> None:
        policy = _make_policy()
        mock_repo.policies[policy.id] = policy
        client = _make_client(viewer_user, repo=mock_repo, engine=mock_engine)
        resp = client.get(
            f"/api/v1/auto-eval/policies/{policy.id}/cost-usage",
            params={"from_date": "2026-04-10", "to_date": "2026-04-01"},
        )
        assert resp.status_code == 422


@pytest.mark.unit
class TestEtagHelpers:
    """ETag/If-Match 헬퍼 단위 — 라우터 외부."""

    def test_etag_excludes_updated_at(self) -> None:
        from app.api.v1.auto_eval import _compute_etag

        a = _make_policy()
        b = a.model_copy(update={"updated_at": a.updated_at + timedelta(seconds=1)})
        # updated_at 만 다르면 ETag 동일
        assert _compute_etag(a) == _compute_etag(b)

    def test_if_match_wildcard_passes(self) -> None:
        from app.api.v1.auto_eval import _check_if_match

        # 어떤 etag 든 통과
        _check_if_match("*", "anything")

    def test_if_match_none_skips(self) -> None:
        from app.api.v1.auto_eval import _check_if_match

        # None → skip
        _check_if_match(None, "x")

    def test_if_match_mismatch_raises(self) -> None:
        from fastapi import HTTPException

        from app.api.v1.auto_eval import _check_if_match

        with pytest.raises(HTTPException) as exc_info:
            _check_if_match('"wrong"', "right")
        assert exc_info.value.status_code == 412

    def test_if_match_quoted_match_passes(self) -> None:
        from app.api.v1.auto_eval import _check_if_match

        _check_if_match('"abc123"', "abc123")
