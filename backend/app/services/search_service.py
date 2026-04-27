"""통합 검색 서비스.

case-insensitive substring 매칭으로 프롬프트/데이터셋/실험을 한 번에 조회한다.

향후 ClickHouse ``positionCaseInsensitiveUTF8`` 기반 인덱스 검색으로 확장 가능하나,
본 단계에서는 Langfuse SDK + Redis 인덱스에서 가져온 메타데이터에 단순 매칭만 수행한다.

보안:
- ``q`` 표현식은 라우터 단에서 길이/문자 검증 후 진입 (API_DESIGN.md §10.1).
- 본 함수에서는 추가 escape 없이 사용하되, 결과 ``snippet``은 ±40자 컨텍스트로 제한한다.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.models.search import SearchResponse, SearchResult, SearchScope

logger = get_logger(__name__)

_SNIPPET_RADIUS = 40
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50

# 점수 가중치
_SCORE_EXACT = 1.0
_SCORE_NAME = 0.8
_SCORE_DESCRIPTION = 0.6


def _normalize_query(query: str) -> str:
    """쿼리 trim + 단일 공백화."""
    return " ".join(query.strip().split())


def _make_snippet(text: str | None, query: str) -> str | None:
    """매칭 위치 ±40자 컨텍스트. 매칭 없으면 None."""
    if not text:
        return None
    idx = text.lower().find(query.lower())
    if idx < 0:
        return None
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + len(query) + _SNIPPET_RADIUS)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _score_match(name: str, description: str | None, query: str) -> float:
    """매칭 강도 점수 산출."""
    q = query.lower()
    n = name.lower()
    if n == q:
        return _SCORE_EXACT
    if q in n:
        return _SCORE_NAME
    if description and q in description.lower():
        return _SCORE_DESCRIPTION
    return 0.0


def _is_match(name: str, description: str | None, query: str) -> bool:
    """이름 또는 설명에 substring 매칭이 있는지."""
    q = query.lower()
    if q in name.lower():
        return True
    if description and q in description.lower():
        return True
    return False


# ---------- 헬퍼: prompts/datasets 메타 추출 ----------
def _list_prompt_meta(langfuse: Any) -> list[dict[str, Any]]:
    """Langfuse(또는 mock)에서 프롬프트 메타 추출.

    Mock(``MockLangfuseClient._prompts``)과 실제 SDK(``list_prompts`` 메서드) 모두 지원.
    """
    if hasattr(langfuse, "list_prompts"):
        try:
            data = langfuse.list_prompts()
            if isinstance(data, list):
                return [
                    {
                        "name": p.get("name"),
                        "description": p.get("description"),
                        "version": p.get("version"),
                    }
                    for p in data
                    if p.get("name")
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_list_prompts_failed", error=str(exc))

    # MockLangfuseClient — 내부 dict 직접 조회
    prompts_attr = getattr(langfuse, "_prompts", None)
    if isinstance(prompts_attr, dict):
        seen: dict[str, dict[str, Any]] = {}
        for (name, version), prompt in prompts_attr.items():
            existing = seen.get(name)
            current_version = getattr(prompt, "version", version)
            if existing is None or current_version > existing.get("version", 0):
                seen[name] = {
                    "name": name,
                    "description": " ".join(getattr(prompt, "tags", []) or []),
                    "version": current_version,
                    "body": getattr(prompt, "body", None),
                }
        return list(seen.values())
    return []


def _list_dataset_meta(langfuse: Any) -> list[dict[str, Any]]:
    """데이터셋 메타 추출."""
    if hasattr(langfuse, "list_datasets"):
        try:
            data = langfuse.list_datasets()
            if isinstance(data, list):
                return [
                    {
                        "name": d.get("name"),
                        "description": d.get("description"),
                        "item_count": d.get("item_count"),
                    }
                    for d in data
                    if d.get("name")
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_list_datasets_failed", error=str(exc))

    datasets_attr = getattr(langfuse, "_datasets", None)
    if isinstance(datasets_attr, dict):
        return [
            {
                "name": ds.name,
                "description": ds.description,
                "item_count": len(ds.items),
            }
            for ds in datasets_attr.values()
        ]
    return []


async def _list_experiment_meta(redis: Any) -> list[dict[str, Any]]:
    """Redis에서 실험 메타 추출 — ``ax:experiment:*`` Hash 스캔."""
    if redis is None:
        return []
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        if hasattr(redis, "scan_iter"):
            async for key in redis.scan_iter(match="ax:experiment:*"):
                # 보조 키(``:runs``, ``:config_blob`` 등) 스킵
                if not isinstance(key, str):
                    key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                if key.count(":") != 2:
                    # ax:experiment:{id} 형태만
                    continue
                exp_id = key.rsplit(":", 1)[-1]
                if exp_id in seen_ids:
                    continue
                seen_ids.add(exp_id)
                # Hash fetch — RedisClient는 직접 hgetall이 없으므로 underlying 사용
                underlying = getattr(redis, "underlying", None) or getattr(
                    redis, "_client", None
                )
                if underlying is None:
                    continue
                raw = await underlying.hgetall(key)
                if not raw:
                    continue
                meta: dict[str, Any] = {}
                for k, v in raw.items():
                    kk = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                    vv = v.decode("utf-8") if isinstance(v, bytes) else v
                    meta[kk] = vv
                results.append(
                    {
                        "id": exp_id,
                        "name": meta.get("name") or exp_id,
                        "description": meta.get("description"),
                        "status": meta.get("status"),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("search_list_experiments_failed", error=str(exc))
    return results


# ---------- 검증 ----------
_ALLOWED_QUERY_RE = re.compile(
    r"^[\w\s\-_.:@/À-￿]+$",
    re.UNICODE,
)


def validate_query(query: str) -> str:
    """``q`` 표현식 검증 — API_DESIGN.md §10.1 준수.

    - 길이 1~200
    - 허용 문자 외 거절
    - 와일드카드/연산자 거절

    위반 시 ``ValueError``.
    """
    q = _normalize_query(query)
    if not 2 <= len(q) <= 200:
        raise ValueError("쿼리 길이는 2~200자여야 합니다.")
    if not _ALLOWED_QUERY_RE.match(q):
        raise ValueError("쿼리에 허용되지 않은 문자가 포함되어 있습니다.")
    forbidden = set("*?%\\|&!()[]{}<>;\"'")
    if any(ch in forbidden for ch in q):
        raise ValueError("쿼리에 허용되지 않은 연산자가 포함되어 있습니다.")
    return q


# ---------- 메인 ----------
async def search(
    query: str,
    type_: SearchScope,
    project_id: str | None,
    limit: int,
    langfuse: Any,
    redis: Any,
) -> SearchResponse:
    """통합 검색 실행.

    Args:
        query: 검증된 쿼리 문자열
        type_: ``prompts | datasets | experiments | all``
        project_id: 프로젝트 ID (현 구현에서는 필터에 미사용 — Langfuse SDK 통합 시 활용)
        limit: 도메인별 최대 결과 수
        langfuse: Langfuse 클라이언트(또는 mock)
        redis: Redis 클라이언트(또는 mock)
    """
    _ = project_id  # 향후 멀티프로젝트 분리에 사용
    capped = max(1, min(_MAX_LIMIT, limit or _DEFAULT_LIMIT))
    q_norm = _normalize_query(query)

    results: dict[str, list[SearchResult]] = {
        "prompts": [],
        "datasets": [],
        "experiments": [],
    }

    # Prompts
    if type_ in ("prompts", "all"):
        for p in _list_prompt_meta(langfuse):
            name = p.get("name") or ""
            desc = p.get("description")
            body = p.get("body")
            score = _score_match(name, desc or body, q_norm)
            if score == 0.0:
                continue
            snippet = _make_snippet(body or desc, q_norm) or _make_snippet(
                desc, q_norm
            )
            results["prompts"].append(
                SearchResult(
                    type="prompt",
                    id=name,
                    name=name,
                    snippet=snippet,
                    score=score,
                )
            )

    # Datasets
    if type_ in ("datasets", "all"):
        for d in _list_dataset_meta(langfuse):
            name = d.get("name") or ""
            desc = d.get("description")
            score = _score_match(name, desc, q_norm)
            if score == 0.0:
                continue
            snippet = _make_snippet(desc, q_norm)
            results["datasets"].append(
                SearchResult(
                    type="dataset",
                    id=name,
                    name=name,
                    snippet=snippet,
                    score=score,
                )
            )

    # Experiments
    if type_ in ("experiments", "all"):
        for e in await _list_experiment_meta(redis):
            name = e.get("name") or ""
            desc = e.get("description")
            score = _score_match(name, desc, q_norm)
            if score == 0.0 and not _is_match(e.get("id", ""), None, q_norm):
                continue
            if score == 0.0:
                # ID 매칭 보너스 점수
                score = _SCORE_NAME
            snippet = _make_snippet(desc, q_norm)
            results["experiments"].append(
                SearchResult(
                    type="experiment",
                    id=str(e.get("id")),
                    name=name,
                    snippet=snippet,
                    score=score,
                )
            )

    # 점수 내림차순 + limit
    for key in results:
        results[key].sort(key=lambda r: r.score, reverse=True)
        results[key] = results[key][:capped]

    total = sum(len(v) for v in results.values())
    return SearchResponse(query=q_norm, results=results, total=total)
