"""ClickHouse 분석 쿼리 템플릿 (Phase 6).

본 모듈은 Langfuse v3 ClickHouse 스키마를 기반으로 한 분석 쿼리만 제공한다.

설계 원칙
- 모든 쿼리는 ``{name:Type}`` ClickHouse parameterized query 문법만 사용
- f-string / ``.format()`` 보간 금지 — `clickhouse_client._validate_sql`이 차단
- LIMIT 절을 명시 (``ClickHouseClient`` 가 자동 LIMIT 10000을 추가하지만,
  분석 쿼리는 page_size를 받아 명시적으로 제한)
- GROUP BY 결과는 항상 정렬 (테스트 안정성)

Langfuse v3 ClickHouse 스키마 (간략)
- ``traces``        : id, name, project_id, user_id, created_at, metadata
- ``observations``  : trace_id, name, type=GENERATION, latency, model, usage,
                      calculated_total_cost, start_time
- ``scores``        : trace_id, name, value, comment, created_at
- ``dataset_run_items`` : dataset_run_id, dataset_item_id, trace_id

본 프로젝트는 Run 식별자로 ``traces.name`` 을 사용한다 (배치 실행 시 trace.name에
``run_name`` 을 기록한다는 가정 — IMPLEMENTATION.md 참조).
"""

from __future__ import annotations

from textwrap import dedent

# ---------- 1) Run 요약 비교 ----------
# Run × (지연 통계, 누적 비용, 평균 토큰, 평균 score, items_completed)
COMPARE_RUNS_QUERY: str = dedent("""
    SELECT
        t.name AS run_name,
        avg(o.latency) AS avg_latency_ms,
        quantile(0.5)(o.latency) AS p50_latency_ms,
        quantile(0.9)(o.latency) AS p90_latency_ms,
        quantile(0.99)(o.latency) AS p99_latency_ms,
        sum(o.calculated_total_cost) AS total_cost_usd,
        avg(toFloat64OrNull(JSONExtractString(o.usage, 'total'))) AS avg_total_tokens,
        avg(s.value) AS avg_score,
        uniqExact(t.id) AS items_completed
    FROM traces AS t
    LEFT JOIN observations AS o
        ON o.trace_id = t.id AND o.type = 'GENERATION'
    LEFT JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
    GROUP BY t.name
    ORDER BY t.name
    LIMIT 100
""").strip()

# ---------- 2) Run × score_name 비교 ----------
SCORE_COMPARISON_QUERY: str = dedent("""
    SELECT
        t.name AS run_name,
        s.name AS score_name,
        avg(s.value) AS avg_value,
        count() AS sample_count
    FROM traces AS t
    INNER JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
    GROUP BY t.name, s.name
    ORDER BY s.name, t.name
    LIMIT 1000
""").strip()

# ---------- 3) 아이템별 상세 비교 ----------
# dataset_run_items 를 통해 dataset_item_id 단위로 trace를 그룹핑.
# Output은 GENERATION observation의 output 컬럼을 가정 (Langfuse v3).
ITEM_COMPARISON_QUERY: str = dedent("""
    SELECT
        dri.dataset_item_id AS dataset_item_id,
        t.name AS run_name,
        any(t.id) AS trace_id,
        any(t.input) AS input,
        any(t.expected_output) AS expected,
        any(o.output) AS output,
        avg(o.latency) AS latency_ms,
        sum(o.calculated_total_cost) AS cost_usd
    FROM dataset_run_items AS dri
    INNER JOIN traces AS t ON t.id = dri.trace_id
    LEFT JOIN observations AS o
        ON o.trace_id = t.id AND o.type = 'GENERATION'
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
    GROUP BY dri.dataset_item_id, t.name
    ORDER BY dri.dataset_item_id, t.name
    LIMIT 5000
""").strip()

# ---------- 4) 아이템별 score (item_id × run × score_name) ----------
ITEM_SCORES_QUERY: str = dedent("""
    SELECT
        dri.dataset_item_id AS dataset_item_id,
        t.name AS run_name,
        s.name AS score_name,
        avg(s.value) AS value
    FROM dataset_run_items AS dri
    INNER JOIN traces AS t ON t.id = dri.trace_id
    INNER JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
    GROUP BY dri.dataset_item_id, t.name, s.name
    ORDER BY dri.dataset_item_id, t.name, s.name
    LIMIT 10000
""").strip()

# ---------- 5) Outlier 감지 (score_range 큰 아이템) ----------
OUTLIER_DETECTION_QUERY: str = dedent("""
    SELECT
        dri.dataset_item_id AS dataset_item_id,
        s.name AS score_name,
        max(s.value) - min(s.value) AS score_range,
        count() AS sample_count
    FROM dataset_run_items AS dri
    INNER JOIN traces AS t ON t.id = dri.trace_id
    INNER JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
      AND s.name = {score_name:String}
    GROUP BY dri.dataset_item_id, s.name
    HAVING score_range > 0
    ORDER BY score_range DESC
    LIMIT 200
""").strip()

