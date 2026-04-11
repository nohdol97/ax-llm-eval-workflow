"""
Custom Evaluator 샌드박스 실행기

stdin에서 JSON Lines를 읽어 사용자 정의 평가 코드를 실행하고,
결과를 stdout으로 JSON으로 출력한다.

입력 형식 (JSON Lines):
    {"id": "item_001", "code": "def evaluate(output, expected, metadata):\\n    ...", "output": "...", "expected": "...", "metadata": {}}

출력 형식:
    성공: {"id": "item_001", "status": "success", "score": 0.85}
    에러: {"id": "item_002", "status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "에러 메시지"}
    타임아웃: {"id": "item_003", "status": "error", "error_code": "EVALUATOR_TIMEOUT", "error_message": "Execution exceeded 5s timeout"}

보안:
    - 허용 모듈: json, re, math, collections, difflib, statistics, unicodedata
    - 위험 함수 차단: exec, eval, __import__, open, compile, getattr, setattr, delattr,
      globals, locals, vars, dir, breakpoint, exit, quit, input, help, print
    - 네트워크/파일시스템 접근 불가 (Docker 레벨에서 차단)
"""

import json
import math
import re
import signal
import sys
import collections
import difflib
import statistics
import unicodedata


# ── 허용 모듈 목록 ──────────────────────────────────────────────────
ALLOWED_MODULES = {
    "json": json,
    "re": re,
    "math": math,
    "collections": collections,
    "difflib": difflib,
    "statistics": statistics,
    "unicodedata": unicodedata,
}

# ── 차단할 내장 함수 목록 ──────────────────────────────────────────
BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "__import__", "open", "compile",
    "getattr", "setattr", "delattr",
    "globals", "locals", "vars", "dir",
    "breakpoint", "exit", "quit", "input", "help", "print",
    "memoryview", "type",
})

# ── 아이템별 타임아웃 (초) ────────────────────────────────────────
TIMEOUT_SECONDS = 5


class EvalTimeoutError(Exception):
    """평가 함수 실행 타임아웃."""
    pass


def _timeout_handler(signum: int, frame) -> None:
    """SIGALRM 핸들러 — 아이템별 타임아웃 시 예외 발생."""
    raise EvalTimeoutError(f"Execution exceeded {TIMEOUT_SECONDS}s timeout")


def _make_safe_builtins() -> dict:
    """위험 함수를 제거한 안전한 __builtins__ 딕셔너리 생성."""
    import builtins

    safe = {}
    for name in dir(builtins):
        if name.startswith("_"):
            continue
        if name in BLOCKED_BUILTINS:
            continue
        safe[name] = getattr(builtins, name)

    # 기본 타입/함수는 유지
    safe["True"] = True
    safe["False"] = False
    safe["None"] = None

    return safe


def _safe_import(name, *args, **kwargs):
    if name not in ALLOWED_MODULES:
        raise ImportError(f"Import of '{name}' is not allowed in sandbox")
    return __import__(name, *args, **kwargs)


def _make_restricted_namespace() -> dict:
    """제한된 실행 네임스페이스 생성."""
    namespace = {
        "__builtins__": _make_safe_builtins(),
    }
    # import 문 차단: 허용된 7개 모듈만 통과
    namespace['__builtins__']['__import__'] = _safe_import
    # 허용 모듈 주입
    namespace.update(ALLOWED_MODULES)
    return namespace


def _handle_sigterm(signum: int, frame) -> None:
    """SIGTERM 시그널 핸들러 — 정상 종료."""
    sys.exit(0)


def _process_line(line: str) -> dict:
    """단일 JSON 라인 처리 — 평가 코드 실행 후 결과 반환."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as e:
        return {
            "id": "unknown",
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": f"Invalid JSON input: {e}",
        }

    item_id = payload.get("id", "unknown")
    code = payload.get("code")
    output = payload.get("output", "")
    expected = payload.get("expected", "")
    metadata = payload.get("metadata", {})

    if not code:
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": "평가 코드(code)가 비어있습니다",
        }

    # 제한된 네임스페이스에서 코드 실행
    namespace = _make_restricted_namespace()

    # SIGALRM 타임아웃을 exec() 이전에 설정하여
    # 모듈 레벨 무한루프도 타임아웃으로 차단한다.
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)

    try:
        exec(code, namespace)  # noqa: S102 — 샌드박스 내 의도적 exec
    except EvalTimeoutError:
        signal.alarm(0)
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_TIMEOUT",
            "error_message": f"Execution exceeded {TIMEOUT_SECONDS}s timeout",
        }
    except Exception as e:
        signal.alarm(0)
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": f"Code compilation failed: {type(e).__name__}: {e}",
        }

    # evaluate 함수 존재 확인
    evaluate_fn = namespace.get("evaluate")
    if evaluate_fn is None or not callable(evaluate_fn):
        signal.alarm(0)
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": "Function 'evaluate' not defined in code",
        }

    # evaluate 함수 호출 (타이머는 exec() 이전에 이미 설정됨)
    try:
        score = evaluate_fn(output, expected, metadata)
    except EvalTimeoutError:
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_TIMEOUT",
            "error_message": f"Execution exceeded {TIMEOUT_SECONDS}s timeout",
        }
    except Exception as e:
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": f"{type(e).__name__}: {e}",
        }
    finally:
        signal.alarm(0)  # 타이머 해제

    # 반환값 검증
    if score is None:
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": "evaluate()가 None을 반환했습니다",
        }

    try:
        score = float(score)
    except (TypeError, ValueError) as e:
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": f"evaluate() returned non-numeric value: {repr(score)}",
        }

    if not math.isfinite(score):
        return {
            "id": item_id,
            "status": "error",
            "error_code": "EVALUATOR_ERROR",
            "error_message": f"score가 유한하지 않습니다: {score}",
        }

    # 0.0~1.0 클램핑
    score = max(0.0, min(1.0, score))

    return {"id": item_id, "status": "success", "score": score}


def main() -> None:
    """메인 루프 — stdin에서 JSON Lines를 읽고 결과를 stdout으로 출력."""
    # SIGTERM 핸들러 등록 (Docker stop 시 정상 종료)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        result = _process_line(line)

        try:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            break


if __name__ == "__main__":
    main()
