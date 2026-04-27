"""``app.evaluators.custom_code.CustomCodeEvaluator`` 단위 테스트.

검증 범위:
- 보안 옵션이 docker run 명령에 포함되는지 (--network=none, --memory=128m 등)
- 정상 케이스: stdout JSON ``{"score": 0.85}`` → 0.85 반환
- 점수 클램핑 (음수 / 1 초과 → 0/1)
- runner.py 에러 응답 → ``None``
- timeout → ``None`` (EvaluatorTimeoutError 캐치)
- JSON 파싱 실패 → ``None``
- ``__aenter__/__aexit__`` lifecycle (컨테이너 spawn → kill)
- ``validate_code`` 모드: 여러 test_case 일괄 처리
- ``code`` 빈 문자열 → ValueError, ``timeout_sec<=0`` → ValueError

전략: ``asyncio.create_subprocess_exec``를 가짜 ``FakeProcess``로 대체하여
실제 docker 호출 없이 stdin/stdout 흐름을 시뮬레이션한다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.evaluators.custom_code import (
    CustomCodeEvaluator,
    SandboxStartupError,
    _build_exec_command,
    _build_run_command,
    validate_code,
)

# ────────────────────── Fake subprocess 인프라 ──────────────────────


class FakeProcess:
    """``asyncio.subprocess.Process`` 호환 fake.

    ``communicate(input=...)`` 호출 시:
        - ``simulate_timeout=True``인 경우 영원히 await — wait_for로 timeout 발생 유도
        - 그 외에는 ``stdout_bytes`` / ``stderr_bytes``를 즉시 반환
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        simulate_timeout: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode: int | None = returncode
        self._simulate_timeout = simulate_timeout
        self.killed = False
        self.stdin_received: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        self.stdin_received = input
        if self._simulate_timeout:
            # asyncio.wait_for가 timeout을 트리거하도록 무한 대기
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        # kill 후 returncode 설정 (실제 Process와 동일)
        if self.returncode == 0 and self._simulate_timeout:
            self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class SubprocessRecorder:
    """``asyncio.create_subprocess_exec`` 호출을 가로채는 mock.

    각 호출마다 등록된 ``FakeProcess``를 순서대로 반환하며, 호출 인자도 ``calls``에 기록.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._queue: list[FakeProcess] = []

    def queue(self, *processes: FakeProcess) -> None:
        self._queue.extend(processes)

    async def __call__(self, *args: str, **kwargs: Any) -> FakeProcess:
        self.calls.append(list(args))
        if not self._queue:
            # 기본: 빈 stdout, returncode 0
            return FakeProcess()
        return self._queue.pop(0)


@pytest.fixture
def subproc(monkeypatch: pytest.MonkeyPatch) -> SubprocessRecorder:
    """``asyncio.create_subprocess_exec``를 가로채는 fixture."""
    recorder = SubprocessRecorder()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    return recorder


# ────────────────────── 보안 옵션 검증 ──────────────────────
@pytest.mark.unit
class TestSecurityOptions:
    """``docker run`` 명령에 보안 옵션이 모두 포함되는지 검증."""

    def test_run_명령에_네트워크_차단_플래그(self) -> None:
        cmd = _build_run_command(
            "img:1.0",
            memory_limit="128m",
            cpu_limit="0.5",
            pids_limit=50,
            tmpfs_size="10m",
        )
        assert "--network=none" in cmd

    def test_run_명령에_메모리_및_swap_제한(self) -> None:
        cmd = _build_run_command(
            "img:1.0",
            memory_limit="128m",
            cpu_limit="0.5",
            pids_limit=50,
            tmpfs_size="10m",
        )
        assert "--memory=128m" in cmd
        assert "--memory-swap=128m" in cmd

    def test_run_명령에_cpu_제한(self) -> None:
        cmd = _build_run_command(
            "img:1.0",
            memory_limit="128m",
            cpu_limit="0.5",
            pids_limit=50,
            tmpfs_size="10m",
        )
        assert "--cpus=0.5" in cmd

    def test_run_명령에_권한_상승_방지(self) -> None:
        cmd = _build_run_command(
            "img:1.0",
            memory_limit="128m",
            cpu_limit="0.5",
            pids_limit=50,
            tmpfs_size="10m",
        )
        assert "--cap-drop=ALL" in cmd
        assert "--security-opt=no-new-privileges" in cmd
        assert "--user=labs" in cmd

    def test_run_명령에_읽기전용_및_tmpfs(self) -> None:
        cmd = _build_run_command(
            "img:1.0",
            memory_limit="128m",
            cpu_limit="0.5",
            pids_limit=50,
            tmpfs_size="10m",
        )
        assert "--read-only" in cmd
        # tmpfs 옵션에 noexec, nosuid, nodev 모두 포함
        tmpfs_arg = next(a for a in cmd if a.startswith("--tmpfs"))
        assert "noexec" in tmpfs_arg
        assert "nosuid" in tmpfs_arg
        assert "nodev" in tmpfs_arg
        assert "size=10m" in tmpfs_arg

    def test_run_명령에_pid_ipc_namespace_격리(self) -> None:
        cmd = _build_run_command(
            "img:1.0",
            memory_limit="128m",
            cpu_limit="0.5",
            pids_limit=50,
            tmpfs_size="10m",
        )
        assert "--pid=private" in cmd
        assert "--ipc=private" in cmd
        assert "--pids-limit=50" in cmd

    def test_exec_명령은_python_runner_호출(self) -> None:
        cmd = _build_exec_command("abc123")
        assert cmd == ["docker", "exec", "-i", "abc123", "python", "-u", "/app/runner.py"]


# ────────────────────── 생성자 검증 ──────────────────────
@pytest.mark.unit
class TestConstruction:
    """생성자 인자 validation."""

    def test_빈_code는_ValueError(self) -> None:
        with pytest.raises(ValueError):
            CustomCodeEvaluator(code="")

    def test_공백만_있는_code는_ValueError(self) -> None:
        with pytest.raises(ValueError):
            CustomCodeEvaluator(code="   \n  ")

    def test_timeout_0은_ValueError(self) -> None:
        with pytest.raises(ValueError):
            CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0", timeout_sec=0)

    def test_timeout_음수는_ValueError(self) -> None:
        with pytest.raises(ValueError):
            CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0", timeout_sec=-1)


# ────────────────────── Lifecycle ──────────────────────
@pytest.mark.unit
class TestLifecycle:
    """``__aenter__/__aexit__`` 컨테이너 spawn / cleanup."""

    async def test_aenter는_docker_run으로_컨테이너_spawn(
        self, subproc: SubprocessRecorder
    ) -> None:
        # spawn 응답: container ID
        subproc.queue(FakeProcess(stdout=b"container-abc-123\n"))
        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")

        async with evaluator as live:
            assert live.container_id == "container-abc-123"

        # spawn(run) + kill + rm = 3번 호출
        assert len(subproc.calls) >= 1
        assert subproc.calls[0][:2] == ["docker", "run"]

    async def test_aexit는_kill과_rm을_호출한다(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid-xyz\n"))
        # kill, rm 응답
        subproc.queue(FakeProcess(stdout=b""), FakeProcess(stdout=b""))
        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 0.5")

        async with evaluator:
            pass

        commands = [call[:2] for call in subproc.calls]
        assert ["docker", "run"] in commands
        assert ["docker", "kill"] in commands
        assert ["docker", "rm"] in commands

    async def test_run_실패시_SandboxStartupError(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(
            FakeProcess(stdout=b"", stderr=b"image not found", returncode=125)
        )
        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")

        with pytest.raises(SandboxStartupError):
            await evaluator.__aenter__()

    async def test_run_빈_stdout이면_SandboxStartupError(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"\n", returncode=0))
        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")

        with pytest.raises(SandboxStartupError):
            await evaluator.__aenter__()


# ────────────────────── 정상 평가 흐름 ──────────────────────
@pytest.mark.unit
class TestEvaluateSuccess:
    """정상 케이스 — runner.py가 ``{score}``를 반환."""

    async def test_정상_score_0_85_반환(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid-1\n"))  # spawn
        # exec 응답
        subproc.queue(
            FakeProcess(
                stdout=json.dumps(
                    {"id": "validate-0", "status": "success", "score": 0.85}
                ).encode()
                + b"\n",
            )
        )
        # cleanup
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 0.85")
        async with evaluator as live:
            score = await live.evaluate("foo", "bar", {}, item_id="validate-0")

        assert score == pytest.approx(0.85)

    async def test_score_1_초과는_1_0으로_클램핑(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(
            FakeProcess(
                stdout=json.dumps({"id": "x", "status": "success", "score": 5.0}).encode()
            )
        )
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 5.0")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score == pytest.approx(1.0)

    async def test_score_음수는_0_으로_클램핑(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(
            FakeProcess(
                stdout=json.dumps({"id": "x", "status": "success", "score": -0.3}).encode()
            )
        )
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return -0.3")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score == pytest.approx(0.0)

    async def test_evaluate는_컨테이너_없이_호출시_None(self) -> None:
        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")
        # __aenter__ 호출 X → container_id is None
        score = await evaluator.evaluate("o", None, {})
        assert score is None

    async def test_payload에_올바른_필드들이_stdin으로_전달된다(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        exec_proc = FakeProcess(
            stdout=json.dumps({"id": "item-9", "status": "success", "score": 1.0}).encode()
        )
        subproc.queue(exec_proc)
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")
        async with evaluator as live:
            await live.evaluate(
                "model output",
                "expected text",
                {"difficulty": "hard"},
                item_id="item-9",
            )

        assert exec_proc.stdin_received is not None
        sent = json.loads(exec_proc.stdin_received.decode().strip())
        assert sent["id"] == "item-9"
        assert sent["output"] == "model output"
        assert sent["expected"] == "expected text"
        assert sent["metadata"] == {"difficulty": "hard"}
        assert "def evaluate" in sent["code"]


# ────────────────────── 에러 / 타임아웃 처리 ──────────────────────
@pytest.mark.unit
class TestEvaluateErrors:
    """runner 에러 / timeout / JSON 파싱 실패."""

    async def test_runner_에러_응답시_None(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(
            FakeProcess(
                stdout=json.dumps(
                    {
                        "id": "x",
                        "status": "error",
                        "error_code": "EVALUATOR_ERROR",
                        "error_message": "ZeroDivisionError",
                    }
                ).encode()
            )
        )
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1/0")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score is None

    async def test_runner_timeout_응답시_None(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(
            FakeProcess(
                stdout=json.dumps(
                    {
                        "id": "x",
                        "status": "error",
                        "error_code": "EVALUATOR_TIMEOUT",
                        "error_message": "exceeded 5s",
                    }
                ).encode()
            )
        )
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score is None

    async def test_wall_clock_timeout시_None_반환(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        # exec proc는 무한 대기 → wait_for(timeout=0.1)이 발동
        subproc.queue(FakeProcess(simulate_timeout=True))
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(
            code="def evaluate(o,e,m): return 1.0",
            timeout_sec=0.1,
        )
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score is None

    async def test_JSON_파싱_실패시_None(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(FakeProcess(stdout=b"not valid json\n"))
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score is None

    async def test_빈_stdout이면_None(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(FakeProcess(stdout=b""))
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 1.0")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score is None

    async def test_score가_숫자가_아니면_None(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(
            FakeProcess(
                stdout=json.dumps(
                    {"id": "x", "status": "success", "score": "not-a-number"}
                ).encode()
            )
        )
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 'x'")
        async with evaluator as live:
            score = await live.evaluate("o", None, {})

        assert score is None


# ────────────────────── 직렬화 ──────────────────────
@pytest.mark.unit
class TestSerialization:
    """비-문자열 output / expected / metadata 직렬화."""

    async def test_dict_output은_JSON으로_직렬화(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        exec_proc = FakeProcess(
            stdout=json.dumps({"id": "x", "status": "success", "score": 0.5}).encode()
        )
        subproc.queue(exec_proc)
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 0.5")
        async with evaluator as live:
            await live.evaluate({"a": 1}, [1, 2, 3], {"k": "v"})

        sent = json.loads(exec_proc.stdin_received.decode().strip())  # type: ignore[union-attr]
        assert sent["output"] == json.dumps({"a": 1}, ensure_ascii=False, sort_keys=True)
        assert sent["expected"] == json.dumps(
            [1, 2, 3], ensure_ascii=False, sort_keys=True
        )

    async def test_None_expected는_빈문자열(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        exec_proc = FakeProcess(
            stdout=json.dumps({"id": "x", "status": "success", "score": 0.5}).encode()
        )
        subproc.queue(exec_proc)
        subproc.queue(FakeProcess(), FakeProcess())

        evaluator = CustomCodeEvaluator(code="def evaluate(o,e,m): return 0.5")
        async with evaluator as live:
            await live.evaluate("o", None, {})

        sent = json.loads(exec_proc.stdin_received.decode().strip())  # type: ignore[union-attr]
        assert sent["expected"] == ""


# ────────────────────── validate_code 모드 ──────────────────────
@pytest.mark.unit
class TestValidateCode:
    """``validate_code`` 함수 — 라우터에서 호출."""

    async def test_빈_test_cases는_빈_결과(self) -> None:
        # 컨테이너 spawn조차 발생하지 않아야 한다
        result = await validate_code(
            code="def evaluate(o,e,m): return 1.0",
            test_cases=[],
        )
        assert result == []

    async def test_여러_test_case_정상_평가(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))  # spawn
        # 3개 test_case → 3번의 exec
        for score in (0.1, 0.5, 1.0):
            subproc.queue(
                FakeProcess(
                    stdout=json.dumps(
                        {"id": "x", "status": "success", "score": score}
                    ).encode()
                )
            )
        subproc.queue(FakeProcess(), FakeProcess())  # cleanup

        result = await validate_code(
            code="def evaluate(o,e,m): return 1.0",
            test_cases=[
                {"output": "a", "expected": "A", "metadata": {}},
                {"output": "b", "expected": "B", "metadata": {}},
                {"output": "c", "expected": "C", "metadata": {}},
            ],
        )

        assert result == [
            {"result": pytest.approx(0.1)},
            {"result": pytest.approx(0.5)},
            {"result": pytest.approx(1.0)},
        ]

    async def test_test_case_에러는_error_필드로_변환(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        subproc.queue(
            FakeProcess(
                stdout=json.dumps(
                    {
                        "id": "x",
                        "status": "error",
                        "error_code": "EVALUATOR_ERROR",
                        "error_message": "boom",
                    }
                ).encode()
            )
        )
        subproc.queue(FakeProcess(), FakeProcess())

        result = await validate_code(
            code="def evaluate(o,e,m): raise Exception('boom')",
            test_cases=[{"output": "a", "expected": "A", "metadata": {}}],
        )

        assert len(result) == 1
        assert "error" in result[0]

    async def test_metadata가_dict가_아니면_error(
        self, subproc: SubprocessRecorder
    ) -> None:
        subproc.queue(FakeProcess(stdout=b"cid\n"))
        # 단 한 번도 exec가 호출되지 않아야 한다
        subproc.queue(FakeProcess(), FakeProcess())  # cleanup

        result = await validate_code(
            code="def evaluate(o,e,m): return 1.0",
            test_cases=[{"output": "a", "expected": "A", "metadata": "not a dict"}],
        )

        assert len(result) == 1
        assert "error" in result[0]
        assert "metadata" in result[0]["error"]

    async def test_컨테이너_spawn_실패시_모든_케이스_에러(
        self, subproc: SubprocessRecorder
    ) -> None:
        # spawn 실패
        subproc.queue(
            FakeProcess(stdout=b"", stderr=b"image missing", returncode=125)
        )

        result = await validate_code(
            code="def evaluate(o,e,m): return 1.0",
            test_cases=[
                {"output": "a", "metadata": {}},
                {"output": "b", "metadata": {}},
            ],
        )

        assert len(result) == 2
        assert all("error" in r for r in result)
