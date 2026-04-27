"""``app.services.single_test_runner.SingleTestRunner`` 단위 테스트.

검증 범위:
- 스트리밍 모드: started → token (N개) → done 순 이벤트 발행
- 비스트리밍 모드: 단일 dict 반환 (output/usage/cost/latency)
- Langfuse trace + generation 기록 (mock_langfuse._get_traces / _get_generations)
- 에러 처리 (LiteLLM 실패 → error 이벤트, trace에 error metadata)
- inline + langfuse 양쪽 prompt source
- 비용 추출 (_litellm_cost)
- 변수 치환 (LiteLLM에 전달된 messages 검증)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.core.errors import LiteLLMError
from app.services.context_engine import ContextEngine
from app.services.single_test_runner import (
    SingleTestRunner,
    _extract_chunk_text,
    _extract_cost,
    _extract_usage,
    _to_messages,
)
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy


# ---------- 공통 fixtures ----------
@pytest.fixture
def runner(
    langfuse_client: MockLangfuseClient,
    litellm_client: MockLiteLLMProxy,
) -> SingleTestRunner:
    """기본 SingleTestRunner — Mock 클라이언트 주입."""
    return SingleTestRunner(
        langfuse=langfuse_client,  # type: ignore[arg-type]
        litellm=litellm_client,  # type: ignore[arg-type]
        context_engine=ContextEngine(),
    )


# ---------- 1) 내부 유틸 ----------
@pytest.mark.unit
class TestInternalHelpers:
    """``_to_messages`` / ``_extract_*`` 헬퍼 단위 테스트."""

    def test_text_프롬프트_user_메시지(self) -> None:
        """text 프롬프트는 user 메시지 1개로 변환된다."""
        msgs = _to_messages("Hello")
        assert msgs == [{"role": "user", "content": "Hello"}]

    def test_text_프롬프트_system_prompt_prepend(self) -> None:
        """system_prompt가 있으면 system 메시지가 앞에 추가."""
        msgs = _to_messages("Hi", system_prompt="You are kind.")
        assert msgs[0] == {"role": "system", "content": "You are kind."}
        assert msgs[1] == {"role": "user", "content": "Hi"}

    def test_chat_프롬프트_그대로_복사(self) -> None:
        """chat 프롬프트는 리스트 사본을 반환."""
        prompt = [{"role": "user", "content": "X"}]
        msgs = _to_messages(prompt)
        assert msgs == prompt
        assert msgs is not prompt  # 사본

    def test_chat_프롬프트_system_prepend(self) -> None:
        """chat 프롬프트에도 system prefix 가능."""
        msgs = _to_messages(
            [{"role": "user", "content": "Q"}], system_prompt="SYS"
        )
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_extract_chunk_text_정상(self) -> None:
        """OpenAI 호환 chunk에서 content 추출."""
        chunk = {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}
        assert _extract_chunk_text(chunk) == "hi"

    def test_extract_chunk_text_빈_choices(self) -> None:
        """choices가 비면 빈 문자열."""
        assert _extract_chunk_text({"choices": []}) == ""
        assert _extract_chunk_text({}) == ""

    def test_extract_chunk_text_finish_chunk(self) -> None:
        """delta가 비어있는 finish chunk에서는 빈 문자열."""
        chunk = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        assert _extract_chunk_text(chunk) == ""

    def test_extract_usage_openai_alias(self) -> None:
        """``prompt_tokens``/``completion_tokens``를 표준화한다."""
        usage = _extract_usage(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        )
        assert usage == {
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
        }

    def test_extract_usage_없으면_None(self) -> None:
        """usage 필드가 없으면 None."""
        assert _extract_usage({"choices": []}) is None

    def test_extract_cost_litellm_cost(self) -> None:
        """``_litellm_cost`` 필드를 float로 반환."""
        assert _extract_cost({"_litellm_cost": "0.0125"}) == 0.0125

    def test_extract_cost_없으면_0(self) -> None:
        """필드 없으면 0.0."""
        assert _extract_cost({}) == 0.0


# ---------- 2) 스트리밍 ----------
@pytest.mark.unit
class TestRunStreaming:
    """``run_streaming`` 이벤트 시퀀스 + Langfuse 기록 검증."""

    async def test_inline_text_스트리밍_started_token_done(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """inline 프롬프트 스트리밍 — started → token... → done 시퀀스."""
        litellm_client.set_response("hello world from llm")
        events: list[dict[str, Any]] = []
        async for ev in runner.run_streaming(
            project_id="proj_x",
            prompt_source={
                "source": "inline",
                "body": "Hi {{name}}",
                "type": "text",
            },
            variables={"name": "World"},
            model="gpt-4o-mini",
            parameters={"temperature": 0.1},
        ):
            events.append(ev)

        assert events[0]["event"] == "started"
        assert "trace_id" in events[0]["data"]
        assert events[0]["data"]["model"] == "gpt-4o-mini"

        token_events = [ev for ev in events if ev["event"] == "token"]
        assert len(token_events) >= 1
        full_content = "".join(ev["data"]["content"] for ev in token_events)
        assert full_content == "hello world from llm"

        assert events[-1]["event"] == "done"
        done_data = events[-1]["data"]
        assert done_data["output"] == "hello world from llm"
        assert done_data["model"] == "gpt-4o-mini"
        assert done_data["latency_ms"] >= 0
        assert "usage" in done_data
        assert "cost_usd" in done_data
        assert done_data["trace_id"] == events[0]["data"]["trace_id"]

    async def test_langfuse_프롬프트_조회_및_변수_바인딩(
        self,
        runner: SingleTestRunner,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """source=langfuse일 때 SDK get_prompt + 변수 치환이 LiteLLM으로 전달."""
        langfuse_client._seed(
            prompts=[{"name": "p_test", "body": "Echo: {{text}}", "version": 1}]
        )
        litellm_client.set_response("ok")
        events: list[dict[str, Any]] = []
        async for ev in runner.run_streaming(
            project_id="proj_y",
            prompt_source={"source": "langfuse", "name": "p_test", "version": 1},
            variables={"text": "안녕하세요"},
            model="gpt-4o",
            parameters={},
        ):
            events.append(ev)

        # LiteLLM에 전달된 messages 검증
        calls = litellm_client._get_calls()
        assert len(calls) == 1
        msgs = calls[0]["messages"]
        # text 프롬프트는 user 메시지 1개
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Echo: 안녕하세요"
        assert calls[0]["stream"] is True
        assert calls[0]["model"] == "gpt-4o"

    async def test_langfuse_trace_생성_및_generation_기록(
        self,
        runner: SingleTestRunner,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """완료 시 Langfuse trace 1건 + generation 1건이 기록된다."""
        litellm_client.set_response("done")
        async for _ in runner.run_streaming(
            project_id="proj_z",
            prompt_source={
                "source": "inline",
                "body": "static prompt",
                "type": "text",
            },
            variables={},
            model="gpt-4o",
            parameters={"temperature": 0.0},
            user_id="user-42",
        ):
            pass

        traces = langfuse_client._get_traces()
        assert len(traces) == 1
        t = traces[0]
        assert t.name == "single_test"
        assert t.user_id == "user-42"
        assert t.metadata["project_id"] == "proj_z"
        assert "single_test" in t.tags
        assert "project:proj_z" in t.tags

        gens = langfuse_client._get_generations()
        assert len(gens) == 1
        g = gens[0]
        assert g.name == "single_test_generation"
        assert g.model == "gpt-4o"
        assert g.output == "done"
        assert g.metadata["parameters"] == {"temperature": 0.0}
        assert g.metadata["latency_ms"] >= 0

    async def test_비용_usage_chunk_에서_추출(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """chunk에 포함된 ``_litellm_cost``/``usage``를 done 이벤트에 포함한다."""
        # MockLiteLLMProxy._stream_chunks는 cost/usage를 chunk에 포함하지 않으므로
        # 본 테스트는 stream 결과를 가로채는 wrapper로 커스텀 chunk를 주입한다.
        from typing import Any as _Any

        async def custom_stream() -> AsyncIterator[dict[str, _Any]]:
            yield {
                "choices": [{"delta": {"content": "hi"}, "finish_reason": None}],
            }
            yield {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                "_litellm_cost": 0.0042,
            }

        async def fake_completion(
            *_args: _Any, **_kwargs: _Any
        ) -> AsyncIterator[dict[str, _Any]]:
            return custom_stream()

        # monkey-patch
        litellm_client.completion = fake_completion  # type: ignore[method-assign]

        events: list[dict[str, _Any]] = []
        async for ev in runner.run_streaming(
            project_id="p",
            prompt_source={"source": "inline", "body": "x", "type": "text"},
            variables={},
            model="gpt-4o",
            parameters={},
        ):
            events.append(ev)

        done = [ev for ev in events if ev["event"] == "done"][0]
        assert done["data"]["cost_usd"] == 0.0042
        assert done["data"]["usage"] == {
            "input_tokens": 5,
            "output_tokens": 7,
            "total_tokens": 12,
        }

    async def test_LiteLLM_실패시_error_이벤트(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """LiteLLM 호출 실패 시 ``error`` 이벤트가 발행되고 trace는 ERROR로 마감."""
        litellm_client.set_failure(LiteLLMError(detail="rate limit"))
        events: list[dict[str, Any]] = []
        async for ev in runner.run_streaming(
            project_id="proj_e",
            prompt_source={"source": "inline", "body": "x", "type": "text"},
            variables={},
            model="gpt-4o",
            parameters={},
        ):
            events.append(ev)

        # started → error
        kinds = [ev["event"] for ev in events]
        assert "error" in kinds
        err = [ev for ev in events if ev["event"] == "error"][0]
        assert err["data"]["code"] == "LLM_ERROR"
        assert "rate limit" in err["data"]["message"]
        # trace_id 첨부
        assert "trace_id" in err["data"]

        # trace에 error generation이 기록되어 있어야 함
        gens = langfuse_client._get_generations()
        error_gens = [g for g in gens if g.metadata.get("level") == "ERROR"]
        assert len(error_gens) == 1

    async def test_inline_body_누락시_validation_error(
        self,
        runner: SingleTestRunner,
    ) -> None:
        """inline body 누락 시 ``error`` 이벤트(VALIDATION_ERROR)."""
        events: list[dict[str, Any]] = []
        async for ev in runner.run_streaming(
            project_id="p",
            prompt_source={"source": "inline"},  # body 누락
            variables={},
            model="gpt-4o",
            parameters={},
        ):
            events.append(ev)

        kinds = [ev["event"] for ev in events]
        assert "error" in kinds
        err = [ev for ev in events if ev["event"] == "error"][0]
        assert err["data"]["code"] == "VALIDATION_ERROR"


# ---------- 3) 비스트리밍 ----------
@pytest.mark.unit
class TestRunNonStreaming:
    """``run_non_streaming`` 단위 테스트."""

    async def test_정상_dict_반환(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """정상 호출 시 결과 dict 반환."""
        litellm_client.set_response("non-stream output")
        result = await runner.run_non_streaming(
            project_id="p",
            prompt_source={"source": "inline", "body": "Hi {{n}}", "type": "text"},
            variables={"n": "A"},
            model="gpt-4o",
            parameters={"max_tokens": 100},
        )
        assert result["output"] == "non-stream output"
        assert result["model"] == "gpt-4o"
        assert result["usage"]["total_tokens"] >= 1
        assert result["cost_usd"] == 0.0023  # mock 기본 cost
        assert result["latency_ms"] >= 0
        assert result["trace_id"]

    async def test_LiteLLM_실패시_LiteLLMError_propagation(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """비스트리밍에서 LiteLLM 실패는 그대로 raise."""
        litellm_client.set_failure(LiteLLMError(detail="boom"))
        with pytest.raises(LiteLLMError, match="boom"):
            await runner.run_non_streaming(
                project_id="p",
                prompt_source={"source": "inline", "body": "x", "type": "text"},
                variables={},
                model="gpt-4o",
                parameters={},
            )

    async def test_langfuse_trace_및_generation_기록(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """비스트리밍에서도 trace + generation이 기록된다."""
        litellm_client.set_response("ok")
        await runner.run_non_streaming(
            project_id="p",
            prompt_source={"source": "inline", "body": "x", "type": "text"},
            variables={},
            model="gpt-4o",
            parameters={},
            user_id="u1",
        )
        traces = langfuse_client._get_traces()
        assert len(traces) == 1
        assert traces[0].metadata.get("stream") is False
        assert traces[0].user_id == "u1"
        gens = langfuse_client._get_generations()
        assert len(gens) == 1
        assert gens[0].output == "ok"


# ---------- 4) Chat 프롬프트 ----------
@pytest.mark.unit
class TestChatPrompt:
    """chat 형식 프롬프트 처리."""

    async def test_inline_chat_프롬프트_messages_그대로_전달(
        self,
        runner: SingleTestRunner,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        """inline chat 프롬프트의 messages가 LiteLLM에 그대로 전달된다."""
        litellm_client.set_response("ok")
        chat_body: list[dict[str, Any]] = [
            {"role": "system", "content": "You are {{persona}}."},
            {"role": "user", "content": "Echo {{x}}"},
        ]
        async for _ in runner.run_streaming(
            project_id="p",
            prompt_source={"source": "inline", "body": chat_body, "type": "chat"},
            variables={"persona": "친절한 AI", "x": "Hi"},
            model="gpt-4o",
            parameters={},
        ):
            pass

        calls = litellm_client._get_calls()
        msgs = calls[0]["messages"]
        assert msgs[0] == {"role": "system", "content": "You are 친절한 AI."}
        assert msgs[1] == {"role": "user", "content": "Echo Hi"}
