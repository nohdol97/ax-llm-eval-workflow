"""데이터셋 API + 서비스 단위 테스트.

검증 범위:
- 파일 파싱 (CSV / JSON / JSONL, 인코딩 fallback)
- 컬럼 매핑 검증 (누락 시 422)
- 파일 크기/행 수 제한
- 비동기 업로드 진행 (Redis 상태 변화)
- SSE 진행률 스트리밍 (TestClient stream)
- CSV formula injection 방지
- RBAC: admin-only DELETE, user+ upload, viewer 차단

본 테스트는 `MockLangfuseClient`(`_datasets` 인메모리)와 `MockRedisClient`(fakeredis)를
`dependency_overrides`로 주입한다.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_langfuse_client, get_redis_client
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.services.dataset_service import (
    MAX_ROWS,
    UploadMappingRequest,
    _load_progress,
    _save_progress,
    detect_encoding,
    detect_file_format,
    is_formula_injection,
    new_upload_id,
    parse_file,
    process_upload,
    sanitize_csv_value,
    stream_upload_progress,
)
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_redis import MockRedisClient

# ---------- 유틸 ----------


def _make_user(role: str = "user", uid: str = "user-1") -> User:
    """가짜 User 객체 — dependency_overrides 주입용."""
    return User(id=uid, email=f"{uid}@x.com", role=role, name=uid, groups=[])


@pytest.fixture
def app_with_overrides(
    langfuse_client: MockLangfuseClient,
    redis_client: MockRedisClient,
) -> Any:
    """기본 user 권한 + Mock 클라이언트가 주입된 FastAPI app."""
    app = create_app()
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_current_user] = lambda: _make_user("user")
    return app


@pytest.fixture
def client(app_with_overrides: Any) -> TestClient:
    """기본 TestClient — user 권한."""
    return TestClient(app_with_overrides)


# ---------- 1) 파일 파싱 ----------


@pytest.mark.unit
class TestParseFile:
    """CSV / JSON / JSONL 파일 파싱."""

    def test_parse_csv_basic(self) -> None:
        """기본 CSV → input/expected/metadata 분리."""
        content = b"text,label,category\nhello,positive,greeting\nbye,negative,farewell\n"
        mapping = UploadMappingRequest(
            input_columns=["text"],
            output_column="label",
            metadata_columns=["category"],
        )
        rows = list(parse_file(content, "data.csv", mapping))
        assert len(rows) == 2
        assert rows[0]["input"] == {"text": "hello"}
        assert rows[0]["expected_output"] == "positive"
        assert rows[0]["metadata"] == {"category": "greeting"}

    def test_parse_jsonl(self) -> None:
        """JSONL 한 줄당 한 객체."""
        content = b'{"q":"hi","a":"hello"}\n{"q":"bye","a":"goodbye"}\n'
        mapping = UploadMappingRequest(input_columns=["q"], output_column="a")
        rows = list(parse_file(content, "data.jsonl", mapping))
        assert len(rows) == 2
        assert rows[1]["input"] == {"q": "bye"}
        assert rows[1]["expected_output"] == "goodbye"

    def test_parse_json_array(self) -> None:
        """JSON 배열 형식."""
        content = json.dumps(
            [
                {"text": "a", "label": "x"},
                {"text": "b", "label": "y"},
            ]
        ).encode("utf-8")
        mapping = UploadMappingRequest(input_columns=["text"], output_column="label")
        rows = list(parse_file(content, "data.json", mapping))
        assert len(rows) == 2
        assert rows[0]["expected_output"] == "x"

    def test_parse_csv_utf8_bom(self) -> None:
        """UTF-8 BOM 감지 후 정상 파싱."""
        content = b"\xef\xbb\xbftext,label\nhi,p\n"
        mapping = UploadMappingRequest(input_columns=["text"], output_column="label")
        rows = list(parse_file(content, "data.csv", mapping))
        assert rows[0]["input"]["text"] == "hi"

    def test_parse_csv_euc_kr(self) -> None:
        """EUC-KR 인코딩 fallback 확인."""
        content = "text,label\n안녕,긍정\n".encode("euc-kr")
        encoding = detect_encoding(content)
        assert encoding in ("euc-kr", "cp949")
        mapping = UploadMappingRequest(input_columns=["text"], output_column="label")
        rows = list(parse_file(content, "data.csv", mapping))
        assert rows[0]["input"]["text"] == "안녕"

    def test_missing_input_column_raises(self) -> None:
        """매핑된 input 컬럼이 없으면 검증 에러."""
        from app.services.dataset_service import DatasetValidationError

        content = b"text,label\nhi,p\n"
        mapping = UploadMappingRequest(input_columns=["nonexistent"], output_column="label")
        with pytest.raises(DatasetValidationError):
            list(parse_file(content, "data.csv", mapping))

    def test_missing_output_column_raises(self) -> None:
        """매핑된 output 컬럼이 없으면 검증 에러."""
        from app.services.dataset_service import DatasetValidationError

        content = b"text,label\nhi,p\n"
        mapping = UploadMappingRequest(input_columns=["text"], output_column="missing")
        with pytest.raises(DatasetValidationError):
            list(parse_file(content, "data.csv", mapping))

    def test_invalid_json_raises(self) -> None:
        """잘못된 JSON은 검증 에러."""
        from app.services.dataset_service import DatasetValidationError

        content = b'{"bad json'
        mapping = UploadMappingRequest(input_columns=["x"], output_column="y")
        with pytest.raises(DatasetValidationError):
            list(parse_file(content, "data.json", mapping))

    def test_too_many_rows_raises(self) -> None:
        """MAX_ROWS 초과 시 즉시 raise."""
        from app.services.dataset_service import TooManyRowsError

        # 1만 + 1건
        rows = ["text,label"] + [f"r{i},p" for i in range(MAX_ROWS + 1)]
        content = ("\n".join(rows) + "\n").encode("utf-8")
        mapping = UploadMappingRequest(input_columns=["text"], output_column="label")
        with pytest.raises(TooManyRowsError):
            list(parse_file(content, "big.csv", mapping))

    def test_file_too_large_raises(self) -> None:
        """50MB 초과 시 즉시 raise."""
        from app.services.dataset_service import MAX_FILE_SIZE_BYTES, FileTooLargeError

        # 헤더 + 큰 단일 행 (decode 부담 회피)
        content = b"text,label\n" + (b"x" * (MAX_FILE_SIZE_BYTES + 100))
        mapping = UploadMappingRequest(input_columns=["text"], output_column="label")
        with pytest.raises(FileTooLargeError):
            list(parse_file(content, "huge.csv", mapping))


# ---------- 2) 형식 감지 ----------


@pytest.mark.unit
class TestDetectFormat:
    """파일 확장자 + 본문 시그니처."""

    def test_csv_extension(self) -> None:
        assert detect_file_format("a.csv", b"a,b\n1,2") == "csv"

    def test_jsonl_extension(self) -> None:
        assert detect_file_format("a.jsonl", b"{}\n{}") == "jsonl"

    def test_ndjson_extension(self) -> None:
        assert detect_file_format("a.ndjson", b"{}\n{}") == "jsonl"

    def test_json_extension_array(self) -> None:
        assert detect_file_format("a.json", b'[{"a":1}]') == "json"

    def test_json_with_multiple_lines_treated_as_jsonl(self) -> None:
        """확장자가 .json이지만 줄마다 객체이면 jsonl로 인식."""
        content = b'{"a":1}\n{"a":2}\n'
        assert detect_file_format("a.json", content) == "jsonl"

    def test_no_extension_detects_by_signature(self) -> None:
        assert detect_file_format("noext", b'[{"a":1}]') == "json"
        assert detect_file_format("noext", b"a,b\n1,2") == "csv"


# ---------- 3) Formula injection ----------


@pytest.mark.unit
class TestFormulaInjection:
    """CSV formula injection 방지."""

    @pytest.mark.parametrize("value", ["=cmd|/c", "+1+1", "-2+3", "@SUM", "\tHIDDEN", "\rEVIL"])
    def test_dangerous_prefix_detected(self, value: str) -> None:
        """위험한 prefix는 sanitize 시 ``'`` 추가."""
        assert is_formula_injection(value) is True
        assert sanitize_csv_value(value).startswith("'")

    def test_safe_value_unchanged(self) -> None:
        """안전한 값은 그대로."""
        assert is_formula_injection("hello") is False
        assert sanitize_csv_value("hello") == "hello"

    def test_non_string_unchanged(self) -> None:
        assert sanitize_csv_value(42) == 42
        assert sanitize_csv_value(None) is None


