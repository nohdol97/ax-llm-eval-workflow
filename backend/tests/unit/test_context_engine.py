"""``app.services.context_engine.ContextEngine`` 단위 테스트.

검증 범위:
- 변수 파싱 (text + chat 형식, dedup, 발견 순서)
- compile (단순 치환, 누락 변수 fallback, strict 모드, 다양한 타입)
- bind_dataset_item (자동 매핑, 명시적 매핑, 부분 매핑)
- validate (필수 변수 누락 검출)
- 엣지 케이스 (빈 프롬프트, 변수 없음, 특수문자, 중첩 chat 세그먼트, dict/list 값)
"""

from __future__ import annotations

import json

import pytest

from app.services.context_engine import VARIABLE_PATTERN, ContextEngine


@pytest.fixture
def engine() -> ContextEngine:
    """fresh ContextEngine 인스턴스 반환."""
    return ContextEngine()


@pytest.mark.unit
class TestParseVariables:
    """``parse_variables`` — 변수 추출 테스트."""

    def test_text_단일변수_추출(self, engine: ContextEngine) -> None:
        """단일 ``{{var}}`` 패턴이 추출된다."""
        result = engine.parse_variables("안녕 {{name}}")
        assert result == ["name"]

    def test_text_다중변수_발견순서_유지(self, engine: ContextEngine) -> None:
        """여러 변수가 발견 순서대로 반환된다."""
        result = engine.parse_variables("{{c}} {{a}} {{b}}")
        assert result == ["c", "a", "b"]

    def test_text_중복변수_dedup(self, engine: ContextEngine) -> None:
        """동일 변수 반복은 한 번만 등장한다 (최초 위치 유지)."""
        result = engine.parse_variables("{{x}} and {{y}} and {{x}} again {{z}} {{y}}")
        assert result == ["x", "y", "z"]

    def test_text_공백포함_변수명(self, engine: ContextEngine) -> None:
        """``{{ var }}``의 양쪽 공백이 무시된다."""
        result = engine.parse_variables("Hello {{ name }} and {{  age  }}")
        assert result == ["name", "age"]

    def test_chat_messages_복수메시지_변수(self, engine: ContextEngine) -> None:
        """chat 형식의 모든 메시지에서 변수를 추출한다."""
        prompt = [
            {"role": "system", "content": "You are {{persona}}."},
            {"role": "user", "content": "Translate {{text}} to {{lang}}."},
        ]
        result = engine.parse_variables(prompt)
        assert result == ["persona", "text", "lang"]

    def test_chat_segment_list_content(self, engine: ContextEngine) -> None:
        """chat content가 segment 리스트인 경우에도 변수 추출."""
        prompt = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello {{name}}"},
                    {"type": "text", "text": "Have a {{mood}} day"},
                ],
            }
        ]
        result = engine.parse_variables(prompt)
        assert result == ["name", "mood"]

    def test_빈_프롬프트(self, engine: ContextEngine) -> None:
        """빈 문자열은 빈 리스트."""
        assert engine.parse_variables("") == []
        assert engine.parse_variables([]) == []

    def test_변수가_없는_프롬프트(self, engine: ContextEngine) -> None:
        """변수가 없으면 빈 리스트."""
        assert engine.parse_variables("Hello world!") == []

    def test_불완전_브래킷은_변수_아님(self, engine: ContextEngine) -> None:
        """``{{var}``, ``{var}}`` 등 불완전 패턴은 변수로 취급되지 않는다."""
        assert engine.parse_variables("{{var} {var}} {var}") == []

    def test_숫자로_시작하는_이름은_변수_아님(self, engine: ContextEngine) -> None:
        """변수명은 알파벳 또는 ``_``로 시작한다."""
        assert engine.parse_variables("{{1var}} {{_ok}}") == ["_ok"]

    def test_VARIABLE_PATTERN_은_export(self) -> None:
        """``VARIABLE_PATTERN``이 모듈 레벨에서 import 가능."""
        assert VARIABLE_PATTERN.match("{{abc}}") is not None