# ---------- 6) 비용 효율 (Run 별 score_per_dollar) ----------
COST_EFFICIENCY_QUERY: str = dedent("""
    SELECT
        t.name AS run_name,
        avg(s.value) AS avg_score,
        sum(o.calculated_total_cost) AS total_cost_usd,
        if(sum(o.calculated_total_cost) > 0,
           avg(s.value) / sum(o.calculated_total_cost), NULL)
          AS score_per_dollar
    FROM traces AS t
    LEFT JOIN observations AS o
        ON o.trace_id = t.id AND o.type = 'GENERATION'
    LEFT JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
    GROUP BY t.name
    ORDER BY t.name
    LIMIT 100
""").strip()

# ---------- 7) 스코어 분포 (히스토그램 bin) ----------
# Run × bin_index → count. score 범위는 [0.0, 1.0] 가정.
SCORE_DISTRIBUTION_QUERY: str = dedent("""
    SELECT
        t.name AS run_name,
        least(toUInt32(floor(s.value * {bins:UInt32})), {bins:UInt32} - 1) AS bin_index,
        count() AS sample_count
    FROM traces AS t
    INNER JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
      AND s.name = {score_name:String}
      AND s.value IS NOT NULL
    GROUP BY t.name, bin_index
    ORDER BY t.name, bin_index
    LIMIT 10000
""").strip()

# ---------- 7-1) 스코어 통계 (avg/stddev/min/max/count per run) ----------
SCORE_STATISTICS_QUERY: str = dedent("""
    SELECT
        t.name AS run_name,
        avg(s.value) AS avg_value,
        stddevPop(s.value) AS stddev_value,
        min(s.value) AS min_value,
        max(s.value) AS max_value,
        count() AS sample_count
    FROM traces AS t
    INNER JOIN scores AS s ON s.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
      AND s.name = {score_name:String}
      AND s.value IS NOT NULL
    GROUP BY t.name
    ORDER BY t.name
    LIMIT 100
""").strip()

# ---------- 8) 지연 분포 (히스토그램 + percentile) ----------
LATENCY_DISTRIBUTION_QUERY: str = dedent("""
    SELECT
        least(toUInt32(floor(o.latency / {bin_width:Float64})),
              {bins:UInt32} - 1) AS bin_index,
        count() AS sample_count
    FROM traces AS t
    INNER JOIN observations AS o
        ON o.trace_id = t.id AND o.type = 'GENERATION'
    WHERE t.project_id = {project_id:String}
      AND t.name = {run_name:String}
      AND o.latency IS NOT NULL
    GROUP BY bin_index
    ORDER BY bin_index
    LIMIT 1000
""").strip()

LATENCY_STATS_QUERY: str = dedent("""
    SELECT
        avg(o.latency) AS avg_latency_ms,
        stddevPop(o.latency) AS stddev_ms,
        quantile(0.5)(o.latency) AS p50_ms,
        quantile(0.9)(o.latency) AS p90_ms,
        quantile(0.99)(o.latency) AS p99_ms,
        max(o.latency) AS max_ms,
        count() AS sample_count
    FROM traces AS t
    INNER JOIN observations AS o
        ON o.trace_id = t.id AND o.type = 'GENERATION'
    WHERE t.project_id = {project_id:String}
      AND t.name = {run_name:String}
      AND o.latency IS NOT NULL
    LIMIT 1
""").strip()

# ---------- 9) 비용 분포 (model_cost vs eval_cost 분리) ----------
# Langfuse 관행상 GENERATION observation은 LLM 호출, EVENT 또는 SPAN type 중에서
# name='judge'/'embedding'은 Judge/Embedding 호출로 보고 비용 분리.
COST_DISTRIBUTION_QUERY: str = dedent("""
    SELECT
        t.name AS run_name,
        sumIf(o.calculated_total_cost,
              o.type = 'GENERATION'
              AND o.name NOT IN ('judge', 'embedding', 'llm_judge')
        ) AS model_cost,
        sumIf(o.calculated_total_cost,
              o.name IN ('judge', 'embedding', 'llm_judge')
        ) AS eval_cost,
        sum(o.calculated_total_cost) AS total_cost
    FROM traces AS t
    LEFT JOIN observations AS o ON o.trace_id = t.id
    WHERE t.project_id = {project_id:String}
      AND t.name IN {run_names:Array(String)}
    GROUP BY t.name
    ORDER BY t.name
    LIMIT 100
""").strip()


# ---------- 모든 템플릿 (테스트용) ----------
ALL_QUERIES: tuple[tuple[str, str], ...] = (
    ("COMPARE_RUNS_QUERY", COMPARE_RUNS_QUERY),
    ("SCORE_COMPARISON_QUERY", SCORE_COMPARISON_QUERY),
    ("ITEM_COMPARISON_QUERY", ITEM_COMPARISON_QUERY),
    ("ITEM_SCORES_QUERY", ITEM_SCORES_QUERY),
    ("OUTLIER_DETECTION_QUERY", OUTLIER_DETECTION_QUERY),
    ("COST_EFFICIENCY_QUERY", COST_EFFICIENCY_QUERY),
    ("SCORE_DISTRIBUTION_QUERY", SCORE_DISTRIBUTION_QUERY),
    ("SCORE_STATISTICS_QUERY", SCORE_STATISTICS_QUERY),
    ("LATENCY_DISTRIBUTION_QUERY", LATENCY_DISTRIBUTION_QUERY),
    ("LATENCY_STATS_QUERY", LATENCY_STATS_QUERY),
    ("COST_DISTRIBUTION_QUERY", COST_DISTRIBUTION_QUERY),
)
