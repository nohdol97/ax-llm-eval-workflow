"""프로젝트 라우터 단위 테스트.

검증 범위:
- ``GET /projects`` 목록 (정적 config 기반)
- ``POST /projects/switch`` 정상/404
- RBAC 강제 (viewer→list OK, viewer→switch 403)
- ``LABS_PROJECTS_JSON`` 파싱 동작 (config side)
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.deps import get_app_settings
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User

ADMIN = User(id="admin-1", email="admin@example.com", role="admin")
USER = User(id="user-1", email="user@example.com", role="user")
VIEWER = User(id="viewer-1", email="viewer@example.com", role="viewer")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """settings 캐시 격리."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _override_user(user: User) -> Any:
    def _resolver() -> User:
        return user

    return _resolver


def _settings_with_projects(payload: list[dict[str, Any]]) -> Settings:
    return Settings(
        LABS_ENV="dev",
        LABS_PROJECTS_JSON=json.dumps(payload),
    )


def _make_client(user: User, settings: Settings) -> TestClient:
    """라우터 테스트용 TestClient (의존성 override)."""
    app = create_app()
    app.dependency_overrides[get_current_user] = _override_user(user)
    app.dependency_overrides[get_app_settings] = lambda: settings
    return TestClient(app)


# ---------- GET /projects ----------
@pytest.mark.unit
class TestListProjects:
    """``GET /api/v1/projects``."""

    def test_returns_configured_projects(self) -> None:
        """등록된 프로젝트 카탈로그 반환."""
        settings = _settings_with_projects(
            [
                {"id": "p1", "name": "Project One", "description": "first"},
                {"id": "p2", "name": "Project Two"},
            ]
        )
        client = _make_client(VIEWER, settings)
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        ids = sorted(it["id"] for it in body["items"])
        assert ids == ["p1", "p2"]
        # 시크릿 노출 금지
        for item in body["items"]:
            assert "langfuse_secret_key" not in item
            assert "langfuse_public_key" not in item

    def test_default_project_when_unconfigured(self) -> None:
        """``LABS_PROJECTS_JSON`` 미설정 → ``default`` 1건."""
        settings = Settings(LABS_PROJECTS_JSON="")
        client = _make_client(VIEWER, settings)
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "default"

    def test_viewer_can_list(self) -> None:
        """viewer 권한으로 list 접근 가능."""
        settings = _settings_with_projects([{"id": "p1", "name": "Project One"}])
        client = _make_client(VIEWER, settings)
        resp = client.get("/api/v1/projects")
        assert resp.status_code == 200


# ---------- POST /projects/switch ----------
@pytest.mark.unit
class TestSwitchProject:
    """``POST /api/v1/projects/switch``."""

    def test_user_can_switch_existing(self) -> None:
        """user 권한 + 등록 프로젝트 → 200 + echo."""
        settings = _settings_with_projects(
            [
                {"id": "p1", "name": "One"},
                {"id": "p2", "name": "Two"},
            ]
        )
        client = _make_client(USER, settings)
        resp = client.post(
            "/api/v1/projects/switch",
            json={"project_id": "p2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"project_id": "p2", "name": "Two"}

    def test_admin_can_switch(self) -> None:
        """admin 권한도 가능."""
        settings = _settings_with_projects([{"id": "p1", "name": "One"}])
        client = _make_client(ADMIN, settings)
        resp = client.post("/api/v1/projects/switch", json={"project_id": "p1"})
        assert resp.status_code == 200

    def test_viewer_cannot_switch(self) -> None:
        """viewer → 403 (user 이상 필요)."""
        settings = _settings_with_projects([{"id": "p1", "name": "One"}])
        client = _make_client(VIEWER, settings)
        resp = client.post("/api/v1/projects/switch", json={"project_id": "p1"})
        assert resp.status_code == 403

    def test_unknown_project_returns_404(self) -> None:
        """미등록 프로젝트 → 404 PROJECT_NOT_FOUND."""
        settings = _settings_with_projects([{"id": "p1", "name": "One"}])
        client = _make_client(USER, settings)
        resp = client.post("/api/v1/projects/switch", json={"project_id": "ghost"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "PROJECT_NOT_FOUND"

    def test_missing_project_id_returns_422(self) -> None:
        """body에 project_id 없음 → 422."""
        settings = _settings_with_projects([{"id": "p1", "name": "One"}])
        client = _make_client(USER, settings)
        resp = client.post("/api/v1/projects/switch", json={})
        assert resp.status_code == 422

    def test_extra_field_rejected(self) -> None:
        """``extra='forbid'`` → 추가 필드 422."""
        settings = _settings_with_projects([{"id": "p1", "name": "One"}])
        client = _make_client(USER, settings)
        resp = client.post(
            "/api/v1/projects/switch",
            json={"project_id": "p1", "evil": "yes"},
        )
        assert resp.status_code == 422


# ---------- 설정 파싱 단위 ----------
@pytest.mark.unit
class TestProjectsConfigParsing:
    """``Settings.projects()`` 파싱 동작."""

    def test_default_when_unset(self) -> None:
        """미설정 시 기본 1건."""
        settings = Settings(LABS_PROJECTS_JSON="")
        result = settings.projects()
        assert len(result) == 1
        assert result[0]["id"] == "default"

    def test_parses_valid_array(self) -> None:
        """정상 배열 파싱."""
        payload = json.dumps(
            [
                {"id": "a", "name": "A"},
                {"id": "b", "name": "B"},
            ]
        )
        settings = Settings(LABS_PROJECTS_JSON=payload)
        result = settings.projects()
        assert len(result) == 2

    def test_invalid_json_raises_value_error(self) -> None:
        """잘못된 JSON → ValueError."""
        settings = Settings(LABS_PROJECTS_JSON="garbage")
        with pytest.raises(ValueError):
            settings.projects()

    def test_non_list_raises_value_error(self) -> None:
        """배열이 아닌 JSON → ValueError."""
        settings = Settings(LABS_PROJECTS_JSON='{"id":"x"}')
        with pytest.raises(ValueError):
            settings.projects()

    def test_non_dict_entry_raises_value_error(self) -> None:
        """배열의 항목이 dict가 아니면 ValueError."""
        settings = Settings(LABS_PROJECTS_JSON='[{"id":"x","name":"X"}, "bad"]')
        with pytest.raises(ValueError):
            settings.projects()
