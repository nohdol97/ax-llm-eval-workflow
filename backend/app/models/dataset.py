"""데이터셋 도메인 Pydantic 모델.

Langfuse v3 데이터셋 / 데이터셋 아이템 표현, 업로드 진행 상태, 컬럼 매핑 요청 등을
정의한다. API_DESIGN.md §3 / §6의 응답 스키마와 일관된다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

UploadStatus = Literal["pending", "running", "completed", "failed"]


class DatasetSummary(BaseModel):
    """데이터셋 목록 응답의 단일 항목."""

    name: str = Field(..., description="데이터셋 이름 (Langfuse 고유 식별자)")
    description: str | None = Field(None, description="데이터셋 설명")
    item_count: int = Field(..., ge=0, description="아이템 수")
    created_at: datetime = Field(..., description="생성 시각 (UTC)")


class DatasetItem(BaseModel):
    """데이터셋 아이템 단일 표현.

    Langfuse 측 ``input`` 은 ``dict`` 형태(컬럼 매핑 결과). ``expected_output``은
    원본 컬럼 값에 따라 문자열, dict, 또는 None일 수 있다.
    """

    id: str = Field(..., description="Langfuse 데이터셋 아이템 ID")
    input: dict[str, Any] = Field(..., description="입력 컬럼 dict")
    expected_output: str | dict[str, Any] | None = Field(
        None, description="기대 출력 (없을 수 있음)"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="부가 메타데이터")


class DatasetListResponse(BaseModel):
    """데이터셋 목록 응답."""

    items: list[DatasetSummary] = Field(default_factory=list, description="데이터셋 목록")
    total: int = Field(..., ge=0, description="전체 데이터셋 수")
    page: int = Field(..., ge=1, description="현재 페이지")
    page_size: int = Field(..., ge=1, le=100, description="페이지 크기")


class DatasetItemListResponse(BaseModel):
    """데이터셋 아이템 목록 응답."""

    items: list[DatasetItem] = Field(default_factory=list, description="아이템 목록")
    total: int = Field(..., ge=0, description="전체 아이템 수")
    page: int = Field(..., ge=1, description="현재 페이지")
    page_size: int = Field(..., ge=1, le=100, description="페이지 크기")


class UploadInitResponse(BaseModel):
    """업로드 초기 응답 — ``POST /datasets/upload``."""

    upload_id: str = Field(..., description="업로드 추적 ID")
    status: UploadStatus = Field(..., description="초기 상태")
    dataset_name: str = Field(..., description="대상 데이터셋 이름")


class UploadProgress(BaseModel):
    """업로드 진행 상태 (Redis 스냅샷 + SSE payload)."""

    upload_id: str = Field(..., description="업로드 ID")
    status: UploadStatus = Field(..., description="상태")
    processed: int = Field(..., ge=0, description="처리된 아이템 수")
    total: int = Field(..., ge=0, description="전체 아이템 수")
    error_message: str | None = Field(None, description="실패 시 에러 메시지")
    dataset_name: str | None = Field(None, description="대상 데이터셋 이름")


class UploadMappingRequest(BaseModel):
    """파일 업로드 / 미리보기 시 컬럼 매핑 요청.

    multipart/form-data 의 ``mapping`` 필드는 JSON 문자열로 전달되며 백엔드에서
    이 모델로 파싱된다. 입력 컬럼은 최소 1개 이상이어야 한다.
    """

    input_columns: list[str] = Field(
        ..., min_length=1, description="입력 컬럼명 목록 (1개 이상)"
    )
    output_column: str = Field(
        ..., min_length=1, description="기대 출력 컬럼명"
    )
    metadata_columns: list[str] = Field(
        default_factory=list, description="메타데이터 컬럼 (선택)"
    )


class FromItemsRequest(BaseModel):
    """실패 아이템 → 새 데이터셋 파생 요청 (API_DESIGN.md §12).

    ``item_ids``의 길이 검증은 ``Field(min_length, max_length)``로 수행하여
    pydantic ``ValueError`` ctx 직렬화 이슈(공통 에러 핸들러)를 회피한다.
    """

    project_id: str = Field(..., min_length=1, description="대상 프로젝트 ID")
    source_experiment_id: str = Field(
        ..., min_length=1, description="원본 실험 ID"
    )
    item_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="복사할 아이템 ID 목록 (1~100건, bulk 한도)",
    )
    new_dataset_name: str = Field(
        ..., min_length=1, max_length=200, description="새 데이터셋 이름"
    )
    description: str | None = Field(None, description="새 데이터셋 설명 (선택)")


class FromItemsResponse(BaseModel):
    """``POST /datasets/from-items`` 응답."""

    dataset_name: str = Field(..., description="새로 만든 데이터셋 이름")
    items_created: int = Field(..., ge=0, description="실제 생성된 아이템 수")
    status: Literal["completed", "partial", "failed"] = Field(
        ..., description="처리 상태"
    )


class PreviewItem(BaseModel):
    """업로드 미리보기 단건."""

    input: dict[str, Any]
    expected_output: str | dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreviewResponse(BaseModel):
    """``POST /datasets/upload/preview`` 응답."""

    columns: list[str] = Field(..., description="감지된 컬럼명 목록")
    preview: list[PreviewItem] = Field(..., description="최대 5건 미리보기")
    total_rows: int = Field(..., ge=0, description="전체 행 수")


class DeleteResponse(BaseModel):
    """``DELETE /datasets/{name}`` 응답."""

    dataset_name: str = Field(..., description="삭제된 데이터셋 이름")
    deleted: bool = Field(..., description="삭제 성공 여부")
    deleted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="삭제 처리 시각 (UTC)",
    )
