"""Auto-Eval lifespan 통합 단위 테스트 (Phase 8-B-2).

검증:
- ``app.state.auto_eval_repo`` / ``app.state.auto_eval_engine`` 가 lifespan 후
  부착되어야 한다 (성공 경로).
- ``AutoEvalScheduler`` 모듈이 미존재해도 부팅이 성공해야 한다 (graceful).
- shutdown 시 scheduler.stop() 호출 (mock scheduler).
- 라우터가 등록되어 12 개 엔드포인트가 활성화되어야 한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
class TestAutoEvalRoutes:
    """라우터 등록 — 12 개 엔드포인트가 ``app.routes`` 에 존재."""

    def test_all_endpoints_registered(self) -> None:
        from app.main import create_app

        app = create_app()
        paths = [getattr(r, "path", "") for r in app.routes]
        expected_routes = [
            "/api/v1/auto-eval/policies",
            "/api/v1/auto-eval/policies/{policy_id}",
            "/api/v1/auto-eval/policies/{policy_id}/pause",
            "/api/v1/auto-eval/policies/{policy_id}/resume",
            "/api/v1/auto-eval/policies/{policy_id}/run-now",
            "/api/v1/auto-eval/policies/{policy_id}/cost-usage",
            "/api/v1/auto-eval/runs",
            "/api/v1/auto-eval/runs/{run_id}",
            "/api/v1/auto-eval/runs/{run_id}/items",
        ]
        for route in expected_routes:
            assert route in paths, f"라우터 미등록: {route}"


@pytest.mark.unit
class TestLifespanSetup:
    """lifespan 진입/종료 시 auto_eval 인스턴스 생성/정리."""

    def test_lifespan_attaches_repo_and_engine(self) -> None:
        """lifespan 진입 시 app.state 에 repo, engine 부착."""
        from app.main import create_app

        app = create_app()
        with TestClient(app):
            # lifespan 진입 후 상태 확인
            assert hasattr(app.state, "auto_eval_repo")
            assert app.state.auto_eval_repo is not None
            assert hasattr(app.state, "auto_eval_engine")
            assert app.state.auto_eval_engine is not None

    def test_scheduler_missing_does_not_break_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """auto_eval_scheduler 모듈이 import 실패해도 부팅 성공."""
        import builtins

        from app.main import create_app

        real_import = builtins.__import__

        def _import_blocker(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "app.services.auto_eval_scheduler":
                raise ImportError("simulated — scheduler not yet implemented")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _import_blocker)

        app = create_app()
        with TestClient(app):
            # repo / engine 은 부착되어야 함
            assert hasattr(app.state, "auto_eval_repo")
            # scheduler 는 None (graceful skip)
            assert getattr(app.state, "auto_eval_scheduler", None) is None


@pytest.mark.unit
class TestSchedulerShutdown:
    """shutdown 시 scheduler.stop() 호출 검증 (mock scheduler 주입)."""

    def test_scheduler_stop_called_on_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """app.state.auto_eval_scheduler 가 mock 이면 stop() 호출되어야 한다."""
        # _setup_auto_eval 을 가로채서 mock scheduler 주입
        import app.main as main_mod
        from app.main import create_app

        stop_called: dict[str, Any] = {"timeout_sec": None, "called": False}

        class _MockScheduler:
            async def start(self) -> None:
                pass

            async def stop(self, timeout_sec: float = 30.0) -> None:
                stop_called["timeout_sec"] = timeout_sec
                stop_called["called"] = True

        original_setup = main_mod._setup_auto_eval

        async def _patched_setup(app: Any, settings: Any) -> None:
            await original_setup(app, settings)
            # 기존 scheduler 가 있으면 None 으로 덮은 후 mock 주입
            app.state.auto_eval_scheduler = _MockScheduler()

        monkeypatch.setattr(main_mod, "_setup_auto_eval", _patched_setup)

        app = create_app()
        with TestClient(app):
            assert isinstance(app.state.auto_eval_scheduler, _MockScheduler)

        # __exit__ 후 stop 호출 확인
        assert stop_called["called"] is True