# ---------- 4) Redis 진행률 ----------


@pytest.mark.unit
class TestProgressPersistence:
    """Redis 진행률 저장/조회."""

    async def test_save_and_load_progress(self, redis_client: MockRedisClient) -> None:
        """진행 상태가 정확히 저장/조회된다."""
        await _save_progress(
            redis_client,  # type: ignore[arg-type]
            "ds_upload_xyz",
            status="running",
            processed=10,
            total=100,
            dataset_name="test_ds",
            owner_user_id="user-1",
        )
        snap = await _load_progress(redis_client, "ds_upload_xyz")  # type: ignore[arg-type]
        assert snap is not None
        assert snap["status"] == "running"
        assert snap["processed"] == 10
        assert snap["total"] == 100
        assert snap["dataset_name"] == "test_ds"
        assert snap["owner_user_id"] == "user-1"

    async def test_load_missing_returns_none(self, redis_client: MockRedisClient) -> None:
        """존재하지 않는 키는 None."""
        snap = await _load_progress(redis_client, "nonexistent")  # type: ignore[arg-type]
        assert snap is None


# ---------- 5) process_upload (전체 흐름) ----------


@pytest.mark.unit
class TestProcessUpload:
    """비동기 업로드 처리."""

    async def test_basic_upload_completes(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """정상 업로드 → status=completed + 데이터셋 아이템 생성."""
        upload_id = new_upload_id()
        content = b"text,label\nhi,p\nbye,n\n"
        mapping = UploadMappingRequest(input_columns=["text"], output_column="label")

        await process_upload(
            upload_id,
            content,
            "data.csv",
            "test_ds",
            "desc",
            mapping,
            langfuse=langfuse_client,
            redis=redis_client,  # type: ignore[arg-type]
            owner_user_id="user-1",
            initial_total=2,
        )

        snap = await _load_progress(redis_client, upload_id)  # type: ignore[arg-type]
        assert snap is not None
        assert snap["status"] == "completed"
        assert snap["processed"] == 2
        assert "test_ds" in langfuse_client._datasets
        assert len(langfuse_client._datasets["test_ds"].items) == 2

    async def test_invalid_file_marks_failed(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """잘못된 파일 → status=failed + error_message."""
        upload_id = new_upload_id()
        content = b"only_one_col\nhi\n"  # output 컬럼 없음
        mapping = UploadMappingRequest(input_columns=["only_one_col"], output_column="missing")

        await process_upload(
            upload_id,
            content,
            "data.csv",
            "bad_ds",
            None,
            mapping,
            langfuse=langfuse_client,
            redis=redis_client,  # type: ignore[arg-type]
            owner_user_id="user-1",
        )

        snap = await _load_progress(redis_client, upload_id)  # type: ignore[arg-type]
        assert snap is not None
        assert snap["status"] == "failed"
        assert snap["error_message"]


# ---------- 6) SSE 진행률 스트리밍 ----------


@pytest.mark.unit
class TestSSEStream:
    """SSE 진행률 스트리밍."""

    async def test_stream_emits_progress_then_done(
        self,
        redis_client: MockRedisClient,
    ) -> None:
        """초기 running → 후속 completed로 갱신되면 progress + done 이벤트 발송."""
        upload_id = "ds_upload_test"

        # 초기 상태
        await _save_progress(
            redis_client,  # type: ignore[arg-type]
            upload_id,
            status="running",
            processed=0,
            total=10,
            dataset_name="ds",
        )

        async def _flip_to_completed() -> None:
            await asyncio.sleep(0.2)
            await _save_progress(
                redis_client,  # type: ignore[arg-type]
                upload_id,
                status="completed",
                processed=10,
                total=10,
                dataset_name="ds",
            )

        flip_task = asyncio.create_task(_flip_to_completed())
        events: list[str] = []
        async for chunk in stream_upload_progress(
            upload_id,
            redis_client,
            poll_interval=0.05,
            timeout_sec=5.0,  # type: ignore[arg-type]
        ):
            events.append(chunk)
            if "event: done" in chunk or "event: error" in chunk:
                break
        await flip_task

        text = "".join(events)
        assert "retry: 3000" in text
        assert "event: progress" in text
        assert "event: done" in text
        # id 라인은 단조 증가
        ids = [
            int(line.split(":", 1)[1].strip())
            for line in text.splitlines()
            if line.startswith("id:")
        ]
        assert ids == sorted(ids)

    async def test_stream_returns_error_when_upload_missing(
        self, redis_client: MockRedisClient
    ) -> None:
        """존재하지 않는 upload_id → error 이벤트."""
        events: list[str] = []
        async for chunk in stream_upload_progress(
            "missing_id",
            redis_client,
            poll_interval=0.05,
            timeout_sec=2.0,  # type: ignore[arg-type]
        ):
            events.append(chunk)
            if "event: error" in chunk:
                break
        text = "".join(events)
        assert "UPLOAD_NOT_FOUND" in text


# ---------- 7) 라우터 통합 테스트 (TestClient) ----------


@pytest.mark.unit
class TestDatasetRouter:
    """라우터 + 의존성 주입 통합."""

    def test_list_empty(self, client: TestClient) -> None:
        """초기 상태 — 빈 목록."""
        resp = client.get("/api/v1/datasets")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_list_after_seed(
        self,
        client: TestClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """데이터셋 시드 후 조회."""
        langfuse_client.create_dataset(name="ds1", description="첫번째")
        langfuse_client.create_dataset(name="ds2")
        resp = client.get("/api/v1/datasets?page=1&page_size=10")
        body = resp.json()
        assert body["total"] == 2
        names = {it["name"] for it in body["items"]}
        assert names == {"ds1", "ds2"}

    def test_dataset_items(
        self,
        client: TestClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """아이템 페이지네이션."""
        langfuse_client.create_dataset(name="ds_items")
        for i in range(5):
            langfuse_client.create_dataset_item(
                dataset_name="ds_items",
                input={"x": i},
                expected_output=f"y{i}",
            )
        resp = client.get("/api/v1/datasets/ds_items/items?page=1&page_size=3")
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 3

    def test_dataset_items_not_found(self, client: TestClient) -> None:
        """존재하지 않는 데이터셋 — 404."""
        resp = client.get("/api/v1/datasets/missing/items")
        assert resp.status_code == 404

    def test_upload_preview(self, client: TestClient) -> None:
        """업로드 미리보기 — 첫 5건 + 컬럼."""
        content = "text,label\n" + "\n".join(f"row_{i},lbl_{i}" for i in range(10))
        files = {"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")}
        data = {"mapping": json.dumps({"input_columns": ["text"], "output_column": "label"})}
        resp = client.post("/api/v1/datasets/upload/preview", files=files, data=data)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_rows"] == 10
        assert len(body["preview"]) == 5

    def test_upload_returns_202_and_processes(
        self,
        client: TestClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """업로드 → 202 + upload_id 반환 후 BackgroundTasks 처리."""
        content = b"text,label\nhi,p\nbye,n\n"
        files = {"file": ("data.csv", io.BytesIO(content), "text/csv")}
        data = {
            "dataset_name": "uploaded_ds",
            "mapping": json.dumps({"input_columns": ["text"], "output_column": "label"}),
        }
        resp = client.post("/api/v1/datasets/upload", files=files, data=data)
        assert resp.status_code == 202
        body = resp.json()
        assert body["upload_id"].startswith("ds_upload_")
        assert body["status"] == "pending"
        # BackgroundTasks가 TestClient context 종료 시 실행됨 → 완료 후 데이터셋 존재
        assert "uploaded_ds" in langfuse_client._datasets

    def test_upload_idempotency(
        self,
        client: TestClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """동일 Idempotency-Key는 동일 upload_id 반환."""
        content = b"text,label\nhi,p\n"
        files = {"file": ("data.csv", io.BytesIO(content), "text/csv")}
        data = {
            "dataset_name": "idem_ds",
            "mapping": json.dumps({"input_columns": ["text"], "output_column": "label"}),
        }
        headers = {"Idempotency-Key": "test-key-1"}

        r1 = client.post("/api/v1/datasets/upload", files=files, data=data, headers=headers)
        files2 = {"file": ("data.csv", io.BytesIO(content), "text/csv")}
        r2 = client.post("/api/v1/datasets/upload", files=files2, data=data, headers=headers)
        assert r1.json()["upload_id"] == r2.json()["upload_id"]


# ---------- 8) RBAC ----------


@pytest.mark.unit
class TestRBAC:
    """RBAC: admin-only DELETE / user+ upload / viewer 차단."""

    def test_viewer_can_list(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """viewer는 목록 조회 가능."""
        app = create_app()
        app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        app.dependency_overrides[get_current_user] = lambda: _make_user("viewer")
        c = TestClient(app)
        resp = c.get("/api/v1/datasets")
        assert resp.status_code == 200

    def test_viewer_cannot_upload(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """viewer는 업로드 차단 (403)."""
        app = create_app()
        app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        app.dependency_overrides[get_current_user] = lambda: _make_user("viewer")
        c = TestClient(app)
        files = {"file": ("d.csv", io.BytesIO(b"x,y\n1,2"), "text/csv")}
        data = {
            "dataset_name": "x",
            "mapping": json.dumps({"input_columns": ["x"], "output_column": "y"}),
        }
        resp = c.post("/api/v1/datasets/upload", files=files, data=data)
        assert resp.status_code == 403

    def test_user_cannot_delete(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """user는 DELETE 차단 (403)."""
        langfuse_client.create_dataset(name="to_delete")
        app = create_app()
        app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        app.dependency_overrides[get_current_user] = lambda: _make_user("user")
        c = TestClient(app)
        resp = c.delete("/api/v1/datasets/to_delete")
        assert resp.status_code == 403
        assert "to_delete" in langfuse_client._datasets  # 보존

    def test_admin_can_delete(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """admin은 DELETE 가능."""
        langfuse_client.create_dataset(name="del_ok")
        app = create_app()
        app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        app.dependency_overrides[get_current_user] = lambda: _make_user("admin")
        c = TestClient(app)
        resp = c.delete("/api/v1/datasets/del_ok")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dataset_name"] == "del_ok"
        assert body["deleted"] is True
        assert "del_ok" not in langfuse_client._datasets

    def test_admin_delete_missing_returns_404(
        self,
        langfuse_client: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        """admin이 없는 데이터셋 삭제 → 404."""
        app = create_app()
        app.dependency_overrides[get_langfuse_client] = lambda: langfuse_client
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        app.dependency_overrides[get_current_user] = lambda: _make_user("admin")
        c = TestClient(app)
        resp = c.delete("/api/v1/datasets/nonexistent")
        assert resp.status_code == 404


# ---------- 9) Idempotency / from-items ----------


@pytest.mark.unit
class TestFromItems:
    """파생 데이터셋 생성."""

    def test_from_items_basic(
        self,
        client: TestClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """source dataset의 아이템을 새 데이터셋에 복사."""
        # source 데이터셋 시드
        langfuse_client.create_dataset(name="source_exp_1")
        item1 = langfuse_client.create_dataset_item(
            dataset_name="source_exp_1",
            input={"q": "1"},
            expected_output="a",
        )
        item2 = langfuse_client.create_dataset_item(
            dataset_name="source_exp_1",
            input={"q": "2"},
            expected_output="b",
        )

        payload = {
            "project_id": "proj_1",
            "source_experiment_id": "source_exp_1",
            "item_ids": [item1.id, item2.id],
            "new_dataset_name": "derived_ds",
            "description": "test 파생",
        }
        resp = client.post("/api/v1/datasets/from-items", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["dataset_name"] == "derived_ds"
        assert body["items_created"] == 2
        assert body["status"] == "completed"
        # 파생 데이터셋이 생성되고 아이템 2건이 들어감
        assert len(langfuse_client._datasets["derived_ds"].items) == 2

    def test_from_items_partial(
        self,
        client: TestClient,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """일부 item_id가 존재하지 않으면 partial."""
        langfuse_client.create_dataset(name="source_p")
        good = langfuse_client.create_dataset_item(
            dataset_name="source_p",
            input={"q": "1"},
            expected_output="a",
        )
        payload = {
            "project_id": "proj_1",
            "source_experiment_id": "source_p",
            "item_ids": [good.id, "missing-id-xyz"],
            "new_dataset_name": "partial_ds",
        }
        resp = client.post("/api/v1/datasets/from-items", json=payload)
        body = resp.json()
        assert body["items_created"] == 1
        assert body["status"] == "partial"

    def test_from_items_validation_empty(self, client: TestClient) -> None:
        """item_ids 비어 있으면 422."""
        payload = {
            "project_id": "p",
            "source_experiment_id": "s",
            "item_ids": [],
            "new_dataset_name": "x",
        }
        resp = client.post("/api/v1/datasets/from-items", json=payload)
        assert resp.status_code == 422
