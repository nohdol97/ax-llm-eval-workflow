# Architecture Decision Records

본 디렉터리는 본 프로젝트의 Architecture Decision Records (ADR)를 보관한다. ADR은 중요한 아키텍처/설계 결정을 컨텍스트와 함께 기록하여 추적성을 확보한다.

## 작성 규칙

- 파일명: `ADR-NNN-{kebab-case-title}.md` (예: `ADR-011-secrets-management.md`)
- 각 ADR은 다음 섹션을 포함한다:
  1. **상태**: Draft / Proposed / Accepted / Deprecated / Superseded
  2. **컨텍스트**: 결정이 필요한 배경, 제약, 이해관계자
  3. **결정**: 채택된 안 (명확하게 한 문장)
  4. **결과**: 긍정적/부정적 영향, trade-off
  5. **대안 검토**: 검토했으나 채택하지 않은 옵션과 그 사유
- 결정 후 이력 변경은 새 ADR로 superseded — 기존 ADR은 보존

## ADR 목록

| 번호 | 제목 | 상태 |
|---|---|---|
| ADR-001~010 | _(과거 — 본 디렉터리 외부에 보관 또는 미작성)_ | — |
| [ADR-011](ADR-011-secrets-management.md) | 시크릿 관리 정책 | Accepted |
| ADR-012 | (조건부) ClickHouse 직접 접근 vs Langfuse public API 폴백 | _(미작성, 1.3 결정 후)_ |

## 참조

- [BUILD_ORDER.md](../BUILD_ORDER.md) — Phase 1 작업 1-9 (ADR-011 작성 위치)
- [INFRA_INTEGRATION_CHECKLIST.md](../INFRA_INTEGRATION_CHECKLIST.md) — §1.3 (ADR-012 트리거)
