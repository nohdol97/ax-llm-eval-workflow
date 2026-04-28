"""``AnalysisService`` 단위 테스트.

검증:
- ``compare_runs``      : Run별 요약 + score 매트릭스 매핑
- ``compare_items``     : 정렬(score_range/latency/cost), 필터, 페이지네이션
- ``score_distribution``: bin 분할 + 통계
- ``latency_distribution``: percentile + 히스토그램
- ``cost_distribution`` : model/eval 비용 분리
- 모든 쿼리에 대해 parameterized 인자가 정상 전달되는지 검증
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.services.analysis_service import AnalysisService
from tests.fixtures.mock_clickhouse import MockClickHouseClient


# ---------- 공통 픽스처 ----------
@pytest.fixture
def mock_ch() -> MockClickHouseClient:
    return MockClickHouseClient()


@pytest.fixture
def service(mock_ch: MockClickHouseClient) -> AnalysisService:
    return AnalysisService(clickhouse=mock_ch)  # type: ignore[arg-type]


# ===================================================================
# 1) compare_runs
# ===================================================================
@pytest.mark.unit
class TestCompareRuns:
    """Run 요약 비교."""

    async def test_basic_summary_metrics(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        mock_ch.register_response(
            r"FROM\s+traces\s+AS\s+t\s+LEFT\s+JOIN\s+observations",
            [
                {
                    "run_name": "run_a",
                    "avg_latency_ms": 100.0,
                    "p50_latency_ms": 95.0,
                    "p90_latency_ms": 180.0,
                    "p99_latency_ms": 220.0,
                    "total_cost_usd": 1.23,
                    "avg_total_tokens": 512.0,
                    "avg_score": 0.82,
                    "items_completed": 50,
                },
                {
                    "run_name": "run_b",
                    "avg_latency_ms": 80.0,
                    "p50_latency_ms": 75.0,
                    "p90_latency_ms": 150.0,
                    "p99_latency_ms": 180.0,
                    "total_cost_usd": 0.95,
                    "avg_total_tokens": 480.0,
                    "avg_score": 0.78,
                    "items_completed": 50,
                },
            ],
        )
        mock_ch.register_response(
            r"INNER\s+JOIN\s+scores\s+AS\s+s.*GROUP\s+BY\s+t\.name,\s+s\.name",
            [
                {"run_name": "run_a", "score_name": "accuracy", "avg_value": 0.85},
                {"run_name": "run_b", "score_name": "accuracy", "avg_value": 0.80},
                {"run_name": "run_a", "score_name": "fluency", "avg_value": 0.92},
                {"run_name": "run_b", "score_name": "fluency", "avg_value": 0.88},
            ],
        )

        result = await service.compare_runs(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
        )

        assert result.project_id == "proj-1"
        assert len(result.runs) == 2
        names = sorted([r.run_name for r in result.runs])
        assert names == ["run_a", "run_b"]

        run_a = next(r for r in result.runs if r.run_name == "run_a")
        assert run_a.avg_latency_ms == pytest.approx(100.0)
        assert run_a.p99_latency_ms == pytest.approx(220.0)
        assert run_a.total_cost_usd == pytest.approx(1.23)
        assert run_a.items_completed == 50

        assert "accuracy" in result.scores
        assert result.scores["accuracy"]["run_a"] == pytest.approx(0.85)
        assert result.scores["fluency"]["run_b"] == pytest.approx(0.88)

    async def test_missing_run_filled_with_zeros(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        """ClickHouse에서 누락된 run은 0으로 채워진다."""
        mock_ch.register_response(
            r"FROM\s+traces\s+AS\s+t\s+LEFT\s+JOIN\s+observations",
            [
                {
                    "run_name": "run_a",
                    "avg_latency_ms": 100.0,
                    "p50_latency_ms": None,
                    "p90_latency_ms": None,
                    "p99_latency_ms": None,
                    "total_cost_usd": 1.0,
                    "avg_total_tokens": None,
                    "avg_score": None,
                    "items_completed": 10,
                },
            ],
        )
        mock_ch.register_response(
            r"GROUP\s+BY\s+t\.name,\s+s\.name",
            [],
        )

        result = await service.compare_runs(
            project_id="proj-1",
            run_names=["run_a", "run_b_missing"],
        )

        assert {r.run_name for r in result.runs} == {"run_a", "run_b_missing"}
        missing = next(r for r in result.runs if r.run_name == "run_b_missing")
        assert missing.total_cost_usd == 0.0
        assert missing.items_completed == 0
        assert missing.avg_score is None

    async def test_passes_parameterized_args(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        """SQL은 parameters에 project_id/run_names 전달."""
        await service.compare_runs(
            project_id="proj-XYZ",
            run_names=["alpha", "beta", "gamma"],
        )
        executed = mock_ch._get_executed_queries()
        assert len(executed) == 2  # summary + score
        for sql, params in executed:
            assert params.get("project_id") == "proj-XYZ"
            assert params.get("run_names") == ["alpha", "beta", "gamma"]
            # SQL은 parameterized syntax 사용
            assert "{project_id:String}" in sql
            assert "{run_names:Array(String)}" in sql


# ===================================================================
# 2) compare_items
# ===================================================================
def _seed_items(mock_ch: MockClickHouseClient) -> None:
    """공용 시드 — 3개 item × 2 run.

    ITEM_COMPARISON_QUERY는 ``LEFT JOIN observations`` 를 사용하고
    ITEM_SCORES_QUERY는 ``INNER JOIN scores`` 를 사용한다 — 이를 패턴 식별자로 활용.
    """
    # ITEM_COMPARISON_QUERY 응답 — observations LEFT JOIN
    mock_ch.register_response(
        r"LEFT\s+JOIN\s+observations\s+AS\s+o[\s\S]*GROUP\s+BY\s+dri\.dataset_item_id",
        [
            {
                "dataset_item_id": "item_1",
                "run_name": "run_a",
                "trace_id": "tr_1a",
                "input": '{"q": "hi"}',
                "expected": "hello",
                "output": "hello world",
                "latency_ms": 100.0,
                "cost_usd": 0.01,
            },
            {
                "dataset_item_id": "item_1",
                "run_name": "run_b",
                "trace_id": "tr_1b",
                "input": '{"q": "hi"}',
                "expected": "hello",
                "output": "hi there",
                "latency_ms": 200.0,
                "cost_usd": 0.02,
            },
            {
                "dataset_item_id": "item_2",
                "run_name": "run_a",
                "trace_id": "tr_2a",
                "input": '{"q": "bye"}',
                "expected": "goodbye",
                "output": "bye!",
                "latency_ms": 50.0,
                "cost_usd": 0.005,
            },
            {
                "dataset_item_id": "item_2",
                "run_name": "run_b",
                "trace_id": "tr_2b",
                "input": '{"q": "bye"}',
                "expected": "goodbye",
                "output": "see you",
                "latency_ms": 80.0,
                "cost_usd": 0.008,
            },
            {
                "dataset_item_id": "item_3",
                "run_name": "run_a",
                "trace_id": "tr_3a",
                "input": '{"q": "thx"}',
                "expected": "thank you",
                "output": "thanks",
                "latency_ms": 30.0,
                "cost_usd": 0.001,
            },
            {
                "dataset_item_id": "item_3",
                "run_name": "run_b",
                "trace_id": "tr_3b",
                "input": '{"q": "thx"}',
                "expected": "thank you",
                "output": "you are welcome",
                "latency_ms": 70.0,
                "cost_usd": 0.002,
            },
        ],
    )

    # ITEM_SCORES_QUERY — INNER JOIN scores AS s
    def _row(item: str, run: str, value: float) -> dict[str, Any]:
        return {
            "dataset_item_id": item,
            "run_name": run,
            "score_name": "accuracy",
            "value": value,
        }

    mock_ch.register_response(
        r"INNER\s+JOIN\s+scores\s+AS\s+s[\s\S]*GROUP\s+BY\s+dri\.dataset_item_id,"
        r"\s+t\.name,\s+s\.name",
        [
            # item_1 — 큰 차이 (range = 0.6)
            _row("item_1", "run_a", 0.9),
            _row("item_1", "run_b", 0.3),
            # item_2 — 작은 차이 (range = 0.1)
            _row("item_2", "run_a", 0.8),
            _row("item_2", "run_b", 0.7),
            # item_3 — 중간 차이 (range = 0.4)
            _row("item_3", "run_a", 0.7),
            _row("item_3", "run_b", 0.3),
        ],
    )


@pytest.mark.unit
class TestCompareItems:
    """아이템 단위 비교 + 정렬/필터/페이지네이션."""

    async def test_basic_grouping(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        result = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
        )
        assert result.total == 3
        # 페이지 디폴트 50이면 전체 반환
        assert len(result.items) == 3

        ids = {it.dataset_item_id for it in result.items}
        assert ids == {"item_1", "item_2", "item_3"}

        item1 = next(i for i in result.items if i.dataset_item_id == "item_1")
        assert item1.outputs == {"run_a": "hello world", "run_b": "hi there"}
        assert item1.scores["run_a"]["accuracy"] == 0.9
        assert item1.score_range == pytest.approx(0.6)
        assert item1.input == {"q": "hi"}
        assert item1.expected == "hello"

    async def test_sort_by_score_range_desc(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        result = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
            sort_by="score_range",
            sort_order="desc",
        )
        ids = [i.dataset_item_id for i in result.items]
        # outlier 우선: item_1 (0.6) > item_3 (0.4) > item_2 (0.1)
        assert ids == ["item_1", "item_3", "item_2"]

    async def test_sort_by_score_range_asc(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        result = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
            sort_by="score_range",
            sort_order="asc",
        )
        ids = [i.dataset_item_id for i in result.items]
        assert ids == ["item_2", "item_3", "item_1"]

    async def test_sort_by_latency(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        result = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            sort_by="latency",
            sort_order="desc",
        )
        # 평균 latency: item_1=150, item_2=65, item_3=50
        ids = [i.dataset_item_id for i in result.items]
        assert ids == ["item_1", "item_2", "item_3"]

    async def test_sort_by_cost(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        result = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            sort_by="cost",
            sort_order="desc",
        )
        # 평균 cost: item_1=0.015, item_2=0.0065, item_3=0.0015
        ids = [i.dataset_item_id for i in result.items]
        assert ids == ["item_1", "item_2", "item_3"]

    async def test_score_filter(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        # item_1 평균 = 0.6, item_2 평균 = 0.75, item_3 평균 = 0.5
        result = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
            score_min=0.55,
            score_max=0.85,
        )
        # item_1(0.6), item_2(0.75) 만 살아남음
        assert result.total == 2
        ids = {i.dataset_item_id for i in result.items}
        assert ids == {"item_1", "item_2"}

    async def test_pagination(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        _seed_items(mock_ch)
        # page_size=2 — 첫 페이지 2건, 두 번째 1건
        result_p1 = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
            sort_by="score_range",
            sort_order="desc",
            page=1,
            page_size=2,
        )
        result_p2 = await service.compare_items(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
            sort_by="score_range",
            sort_order="desc",
            page=2,
            page_size=2,
        )
        assert result_p1.total == 3
        assert len(result_p1.items) == 2
        assert result_p2.total == 3
        assert len(result_p2.items) == 1
        # p1 = [item_1, item_3], p2 = [item_2]
        assert [i.dataset_item_id for i in result_p1.items] == ["item_1", "item_3"]
        assert [i.dataset_item_id for i in result_p2.items] == ["item_2"]


# ===================================================================
# 3) score_distribution
# ===================================================================
@pytest.mark.unit
class TestScoreDistribution:
    """스코어 히스토그램 + 통계."""

    async def test_basic_histogram(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        # bins=10 → bin_width=0.1, range [0,1)
        mock_ch.register_response(
            r"GROUP\s+BY\s+t\.name,\s+bin_index",
            [
                {"run_name": "run_a", "bin_index": 0, "sample_count": 5},
                {"run_name": "run_a", "bin_index": 5, "sample_count": 10},
                {"run_name": "run_a", "bin_index": 9, "sample_count": 3},
                {"run_name": "run_b", "bin_index": 5, "sample_count": 8},
            ],
        )
        mock_ch.register_response(
            r"avg\(s\.value\)\s+AS\s+avg_value",
            [
                {
                    "run_name": "run_a",
                    "avg_value": 0.55,
                    "stddev_value": 0.15,
                    "min_value": 0.0,
                    "max_value": 0.95,
                    "sample_count": 18,
                },
                {
                    "run_name": "run_b",
                    "avg_value": 0.5,
                    "stddev_value": 0.1,
                    "min_value": 0.4,
                    "max_value": 0.6,
                    "sample_count": 8,
                },
            ],
        )

        result = await service.score_distribution(
            project_id="proj-1",
            run_names=["run_a", "run_b"],
            score_name="accuracy",
            bins=10,
        )

        assert len(result.bins) == 10
        # bin 0: range [0.0, 0.1), count = 5 (run_a)
        assert result.bins[0].range_start == 0.0
        assert result.bins[0].range_end == pytest.approx(0.1)
        assert result.bins[0].count == 5
        # bin 5: count = 10 + 8 = 18 (모든 run 합산)
        assert result.bins[5].count == 18
        # bin 9: count = 3
        assert result.bins[9].count == 3

        # 통계
        assert "run_a" in result.statistics
        assert result.statistics["run_a"].avg == pytest.approx(0.55)
        assert result.statistics["run_a"].count == 18

    async def test_invalid_bins_raises(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        with pytest.raises(ValueError, match="bins must be"):
            await service.score_distribution(
                project_id="p",
                run_names=["a", "b"],
                score_name="acc",
                bins=1,
            )

    async def test_param_passing(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        await service.score_distribution(
            project_id="p1",
            run_names=["r1", "r2"],
            score_name="acc",
            bins=5,
        )
        executed = mock_ch._get_executed_queries()
        # 두 쿼리 모두 score_name 파라미터 전달
        for _sql, params in executed:
            assert params.get("score_name") == "acc"
            assert params.get("project_id") == "p1"


# ===================================================================
# 4) latency_distribution
# ===================================================================
@pytest.mark.unit
class TestLatencyDistribution:
    """지연 히스토그램 + percentile."""

    async def test_percentile_and_bins(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        # 통계 쿼리
        mock_ch.register_response(
            r"avg\(o\.latency\)\s+AS\s+avg_latency_ms",
            [
                {
                    "avg_latency_ms": 150.0,
                    "stddev_ms": 40.0,
                    "p50_ms": 140.0,
                    "p90_ms": 220.0,
                    "p99_ms": 320.0,
                    "max_ms": 400.0,
                    "sample_count": 100,
                }
            ],
        )
        # 분포 쿼리
        mock_ch.register_response(
            r"GROUP\s+BY\s+bin_index",
            [
                {"bin_index": 0, "sample_count": 10},
                {"bin_index": 5, "sample_count": 50},
                {"bin_index": 19, "sample_count": 1},
            ],
        )

        result = await service.latency_distribution(
            project_id="p1",
            run_name="run_a",
            bins=20,
        )
        assert result.run_name == "run_a"
        assert result.p50 == 140.0
        assert result.p90 == 220.0
        assert result.p99 == 320.0
        assert result.avg == 150.0
        assert result.count == 100
        assert len(result.bins) == 20

        # bin_width = max(1.0, 400/20) = 20.0
        assert result.bins[0].range_start == 0.0
        assert result.bins[0].range_end == pytest.approx(20.0)
        assert result.bins[0].count == 10
        assert result.bins[5].count == 50

    async def test_empty_stats_handled(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        """ClickHouse에서 데이터 없을 때 None 통계."""
        mock_ch.register_response(r"avg\(o\.latency\)\s+AS\s+avg_latency_ms", [])
        mock_ch.register_response(r"GROUP\s+BY\s+bin_index", [])

        result = await service.latency_distribution(project_id="p1", run_name="run_x", bins=10)
        assert result.p50 is None
        assert result.count == 0
        assert len(result.bins) == 10
        assert all(b.count == 0 for b in result.bins)

    async def test_invalid_bins_raises(self, service: AnalysisService) -> None:
        with pytest.raises(ValueError):
            await service.latency_distribution(project_id="p", run_name="r", bins=1)

    async def test_passes_run_name_param(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        await service.latency_distribution(project_id="p1", run_name="my_run", bins=10)
        executed = mock_ch._get_executed_queries()
        for _sql, params in executed:
            assert params.get("run_name") == "my_run"
            assert params.get("project_id") == "p1"


# ===================================================================
# 5) cost_distribution
# ===================================================================
@pytest.mark.unit
class TestCostDistribution:
    """Run별 model_cost / eval_cost 분리."""

    async def test_split_costs(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        mock_ch.register_response(
            r"sumIf\(o\.calculated_total_cost",
            [
                {
                    "run_name": "run_a",
                    "model_cost": 0.8,
                    "eval_cost": 0.2,
                    "total_cost": 1.0,
                },
                {
                    "run_name": "run_b",
                    "model_cost": 0.5,
                    "eval_cost": 0.05,
                    "total_cost": 0.55,
                },
            ],
        )
        result = await service.cost_distribution(project_id="p1", run_names=["run_a", "run_b"])
        assert "run_a" in result.runs
        assert result.runs["run_a"].model_cost == pytest.approx(0.8)
        assert result.runs["run_a"].eval_cost == pytest.approx(0.2)
        assert result.runs["run_a"].total_cost == pytest.approx(1.0)
        assert result.runs["run_b"].eval_cost == pytest.approx(0.05)

    async def test_missing_run_filled_with_zero(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        mock_ch.register_response(
            r"sumIf",
            [
                {
                    "run_name": "run_a",
                    "model_cost": 0.5,
                    "eval_cost": 0.1,
                    "total_cost": 0.6,
                }
            ],
        )
        result = await service.cost_distribution(project_id="p1", run_names=["run_a", "run_b"])
        assert "run_b" in result.runs
        assert result.runs["run_b"].model_cost == 0.0
        assert result.runs["run_b"].total_cost == 0.0


# ===================================================================
# 6) Security — 모든 쿼리가 parameterized로만 호출되는지
# ===================================================================
@pytest.mark.unit
class TestSecurityInvariants:
    """``MockClickHouseClient``의 unsafe-pattern 검증을 통과해야 한다."""

    async def test_no_unsafe_patterns_in_executed_queries(
        self, service: AnalysisService, mock_ch: MockClickHouseClient
    ) -> None:
        """모든 분석 메서드를 호출 후 SQL에 f-string 잔재가 없는지 검증."""
        mock_ch.register_response(r".*", [])
        await service.compare_runs(project_id="p", run_names=["a", "b"])
        await service.compare_items(project_id="p", run_names=["a", "b"], score_name="acc")
        await service.score_distribution(
            project_id="p", run_names=["a", "b"], score_name="acc", bins=10
        )
        await service.latency_distribution(project_id="p", run_name="a", bins=10)
        await service.cost_distribution(project_id="p", run_names=["a", "b"])

        unsafe_re = re.compile(r"\{[a-zA-Z_]\w*\}(?!:)")  # f-string {var} 단독
        for sql, _params in mock_ch._get_executed_queries():
            assert unsafe_re.search(sql) is None, f"unsafe SQL: {sql[:200]}"
