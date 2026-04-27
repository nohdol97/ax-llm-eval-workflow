"""OpenTelemetry SpanExporter Mock.

가능하면 ``opentelemetry-sdk``의 ``InMemorySpanExporter``를 사용하고,
의존성이 누락된 경우 자체 in-memory 구현으로 fallback한다.

테스트는 ``get_finished_spans()``로 완료된 span을 dict 형태로 검사할 수 있다.
"""

from __future__ import annotations

from typing import Any

# OTel SDK가 설치되어 있을 경우에만 import (의존성 가드)
try:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover — fallback 테스트 어려움
    _OTEL_AVAILABLE = False
    InMemorySpanExporter = None  # type: ignore[assignment,misc]


class _FallbackSpan:
    """OTel SDK 미설치 시 사용하는 단순 span 컨테이너."""

    def __init__(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        status: str = "OK",
        duration_ns: int = 0,
    ) -> None:
        self.name = name
        self.attributes = dict(attributes or {})
        self.status = status
        self.duration_ns = duration_ns


class MockOTelExporter:
    """OTel span exporter mock.

    실제 OTel SDK가 사용 가능하면 ``InMemorySpanExporter``를 래핑하고,
    아니면 자체 list 기반 구현으로 fallback.

    ``get_finished_spans()``는 항상 dict list를 반환하여 backend 코드가 어떤 SDK를
    사용하든 동일한 검증 인터페이스를 보장한다.
    """

    def __init__(self) -> None:
        self._healthy = True
        if _OTEL_AVAILABLE:
            self._exporter = InMemorySpanExporter()
        else:  # pragma: no cover
            self._exporter = None
        self._fallback_spans: list[_FallbackSpan] = []

    # ---------- 인터페이스 ----------
    def get_finished_spans(self) -> list[dict[str, Any]]:
        """완료된 span을 dict list로 반환.

        OTel SDK가 설치되어 있으면 SDK exporter의 span과 ``record_span``으로 직접
        기록된 fallback span을 모두 합쳐 반환한다.
        """
        result: list[dict[str, Any]] = []
        if self._exporter is not None:
            spans = self._exporter.get_finished_spans()
            result.extend(self._span_to_dict(s) for s in spans)
        result.extend(
            {
                "name": s.name,
                "attributes": dict(s.attributes),
                "status": s.status,
                "duration_ns": s.duration_ns,
            }
            for s in self._fallback_spans
        )
        return result

    def clear(self) -> None:
        """모든 buffered span 삭제 (테스트 격리)."""
        if self._exporter is not None:
            self._exporter.clear()
        self._fallback_spans.clear()

    def health_check(self) -> bool:
        """헬스 체크."""
        return self._healthy

    def set_unhealthy(self) -> None:
        """헬스 강제 unhealthy."""
        self._healthy = False

    def set_healthy(self) -> None:
        """헬스 복원."""
        self._healthy = True

    # ---------- 직접 record (fallback / 단위 테스트용) ----------
    def record_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        status: str = "OK",
        duration_ns: int = 0,
    ) -> None:
        """OTel SDK 없이 span을 직접 기록 (fallback / 검증용)."""
        self._fallback_spans.append(
            _FallbackSpan(
                name=name,
                attributes=attributes,
                status=status,
                duration_ns=duration_ns,
            )
        )

    # ---------- 내부 변환 ----------
    @staticmethod
    def _span_to_dict(span: Any) -> dict[str, Any]:
        """OTel ReadableSpan → dict."""
        attrs = dict(span.attributes) if span.attributes else {}
        status_obj = getattr(span, "status", None)
        status_name = (
            status_obj.status_code.name
            if status_obj is not None and hasattr(status_obj, "status_code")
            else "OK"
        )
        end = getattr(span, "end_time", None)
        start = getattr(span, "start_time", None)
        duration = (end - start) if (end is not None and start is not None) else 0
        return {
            "name": span.name,
            "attributes": attrs,
            "status": status_name,
            "duration_ns": int(duration),
        }

    # ---------- 내부 노출 (선택적) ----------
    @property
    def underlying_exporter(self) -> Any:
        """OTel SDK SpanExporter 직접 접근 (TracerProvider 등록 시 필요)."""
        return self._exporter