@pytest.mark.unit
class TestCompile:
    """``compile`` — 변수 치환 테스트."""

    def test_단순_치환(self, engine: ContextEngine) -> None:
        """``{{name}}``이 실제 값으로 치환된다."""
        assert engine.compile("Hi {{name}}!", {"name": "Sam"}) == "Hi Sam!"

    def test_다중_변수_치환(self, engine: ContextEngine) -> None:
        """여러 변수가 모두 치환된다."""
        result = engine.compile(
            "{{greet}} {{name}}, age={{age}}",
            {"greet": "Hello", "name": "Sam", "age": 30},
        )
        assert result == "Hello Sam, age=30"

    def test_누락_변수_빈문자열_fallback(self, engine: ContextEngine) -> None:
        """누락 변수는 기본 모드에서 빈 문자열로 치환된다."""
        result = engine.compile("Hi {{a}} and {{b}}", {"a": "X"})
        assert result == "Hi X and "

    def test_누락_변수_strict_모드_ValueError(self, engine: ContextEngine) -> None:
        """strict=True에서 누락 변수가 있으면 ``ValueError``."""
        with pytest.raises(ValueError, match="누락"):
            engine.compile("Hi {{a}}", {}, strict=True)

    def test_dict_값은_json_dumps(self, engine: ContextEngine) -> None:
        """dict 값은 JSON 문자열로 직렬화된다."""
        result = engine.compile("payload={{p}}", {"p": {"x": 1, "y": "t"}})
        # 키 순서는 dict 입력 순서를 유지
        assert "payload=" in result
        assert json.loads(result.replace("payload=", "")) == {"x": 1, "y": "t"}

    def test_list_값은_json_dumps(self, engine: ContextEngine) -> None:
        """list 값도 JSON 직렬화."""
        result = engine.compile("items={{xs}}", {"xs": [1, 2, 3]})
        assert result == "items=[1, 2, 3]"

    def test_int_float_bool_None_변환(self, engine: ContextEngine) -> None:
        """기본 타입은 문자열로 강제 변환."""
        assert engine.compile("{{x}}", {"x": 42}) == "42"
        assert engine.compile("{{x}}", {"x": 3.14}) == "3.14"
        assert engine.compile("{{x}}", {"x": True}) == "true"
        assert engine.compile("{{x}}", {"x": False}) == "false"
        assert engine.compile("{{x}}", {"x": None}) == ""

    def test_특수문자_포함_값(self, engine: ContextEngine) -> None:
        """치환 값에 특수문자/유니코드가 포함되어도 안전."""
        v = '한글 + emoji 🚀 + \\n + "quotes"'
        assert engine.compile("v={{x}}", {"x": v}) == f"v={v}"

    def test_치환_값_안에_변수_패턴_있어도_재치환_안됨(self, engine: ContextEngine) -> None:
        """치환 값에 ``{{other}}``가 있어도 한 번만 치환된다 (재귀 미적용)."""
        result = engine.compile("Hi {{a}}", {"a": "{{b}}"})
        assert result == "Hi {{b}}"

    def test_chat_messages_치환_원본보존(self, engine: ContextEngine) -> None:
        """chat 형식 치환 시 원본 메시지가 변경되지 않는다."""
        original = [
            {"role": "system", "content": "You are {{persona}}."},
            {"role": "user", "content": "Echo {{text}}"},
        ]
        original_snapshot = json.loads(json.dumps(original))
        result = engine.compile(original, {"persona": "친절한 AI", "text": "안녕"})
        assert isinstance(result, list)
        assert result[0]["content"] == "You are 친절한 AI."
        assert result[1]["content"] == "Echo 안녕"
        # 원본은 그대로
        assert original == original_snapshot

    def test_chat_segment_리스트_content_치환(self, engine: ContextEngine) -> None:
        """chat segment(list)도 치환된다."""
        prompt = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello {{name}}"},
                ],
            }
        ]
        result = engine.compile(prompt, {"name": "World"})
        assert isinstance(result, list)
        assert result[0]["content"][0]["text"] == "Hello World"

    def test_빈_프롬프트_치환(self, engine: ContextEngine) -> None:
        """빈 프롬프트는 빈 결과."""
        assert engine.compile("", {"x": "v"}) == ""

    def test_변수_없는_프롬프트_그대로(self, engine: ContextEngine) -> None:
        """변수 없는 프롬프트는 변경되지 않는다."""
        assert engine.compile("Hello world", {"x": "v"}) == "Hello world"


