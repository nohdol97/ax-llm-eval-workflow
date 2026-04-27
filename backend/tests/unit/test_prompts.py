"""프롬프트 라우터 + 변수 추출 단위 테스트.

검증 범위:
- ``extract_variables`` 변수 추출 (text, chat, edge cases)
- ``GET /prompts`` 목록 (페이지네이션, project 검증)
- ``GET /prompts/{name}`` 상세 (변수 자동 파싱)
- ``GET /prompts/{name}/versions`` 버전 목록
- ``POST /prompts`` 신규 버전 생성 (RBAC, Idempotency-Key)
- ``PATCH /prompts/{name}/versions/{version}/labels`` 라벨 승격 (admin only, ETag)
- RBAC 강제 (viewer→403 for write, user→403 for label promotion)

실 외부 의존성 없음 — ``MockLangfuseClient``를 ``app.dependency_overrides``로 주입하고,
``get_current_user``/``require_role``도 stub 사용자로 override하여 JWT 검증을 우회한다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.deps import (
    get_app_settings,
    get_langfuse_client,
)
from app.core.security import get_current_user, require_role
from app.main import create_app
from app.models.auth import RBACRole, User
from app.models.project import ProjectConfig
from app.services.prompt_utils import extract_variables
from tests.fixtures.mock_langfuse import MockLangfuseClient


# ---------- 1) 변수 추출 단위 ----------
@pytest.mark.unit
class TestExtractVariables:
    """``extract_variables`` 정규식 + 입력 형태별 동작."""

    def test_single_variable(self) -> None:
        """단일 변수 추출."""
        assert extract_variables("Hello {{name}}") == ["name"]

    def test_multiple_variables_preserve_order(self) -> None:
        """발견 순서 보존."""
        result = extract_variables("Hi {{user}}, today is {{date}}, see {{topic}}")
        assert result == ["user", "date", "topic"]

    def test_duplicate_variables_deduplicated(self) -> None:
        """중복 제거 (최초 출현 순서 유지)."""
        result = extract_variables("{{a}} {{b}} {{a}} {{c}} {{b}}")
        assert result == ["a", "b", "c"]

    def test_whitespace_inside_braces(self) -> None:
        """``{{ var }}`` 공백 허용."""
        assert extract_variables("Hello {{   greeting   }}") == ["greeting"]

    def test_no_variables_returns_empty(self) -> None:
        """변수 없음 → 빈 리스트."""
        assert extract_variables("No placeholders here") == []

    def test_empty_string(self) -> None:
        """빈 문자열 → 빈 리스트."""
        assert extract_variables("") == []

    def test_chat_messages_string_content(self) -> None:
        """chat 형식 (content=str) 변수 추출."""
        messages = [
            {"role": "system", "content": "You are {{persona}}."},
            {"role": "user", "content": "Question: {{question}}"},
        ]
        assert extract_variables(messages) == ["persona", "question"]

    def test_chat_messages_list_content(self) -> None:
        """chat 형식 (content=[{type:text, text:...}])."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello {{a}}"},
                    {"type": "text", "text": "Goodbye {{b}}"},
                ],
            }
        ]
        assert extract_variables(messages) == ["a", "b"]

    def test_invalid_variable_names_ignored(self) -> None:
        """숫자로 시작하는 등 잘못된 식별자는 매치되지 않음."""
        assert extract_variables("{{123abc}} {{ valid_one }}") == ["valid_one"]

    def test_unmatched_braces_ignored(self) -> None:
        """단일 중괄호는 무시."""
        assert extract_variables("{not_a_var} {{ok}}") == ["ok"]


# ---------- 2) 라우터 fixtures ----------
ADMIN = User(id="admin-1", email="admin@example.com", role="admin")
USER = User(id="user-1", email="user@example.com", role="user")
VIEWER = User(id="viewer-1", email="viewer@example.com", role="viewer")


