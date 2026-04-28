"""Langfuse v3 SDK 래퍼.

본 백엔드의 모든 Langfuse 호출은 이 클라이언트를 경유한다. 사내 endpoint 미설정 시
graceful 처리하여 부팅은 성공하지만 실제 호출은 ``LangfuseError``를 raise한다.

retry: tenacity (max 3, exponential backoff). ``health_check``는 retry 미적용.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.errors import LangfuseError
from app.core.logging import get_logger
from app.models.health import ServiceHealth

logger = get_logger(__name__)

# tenacity 데코레이터 — 외부 호출 일관 정책
_retry_policy = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
    retry=retry_if_exception_type((httpx.HTTPError, LangfuseError)),
)


class LangfuseClient:
    """Langfuse SDK 래퍼.

    실 SDK는 ``langfuse.Langfuse``. 사내 endpoint(LANGFUSE_HOST) + 자격증명
    (LANGFUSE_PUBLIC_KEY/SECRET_KEY)이 모두 설정되어야 정상 호출 가능.
    """

    def __init__(self, settings: Settings) -> None:
        """settings 주입 + lazy SDK 초기화."""
        self._settings = settings
        self._sdk: Any | None = None
        self._sdk_init_failed = False

    # ---------- SDK lazy 초기화 ----------
    def _get_sdk(self) -> Any:
        """``langfuse.Langfuse`` 인스턴스를 lazy 초기화."""
        if self._sdk is not None:
            return self._sdk
        if self._sdk_init_failed:
            raise LangfuseError(detail="Langfuse SDK 초기화 실패 (이전 시도)")
        if not self._settings.langfuse_configured:
            raise LangfuseError(
                detail="Langfuse 자격증명 미설정 — LANGFUSE_PUBLIC_KEY / SECRET_KEY 필요"
            )

        try:
            from langfuse import Langfuse
        except ImportError as exc:  # pragma: no cover
            self._sdk_init_failed = True
            raise LangfuseError(detail=f"langfuse SDK import 실패: {exc}") from exc

        try:
            self._sdk = Langfuse(
                host=self._settings.LANGFUSE_HOST,
                public_key=self._settings.LANGFUSE_PUBLIC_KEY,
                secret_key=self._settings.LANGFUSE_SECRET_KEY.get_secret_value(),
            )
        except Exception as exc:  # noqa: BLE001
            self._sdk_init_failed = True
            raise LangfuseError(detail=f"Langfuse SDK 인스턴스화 실패: {exc}") from exc

        return self._sdk

    # ---------- Prompt 관리 ----------
    @_retry_policy
    def get_prompt(
        self,
        name: str,
        version: int | None = None,
        label: str | None = None,
    ) -> Any:
        """Langfuse 프롬프트 조회."""
        sdk = self._get_sdk()
        try:
            return sdk.get_prompt(name=name, version=version, label=label)
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"get_prompt 실패: name={name!r} ({exc})") from exc

    @_retry_policy
    def create_prompt(
        self,
        name: str,
        prompt: str,
        labels: list[str] | None = None,
        config: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        prompt_type: str = "text",
    ) -> Any:
        """프롬프트 신규 버전 생성."""
        sdk = self._get_sdk()
        try:
            return sdk.create_prompt(
                name=name,
                prompt=prompt,
                labels=labels or [],
                config=config or {},
                tags=tags or [],
                type=prompt_type,
            )
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"create_prompt 실패: name={name!r} ({exc})") from exc

    @_retry_policy
    def update_prompt_labels(
        self,
        name: str,
        version: int,
        labels: list[str],
    ) -> Any:
        """프롬프트 라벨 업데이트 (승격)."""
        sdk = self._get_sdk()
        try:
            return sdk.update_prompt_labels(name=name, version=version, labels=labels)
        except AttributeError:
            # SDK 구버전 호환 — fallback
            return sdk.update_prompt(name=name, version=version, labels=labels)
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(
                detail=f"update_prompt_labels 실패: name={name!r} v={version} ({exc})"
            ) from exc

    # ---------- Dataset ----------
    @_retry_policy
    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """데이터셋 생성."""
        sdk = self._get_sdk()
        try:
            return sdk.create_dataset(name=name, description=description, metadata=metadata or {})
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"create_dataset 실패: name={name!r} ({exc})") from exc

    @_retry_policy
    def get_dataset(self, name: str) -> Any:
        """데이터셋 조회."""
        sdk = self._get_sdk()
        try:
            return sdk.get_dataset(name=name)
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"get_dataset 실패: name={name!r} ({exc})") from exc

    @_retry_policy
    def create_dataset_item(
        self,
        dataset_name: str,
        input: Any,  # noqa: A002 — SDK 시그니처 일치
        expected_output: Any,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """데이터셋 아이템 추가."""
        sdk = self._get_sdk()
        try:
            return sdk.create_dataset_item(
                dataset_name=dataset_name,
                input=input,
                expected_output=expected_output,
                metadata=metadata or {},
            )
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(
                detail=f"create_dataset_item 실패: dataset={dataset_name!r} ({exc})"
            ) from exc

    # ---------- Trace / Generation / Score ----------
    @_retry_policy
    def create_trace(
        self,
        name: str,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Trace 생성. trace_id(str) 반환."""
        sdk = self._get_sdk()
        try:
            trace = sdk.trace(
                name=name,
                user_id=user_id,
                session_id=session_id,
                metadata=metadata or {},
                tags=tags or [],
            )
            # SDK의 trace 객체에는 ``id`` 속성이 있을 것으로 가정
            trace_id = getattr(trace, "id", None) or str(trace)
            return str(trace_id)
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"create_trace 실패: name={name!r} ({exc})") from exc

    @_retry_policy
    def create_generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input: Any,  # noqa: A002
        output: Any,
        usage: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Generation 추가."""
        sdk = self._get_sdk()
        try:
            return sdk.generation(
                trace_id=trace_id,
                name=name,
                model=model,
                input=input,
                output=output,
                usage=usage or {},
                metadata=metadata or {},
            )
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(
                detail=f"create_generation 실패: trace_id={trace_id!r} ({exc})"
            ) from exc

    @_retry_policy
    def score(
        self,
        trace_id: str,
        name: str,
        value: float | str | bool,
        comment: str | None = None,
    ) -> Any:
        """Score 기록."""
        sdk = self._get_sdk()
        try:
            return sdk.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
            )
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(
                detail=f"score 실패: trace_id={trace_id!r} name={name!r} ({exc})"
            ) from exc

    # ---------- Trace 조회 (Phase 8-A-1, 폴백 모드용) ----------
    @_retry_policy
    def list_traces(
        self,
        project_id: str,
        name: str | None = None,
        tags: list[str] | None = None,
        user_ids: list[str] | None = None,
        session_ids: list[str] | None = None,
        from_timestamp: Any | None = None,
        to_timestamp: Any | None = None,
        limit: int = 1000,
    ) -> list[Any]:
        """Langfuse trace 목록 조회 (폴백 모드).

        SDK가 ``fetch_traces`` 또는 ``api.trace.list`` 등 다양한 메서드를 노출할 수
        있어, 가능한 후보를 순회한다. 모두 실패하면 ``LangfuseError``.
        """
        sdk = self._get_sdk()
        kwargs: dict[str, Any] = {
            "project_id": project_id,
            "name": name,
            "tags": tags or [],
            "user_id": user_ids[0] if user_ids else None,
            "session_id": session_ids[0] if session_ids else None,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
            "limit": limit,
        }
        for attr_path in (("fetch_traces",), ("api", "trace", "list")):
            obj: Any = sdk
            try:
                for attr in attr_path:
                    obj = getattr(obj, attr)
            except AttributeError:
                continue
            if not callable(obj):
                continue
            filtered = {k: v for k, v in kwargs.items() if v is not None}
            try:
                result = obj(**filtered)
            except TypeError:
                # 일부 SDK는 키워드 시그니처가 다를 수 있어 최소 인자만으로 재시도
                try:
                    result = obj(project_id=project_id, limit=limit)
                except Exception as exc:  # noqa: BLE001
                    raise LangfuseError(detail=f"list_traces 실패: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                raise LangfuseError(detail=f"list_traces 실패: {exc}") from exc
            # 결과가 ``data`` 속성을 가지면 풀어준다
            data = getattr(result, "data", None)
            if data is not None:
                return list(data)
            if isinstance(result, list):
                return result
            return list(result) if result is not None else []
        raise LangfuseError(detail="SDK가 list_traces / fetch_traces 메서드를 제공하지 않습니다.")

    @_retry_policy
    def get_trace(self, trace_id: str) -> Any:
        """Langfuse trace 단건 조회 (폴백 모드).

        SDK 후보: ``fetch_trace(trace_id)``, ``get_trace(trace_id)``,
        ``api.trace.get(trace_id)``.
        """
        sdk = self._get_sdk()
        for attr_path in (("fetch_trace",), ("get_trace",), ("api", "trace", "get")):
            obj: Any = sdk
            try:
                for attr in attr_path:
                    obj = getattr(obj, attr)
            except AttributeError:
                continue
            if not callable(obj):
                continue
            try:
                result = obj(trace_id)
            except Exception as exc:  # noqa: BLE001
                raise LangfuseError(
                    detail=f"get_trace 실패: trace_id={trace_id!r} ({exc})"
                ) from exc
            return getattr(result, "data", None) or result
        raise LangfuseError(detail="SDK가 get_trace / fetch_trace 메서드를 제공하지 않습니다.")

    # ---------- Score Config ----------
    @_retry_policy
    def register_score_config(
        self,
        name: str,
        data_type: str,
        range: tuple[float, float] | dict[str, Any] | None = None,  # noqa: A002
        description: str | None = None,
    ) -> str:
        """Score config 등록 (idempotent — 이미 존재하면 기존 id 반환)."""
        sdk = self._get_sdk()
        # SDK가 ``register_score_config``를 직접 노출하지 않을 수 있으므로
        # 본 프로젝트 정책: 가능한 한 SDK 메서드를 호출하고, 없으면 REST API 직접 호출.
        try:
            if hasattr(sdk, "register_score_config"):
                result = sdk.register_score_config(
                    name=name,
                    data_type=data_type,
                    range=self._normalize_range(range),
                    description=description,
                )
                return str(getattr(result, "id", result))
        except Exception as exc:  # noqa: BLE001
            # idempotent: 이미 존재하면 통과
            if "already" in str(exc).lower() or "exists" in str(exc).lower():
                return f"existing:{name}"
            raise LangfuseError(
                detail=f"register_score_config 실패: name={name!r} ({exc})"
            ) from exc

        # SDK가 메서드를 제공하지 않으면 REST 직접 호출
        return self._register_score_config_rest(
            name=name,
            data_type=data_type,
            range=range,
            description=description,
        )

    def _register_score_config_rest(
        self,
        name: str,
        data_type: str,
        range: tuple[float, float] | dict[str, Any] | None,  # noqa: A002
        description: str | None,
    ) -> str:
        """REST API 직접 호출 fallback."""
        endpoint = f"{self._settings.LANGFUSE_HOST}/api/public/score-configs"
        auth = (
            self._settings.LANGFUSE_PUBLIC_KEY,
            self._settings.LANGFUSE_SECRET_KEY.get_secret_value(),
        )
        payload = {
            "name": name,
            "dataType": data_type,
            "description": description,
        }
        normalized = self._normalize_range(range)
        if normalized:
            payload.update(normalized)

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(endpoint, json=payload, auth=auth)
        except httpx.HTTPError as exc:
            raise LangfuseError(detail=f"register_score_config(REST) 실패: {exc}") from exc

        if resp.status_code == 409:
            # 이미 존재 → idempotent
            return f"existing:{name}"
        if resp.status_code >= 400:
            # 400대: 이미 존재 가능성 (메시지 검사)
            if "exists" in resp.text.lower() or "already" in resp.text.lower():
                return f"existing:{name}"
            raise LangfuseError(
                detail=f"register_score_config(REST) HTTP {resp.status_code}: {resp.text}"
            )
        try:
            data = resp.json()
            return str(data.get("id") or data.get("name") or name)
        except Exception:  # noqa: BLE001
            return name

    @staticmethod
    def _normalize_range(
        value: tuple[float, float] | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """range 인자를 ``{minValue, maxValue}`` dict로 정규화."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, tuple) and len(value) == 2:
            return {"minValue": value[0], "maxValue": value[1]}
        return {}

    # ---------- Buffer ----------
    def flush(self) -> None:
        """SDK 내부 버퍼 flush (graceful — 미설정 시 noop)."""
        if not self._settings.langfuse_configured:
            return
        try:
            sdk = self._get_sdk()
            if hasattr(sdk, "flush"):
                sdk.flush()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("langfuse_flush_failed", error=str(exc))

    # ---------- 헬스 체크 ----------
    async def health_check(self) -> ServiceHealth:
        """``GET /api/public/health`` 호출 — retry 없음."""
        endpoint = f"{self._settings.LANGFUSE_HOST}/api/public/health"
        if not self._settings.LANGFUSE_HOST:
            return ServiceHealth(
                status="warn",
                endpoint=None,
                detail="LANGFUSE_HOST not configured",
                checked_at=datetime.now(UTC),
            )
        start = time.perf_counter()
        try:
            timeout = self._settings.LABS_HEALTH_CHECK_TIMEOUT_SEC
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(endpoint)
            latency_ms = (time.perf_counter() - start) * 1000.0
            if 200 <= resp.status_code < 300:
                return ServiceHealth(
                    status="ok",
                    latency_ms=latency_ms,
                    endpoint=endpoint,
                    checked_at=datetime.now(UTC),
                )
            return ServiceHealth(
                status="error",
                latency_ms=latency_ms,
                endpoint=endpoint,
                detail=f"HTTP {resp.status_code}",
                checked_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000.0
            return ServiceHealth(
                status="error",
                latency_ms=latency_ms,
                endpoint=endpoint,
                detail=str(exc),
                checked_at=datetime.now(UTC),
            )
