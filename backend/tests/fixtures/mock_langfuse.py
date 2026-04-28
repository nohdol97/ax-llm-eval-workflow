"""Langfuse 클라이언트 Mock.

실제 ``langfuse.Langfuse`` 클라이언트와 호환되는 인메모리 mock.
프롬프트/데이터셋/Trace/Generation/Score를 모두 메모리 dict에 보관한다.

Phase 2 코드의 ``services/langfuse_client.py``에서 swap-in 가능하도록
주요 메서드 시그니처를 일치시킨다.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class LangfuseNotFoundError(Exception):
    """프롬프트/데이터셋/Trace 등이 존재하지 않을 때 발생."""


@dataclass
class MockPrompt:
    """Langfuse Prompt 객체 mock.

    실제 SDK의 ``TextPromptClient`` / ``ChatPromptClient``와 유사한 인터페이스를 제공한다.
    """

    name: str
    body: str
    version: int
    labels: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    prompt_type: str = "text"
    variables: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """본문에서 ``{{var}}`` 패턴을 추출하여 variables 자동 채움."""
        if not self.variables:
            self.variables = sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", self.body)))

    def compile(self, **kwargs: Any) -> str:
        """변수 치환된 본문 반환."""
        result = self.body
        for key, value in kwargs.items():
            result = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", str(value), result)
        return result


@dataclass
class MockDataset:
    """Langfuse Dataset 객체 mock."""

    name: str
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    items: list[MockDatasetItem] = field(default_factory=list)


@dataclass
class MockDatasetItem:
    """Langfuse DatasetItem 객체 mock."""

    id: str
    dataset_name: str
    input: Any
    expected_output: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MockTrace:
    """Langfuse Trace 객체 mock."""

    id: str
    name: str
    user_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    generations: list[MockGeneration] = field(default_factory=list)
    scores: list[MockScore] = field(default_factory=list)


@dataclass
class MockGeneration:
    """Langfuse Generation 객체 mock."""

    id: str
    trace_id: str
    name: str
    model: str
    input: Any
    output: Any
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MockScore:
    """Langfuse Score 객체 mock."""

    id: str
    trace_id: str
    name: str
    value: float | str | bool
    comment: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MockScoreConfig:
    """Langfuse Score Config 객체 mock."""

    id: str
    name: str
    data_type: str
    range: dict[str, Any] = field(default_factory=dict)
    description: str | None = None


class MockLangfuseClient:
    """Langfuse v3 클라이언트 mock.

    실제 ``langfuse.Langfuse``의 핵심 메서드(get_prompt, create_prompt,
    create_dataset, create_trace, create_generation, score, flush 등)를 동일한
    시그니처로 제공하며, 모든 데이터를 인메모리 dict에 저장한다.
    """

    def __init__(self) -> None:
        self._prompts: dict[tuple[str, int], MockPrompt] = {}
        self._prompt_labels: dict[tuple[str, str], int] = {}  # (name, label) → version
        self._datasets: dict[str, MockDataset] = {}
        self._traces: dict[str, MockTrace] = {}
        self._score_configs: dict[str, MockScoreConfig] = {}
        self._healthy = True

    # ---------- Prompt 관리 ----------
    def get_prompt(
        self,
        name: str,
        version: int | None = None,
        label: str | None = None,
    ) -> MockPrompt:
        """프롬프트 조회. 미존재 시 ``LangfuseNotFoundError``."""
        if label is not None:
            key = (name, label)
            if key not in self._prompt_labels:
                raise LangfuseNotFoundError(f"prompt name={name!r} label={label!r} not found")
            version = self._prompt_labels[key]

        if version is None:
            # latest version
            versions = [v for (n, v) in self._prompts if n == name]
            if not versions:
                raise LangfuseNotFoundError(f"prompt name={name!r} not found")
            version = max(versions)

        if (name, version) not in self._prompts:
            raise LangfuseNotFoundError(f"prompt name={name!r} version={version} not found")
        return self._prompts[(name, version)]

    def create_prompt(
        self,
        name: str,
        prompt: str,
        labels: list[str] | None = None,
        config: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        prompt_type: str = "text",
    ) -> MockPrompt:
        """새 프롬프트 버전 생성. 동일 이름이 있으면 자동 increment."""
        existing_versions = [v for (n, v) in self._prompts if n == name]
        next_version = max(existing_versions, default=0) + 1
        p = MockPrompt(
            name=name,
            body=prompt,
            version=next_version,
            labels=labels or [],
            config=config or {},
            tags=tags or [],
            prompt_type=prompt_type,
        )
        self._prompts[(name, next_version)] = p
        for lbl in p.labels:
            self._prompt_labels[(name, lbl)] = next_version
        return p

    def update_prompt_labels(
        self,
        name: str,
        version: int,
        labels: list[str],
    ) -> MockPrompt:
        """프롬프트 라벨 업데이트 (승격용)."""
        if (name, version) not in self._prompts:
            raise LangfuseNotFoundError(f"prompt name={name!r} version={version} not found")
        p = self._prompts[(name, version)]
        # 기존 라벨 → 다른 버전이 가지고 있을 수 있으므로 새 라벨만 추가
        for lbl in labels:
            self._prompt_labels[(name, lbl)] = version
        p.labels = sorted(set(p.labels) | set(labels))
        return p

    # ---------- Dataset 관리 ----------
    def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MockDataset:
        """데이터셋 생성. 이미 존재하면 기존 객체 반환 (idempotent)."""
        if name in self._datasets:
            return self._datasets[name]
        ds = MockDataset(name=name, description=description, metadata=metadata or {})
        self._datasets[name] = ds
        return ds

    def get_dataset(self, name: str) -> MockDataset | None:
        """데이터셋 조회. 미존재 시 ``None``."""
        return self._datasets.get(name)

    def create_dataset_item(
        self,
        dataset_name: str,
        input: Any,  # noqa: A002 — Langfuse SDK 시그니처 일치
        expected_output: Any,
        metadata: dict[str, Any] | None = None,
    ) -> MockDatasetItem:
        """데이터셋 아이템 추가. 데이터셋 미존재 시 자동 생성."""
        if dataset_name not in self._datasets:
            self.create_dataset(dataset_name)
        item = MockDatasetItem(
            id=str(uuid.uuid4()),
            dataset_name=dataset_name,
            input=input,
            expected_output=expected_output,
            metadata=metadata or {},
        )
        self._datasets[dataset_name].items.append(item)
        return item

    # ---------- Trace / Generation / Score ----------
    def create_trace(
        self,
        name: str,
        user_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Trace 생성. trace_id 반환."""
        trace_id = str(uuid.uuid4())
        self._traces[trace_id] = MockTrace(
            id=trace_id,
            name=name,
            user_id=user_id,
            session_id=session_id,
            metadata=metadata or {},
            tags=tags or [],
        )
        return trace_id

    def create_generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input: Any,  # noqa: A002
        output: Any,
        usage: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MockGeneration:
        """Generation 추가. trace 미존재 시 ``LangfuseNotFoundError``."""
        if trace_id not in self._traces:
            raise LangfuseNotFoundError(f"trace_id={trace_id!r} not found")
        gen = MockGeneration(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            name=name,
            model=model,
            input=input,
            output=output,
            usage=usage or {},
            metadata=metadata or {},
        )
        self._traces[trace_id].generations.append(gen)
        return gen

    def score(
        self,
        trace_id: str,
        name: str,
        value: float | str | bool,
        comment: str | None = None,
    ) -> MockScore:
        """점수 기록. trace 미존재 시 ``LangfuseNotFoundError``."""
        if trace_id not in self._traces:
            raise LangfuseNotFoundError(f"trace_id={trace_id!r} not found")
        s = MockScore(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment,
        )
        self._traces[trace_id].scores.append(s)
        return s

    def flush(self) -> None:
        """버퍼 flush — mock에서는 no-op."""
        return None

    # ---------- Score Config ----------
    def register_score_config(
        self,
        name: str,
        data_type: str,
        range: dict[str, Any] | None = None,  # noqa: A002
        description: str | None = None,
    ) -> str:
        """Score config 등록. 동일 name이 있으면 기존 id 반환 (idempotent)."""
        if name in self._score_configs:
            return self._score_configs[name].id
        cfg_id = str(uuid.uuid4())
        self._score_configs[name] = MockScoreConfig(
            id=cfg_id,
            name=name,
            data_type=data_type,
            range=range or {},
            description=description,
        )
        return cfg_id

    # ---------- Health ----------
    def health_check(self) -> bool:
        """헬스 체크. 기본 True, ``set_unhealthy()`` 호출 시 False."""
        return self._healthy

    def set_unhealthy(self) -> None:
        """테스트에서 강제로 unhealthy 상태 시뮬레이트."""
        self._healthy = False

    def set_healthy(self) -> None:
        """헬스 상태 복원."""
        self._healthy = True

    # ---------- 테스트 헬퍼 ----------
    def _seed(
        self,
        prompts: list[dict[str, Any]] | None = None,
        datasets: list[dict[str, Any]] | None = None,
    ) -> None:
        """테스트 셋업용 헬퍼.

        Args:
            prompts: ``[{"name": "p", "body": "...", "version": 1, "labels": [...]}]``
            datasets: ``[{"name": "d", "items": [{"input":..., "expected_output":...}]}]``
        """
        for p in prompts or []:
            name = p["name"]
            version = p.get("version", 1)
            mp = MockPrompt(
                name=name,
                body=p["body"],
                version=version,
                labels=p.get("labels", []),
                config=p.get("config", {}),
                tags=p.get("tags", []),
                prompt_type=p.get("prompt_type", "text"),
            )
            self._prompts[(name, version)] = mp
            for lbl in mp.labels:
                self._prompt_labels[(name, lbl)] = version

        for d in datasets or []:
            ds = self.create_dataset(
                name=d["name"],
                description=d.get("description"),
                metadata=d.get("metadata"),
            )
            for it in d.get("items", []):
                self.create_dataset_item(
                    dataset_name=ds.name,
                    input=it["input"],
                    expected_output=it["expected_output"],
                    metadata=it.get("metadata"),
                )

    def _get_traces(self) -> list[MockTrace]:
        """기록된 모든 trace 반환 (검증용)."""
        return list(self._traces.values())

    def _get_scores(self) -> list[MockScore]:
        """기록된 모든 score 반환 (검증용, trace를 가로질러 평탄화)."""
        result: list[MockScore] = []
        for t in self._traces.values():
            result.extend(t.scores)
        return result

    def _get_generations(self) -> list[MockGeneration]:
        """기록된 모든 generation 반환 (검증용)."""
        result: list[MockGeneration] = []
        for t in self._traces.values():
            result.extend(t.generations)
        return result