def _override_user(user: User) -> Any:
    """``get_current_user``/``require_role`` 의존성을 ``user``로 고정.

    각 라우터는 ``Depends(require_role("..."))``로 의존성을 주입하므로,
    ``app.dependency_overrides``에 같은 callable을 키로 등록할 수 없다 (factory).
    대신 ``get_current_user``를 override하면 모든 ``require_role`` 의존성 트리
    하단에서 일관된 user를 사용한다 — 단, 권한 체크 로직 자체는 그대로 동작.
    """
    def _resolver() -> User:
        return user
    return _resolver


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """settings 캐시 격리 (환경변수 의존 회피)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_settings() -> Settings:
    """단위 테스트용 Settings — 단일 'default' 프로젝트만 등록."""
    return Settings(
        LABS_ENV="dev",
        LABS_PROJECTS_JSON=json.dumps(
            [
                {"id": "default", "name": "Default Project"},
                {"id": "proj_b", "name": "Project B"},
            ]
        ),
    )


def _client_with_user(
    user: User, langfuse: MockLangfuseClient
) -> TestClient:
    """주어진 사용자/Langfuse mock으로 dependency-override된 TestClient."""
    app = create_app()
    app.dependency_overrides[get_current_user] = _override_user(user)
    app.dependency_overrides[get_app_settings] = _make_settings
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse
    # get_project_configs는 settings에 의존하므로 자동 작동
    return TestClient(app)


def _seed_three_prompts(mock: MockLangfuseClient) -> None:
    """프롬프트 3개(서로 다른 name)를 mock에 주입."""
    mock._seed(
        prompts=[
            {
                "name": "summary",
                "body": "Summarize {{text}}",
                "version": 1,
                "labels": ["staging"],
                "tags": ["v1"],
            },
            {
                "name": "summary",
                "body": "Summarize {{text}} carefully",
                "version": 2,
                "labels": [],
                "tags": ["v2"],
            },
            {
                "name": "translate",
                "body": "Translate {{src}} to {{dest}}",
                "version": 1,
                "labels": ["production"],
            },
            {
                "name": "qa",
                "body": "Answer {{question}}",
                "version": 1,
            },
        ]
    )


# ---------- 3) GET /prompts ----------
@pytest.mark.unit
class TestListPrompts:
    """``GET /api/v1/prompts``."""

    def test_returns_latest_version_per_name(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """같은 이름의 여러 버전 중 최신만 노출."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get("/api/v1/prompts?project_id=default")
        assert resp.status_code == 200
        body = resp.json()
        names = sorted(item["name"] for item in body["items"])
        assert names == ["qa", "summary", "translate"]
        # summary는 v2가 노출됨
        summary = next(it for it in body["items"] if it["name"] == "summary")
        assert summary["latest_version"] == 2

    def test_pagination(self, langfuse_client: MockLangfuseClient) -> None:
        """page_size=2 + page=1 → 2건, page=2 → 1건."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(VIEWER, langfuse_client)
        resp1 = client.get(
            "/api/v1/prompts?project_id=default&page=1&page_size=2"
        )
        resp2 = client.get(
            "/api/v1/prompts?project_id=default&page=2&page_size=2"
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert len(resp1.json()["items"]) == 2
        assert len(resp2.json()["items"]) == 1
        assert resp1.json()["total"] == 3

    def test_unknown_project_returns_404(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """등록되지 않은 ``project_id`` → 404 PROJECT_NOT_FOUND."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get("/api/v1/prompts?project_id=nope")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "PROJECT_NOT_FOUND"

    def test_missing_project_id_returns_422(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``project_id`` 누락 → 422 VALIDATION."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get("/api/v1/prompts")
        assert resp.status_code == 422

    def test_page_size_too_large_returns_422(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``page_size > 100`` → 422."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get("/api/v1/prompts?project_id=default&page_size=200")
        assert resp.status_code == 422

    def test_empty_project_returns_zero_items(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """seed 없음 → items=[]."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get("/api/v1/prompts?project_id=default")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


# ---------- 4) GET /prompts/{name} ----------
@pytest.mark.unit
class TestGetPrompt:
    """``GET /api/v1/prompts/{name}``."""

    def test_detail_includes_extracted_variables(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """본문에서 ``{{var}}`` 추출되어 응답에 포함."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get(
            "/api/v1/prompts/translate?project_id=default"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "translate"
        assert body["version"] == 1
        assert body["variables"] == ["src", "dest"]
        assert body["type"] == "text"

    def test_specific_version(self, langfuse_client: MockLangfuseClient) -> None:
        """``version=1`` 명시 → 그 버전 반환."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get(
            "/api/v1/prompts/summary?project_id=default&version=1"
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 1
        assert resp.json()["prompt"] == "Summarize {{text}}"

    def test_label_resolves_to_version(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``label=production`` → 해당 라벨이 가리키는 버전."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get(
            "/api/v1/prompts/translate?project_id=default&label=production"
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 1

    def test_unknown_prompt_returns_404(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """존재하지 않는 프롬프트 → 404 PROMPT_NOT_FOUND."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get(
            "/api/v1/prompts/missing?project_id=default"
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "PROMPT_NOT_FOUND"


# ---------- 5) GET /prompts/{name}/versions ----------
@pytest.mark.unit
class TestListVersions:
    """``GET /api/v1/prompts/{name}/versions``."""

    def test_returns_all_versions_descending(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """버전 2,1 순서로 반환."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get(
            "/api/v1/prompts/summary/versions?project_id=default"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert [v["version"] for v in body["items"]] == [2, 1]

    def test_unknown_prompt_returns_404(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """존재 안 함 → 404."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.get("/api/v1/prompts/none/versions?project_id=default")
        assert resp.status_code == 404


# ---------- 6) POST /prompts ----------
@pytest.mark.unit
class TestCreatePrompt:
    """``POST /api/v1/prompts``."""

    def test_user_can_create_new_version(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """user 권한으로 신규 프롬프트 생성 → 201."""
        client = _client_with_user(USER, langfuse_client)
        resp = client.post(
            "/api/v1/prompts",
            json={
                "project_id": "default",
                "name": "new_prompt",
                "prompt": "Do {{task}}",
                "type": "text",
                "labels": ["staging"],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "new_prompt"
        assert body["version"] == 1
        assert "staging" in body["labels"]

    def test_create_increments_version(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """기존 이름으로 두 번째 호출 → version=2."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(USER, langfuse_client)
        resp = client.post(
            "/api/v1/prompts",
            json={
                "project_id": "default",
                "name": "summary",
                "prompt": "Summarize new",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["version"] == 3  # seed에 v1, v2 존재

    def test_viewer_cannot_create(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """viewer 권한 → 403 FORBIDDEN."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.post(
            "/api/v1/prompts",
            json={
                "project_id": "default",
                "name": "any",
                "prompt": "x",
            },
        )
        assert resp.status_code == 403

    def test_idempotency_key_echoed(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``Idempotency-Key`` 헤더는 응답 헤더로 echo."""
        client = _client_with_user(USER, langfuse_client)
        key = "11111111-2222-3333-4444-555555555555"
        resp = client.post(
            "/api/v1/prompts",
            json={"project_id": "default", "name": "p", "prompt": "x"},
            headers={"Idempotency-Key": key},
        )
        assert resp.status_code == 201
        assert resp.headers.get("Idempotency-Key") == key

    def test_unknown_project_returns_404(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``project_id`` 미등록 → 404 PROJECT_NOT_FOUND."""
        client = _client_with_user(USER, langfuse_client)
        resp = client.post(
            "/api/v1/prompts",
            json={"project_id": "ghost", "name": "p", "prompt": "x"},
        )
        assert resp.status_code == 404


# ---------- 7) PATCH /prompts/{name}/versions/{version}/labels ----------
def _expected_etag(name: str, version: int, labels: list[str]) -> str:
    """라우터의 ETag 산식과 동일한 값 계산 (테스트 검증용)."""
    payload = {"name": name, "version": version, "labels": sorted(labels)}
    serialized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()[:16]
    return f'"{digest}"'


@pytest.mark.unit
class TestUpdatePromptLabels:
    """``PATCH /api/v1/prompts/{name}/versions/{version}/labels``."""

    def test_admin_can_promote_with_wildcard_etag(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """admin + ``If-Match: *`` → 200, 라벨 업데이트."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(ADMIN, langfuse_client)
        resp = client.patch(
            "/api/v1/prompts/summary/versions/2/labels",
            json={"project_id": "default", "labels": ["production"]},
            headers={"If-Match": "*"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 2
        assert "production" in body["labels"]
        assert resp.headers.get("ETag")

    def test_admin_with_correct_etag(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """admin + 올바른 ETag → 200."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(ADMIN, langfuse_client)
        # 현재 라벨은 [] (summary v2)
        etag = _expected_etag("summary", 2, [])
        resp = client.patch(
            "/api/v1/prompts/summary/versions/2/labels",
            json={"project_id": "default", "labels": ["production"]},
            headers={"If-Match": etag},
        )
        assert resp.status_code == 200

    def test_etag_mismatch_returns_412(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``If-Match`` 불일치 → 412 ETAG_MISMATCH."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(ADMIN, langfuse_client)
        resp = client.patch(
            "/api/v1/prompts/summary/versions/2/labels",
            json={"project_id": "default", "labels": ["production"]},
            headers={"If-Match": '"deadbeefdeadbeef"'},
        )
        assert resp.status_code == 412
        assert resp.json()["code"] == "ETAG_MISMATCH"

    def test_missing_if_match_returns_412(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """``If-Match`` 헤더 없음 → 412."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(ADMIN, langfuse_client)
        resp = client.patch(
            "/api/v1/prompts/summary/versions/2/labels",
            json={"project_id": "default", "labels": ["production"]},
        )
        assert resp.status_code == 412

    def test_user_cannot_promote(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """user 권한 → 403 (admin only)."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(USER, langfuse_client)
        resp = client.patch(
            "/api/v1/prompts/summary/versions/2/labels",
            json={"project_id": "default", "labels": ["production"]},
            headers={"If-Match": "*"},
        )
        assert resp.status_code == 403

    def test_viewer_cannot_promote(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """viewer 권한 → 403."""
        client = _client_with_user(VIEWER, langfuse_client)
        resp = client.patch(
            "/api/v1/prompts/summary/versions/2/labels",
            json={"project_id": "default", "labels": ["production"]},
            headers={"If-Match": "*"},
        )
        assert resp.status_code == 403

    def test_unknown_version_returns_404(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """존재하지 않는 버전 → 404."""
        _seed_three_prompts(langfuse_client)
        client = _client_with_user(ADMIN, langfuse_client)
        resp = client.patch(
            "/api/v1/prompts/summary/versions/999/labels",
            json={"project_id": "default", "labels": ["production"]},
            headers={"If-Match": "*"},
        )
        assert resp.status_code == 404


# ---------- 8) Project config 검증 ----------
@pytest.mark.unit
class TestProjectConfigDeps:
    """``get_project_configs`` 직접 호출 — pydantic 검증 동작."""

    def test_valid_project_list(self) -> None:
        """JSON 정상 파싱."""
        settings = Settings(
            LABS_PROJECTS_JSON=json.dumps([{"id": "x", "name": "X"}])
        )
        configs = [
            ProjectConfig.model_validate(p) for p in settings.projects()
        ]
        assert len(configs) == 1
        assert configs[0].id == "x"

    def test_default_when_unset(self) -> None:
        """``LABS_PROJECTS_JSON`` 미설정 → ``default`` 1건."""
        settings = Settings(LABS_PROJECTS_JSON="")
        projects = settings.projects()
        assert len(projects) == 1
        assert projects[0]["id"] == "default"

    def test_invalid_json_raises(self) -> None:
        """잘못된 JSON → ValueError."""
        settings = Settings(LABS_PROJECTS_JSON="{not valid")
        with pytest.raises(ValueError):
            settings.projects()

    def test_non_list_json_raises(self) -> None:
        """배열이 아닌 JSON → ValueError."""
        settings = Settings(LABS_PROJECTS_JSON='{"id":"x"}')
        with pytest.raises(ValueError):
            settings.projects()


# ---------- 9) RBAC 의존성 직접 호출 (단위) ----------
@pytest.mark.unit
class TestRequireRoleDep:
    """``require_role`` 의존성 자체의 동작 검증 (라우터 비통과)."""

    @pytest.mark.parametrize(
        ("role", "required", "ok"),
        [
            ("admin", "admin", True),
            ("admin", "user", True),
            ("admin", "viewer", True),
            ("user", "admin", False),
            ("user", "user", True),
            ("user", "viewer", True),
            ("viewer", "admin", False),
            ("viewer", "user", False),
            ("viewer", "viewer", True),
        ],
    )
    def test_role_priority_matrix(
        self, role: RBACRole, required: RBACRole, ok: bool
    ) -> None:
        """RBAC 우선순위 매트릭스."""
        from app.core.errors import ForbiddenError

        user = User(id="x", role=role)
        checker = require_role(required)
        if ok:
            assert checker(current_user=user) is user
        else:
            with pytest.raises(ForbiddenError):
                checker(current_user=user)
