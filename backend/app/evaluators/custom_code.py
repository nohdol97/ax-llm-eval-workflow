"""Custom Code Evaluator — Docker 샌드박스에서 사용자 작성 Python 코드를 격리 실행.

EVALUATION.md §4 / IMPLEMENTATION.md §2 기준. 샌드박스 이미지(``ax-eval-sandbox``)는
``docker/eval-sandbox/runner.py``에 정의된 line-delimited JSON 프로토콜로 통신한다.

라이프사이클:
    - 실험 시작: :meth:`CustomCodeEvaluator.__aenter__` → idle 컨테이너 1개 spawn
    - 아이템 평가: :meth:`evaluate` 호출마다 동일 컨테이너에 ``docker exec``로 runner 실행
    - 실험 종료/취소: :meth:`__aexit__` → ``docker kill`` + ``docker rm``

보안 옵션 (모두 강제 적용):
    --network=none / --memory=128m / --memory-swap=128m / --cpus=0.5
    --user=labs (uid 1000) / --read-only / --tmpfs /tmp:size=10m,noexec,nosuid,nodev
    --cap-drop=ALL / --security-opt=no-new-privileges / --pids-limit=50
    --pid=private / --ipc=private

호출 정책:
    - admin 역할만 사용 — 라우터 레벨에서 강제 (이 모듈은 호출자 신뢰).
    - 코드 본문 / 모델 출력은 INFO 로그 금지 (PII 차단).
    - ``asyncio.create_subprocess_exec`` (list 인자) 사용 — shell injection 차단.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import get_logger
from app.evaluators.base import EvaluatorError, EvaluatorTimeoutError, clamp

logger = get_logger(__name__)


# ── 샌드박스 기본값 ────────────────────────────────────────────────────
DEFAULT_SANDBOX_IMAGE = "ax-eval-sandbox:1.0.0"
DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_MEMORY_LIMIT = "128m"
DEFAULT_CPU_LIMIT = "0.5"
DEFAULT_PIDS_LIMIT = 50
DEFAULT_TMPFS_SIZE = "10m"

# 컨테이너 idle 시 사용할 entrypoint (sleep으로 유지)
_IDLE_ENTRYPOINT = ["sleep", "infinity"]


class SandboxStartupError(EvaluatorError):
    """컨테이너 spawn 실패 (이미지 미존재 / Docker 데몬 다운 등)."""


class SandboxExecError(EvaluatorError):
    """``docker exec`` 호출 실패."""


def _build_run_command(
    image: str,
    *,
    memory_limit: str,
    cpu_limit: str,
    pids_limit: int,
    tmpfs_size: str,
) -> list[str]:
    """``docker run`` 명령 리스트 생성. shell-quote 필요 없도록 list 형태."""
    return [
        "docker",
        "run",
        "-d",
        "--rm",
        "--network=none",
        f"--memory={memory_limit}",
        f"--memory-swap={memory_limit}",
        f"--cpus={cpu_limit}",
        "--user=labs",
        "--read-only",
        f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={tmpfs_size}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={pids_limit}",
        "--pid=private",
        "--ipc=private",
        "--entrypoint=sleep",
        image,
        "infinity",
    ]


def _build_exec_command(container_id: str) -> list[str]:
    """``docker exec -i`` — runner.py에 stdin/stdout 파이프."""
    return [
        "docker",
        "exec",
        "-i",
        container_id,
        "python",
        "-u",
        "/app/runner.py",
    ]


def _build_kill_command(container_id: str) -> list[str]:
    return ["docker", "kill", container_id]


def _build_rm_command(container_id: str) -> list[str]:
    return ["docker", "rm", "-f", container_id]


class CustomCodeEvaluator:
    """샌드박스 컨테이너에서 사용자 작성 Python 코드를 실행하는 evaluator.

    Args:
        code: 사용자 작성 Python 평가 코드. ``def evaluate(output, expected, metadata)`` 정의 필요.
        sandbox_image: 사용할 Docker 이미지 (기본 ``ax-eval-sandbox:1.0.0``).
        timeout_sec: 아이템별 wall-clock 타임아웃 (기본 5초).
        memory_limit: 컨테이너 메모리 한도 (기본 ``128m``).
        cpu_limit: 컨테이너 CPU 한도 (기본 ``0.5``).
        pids_limit: PID 한도 (기본 50).
        tmpfs_size: ``/tmp`` tmpfs 크기 (기본 ``10m``).
    """

    name: str = "custom_code"

    def __init__(
        self,
        code: str,
        sandbox_image: str = DEFAULT_SANDBOX_IMAGE,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
        cpu_limit: str = DEFAULT_CPU_LIMIT,
        pids_limit: int = DEFAULT_PIDS_LIMIT,
        tmpfs_size: str = DEFAULT_TMPFS_SIZE,
    ) -> None:
        if not code or not code.strip():
            raise ValueError("code는 비어있을 수 없습니다")
        if timeout_sec <= 0:
            raise ValueError("timeout_sec는 0보다 커야 합니다")

        self._code = code
        self._sandbox_image = sandbox_image
        self._timeout = float(timeout_sec)
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._pids_limit = pids_limit
        self._tmpfs_size = tmpfs_size

        self._container_id: str | None = None
        self._exec_lock = asyncio.Lock()  # 동일 컨테이너 직렬 exec 보장

    # ───────────────────────── async context ─────────────────────────
    async def __aenter__(self) -> CustomCodeEvaluator:
        self._container_id = await self._spawn_container()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        cid = self._container_id
        self._container_id = None
        if cid:
            await self._kill_container(cid)

    # ───────────────────────── 공개 API ──────────────────────────────
    @property
    def container_id(self) -> str | None:
        """현재 사용 중인 컨테이너 ID (테스트/디버깅 용)."""
        return self._container_id

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        """샌드박스 컨테이너에서 평가 코드 실행 → 0.0~1.0 score 반환.

        흐름:
            1. ``{id, code, output, expected, metadata}`` JSON 라인을 stdin에 전달
            2. runner.py가 ``{score: 0~1}`` 또는 ``{error_code, error_message}`` 응답
            3. 점수 클램핑

        에러 처리:
            - Docker exec 실패 / 컨테이너 미준비 → ``None`` 반환 (로그 경고)
            - timeout → :class:`EvaluatorTimeoutError` 캐치 후 ``None``
            - JSON 파싱 실패 / runner 에러 응답 → ``None`` 반환
        """
        if self._container_id is None:
            logger.warning(
                "custom_code.no_container",
                evaluator=self.name,
            )
            return None

        item_id = config.get("item_id") or metadata.get("dataset_item_id") or "unknown"
        payload = {
            "id": str(item_id),
            "code": self._code,
            "output": _stringify(output),
            "expected": _stringify(expected) if expected is not None else "",
            "metadata": _ensure_json_dict(metadata),
        }

        try:
            response = await self._exec_in_container(self._container_id, payload)
        except EvaluatorTimeoutError:
            logger.warning(
                "custom_code.timeout",
                evaluator=self.name,
                container=self._container_id,
                timeout_sec=self._timeout,
            )
            return None
        except SandboxExecError as exc:
            logger.warning(
                "custom_code.exec_failed",
                evaluator=self.name,
                error=str(exc),
            )
            return None

        status = response.get("status")
        if status != "success":
            logger.warning(
                "custom_code.runner_error",
                evaluator=self.name,
                error_code=response.get("error_code"),
                # error_message는 코드/출력 일부를 포함할 수 있으므로 INFO 이상 금지
            )
            return None

        score_raw = response.get("score")
        if not isinstance(score_raw, (int, float)):
            return None
        return clamp(float(score_raw))

    # ───────────────────────── 내부 메서드 ───────────────────────────
    async def _spawn_container(self) -> str:
        """``docker run -d`` — idle 컨테이너 1개 시작. 컨테이너 ID 반환."""
        cmd = _build_run_command(
            self._sandbox_image,
            memory_limit=self._memory_limit,
            cpu_limit=self._cpu_limit,
            pids_limit=self._pids_limit,
            tmpfs_size=self._tmpfs_size,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SandboxStartupError("docker 명령을 찾을 수 없습니다 (PATH 확인 필요)") from exc
        except OSError as exc:
            raise SandboxStartupError(f"docker run 실행 실패: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise SandboxStartupError("docker run 응답 시간 초과") from exc

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
            raise SandboxStartupError(f"docker run 실패 (exit={proc.returncode}): {err[:200]}")

        cid = stdout.decode("utf-8", errors="replace").strip()
        if not cid:
            raise SandboxStartupError("docker run이 빈 컨테이너 ID를 반환했습니다")
        return cid

    async def _exec_in_container(
        self,
        container_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """``docker exec -i`` 실행 — stdin에 JSON 라인 1개, stdout에서 응답 파싱."""
        cmd = _build_exec_command(container_id)
        json_line = json.dumps(payload, ensure_ascii=False) + "\n"

        async with self._exec_lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise SandboxExecError("docker 명령을 찾을 수 없습니다") from exc
            except OSError as exc:
                raise SandboxExecError(f"docker exec 실행 실패: {exc}") from exc

            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(input=json_line.encode("utf-8")),
                    timeout=self._timeout,
                )
            except TimeoutError as exc:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except TimeoutError:
                    pass
                raise EvaluatorTimeoutError(f"custom_code timeout after {self._timeout}s") from exc

        if proc.returncode not in (0, None):
            raise SandboxExecError(f"docker exec returned {proc.returncode}")

        text = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        if not text:
            raise SandboxExecError("runner.py 응답 없음 (stdout empty)")

        # runner.py는 multiple JSON line을 출력할 수 있다. 첫 번째 유효 객체를 사용.
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload

        raise SandboxExecError(f"runner.py 응답 JSON 파싱 실패: {text[:120]!r}")

    async def _kill_container(self, container_id: str) -> None:
        """컨테이너 강제 종료 + 정리. 실패해도 raise하지 않는다."""
        # docker run이 --rm으로 시작되었으므로 kill만 해도 정리되지만,
        # 안전을 위해 명시적으로 rm까지 시도 (idempotent).
        for cmd in (_build_kill_command(container_id), _build_rm_command(container_id)):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    await asyncio.wait_for(proc.communicate(), timeout=10.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
                    logger.warning(
                        "custom_code.cleanup_timeout",
                        container=container_id,
                        cmd=cmd[1],
                    )
            except (FileNotFoundError, OSError) as exc:
                logger.warning(
                    "custom_code.cleanup_failed",
                    container=container_id,
                    cmd=cmd[1],
                    error=str(exc),
                )


# ────────────────────── Validate 모드 (라우터용) ──────────────────────
async def validate_code(
    code: str,
    test_cases: list[dict[str, Any]],
    sandbox_image: str = DEFAULT_SANDBOX_IMAGE,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    """admin/user의 평가 코드 사전 검증.

    각 ``test_case``는 ``{output, expected, metadata}`` 형식.
    결과: ``[{result: float} | {error: str}]`` — 입력 순서 유지.

    ``POST /api/v1/evaluators/validate`` 라우터에서 호출되며, 별도 컨테이너를 spawn해
    test_case 갯수만큼 평가 후 정리한다.
    """
    if not test_cases:
        return []

    results: list[dict[str, Any]] = []
    evaluator = CustomCodeEvaluator(
        code=code,
        sandbox_image=sandbox_image,
        timeout_sec=timeout_sec,
    )
    try:
        async with evaluator as live:
            for index, case in enumerate(test_cases):
                output = case.get("output", "")
                expected = case.get("expected")
                metadata = case.get("metadata") or {}
                if not isinstance(metadata, dict):
                    results.append({"error": f"metadata must be a dict (case {index})"})
                    continue
                try:
                    score = await live.evaluate(
                        output=output,
                        expected=expected,
                        metadata=metadata,
                        item_id=f"validate-{index}",
                    )
                except Exception as exc:  # noqa: BLE001 — validate는 모든 에러를 결과로 변환
                    results.append({"error": f"{type(exc).__name__}: {exc}"})
                    continue

                if score is None:
                    results.append({"error": "evaluation failed (score=None)"})
                else:
                    results.append({"result": score})
    except SandboxStartupError as exc:
        # 컨테이너 자체 spawn 실패 → 모든 test_case 동일 에러로 마킹
        return [{"error": f"sandbox startup failed: {exc}"} for _ in test_cases]

    return results


# ─────────────────────────── 모듈 헬퍼 ───────────────────────────────
def _stringify(value: Any) -> str:
    """평가 입력을 문자열로 정규화 (runner.py가 문자열 output/expected를 기대)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _ensure_json_dict(metadata: Any) -> dict[str, Any]:
    """metadata를 JSON 직렬화 가능 dict로 정규화."""
    if not isinstance(metadata, dict):
        return {}
    # 비-직렬화 값은 str()로 폴백
    safe: dict[str, Any] = {}
    for key, val in metadata.items():
        try:
            json.dumps(val)
            safe[str(key)] = val
        except (TypeError, ValueError):
            safe[str(key)] = str(val)
    return safe
