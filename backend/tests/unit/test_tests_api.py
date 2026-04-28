"""``POST /api/v1/tests/single`` 라우터 단위 테스트.

검증 범위:
- 인증 미포함 → 401
- 잘못된 prompt source → 422 (PromptSource validator)
- 스트리밍 응답: ``text/event-stream`` Content-Type, Cache-Control, X-Accel-Buffering
- 스트리밍 본문에 ``event: started/token/done`` 라인 포함
- 비스트리밍 응답: 200 JSON, trace_id/output/usage 포함
- ``run_streaming`` 의존성 주입 — Mock 클라이언트가 LiteLLM 호출 받음
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.deps import (
    get_context_engine,
    get_langfuse_client,
    get_litellm_client,
    get_single_test_runner,
)
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.services.context_engine import ContextEngine
from app.services.single_test_runner import SingleTestRunner
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy


def _make_user(role: str = "user", uid: str = "user-1") -> User:
    """가짜 User — dependency_overrides 주입용."""
    return User(id=uid, email=f"{uid}@x.com", role=role, name=uid, groups=[])


@pytest.fixture
def app_with_overrides(
    langfuse_client: MockLangfuseClient,
    litellm_client: MockLiteLLMProxy,
) -> Any:
    """기본 user 권한 + Mock 클라이언트가 주입된 FastAPI app."""
    app = create_app()
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
    app.dependency_overrides[get_litellm_client] = lambda: litellm_client
    app.dependency_overrides[get_context_engine] = lambda: ContextEngine()
    # Runner 의존성도 명시적으로 주입 (위 3개를 사용하는 합성 의존성)
    app.dependency_overrides[get_single_test_runner] = lambda: SingleTestRunner(
        langfuse=langfuse_client,  # type: ignore[arg-type]
        litellm=litellm_client,  # type: ignore[arg-type]
        context_engine=ContextEngine(),
    )
    app.dependency_overrides[get_current_user] = lambda: _make_user("user")
    return app


@pytest.fixture
def client(app_with_overrides: Any) -> TestClient:
    """기본 TestClient — user 권한."""
    return TestClient(app_with_overrides)


# ---------- 1) 인증 ----------
@pytest.mark.unit
class TestAuth:
    """인증/인가 흐름."""

    def test_인증_미포함시_401(self) -> None:
        """``get_current_user`` override 없이 호출 → 401."""
        app = create_app()
        with TestClient(app) as raw_client:
            resp = raw_client.post(
                "/api/v1/tests/single",
                json={
                    "project_id": "p",
                    "prompt": {"source": "inline", "body": "x", "type": "text"},
                    "model": "gpt-4o",
                    "stream": False,
                },
            )
        # 401 또는 403 (Authorization 헤더 부재)
        assert resp.status_code in (401, 403)


# ---------- 2) 검증 ----------
@pytest.mark.unit
class TestValidation:
    """PromptSource / SingleTestRequest 검증."""

    def test_langfuse_source_name_누락시_422(self, client: TestClient) -> None:
        """``source=langfuse``인데 name이 없으면 422."""
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "langfuse"},
                "model": "gpt-4o",
                "stream": False,
            },
        )
        assert resp.status_code == 422

    def test_inline_source_body_누락시_422(self, client: TestClient) -> None:
        """``source=inline``인데 body가 없으면 422."""
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline"},
                "model": "gpt-4o",
                "stream": False,
            },
        )
        assert resp.status_code == 422

    def test_unknown_source_422(self, client: TestClient) -> None:
        """알 수 없는 source 값은 422."""
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "unknown"},
                "model": "gpt-4o",
                "stream": False,
            },
        )
        assert resp.status_code == 422

    def test_chat_타입에_str_body_422(self, client: TestClient) -> None:
        """type=chat인데 body가 string이면 422."""
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline", "type": "chat", "body": "string"},
                "model": "gpt-4o",
                "stream": False,
            },
        )
        assert resp.status_code == 422

    def test_model_누락시_422(self, client: TestClient) -> None:
        """필수 ``model`` 필드 누락은 422."""
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline", "body": "x", "type": "text"},
                "stream": False,
            },
        )
        assert resp.status_code == 422


# ---------- 3) 스트리밍 ----------
@pytest.mark.unit
class TestStreaming:
    """SSE 스트리밍 응답 검증."""

    def test_sse_헤더_및_이벤트_시퀀스(
        self,
        client: TestClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """Content-Type / Cache-Control / X-Accel-Buffering 헤더 + 이벤트 시퀀스."""
        litellm_client.set_response("hello world")
        with client.stream(
            "POST",
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline", "body": "Hi {{n}}", "type": "text"},
                "variables": {"n": "X"},
                "model": "gpt-4o",
                "parameters": {"temperature": 0.0},
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers.get("cache-control") == "no-store"
            assert resp.headers.get("x-accel-buffering") == "no"

            body = "".join(resp.iter_text())

        # 최소한 started, token, done 이벤트가 포함되어야 함
        assert "event: started" in body
        assert "event: token" in body
        assert "event: done" in body
        # retry 지시문도 첫 부분에
        assert "retry: " in body

    def test_litellm_실패시_error_이벤트(
        self,
        client: TestClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """LiteLLM 실패 시 SSE에 ``event: error``가 포함된다."""
        from app.core.errors import LiteLLMError

        litellm_client.set_failure(LiteLLMError(detail="upstream down"))
        with client.stream(
            "POST",
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline", "body": "x", "type": "text"},
                "model": "gpt-4o",
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert "event: error" in body
        assert "LLM_ERROR" in body


# ---------- 4) 비스트리밍 ----------
@pytest.mark.unit
class TestNonStreaming:
    """JSON 응답 검증."""

    def test_정상_200_JSON(
        self,
        client: TestClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """비스트리밍 모드는 200 JSON으로 응답한다."""
        litellm_client.set_response("non-stream")
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline", "body": "Hi", "type": "text"},
                "model": "gpt-4o",
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["output"] == "non-stream"
        assert data["model"] == "gpt-4o"
        assert "trace_id" in data
        assert data["usage"]["total_tokens"] >= 1
        assert "started_at" in data
        assert "completed_at" in data

    def test_litellm_실패시_502(
        self,
        client: TestClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """비스트리밍 모드에서 LiteLLM 실패는 502 ProblemDetails."""
        from app.core.errors import LiteLLMError

        litellm_client.set_failure(LiteLLMError(detail="boom"))
        resp = client.post(
            "/api/v1/tests/single",
            json={
                "project_id": "p",
                "prompt": {"source": "inline", "body": "x", "type": "text"},
                "model": "gpt-4o",
                "stream": False,
            },
        )
        assert resp.status_code == 502
        body = resp.json()
        assert body.get("code") == "litellm_error"

    def test_langfuse_source_조회_후_치환(
        self,
        app_with_overrides: Any,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """source=langfuse: 등록된 프롬프트가 변수 치환되어 LiteLLM에 전달."""
        langfuse_client._seed(prompts=[{"name": "p_api", "body": "Hello {{name}}!", "version": 1}])
        litellm_client.set_response("done")
        with TestClient(app_with_overrides) as c:
            resp = c.post(
                "/api/v1/tests/single",
                json={
                    "project_id": "p",
                    "prompt": {
                        "source": "langfuse",
                        "name": "p_api",
                        "version": 1,
                    },
                    "variables": {"name": "World"},
                    "model": "gpt-4o",
                    "stream": False,
                },
            )
        assert resp.status_code == 200

        # LiteLLM에 전달된 messages — 변수 치환 검증
        calls = litellm_client._get_calls()
        assert len(calls) == 1
        last = calls[0]["messages"][-1]
        assert last["content"] == "Hello World!"
