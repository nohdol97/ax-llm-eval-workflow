#!/bin/bash
# ClickHouse 읽기 전용 계정 설정
# Labs Backend에서 분석/대시보드용 직접 쿼리 시 사용하는 읽기 전용 계정 생성
#
# 사용법 (ClickHouse 컨테이너가 healthy 상태가 된 후 수동 실행):
#   docker compose exec clickhouse bash /scripts/setup-clickhouse-readonly.sh
#
# 보안 규칙:
#   - ClickHouse 직접 쿼리는 읽기 전용 계정 필수
#   - 파라미터화된 쿼리(parameterized query) 필수 (f-string 금지)
#   - 분석/대시보드 용도로만 사용

set -euo pipefail

CLICKHOUSE_READONLY_USER="${CLICKHOUSE_READONLY_USER:-labs_readonly}"
CLICKHOUSE_READONLY_PASSWORD="${CLICKHOUSE_READONLY_PASSWORD:?CLICKHOUSE_READONLY_PASSWORD 환경변수가 설정되지 않았습니다}"
CLICKHOUSE_DB="${CLICKHOUSE_DB:-langfuse}"

echo "[setup-clickhouse-readonly] 읽기 전용 계정 생성 시작..."

# clickhouse-client로 SQL 실행
clickhouse-client --query "
    CREATE USER IF NOT EXISTS ${CLICKHOUSE_READONLY_USER}
    IDENTIFIED BY '${CLICKHOUSE_READONLY_PASSWORD}'
    SETTINGS readonly = 1;
"

clickhouse-client --query "
    GRANT SELECT ON ${CLICKHOUSE_DB}.* TO ${CLICKHOUSE_READONLY_USER};
"

echo "[setup-clickhouse-readonly] 완료: ${CLICKHOUSE_READONLY_USER}@${CLICKHOUSE_DB} (SELECT 전용)"
