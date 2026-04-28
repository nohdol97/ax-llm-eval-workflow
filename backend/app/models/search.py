"""통합 검색 도메인 모델.

프롬프트/데이터셋/실험을 하나의 검색 결과로 묶기 위한 응답 스키마.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SearchResultType = Literal["prompt", "dataset", "experiment"]
"""검색 대상 타입."""


SearchScope = Literal["prompts", "datasets", "experiments", "all"]
"""``type`` 쿼리 파라미터에 허용되는 값."""


class SearchResult(BaseModel):
    """단일 검색 결과 항목.

    매칭 부위 ±40자 컨텍스트만 ``snippet``으로 노출하여 XSS/노이즈 위험을 줄인다.
    """

    type: SearchResultType = Field(..., description="검색 대상 타입")
    id: str = Field(..., description="리소스 식별자")
    name: str = Field(..., description="리소스 표시명")
    snippet: str | None = Field(
        None,
        description="매칭 컨텍스트 (None이면 이름 매칭 only)",
    )
    score: float = Field(..., description="단순 매칭 점수 (1.0 exact / 0.8 name / 0.6 description)")


class SearchResponse(BaseModel):
    """``GET /api/v1/search`` 응답.

    ``results``는 도메인 키를 가진 dict 형태로 반환하여 프론트엔드 렌더링이 단순화되도록 한다.
    """

    query: str = Field(..., description="입력 검색어 (정규화 후)")
    results: dict[str, list[SearchResult]] = Field(
        default_factory=dict,
        description="``prompts | datasets | experiments`` 키별 결과",
    )
    total: int = Field(0, description="모든 결과 합계")