@pytest.mark.unit
class TestBindDatasetItem:
    """``bind_dataset_item`` — 데이터셋 아이템 바인딩."""

    def test_자동_매핑_변수명_동일(self, engine: ContextEngine) -> None:
        """변수명과 item_input 키가 같으면 자동 매핑된다."""
        result = engine.bind_dataset_item(
            "Sentiment of: {{input_text}}",
            {"input_text": "이 제품 너무 좋다", "ignored": "x"},
        )
        assert result == "Sentiment of: 이 제품 너무 좋다"

    def test_명시적_매핑_변수명_다름(self, engine: ContextEngine) -> None:
        """``variable_mapping``으로 다른 키를 매핑할 수 있다."""
        result = engine.bind_dataset_item(
            "Sentiment of: {{text}}",
            {"raw_text": "행복하다"},
            variable_mapping={"text": "raw_text"},
        )
        assert result == "Sentiment of: 행복하다"

    def test_명시적_매핑_우선(self, engine: ContextEngine) -> None:
        """명시적 매핑이 자동 매핑보다 우선한다."""
        # 변수명 'x' 가 item_input에도 있고, 명시 매핑도 있는 경우
        result = engine.bind_dataset_item(
            "{{x}}",
            {"x": "auto_value", "y": "explicit_value"},
            variable_mapping={"x": "y"},
        )
        assert result == "explicit_value"

    def test_매핑되지_않은_변수는_빈문자열(self, engine: ContextEngine) -> None:
        """매핑되지 않은 변수는 빈 문자열로 fallback."""
        result = engine.bind_dataset_item(
            "{{a}} - {{b}}",
            {"a": "X"},
        )
        assert result == "X - "

    def test_chat_messages_바인딩(self, engine: ContextEngine) -> None:
        """chat 메시지에서도 바인딩이 동작."""
        prompt = [{"role": "user", "content": "Q: {{q}}"}]
        result = engine.bind_dataset_item(prompt, {"q": "What is 1+1?"})
        assert isinstance(result, list)
        assert result[0]["content"] == "Q: What is 1+1?"

    def test_item_input_dict_아니면_TypeError(self, engine: ContextEngine) -> None:
        """item_input이 dict가 아니면 ``TypeError``."""
        with pytest.raises(TypeError, match="item_input"):
            engine.bind_dataset_item("{{x}}", "not a dict")  # type: ignore[arg-type]

    def test_strict_모드_누락시_ValueError(self, engine: ContextEngine) -> None:
        """strict=True에서 매핑 누락 시 ``ValueError``."""
        with pytest.raises(ValueError, match="누락"):
            engine.bind_dataset_item("{{a}} {{b}}", {"a": "x"}, strict=True)


@pytest.mark.unit
class TestValidate:
    """``validate`` — 필수 변수 검증."""

    def test_모두_존재시_빈리스트(self, engine: ContextEngine) -> None:
        """모든 필수 변수가 존재하면 빈 리스트."""
        assert engine.validate("{{a}} {{b}} {{c}}", ["a", "b"]) == []

    def test_누락된_변수_반환(self, engine: ContextEngine) -> None:
        """누락된 변수만 반환된다 (입력 순서 유지)."""
        assert engine.validate("{{a}}", ["a", "b", "c"]) == ["b", "c"]

    def test_chat_프롬프트_validate(self, engine: ContextEngine) -> None:
        """chat 형식도 동일하게 동작."""
        prompt = [{"role": "user", "content": "Hi {{name}}"}]
        assert engine.validate(prompt, ["name"]) == []
        assert engine.validate(prompt, ["name", "age"]) == ["age"]

    def test_빈_required_iterable(self, engine: ContextEngine) -> None:
        """required가 비어있으면 빈 리스트."""
        assert engine.validate("{{x}}", []) == []


@pytest.mark.unit
class TestCompileWithSdk:
    """``compile_with_sdk`` — Langfuse SDK prompt 객체 위임 테스트."""

    def test_sdk_compile_메서드_사용(self, engine: ContextEngine) -> None:
        """SDK가 ``compile`` 메서드를 제공하면 위임한다."""

        class FakePrompt:
            def __init__(self) -> None:
                self.body = "Hi {{name}}"

            def compile(self, **kwargs: object) -> str:
                # 의도적으로 다른 결과를 반환하여 위임 여부 확인
                return f"SDK_COMPILED:{kwargs.get('name')}"

        result = engine.compile_with_sdk(FakePrompt(), {"name": "X"})
        assert result == "SDK_COMPILED:X"

    def test_sdk_compile_없으면_engine_fallback(self, engine: ContextEngine) -> None:
        """SDK에 ``compile``이 없으면 엔진 자체 로직으로 fallback."""

        class NoCompilePrompt:
            def __init__(self) -> None:
                self.prompt = "Hi {{name}}"

        result = engine.compile_with_sdk(NoCompilePrompt(), {"name": "Y"})
        assert result == "Hi Y"

    def test_sdk_compile_TypeError시_dict_재시도(self, engine: ContextEngine) -> None:
        """SDK compile이 kwargs 거부 시 dict 단일 인자로 재시도."""

        class DictOnlyCompile:
            def __init__(self) -> None:
                self.body = "Hi {{name}}"

            def compile(self, *args: object, **kwargs: object) -> str:
                if kwargs:
                    raise TypeError("kwargs not supported")
                return f"DICT:{args[0]}"  # type: ignore[index]

        result = engine.compile_with_sdk(DictOnlyCompile(), {"name": "Z"})
        assert result.startswith("DICT:")
