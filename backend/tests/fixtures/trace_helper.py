"""TraceTree / TraceObservation 빠른 생성 헬퍼 (Phase 8-A-2 테스트용).

테스트 코드 안에서 trace 시나리오를 한 줄로 표현하기 위한 팩토리 함수.

사용 예::

    trace = make_trace(
        output="hello",
        tool_calls=[
            ("web_search", {"query": "weather"}, {"result": "sunny"}),
            ("calculator", {"expr": "1+1"}, {"value": 2}),
        ],
        llm_call_count=1,
    )
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.trace import TraceObservation, TraceTree


def make_observation(
    *,
    name: str,
    type: str = "span",  # noqa: A002 — 도메인 의도상 type 사용
    input: Any = None,  # noqa: A002
    output: Any = None,
    level: str = "DEFAULT",
    latency_ms: float | None = 100.0,
    start_time: datetime | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> TraceObservation:
    """단일 observation 생성."""
    if start_time is None:
        start_time = datetime.now(UTC)
    end_time = start_time + timedelta(milliseconds=latency_ms or 0)
    return TraceObservation(
        id=str(uuid.uuid4()),
        type=type,  # type: ignore[arg-type]
        name=name,
        parent_observation_id=None,
        input=input,
        output=output,
        level=level,  # type: ignore[arg-type]
        status_message=None,
        start_time=start_time,
        end_time=end_time,
        latency_ms=latency_ms,
        model=model,
        usage=None,
        cost_usd=cost_usd,
        metadata=metadata or {},
    )


def make_trace(
    *,
    trace_id: str | None = None,
    name: str = "agent_test",
    project_id: str = "proj-test",
    output: Any = "ok",
    input_value: Any = None,
    tool_calls: list[tuple[str, Any, Any]] | None = None,
    tool_levels: list[str] | None = None,
    tool_latencies: list[float | None] | None = None,
    error_spans: list[tuple[str, Any]] | None = None,
    llm_call_count: int = 0,
    llm_latency_ms: float = 200.0,
    llm_model: str = "gpt-4o-mini",
    total_cost: float = 0.01,
    total_latency: float = 1000.0,
    metadata: dict[str, Any] | None = None,
    extra_observations: list[TraceObservation] | None = None,
    base_time: datetime | None = None,
) -> TraceTree:
    """테스트용 :class:`TraceTree` 빠른 생성.

    Args:
        tool_calls: ``[(name, input, output), ...]`` — span 타입으로 추가.
        tool_levels: tool_calls 와 동일 길이의 level 리스트 (없으면 모두 ``DEFAULT``).
        tool_latencies: tool_calls 와 동일 길이의 latency_ms 리스트.
        error_spans: ``[(name, status_message), ...]`` — level=ERROR span 추가.
        llm_call_count: 추가할 generation 수.
        llm_latency_ms: 각 generation의 latency_ms.
        extra_observations: 미리 만든 observation 들을 직접 append.
    """
    if base_time is None:
        base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    obs: list[TraceObservation] = []
    cursor = base_time

    tool_calls = tool_calls or []
    tool_levels = tool_levels or ["DEFAULT"] * len(tool_calls)
    tool_latencies = tool_latencies or [100.0] * len(tool_calls)

    for idx, (tname, tinput, toutput) in enumerate(tool_calls):
        latency = tool_latencies[idx] if idx < len(tool_latencies) else 100.0
        level = tool_levels[idx] if idx < len(tool_levels) else "DEFAULT"
        obs.append(
            make_observation(
                name=tname,
                type="span",
                input=tinput,
                output=toutput,
                level=level,
                latency_ms=latency,
                start_time=cursor,
            )
        )
        cursor = cursor + timedelta(milliseconds=(latency or 0) + 1)

    for ename, status in error_spans or []:
        obs.append(
            make_observation(
                name=ename,
                type="span",
                level="ERROR",
                latency_ms=50.0,
                start_time=cursor,
                metadata={"status_message": status},
            )
        )
        cursor = cursor + timedelta(milliseconds=51)

    for i in range(llm_call_count):
        obs.append(
            make_observation(
                name=f"llm-call-{i}",
                type="generation",
                input={"prompt": f"call-{i}"},
                output={"text": f"resp-{i}"},
                latency_ms=llm_latency_ms,
                start_time=cursor,
                model=llm_model,
                cost_usd=0.001,
            )
        )
        cursor = cursor + timedelta(milliseconds=int(llm_latency_ms) + 1)

    if extra_observations:
        obs.extend(extra_observations)

    # observation 시간순 정렬 보장
    obs.sort(key=lambda o: o.start_time)

    return TraceTree(
        id=trace_id or str(uuid.uuid4()),
        project_id=project_id,
        name=name,
        input=input_value,
        output=output,
        user_id=None,
        session_id=None,
        tags=[],
        metadata=metadata or {},
        observations=obs,
        scores=[],
        total_cost_usd=total_cost,
        total_latency_ms=total_latency,
        timestamp=base_time,
    )


__all__ = ["make_observation", "make_trace"]
