# 테스트 명세 Part 2: Phase 4~7

Phase 4(실험 실행 엔진), Phase 5(평가 시스템), Phase 6(분석), Phase 7(Frontend) 테스트 명세.

---

## Phase 4: 실험 실행 엔진 테스트

### 4.1 Context Engine 테스트

#### 4.1.1 단일 변수 바인딩

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_bind_single_variable_when_template_has_one_placeholder` |
| **입력/설정** | 프롬프트: `"분석 대상: {{input_text}}"`, 변수: `{"input_text": "이 서비스는 만족스럽습니다"}` |
| **기대 결과** | `"분석 대상: 이 서비스는 만족스럽습니다"` |
| **fixture/mock** | Langfuse prompt mock (`TextPromptClient.compile()` 반환값 설정) |
| **엣지케이스** | 변수값이 빈 문자열 `""` → 치환은 수행하되 빈 문자열로 대체. 변수값에 특수문자(`{{`, `}}`, `\n`, `\t`) 포함 시 그대로 삽입 |

#### 4.1.2 복수 변수 바인딩

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_bind_all_variables_when_template_has_multiple_placeholders` |
| **입력/설정** | 프롬프트: `"입력: {{input_text}}\n규칙: {{rules}}\n포맷: {{output_format}}"`, 변수: `{"input_text": "텍스트", "rules": "규칙 JSON", "output_format": "json"}` |
| **기대 결과** | 모든 변수가 올바르게 치환된 최종 프롬프트 문자열 |
| **fixture/mock** | Langfuse prompt mock |
| **엣지케이스** | 변수 3개 중 2개만 제공 → 마지막 변수는 `{{output_format}}` 문자열 그대로 유지 또는 ValidationError 발생 (설계에 따름) |

#### 4.1.3 중첩 변수 바인딩

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_resolve_nested_path_when_variable_has_dot_notation` |
| **입력/설정** | 프롬프트: `"데이터: {{context.user_info}}"`, 변수: `{"context": {"user_info": "이름: 홍길동"}}` |
| **기대 결과** | 중첩 구조의 변수가 올바르게 참조 및 치환됨 |
| **fixture/mock** | Langfuse prompt mock |
| **엣지케이스** | 존재하지 않는 중첩 경로 `{{context.nonexistent}}` → 에러 또는 빈 문자열 |

#### 4.1.4 누락 변수 처리

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_validation_error_when_required_variable_missing` |
| **입력/설정** | 프롬프트: `"입력: {{input_text}} 규칙: {{rules}}"`, 변수: `{"input_text": "텍스트"}` (rules 누락) |
| **기대 결과** | `VALIDATION_ERROR` (422) 반환, 누락된 변수 이름 `rules`가 에러 메시지에 포함 |
| **fixture/mock** | Langfuse prompt mock |
| **엣지케이스** | 모든 변수 누락, 변수 이름 오타 (`input_text` vs `inputText`) |

#### 4.1.5 변수 타입별 바인딩 (text)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_insert_text_as_is_when_variable_type_is_text` |
| **입력/설정** | 변수 타입: `text`, 값: `"일반 텍스트 문자열"` |
| **기대 결과** | 문자열 그대로 삽입 |
| **fixture/mock** | 없음 (순수 함수 테스트) |
| **엣지케이스** | 유니코드 문자열 (`"한글🔥emoji"`), 매우 긴 문자열 (100KB), 줄바꿈 포함 문자열 |

#### 4.1.6 변수 타입별 바인딩 (json)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_serialize_to_json_string_when_variable_type_is_json` |
| **입력/설정** | 변수 타입: `json`, 값: `{"categories": ["긍정", "부정"], "threshold": 0.5}` |
| **기대 결과** | JSON 문자열로 직렬화되어 프롬프트에 삽입 |
| **fixture/mock** | 없음 |
| **엣지케이스** | 유효하지 않은 JSON 값 → `VALIDATION_ERROR`. 빈 객체 `{}`, 빈 배열 `[]`, 깊은 중첩 (depth 10) |

#### 4.1.7 변수 타입별 바인딩 (file)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_read_file_content_when_variable_type_is_file` |
| **입력/설정** | 변수 타입: `file`, 값: 텍스트 파일 내용 (UTF-8) |
| **기대 결과** | 파일 내용이 문자열로 변환되어 프롬프트에 삽입 |
| **fixture/mock** | 파일 내용 mock |
| **엣지케이스** | 빈 파일, 바이너리 파일 (인코딩 에러), 대용량 파일 (1MB 초과) |

---

### 4.2 단일 테스트 Runner

#### 4.2.1 정상 실행 (스트리밍)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_stream_response_when_streaming_mode_enabled` |
| **입력/설정** | `POST /api/v1/tests/single` — `stream: true`, 모델: `gpt-4o`, 프롬프트: inline 텍스트, 변수 1개 |
| **기대 결과** | SSE 스트림 반환. `event: token` 이벤트 1개 이상 수신 후 `event: done` 이벤트 수신. `done` 데이터에 `trace_id`, `usage`, `latency_ms`, `cost_usd` 포함 |
| **fixture/mock** | LiteLLM `acompletion()` mock — 스트리밍 청크 3개 반환 (`"감성"`, `" 분석"`, `" 완료"`). Langfuse client mock |
| **엣지케이스** | 응답이 빈 문자열인 경우 (token 이벤트 0개, done 이벤트만 발생) |

#### 4.2.2 정상 실행 (비스트리밍)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_json_response_when_streaming_mode_disabled` |
| **입력/설정** | `POST /api/v1/tests/single` — `stream: false`, 모델: `gpt-4o` |
| **기대 결과** | JSON 응답. `output`, `trace_id`, `usage.input_tokens`, `usage.output_tokens`, `usage.total_tokens`, `latency_ms`, `cost_usd`, `model` 필드 포함 |
| **fixture/mock** | LiteLLM `acompletion()` mock — 단일 응답, `usage` 포함 |
| **엣지케이스** | 없음 |

#### 4.2.3 SSE 이벤트 순서 검증 (token → done)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_emit_tokens_before_done_when_sse_streaming` |
| **입력/설정** | `stream: true`, LLM 응답 청크 5개 |
| **기대 결과** | 수신된 이벤트 목록에서 모든 `token` 이벤트가 `done` 이벤트 이전에 위치. `done` 이벤트는 정확히 1개. `token` 이벤트 순서가 LLM 응답 청크 순서와 일치 |
| **fixture/mock** | LiteLLM mock — 5개 청크 순차 반환 |
| **엣지케이스** | 청크 1개만 있는 경우 (`token` 1개 + `done` 1개) |

#### 4.2.4 SSE 에러 이벤트

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_emit_error_event_when_llm_call_fails_mid_stream` |
| **입력/설정** | `stream: true`, LLM 호출이 중간에 실패하도록 mock 설정 |
| **기대 결과** | `event: error` 이벤트 수신. `data`에 `code` (`LLM_ERROR`) 및 `message` 포함. `done` 이벤트는 발생하지 않음 |
| **fixture/mock** | LiteLLM mock — 2개 청크 후 `Exception` 발생 |
| **엣지케이스** | 첫 청크부터 에러 발생 (token 이벤트 0개 + error 이벤트) |

#### 4.2.5 실행 중단 (cancel)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_stop_stream_when_cancel_requested` |
| **입력/설정** | `stream: true`로 실행 시작 후, `POST /api/v1/tests/single/{trace_id}/cancel` 호출 |
| **기대 결과** | SSE 스트림이 종료됨. 응답 상태코드 200. Langfuse trace에 cancellation 메타데이터 기록 |
| **fixture/mock** | LiteLLM mock — 느린 스트리밍 (각 청크 사이 지연). Langfuse mock |
| **엣지케이스** | 이미 완료된 trace에 cancel 요청 → 404 또는 무시. 존재하지 않는 trace_id로 cancel → 404 |

#### 4.2.6 멀티모달 (이미지 포함)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_include_image_content_when_images_provided` |
| **입력/설정** | `images: ["base64_encoded_png"]`, 모델: `gpt-4o` (supports_vision=true) |
| **기대 결과** | LiteLLM에 전달되는 messages에 `image_url` 타입 content 포함. 정상 응답 반환 |
| **fixture/mock** | LiteLLM mock — vision 모델 응답. base64 인코딩된 1x1 PNG fixture |
| **엣지케이스** | vision 미지원 모델로 이미지 전송 → 에러 또는 경고. 여러 이미지 동시 첨부 (3개). 잘못된 base64 문자열 → `VALIDATION_ERROR` |

#### 4.2.7 LLM 호출 실패 — Timeout

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_LLM_TIMEOUT_when_llm_call_times_out` |
| **입력/설정** | LiteLLM mock이 `asyncio.TimeoutError` 발생 |
| **기대 결과** | 스트리밍: `event: error`, `code: "LLM_TIMEOUT"`. 비스트리밍: HTTP 504, `error.code: "LLM_TIMEOUT"` |
| **fixture/mock** | LiteLLM mock — `TimeoutError` raise |
| **엣지케이스** | 없음 |

#### 4.2.8 LLM 호출 실패 — Rate Limit

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_LLM_RATE_LIMIT_when_rate_limited` |
| **입력/설정** | LiteLLM mock이 `RateLimitError` (429) 발생 |
| **기대 결과** | HTTP 429, `error.code: "LLM_RATE_LIMIT"`, `error.message`에 rate limit 관련 내용 포함 |
| **fixture/mock** | LiteLLM mock — 429 에러 응답 |
| **엣지케이스** | 없음 |

#### 4.2.9 LLM 호출 실패 — 일반 에러

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_LLM_ERROR_when_llm_returns_500` |
| **입력/설정** | LiteLLM mock이 `APIError` (500) 발생 |
| **기대 결과** | HTTP 502, `error.code: "LLM_ERROR"` |
| **fixture/mock** | LiteLLM mock — 500 에러 응답 |
| **엣지케이스** | 에러 메시지에 민감 정보(API 키) 포함 시 마스킹 확인 |

#### 4.2.10 비용/토큰 계산 정확성

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_calculate_cost_and_tokens_when_usage_present` |
| **입력/설정** | LiteLLM 응답 usage: `{"input_tokens": 150, "output_tokens": 25, "total_tokens": 175}`, `completion_cost()` 반환: `0.0023` |
| **기대 결과** | 응답의 `usage.input_tokens` = 150, `usage.output_tokens` = 25, `usage.total_tokens` = 175, `cost_usd` = 0.0023 |
| **fixture/mock** | LiteLLM mock — usage 포함 응답. `litellm.completion_cost()` mock 반환값 `0.0023` |
| **엣지케이스** | usage 필드 누락 시 → 0으로 기록. `completion_cost()` 실패 시 → `cost_usd: null` |

#### 4.2.11 Langfuse trace 기록 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_record_trace_when_test_executed` |
| **입력/설정** | 정상 단일 테스트 실행 |
| **기대 결과** | `langfuse.trace()` 호출됨 — `name`, `metadata.source="ax-llm-eval-workflow"`, `metadata.experiment_type="single_test"`, `metadata.model`, `metadata.prompt_name`, `metadata.prompt_version`, `tags` 포함. `trace.generation()` 호출됨 — `model`, `usage`, `input`, `output` 포함 |
| **fixture/mock** | Langfuse client mock (spy) |
| **엣지케이스** | Langfuse SDK 호출 실패 시 → 실험 결과 자체는 반환하되 경고 로그 기록 |

#### 4.2.12 Langfuse trace 메타데이터 및 태그 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_include_metadata_and_tags_when_trace_recorded` |
| **입력/설정** | 프롬프트 이름: `"sentiment-analysis"`, 버전: 3, 모델: `gpt-4o` |
| **기대 결과** | `metadata.prompt_name` = `"sentiment-analysis"`, `metadata.prompt_version` = 3, `metadata.model` = `"gpt-4o"`. `tags`에 `"ax-eval"`, `"single-test"` 포함 |
| **fixture/mock** | Langfuse client mock |
| **엣지케이스** | 없음 |

#### 4.2.13 Langfuse usage 기록 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_record_usage_in_generation_when_trace_created` |
| **입력/설정** | LiteLLM 응답 usage: `{"input": 150, "output": 25}` |
| **기대 결과** | `trace.generation()`에 전달된 `usage` 딕셔너리가 `{"input": 150, "output": 25, "total": 175, "unit": "TOKENS"}` |
| **fixture/mock** | LiteLLM mock, Langfuse mock |
| **엣지케이스** | 없음 |

#### 4.2.14 evaluator 포함 시 scores가 done 이벤트에 포함

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_include_scores_in_done_event_when_evaluators_configured` |
| **입력/설정** | `evaluators: [{"type": "built_in", "name": "exact_match"}, {"type": "built_in", "name": "json_validity"}]`, LLM 응답: `"positive"`, expected: `"positive"` |
| **기대 결과** | SSE `event: done`의 `data.scores`에 `{"exact_match": 1.0, "json_validity": 0.0}` 포함 (응답 `"positive"`는 유효한 JSON이 아니므로) |
| **fixture/mock** | LiteLLM mock, Langfuse mock (score 기록 spy) |
| **엣지케이스** | evaluator 실행 실패 → 해당 스코어는 `null`로 done에 포함 |

---

### 4.3 배치 실험 Runner

#### 4.3.1 실험 생성 (Redis 상태 초기화 검증)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_initialize_redis_state_when_experiment_created` |
| **입력/설정** | `POST /api/v1/experiments` — 프롬프트 1개, 모델 2개, 데이터셋 100 아이템, evaluator 1개, concurrency 5 |
| **기대 결과** | HTTP 200. 응답: `experiment_id` (UUID), `status: "running"`, `total_runs: 2`, `total_items: 200`. Redis 검증: `ax:experiment:{id}` Hash에 `name`, `status="running"`, `total_items=200`, `completed_items=0`, `failed_items=0`, `total_cost_usd=0`, `created_at`, `total_runs=2`, `config` (JSON). `ax:experiment:{id}:runs` Set에 2개 Run 이름. 각 `ax:run:{id}:{name}` Hash에 `status="running"`, `model`, `prompt_name`, `prompt_version`, `total_items=100`. `ax:project:{pid}:experiments` Sorted Set에 experiment_id 추가. TTL이 86400초 (24시간)로 설정 |
| **fixture/mock** | Redis (실제 또는 fakeredis), Langfuse dataset mock (100 아이템 반환), LiteLLM mock |
| **엣지케이스** | 존재하지 않는 데이터셋 이름 → `DATASET_NOT_FOUND` (404). 빈 evaluators 배열 → 정상 생성 (평가 없이 실행) |

#### 4.3.2 SSE progress 이벤트 (완료 수 증가)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_emit_progress_events_when_items_completed` |
| **입력/설정** | `GET /api/v1/experiments/{experiment_id}/stream`, 실험 진행 중 |
| **기대 결과** | `event: progress` 이벤트 수신. `data.run_name` 존재. `data.completed` 값이 시간에 따라 증가 (0 → 1 → 2 → ...). `data.total`은 해당 Run의 총 아이템 수와 일치. `data.current_item`에 `id`, `status`, `score` 포함 |
| **fixture/mock** | Redis mock (진행 상태 업데이트), 실험 Runner mock |
| **엣지케이스** | 아이템 실패 시 progress에 `status: "failed"` 포함 |

#### 4.3.3 SSE run_complete 이벤트 (요약 정확성)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_emit_run_complete_with_summary_when_run_finished` |
| **입력/설정** | Run의 모든 아이템 완료 후 |
| **기대 결과** | `event: run_complete` 이벤트 수신. `data.run_name` 존재. `data.summary.avg_score`가 Redis의 `total_score_sum / scored_count`와 일치. `data.summary.total_cost`가 Redis `total_cost_usd`와 일치. `data.summary.avg_latency`가 Redis `total_latency_ms / completed_items`와 일치 |
| **fixture/mock** | Redis mock (Run 완료 상태), 사전 설정된 score/cost/latency 값 |
| **엣지케이스** | 모든 아이템 실패 시 `avg_score: null` (scored_count=0이므로 나눗셈 불가) |

#### 4.3.4 SSE experiment_complete 이벤트

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_emit_experiment_complete_when_all_runs_finished` |
| **입력/설정** | 실험의 모든 Run 완료 후 |
| **기대 결과** | `event: experiment_complete` 이벤트 수신. `data.experiment_id` 존재. `data.total_duration_sec` > 0. `data.total_cost_usd` = 모든 Run의 비용 합계 |
| **fixture/mock** | Redis mock |
| **엣지케이스** | 없음 |

#### 4.3.5 concurrency 제한 (동시 실행 수 검증)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_limit_concurrent_calls_when_concurrency_set` |
| **입력/설정** | `concurrency: 3`, 데이터셋 10 아이템 |
| **기대 결과** | 동시에 실행 중인 LLM 호출이 최대 3개를 초과하지 않음. 모든 10 아이템이 최종적으로 완료됨 |
| **fixture/mock** | LiteLLM mock — 각 호출에 100ms 지연. `asyncio.Semaphore` 카운터 spy |
| **엣지케이스** | `concurrency: 1` → 순차 실행. `concurrency: 100` (아이템 수보다 큼) → 모든 아이템 동시 실행 |

#### 4.3.6 실험 일시정지 → 재개 → 완료

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_pause_and_resume_when_lifecycle_commands_called` |
| **입력/설정** | 실험 실행 중 `POST /api/v1/experiments/{id}/pause` → Redis 상태 확인 → `POST /api/v1/experiments/{id}/resume` → 나머지 아이템 완료 |
| **기대 결과** | pause 후: `status = "paused"`, 진행 중인 아이템 완료 후 새 아이템 실행 중단. resume 후: `status = "running"`, 남은 아이템 실행 재개. 최종: `status = "completed"`, `completed_items = total_items` |
| **fixture/mock** | Redis (실제 또는 fakeredis), LiteLLM mock |
| **엣지케이스** | pause 직후 즉시 resume (빠른 연속 호출). 이미 paused인 상태에서 pause 재호출 → 409 |

#### 4.3.7 실험 취소 → 상태 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_cancel_experiment_when_cancel_requested` |
| **입력/설정** | 실험 실행 중 `POST /api/v1/experiments/{id}/cancel` |
| **기대 결과** | Redis: `status = "cancelled"`, `completed_at` 기록, TTL 1시간으로 단축. 진행 중인 LLM 호출은 완료 대기 후 종료 (새 아이템 시작 금지). SSE 스트림 종료 |
| **fixture/mock** | Redis, LiteLLM mock |
| **엣지케이스** | paused 상태에서 cancel → 정상 (허용 전이). cancelled 상태에서 재취소 → 409 |

#### 4.3.8 실패 아이템 재시도 (retry-failed)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_retry_only_failed_items_when_retry_requested` |
| **입력/설정** | 실험 10 아이템 중 3개 실패 (failed_items Set에 3개 ID). `POST /api/v1/experiments/{id}/retry-failed` |
| **기대 결과** | `status`가 `"running"`으로 전이. 실패했던 3개 아이템만 재실행. 재실행 성공 시 failed_items에서 제거, completed_items 증가. 최종 `status = "completed"` |
| **fixture/mock** | Redis (사전 설정된 실패 상태), LiteLLM mock (재시도 시 성공) |
| **엣지케이스** | failed 상태에서 retry → 정상. completed 상태에서 retry → 정상 (실패 아이템이 있는 경우). cancelled 상태에서 retry → 409. 실패 아이템 0개에서 retry → 즉시 completed |

#### 4.3.9 모든 아이템 실패 시 상태 = failed

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_set_status_failed_when_all_items_fail` |
| **입력/설정** | 데이터셋 5 아이템, LiteLLM mock이 모든 호출에서 에러 발생 |
| **기대 결과** | `status = "failed"`. `completed_items = 0`. `failed_items = 5`. `error_message`에 전체 실패 관련 메시지 포함 |
| **fixture/mock** | LiteLLM mock — 모든 호출에서 `APIError` 발생 |
| **엣지케이스** | 없음 |

#### 4.3.10 빈 데이터셋 (0 아이템)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_validation_error_when_dataset_empty` |
| **입력/설정** | 데이터셋 아이템 0개 |
| **기대 결과** | `VALIDATION_ERROR` (422) 반환, "데이터셋에 아이템이 없습니다" 메시지. 또는 즉시 `completed` 상태 (설계 결정에 따름) |
| **fixture/mock** | Langfuse dataset mock — 빈 items 배열 반환 |
| **엣지케이스** | 없음 |

#### 4.3.11 대규모 데이터셋 (1000 아이템)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_process_all_items_when_dataset_has_1000_items` |
| **입력/설정** | 데이터셋 1000 아이템, concurrency 10, 프롬프트 1개, 모델 1개 |
| **기대 결과** | 모든 1000 아이템 처리 완료. `completed_items = 1000`. 메모리 사용량이 합리적 범위 내. progress 이벤트가 주기적으로 발생 (1000개 모두가 아닌 배치 단위) |
| **fixture/mock** | LiteLLM mock (즉시 응답), Langfuse mock |
| **엣지케이스** | 부분 실패 (1000 중 50개 실패) 시 정상 완료 처리 |

#### 4.3.12 Redis TTL 검증 (활성 24h, 완료 1h)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_set_correct_ttl_when_experiment_active_and_completed` |
| **입력/설정** | 실험 생성 → TTL 확인 → 실험 완료 → TTL 재확인 |
| **기대 결과** | 생성 직후: `ax:experiment:{id}` TTL ≈ 86400초 (24h). 완료 후: TTL ≈ 3600초 (1h). Run Hash, Run Set, Failed Items Set도 동일한 TTL 적용 |
| **fixture/mock** | Redis (실제, TTL 검증 가능해야 함) |
| **엣지케이스** | 활성 상태에서 아이템 완료 이벤트 시 TTL 재설정 (24h로 갱신) |

#### 4.3.13 Langfuse 영속화 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_persist_traces_to_langfuse_when_experiment_completed` |
| **입력/설정** | 실험 5 아이템, evaluator 1개 |
| **기대 결과** | Langfuse `trace()` 호출 5회 (아이템당 1회). `trace.generation()` 호출 5회. `langfuse.score()` 호출 5회. `dataset_item.link()` 호출 5회 (각 아이템의 trace를 dataset run에 연결). `langfuse.flush()` 호출 확인 (배치 단위) |
| **fixture/mock** | Langfuse client mock (spy) |
| **엣지케이스** | 없음 |

#### 4.3.14 LLM 호출 중 Langfuse 다운 시 동작

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_continue_execution_when_langfuse_unavailable` |
| **입력/설정** | 실험 실행 중 Langfuse SDK 호출이 `ConnectionError` 발생 |
| **기대 결과** | LLM 호출 자체는 계속 진행. 실험 상태 업데이트 (Redis)는 정상 작동. Langfuse 기록 실패에 대한 경고 로그 발생. 실험 최종 상태에 Langfuse 기록 실패 경고 포함 |
| **fixture/mock** | LiteLLM mock (정상 응답), Langfuse mock (모든 호출에서 `ConnectionError`) |
| **엣지케이스** | Langfuse가 간헐적으로 실패 (5회 중 2회 성공) → 성공한 건만 기록 |

---

### 4.4 Redis 상태 전이 테스트

#### 4.4.1 유효한 전이: running → paused

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_transition_to_paused_when_running_experiment_paused` |
| **입력/설정** | Redis에 `status="running"` 실험 생성. `POST /api/v1/experiments/{id}/pause` |
| **기대 결과** | HTTP 200. Redis `status = "paused"`. `updated_at` 갱신. TTL 변경 없음 (24h 유지) |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.2 유효한 전이: paused → running

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_transition_to_running_when_paused_experiment_resumed` |
| **입력/설정** | Redis에 `status="paused"` 실험. `POST /api/v1/experiments/{id}/resume` |
| **기대 결과** | HTTP 200. Redis `status = "running"`. `updated_at` 갱신 |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.3 유효한 전이: running → cancelled

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_transition_to_cancelled_when_running_experiment_cancelled` |
| **입력/설정** | Redis에 `status="running"` 실험. `POST /api/v1/experiments/{id}/cancel` |
| **기대 결과** | HTTP 200. Redis `status = "cancelled"`. `completed_at` 기록. TTL 3600초로 단축 |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.4 유효한 전이: paused → cancelled

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_transition_to_cancelled_when_paused_experiment_cancelled` |
| **입력/설정** | Redis에 `status="paused"` 실험. `POST /api/v1/experiments/{id}/cancel` |
| **기대 결과** | HTTP 200. Redis `status = "cancelled"`. `completed_at` 기록. TTL 3600초로 단축 |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.5 유효한 전이: completed → running (retry-failed)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_transition_to_running_when_completed_experiment_retried` |
| **입력/설정** | Redis에 `status="completed"`, `failed_items > 0` 실험. `POST /api/v1/experiments/{id}/retry-failed` |
| **기대 결과** | HTTP 200. Redis `status = "running"`. TTL이 86400초로 재설정 |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.6 유효한 전이: failed → running (retry-failed)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_transition_to_running_when_failed_experiment_retried` |
| **입력/설정** | Redis에 `status="failed"` 실험. `POST /api/v1/experiments/{id}/retry-failed` |
| **기대 결과** | HTTP 200. Redis `status = "running"` |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.7 무효한 전이: cancelled → running

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_409_when_resuming_cancelled_experiment` |
| **입력/설정** | Redis에 `status="cancelled"` 실험. `POST /api/v1/experiments/{id}/resume` |
| **기대 결과** | HTTP 409, `error.code: "STATE_CONFLICT"`. Redis `status` 변경 없음 (`"cancelled"` 유지) |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.8 무효한 전이: completed → paused

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_409_when_pausing_completed_experiment` |
| **입력/설정** | Redis에 `status="completed"` 실험. `POST /api/v1/experiments/{id}/pause` |
| **기대 결과** | HTTP 409, `error.code: "STATE_CONFLICT"` |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.9 무효한 전이: cancelled → paused

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_409_when_pausing_cancelled_experiment` |
| **입력/설정** | Redis에 `status="cancelled"` 실험. `POST /api/v1/experiments/{id}/pause` |
| **기대 결과** | HTTP 409, `error.code: "STATE_CONFLICT"` |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.10 무효한 전이: failed → paused

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_409_when_pausing_failed_experiment` |
| **입력/설정** | Redis에 `status="failed"` 실험. `POST /api/v1/experiments/{id}/pause` |
| **기대 결과** | HTTP 409, `error.code: "STATE_CONFLICT"` |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.11 무효한 전이: cancelled → retry-failed

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_409_when_retrying_cancelled_experiment` |
| **입력/설정** | Redis에 `status="cancelled"` 실험. `POST /api/v1/experiments/{id}/retry-failed` |
| **기대 결과** | HTTP 409, `error.code: "STATE_CONFLICT"` |
| **fixture/mock** | Redis |
| **엣지케이스** | 없음 |

#### 4.4.12 존재하지 않는 실험 상태 전이

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_404_when_transitioning_nonexistent_experiment` |
| **입력/설정** | 존재하지 않는 `experiment_id`로 pause 요청 |
| **기대 결과** | HTTP 404, `error.code: "EXPERIMENT_NOT_FOUND"` |
| **fixture/mock** | Redis (빈 상태) |
| **엣지케이스** | 없음 |

#### 4.4.13 동시 상태 전이 (race condition)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_allow_only_one_transition_when_concurrent_requests` |
| **입력/설정** | `status="running"` 실험에 대해 동시에 `pause`와 `cancel` 요청 (2개 동시 호출) |
| **기대 결과** | Lua script의 원자적 실행으로 하나만 성공, 다른 하나는 409 반환. Redis 상태는 `"paused"` 또는 `"cancelled"` 중 정확히 하나. 중간 상태(inconsistent state) 없음 |
| **fixture/mock** | Redis (실제), `asyncio.gather()` 또는 스레드로 동시 호출 |
| **엣지케이스** | 3개 이상 동시 전이 요청 → 정확히 1개만 성공 |

---

## Phase 5: 평가 시스템 테스트

### 5.1 Built-in Evaluator (13개)

#### 5.1.1 exact_match

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_output_matches_expected` / `test_should_return_0_when_output_differs_from_expected` |
| **입력/설정** | 통과: output=`"positive"`, expected=`"positive"`. 실패: output=`"Positive"`, expected=`"positive"` |
| **기대 결과** | 통과: `1.0`. 실패: `0.0` |
| **fixture/mock** | 없음 (순수 함수) |
| **엣지케이스** | 빈 문자열 두 개 → 1.0. `null` output → 0.0. 유니코드 (`"한글"` vs `"한글"`) → 1.0. 대소문자 무시 옵션 켜면 `"Positive"` vs `"positive"` → 1.0. 공백 정규화 옵션 켜면 `"a  b"` vs `"a b"` → 1.0. 매우 긴 문자열 (10KB) 비교 |

#### 5.1.2 contains

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_output_contains_keyword` / `test_should_return_1_when_output_contains_all_keywords` / `test_should_return_1_when_output_contains_any_keyword` |
| **입력/설정** | 단일: output=`"감성 분석 결과: 긍정"`, keyword=`"긍정"`. AND: keywords=`["긍정", "분석"]`. OR: keywords=`["부정", "긍정"]` |
| **기대 결과** | 단일: `1.0`. AND: `1.0` (둘 다 포함). OR: `1.0` (하나 이상 포함) |
| **fixture/mock** | 없음 |
| **엣지케이스** | 빈 키워드 목록 → 1.0. output이 빈 문자열 → 0.0 (키워드가 있는 경우). 키워드 자체가 빈 문자열 → 항상 포함으로 판정. 유니코드 키워드 (`"한글"`). AND 조건에서 하나만 포함 → 0.0 |

#### 5.1.3 regex_match

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_output_matches_regex` / `test_should_return_0_when_output_not_matches_regex` |
| **입력/설정** | 통과: output=`"score: 0.95"`, pattern=`r"score:\s*\d+\.\d+"`. 실패: output=`"no score"`, 동일 패턴 |
| **기대 결과** | 통과: `1.0`. 실패: `0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | 잘못된 정규표현식 패턴 (`"[invalid"`) → 에러 반환. 멀티라인 매칭. 유니코드 패턴 (`r"[가-힣]+"`) |

#### 5.1.4 json_validity

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_output_is_valid_json` / `test_should_return_0_when_output_is_invalid_json` |
| **입력/설정** | 유효: output=`'{"result": "positive"}'`. 무효: output=`"not json"` |
| **기대 결과** | 유효: `1.0`. 무효: `0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | 빈 문자열 → 0.0. `"null"` → 1.0 (유효 JSON). `"123"` → 1.0. `"true"` → 1.0. JSON 앞뒤 공백 → 1.0. 깊은 중첩 JSON (depth 100). 대용량 JSON (1MB) |

#### 5.1.5 json_schema_match

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_output_matches_schema` / `test_should_return_0_when_output_violates_schema` |
| **입력/설정** | 스키마: `{"type": "object", "required": ["label", "confidence"], "properties": {"label": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}`. 유효: `'{"label": "positive", "confidence": 0.95}'`. 무효: `'{"label": "positive"}'` (confidence 누락) |
| **기대 결과** | 유효: `1.0`. 무효: `0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | output이 유효 JSON이 아닌 경우 → 0.0. 빈 스키마 `{}` → 항상 1.0. 스키마 자체가 유효하지 않은 경우 → 에러 |

#### 5.1.6 json_key_presence

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_all_required_keys_present` / `test_should_return_partial_score_when_some_keys_missing` / `test_should_return_0_when_no_required_keys_present` |
| **입력/설정** | 필수 키: `["label", "confidence", "reasoning"]`. 전체: output에 3개 모두 있음. 부분: 2개만 있음. 없음: 0개 |
| **기대 결과** | 전체: `1.0`. 부분: `0.667` (2/3). 없음: `0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | output이 JSON이 아닌 경우 → 0.0. 빈 필수 키 목록 → 1.0. 중첩 키 체크 여부 (설계 결정) |

#### 5.1.7 levenshtein_similarity

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_strings_identical` / `test_should_return_partial_score_when_strings_similar` / `test_should_return_0_when_strings_completely_different` |
| **입력/설정** | 완전 일치: output=expected=`"hello"`. 부분: output=`"hello"`, expected=`"hallo"`. 완전 불일치: output=`"abc"`, expected=`"xyz"` |
| **기대 결과** | 완전 일치: `1.0`. 부분: `0.8` (편집거리 1, 길이 5). 완전 불일치: `0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | 빈 문자열 둘 다 → 1.0. 한쪽만 빈 문자열 → 0.0. 유니코드 문자열. 매우 긴 문자열 (10KB, 성능 주의) |

#### 5.1.8 cosine_similarity

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_high_score_when_texts_semantically_similar` / `test_should_return_low_score_when_texts_semantically_different` |
| **입력/설정** | 높은 유사도: output=`"The weather is nice"`, expected=`"The weather is beautiful"`. 낮은 유사도: output=`"I like cats"`, expected=`"Quantum physics is complex"` |
| **기대 결과** | 높은 유사도: `> 0.8`. 낮은 유사도: `< 0.5` |
| **fixture/mock** | LiteLLM embedding mock — 사전 계산된 벡터 반환 |
| **엣지케이스** | 빈 문자열 → 에러 또는 0.0. 동일 문자열 → 1.0. embedding_model 파라미터 변경 (`text-embedding-3-large`) |

#### 5.1.9 bleu

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_bleu_output_matches_reference` / `test_should_return_partial_when_bleu_partially_matches` / `test_should_return_0_when_bleu_no_match` |
| **입력/설정** | 완벽: output=expected=`"the cat sat on the mat"`. 부분: output=`"the cat sat"`, expected=`"the cat sat on the mat"`. 불일치: output=`"hello world"`, expected=`"the cat sat on the mat"` |
| **기대 결과** | 완벽: `1.0`. 부분: `> 0.3` (정밀도 높으나 brevity penalty). 불일치: `≈ 0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | 빈 문자열 output → 0.0. 한 단어 output → brevity penalty로 매우 낮은 점수. 유니코드 텍스트 (한글 토크나이징) |

#### 5.1.10 rouge

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_rouge_output_matches_reference` / `test_should_return_partial_when_rouge_partially_matches` / `test_should_return_0_when_rouge_no_match` |
| **입력/설정** | 완벽: 동일 문자열. 부분: output이 expected의 부분 수열 포함. 불일치: 완전히 다른 문자열 |
| **기대 결과** | 완벽: `1.0`. 부분: `0.3~0.7`. 불일치: `0.0` |
| **fixture/mock** | 없음 |
| **엣지케이스** | 빈 문자열 → 0.0. 매우 긴 텍스트 (1000 단어) |

#### 5.1.11 latency_check

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_latency_within_threshold` / `test_should_return_0_when_latency_exceeds_threshold` |
| **입력/설정** | 임계값: 2000ms. 통과: latency_ms=1500. 실패: latency_ms=2500 |
| **기대 결과** | 통과: `1.0`. 실패: `0.0` |
| **fixture/mock** | 없음 (metadata에서 latency_ms 참조) |
| **엣지케이스** | 정확히 임계값과 같은 경우 (경계값: 2000ms) → 1.0 (이하 조건). 임계값 0ms. 음수 latency → 에러 |

#### 5.1.12 token_budget_check

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_tokens_within_budget` / `test_should_return_0_when_tokens_exceed_budget` |
| **입력/설정** | 예산: 100 tokens. 통과: output_tokens=80. 실패: output_tokens=120 |
| **기대 결과** | 통과: `1.0`. 실패: `0.0` |
| **fixture/mock** | 없음 (metadata에서 output_tokens 참조) |
| **엣지케이스** | 정확히 예산과 같은 경우 (100) → 1.0. 예산 0 → 항상 실패 (토큰 있으면). output_tokens 누락 → 에러 |

#### 5.1.13 cost_check

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_1_when_cost_within_threshold` / `test_should_return_0_when_cost_exceeds_threshold` |
| **입력/설정** | 임계값: $0.01. 통과: cost_usd=0.005. 실패: cost_usd=0.015 |
| **기대 결과** | 통과: `1.0`. 실패: `0.0` |
| **fixture/mock** | 없음 (metadata에서 cost_usd 참조) |
| **엣지케이스** | 정확히 임계값과 같은 경우 → 1.0. cost_usd=0.0 → 1.0. cost_usd 누락 → 에러 |

---

### 5.2 LLM-as-Judge

#### 5.2.1 정상 평가 (0-10 → 0.0-1.0 정규화)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_normalized_score_when_judge_evaluates_normally` |
| **입력/설정** | Judge 모델: `gpt-4o`, temperature: 0.0. input: `"이 서비스의 감성을 분석하세요"`, output: `"긍정"`, expected: `"긍정"` |
| **기대 결과** | Judge LLM 응답: `{"score": 9, "reasoning": "정확한 감성 분류"}`. 정규화된 스코어: `0.9`. reasoning이 Langfuse score의 comment에 기록 |
| **fixture/mock** | LiteLLM mock — Judge 호출에 대해 `'{"score": 9, "reasoning": "정확한 감성 분류"}'` 반환 |
| **엣지케이스** | score 0 → 0.0. score 10 → 1.0. score 5 → 0.5 |

#### 5.2.2 Judge 응답 파싱 실패 → 재시도 (총 3회)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_retry_when_judge_response_parse_fails` |
| **입력/설정** | Judge 응답 1회차: `"점수는 8점입니다"` (JSON 아님). 2회차: `"{"score": 8, "reasoning": "좋음"}"` (정상) |
| **기대 결과** | 총 2회 LLM 호출. 최종 스코어: `0.8`. 재시도 로그 기록 |
| **fixture/mock** | LiteLLM mock — `side_effect` 사용하여 1차 비정상, 2차 정상 응답 |
| **엣지케이스** | 1차 정상 → 재시도 없이 1회만 호출 |

#### 5.2.3 3회 모두 실패 → score=null

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_null_score_when_all_retries_exhausted` |
| **입력/설정** | Judge 응답이 3회 모두 파싱 불가한 형식 |
| **기대 결과** | 총 3회 LLM 호출 (초기 1회 + 재시도 2회). 최종 스코어: `null`. Langfuse에 `score=null` 기록 (또는 스코어 기록 건너뜀). 에러 로그에 3회 실패 기록 |
| **fixture/mock** | LiteLLM mock — 3회 모두 비정상 JSON 반환 |
| **엣지케이스** | Judge LLM 자체가 에러 (APIError) 발생 시에도 재시도 3회 후 null |

#### 5.2.4 Judge 모델 = 평가 대상 모델 → 경고

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_warn_when_judge_model_equals_target_model` |
| **입력/설정** | 평가 대상 모델: `gpt-4o`, Judge 모델: `gpt-4o` |
| **기대 결과** | 평가는 정상 실행. 응답에 경고 메시지 포함: "Judge 모델과 평가 대상 모델이 동일합니다. 편향 가능성이 있습니다." 또는 로그에 경고 기록 |
| **fixture/mock** | LiteLLM mock |
| **엣지케이스** | 다른 모델 사용 시 경고 없음 확인 |

#### 5.2.5 커스텀 Judge 프롬프트

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_substitute_variables_when_custom_judge_prompt_provided` |
| **입력/설정** | 커스텀 프롬프트: `"의료 용어 정확성을 평가하세요.\n입력: {input}\n출력: {output}\n기대: {expected}\nJSON: {\"score\": 0-10, \"reasoning\": \"...\"}"` |
| **기대 결과** | Judge LLM에 전달된 프롬프트에 `{input}`, `{output}`, `{expected}` 자리에 실제 값이 치환됨. 정상 스코어 반환 |
| **fixture/mock** | LiteLLM mock (전달된 messages 캡처 spy) |
| **엣지케이스** | 커스텀 프롬프트에 `{input}`, `{output}` 자리 표시자가 없는 경우 → 그대로 전달 (치환 없음). `{expected}`가 없는 경우 (expected 없는 평가) |

---

### 5.3 Custom Code Evaluator (Docker 샌드박스)

#### 5.3.1 정상 evaluate 실행 → score 반환

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_score_when_evaluate_function_executes_normally` |
| **입력/설정** | stdin JSON: `{"id": "item_001", "code": "def evaluate(output, expected, metadata):\n    return 1.0 if output == expected else 0.0", "output": "positive", "expected": "positive", "metadata": {}}` |
| **기대 결과** | stdout: `{"id": "item_001", "status": "success", "score": 1.0}` |
| **fixture/mock** | runner.py 직접 실행 (subprocess 또는 함수 호출) |
| **엣지케이스** | score 반환값이 정확히 0.0 또는 1.0인 경우 |

#### 5.3.2 타임아웃 (while True: pass)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_EVALUATOR_TIMEOUT_when_infinite_loop_in_evaluate` |
| **입력/설정** | code: `"def evaluate(output, expected, metadata):\n    while True: pass"` |
| **기대 결과** | stdout: `{"id": "...", "status": "error", "error_code": "EVALUATOR_TIMEOUT", "error_message": "Execution exceeded 5s timeout"}`. 5초 이내에 응답 반환 |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 없음 |

#### 5.3.3 모듈 레벨 무한루프 → EVALUATOR_TIMEOUT

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_EVALUATOR_TIMEOUT_when_infinite_loop_at_module_level` |
| **입력/설정** | code: `"while True: pass\ndef evaluate(output, expected, metadata):\n    return 1.0"` (evaluate 함수 정의 전에 무한루프) |
| **기대 결과** | `{"status": "error", "error_code": "EVALUATOR_TIMEOUT"}`. SIGALRM이 exec() 단계에서 발동 |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 없음 |

#### 5.3.4 허용 모듈 import 성공

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_succeed_when_importing_allowed_modules` |
| **입력/설정** | code에 `import json`, `import re`, `import math`, `import collections`, `import difflib`, `import statistics`, `import unicodedata` 각각 포함하는 7개 테스트 케이스 |
| **기대 결과** | 모두 `{"status": "success"}` 반환. 각 모듈의 기본 기능 사용 가능 (예: `json.loads()`, `re.match()`) |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | `from json import loads` 형태의 import |

#### 5.3.5 비허용 모듈 import → ImportError

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_importing_blocked_modules` |
| **입력/설정** | code: `"import os"`, `"import sys"`, `"import subprocess"`, `"import socket"`, `"import http"`, `"import urllib"` 각각 |
| **기대 결과** | 모두 `{"status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "Import of 'os' is not allowed in sandbox"}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | `__import__('os')` 직접 호출 시도 → 차단 (\_\_import\_\_ 가 _safe_import로 교체됨). `importlib` import 시도 → 차단 |

#### 5.3.6 \_\_builtins\_\_ 우회 시도

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_block_when_builtins_bypass_attempted` |
| **입력/설정** | code: `"json.__builtins__['__import__']('os')"` |
| **기대 결과** | `{"status": "error"}` — 허용 모듈의 `__builtins__`에서도 `__import__`가 `_safe_import`로 교체되어 있으므로 `ImportError` 발생 |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | `json.__builtins__` 직접 접근 시도. `type(json).__dict__` 접근 시도 |

#### 5.3.7 \_\_subclasses\_\_ 체인 공격 → Docker 격리

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_block_when_subclasses_chain_attack_attempted` |
| **입력/설정** | code: `"().__class__.__bases__[0].__subclasses__()"` 을 사용한 escape 시도 |
| **기대 결과** | `type` 빌트인이 차단되어 있으므로 에러 발생. Docker 컨테이너 레벨에서 네트워크/파일 시스템이 차단되어 있어 실질적 피해 불가 |
| **fixture/mock** | runner.py 직접 실행 (Docker 컨테이너 내) |
| **엣지케이스** | 다양한 escape 변형: `''.__class__.__mro__[1].__subclasses__()` 등 |

#### 5.3.8 signal.alarm(0) 무력화 시도

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_block_when_signal_alarm_disable_attempted` |
| **입력/설정** | code: `"import signal\nsignal.alarm(0)\ndef evaluate(output, expected, metadata):\n    while True: pass"` |
| **기대 결과** | `signal` 모듈은 허용 목록에 없으므로 `ImportError` 발생. `{"status": "error", "error_code": "EVALUATOR_ERROR"}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | `__import__('signal')` 시도 → 차단 |

#### 5.3.9 비정상 반환값 — 문자열

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_evaluate_returns_string` |
| **입력/설정** | code: `"def evaluate(output, expected, metadata):\n    return 'good'"` |
| **기대 결과** | `{"status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "evaluate() returned non-numeric value: 'good'"}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 없음 |

#### 5.3.10 비정상 반환값 — None

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_evaluate_returns_none` |
| **입력/설정** | code: `"def evaluate(output, expected, metadata):\n    return None"` |
| **기대 결과** | `{"status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "evaluate()가 None을 반환했습니다"}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 없음 |

#### 5.3.11 비정상 반환값 — 리스트

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_evaluate_returns_list` |
| **입력/설정** | code: `"def evaluate(output, expected, metadata):\n    return [0.5, 0.8]"` |
| **기대 결과** | `{"status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "evaluate() returned non-numeric value: [0.5, 0.8]"}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 없음 |

#### 5.3.12 비정상 반환값 — 범위 초과 (클램핑)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_clamp_score_when_evaluate_returns_out_of_range` |
| **입력/설정** | code: `"def evaluate(output, expected, metadata):\n    return 999.0"`. 두 번째: `"return -5.0"` |
| **기대 결과** | 999.0 → 클램핑 → `{"status": "success", "score": 1.0}`. -5.0 → 클램핑 → `{"status": "success", "score": 0.0}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | `float('inf')` → 에러 ("score가 유한하지 않습니다"). `float('nan')` → 에러 |

#### 5.3.13 evaluate 함수 없음

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_no_evaluate_function_defined` |
| **입력/설정** | code: `"def my_eval(output, expected, metadata):\n    return 1.0"` (이름이 `evaluate`가 아님) |
| **기대 결과** | `{"status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "Function 'evaluate' not defined in code"}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 빈 코드 문자열 → "평가 코드(code)가 비어있습니다" |

#### 5.3.14 구문 에러

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_code_has_syntax_error` |
| **입력/설정** | code: `"def evaluate(output, expected, metadata)\n    return 1.0"` (콜론 누락) |
| **기대 결과** | `{"status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "Code compilation failed: SyntaxError: ..."}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 들여쓰기 에러. 미닫힌 괄호 |

#### 5.3.15 잘못된 JSON 입력

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_error_when_stdin_contains_invalid_json` |
| **입력/설정** | stdin: `"not valid json\n"` |
| **기대 결과** | `{"id": "unknown", "status": "error", "error_code": "EVALUATOR_ERROR", "error_message": "Invalid JSON input: ..."}` |
| **fixture/mock** | runner.py 직접 실행 |
| **엣지케이스** | 빈 줄 → 건너뜀 (결과 없음). JSON에 id 필드 누락 → id="unknown" |

#### 5.3.16 연속 아이템 처리 (아이템 간 상태 격리)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_isolate_state_when_processing_multiple_items` |
| **입력/설정** | 아이템 1: code에 `"counter = 0\ndef evaluate(...):\n    global counter\n    counter += 1\n    return counter / 10"`. 아이템 2: 동일 코드 |
| **기대 결과** | 아이템 1: `score = 0.1` (counter=1). 아이템 2: `score = 0.1` (counter=1, 새 네임스페이스이므로 리셋). 각 아이템이 독립된 namespace에서 실행됨을 확인 |
| **fixture/mock** | runner.py 직접 실행 (2개 JSON 라인 순차 입력) |
| **엣지케이스** | 없음 |

#### 5.3.17 stdin EOF → 정상 종료

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_exit_gracefully_when_stdin_eof_received` |
| **입력/설정** | 3개 아이템 전송 후 stdin 닫기 (EOF) |
| **기대 결과** | 3개 결과 출력 후 프로세스 정상 종료 (exit code 0) |
| **fixture/mock** | subprocess로 runner.py 실행 |
| **엣지케이스** | 0개 아이템 전송 후 즉시 EOF → 정상 종료 |

#### 5.3.18 SIGTERM → 정상 종료

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_exit_gracefully_when_sigterm_received` |
| **입력/설정** | runner.py 실행 중 SIGTERM 전송 |
| **기대 결과** | 프로세스가 즉시 정상 종료 (exit code 0). 처리 중인 아이템이 있으면 해당 결과까지 출력 후 종료 |
| **fixture/mock** | subprocess로 runner.py 실행, `os.kill(pid, signal.SIGTERM)` |
| **엣지케이스** | 없음 |

#### 5.3.19 admin 권한 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_403_when_non_admin_runs_custom_evaluator` |
| **입력/설정** | evaluator `type: "custom_code"` 포함 실험. 1) admin JWT로 실행. 2) user JWT로 실행. 3) viewer JWT로 실행 |
| **기대 결과** | admin: 정상 실행. user: HTTP 403 `FORBIDDEN`. viewer: HTTP 403 `FORBIDDEN` |
| **fixture/mock** | JWT mock (role별), FastAPI TestClient |
| **엣지케이스** | JWT에 role 클레임이 누락된 경우 → 403 |

---

### 5.4 평가 파이프라인

#### 5.4.1 여러 evaluator 병렬 실행

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_run_all_evaluators_when_multiple_configured` |
| **입력/설정** | evaluators: `[exact_match, json_validity, latency_check]` (built-in 3개). output: `'{"label": "positive"}'`, expected: `'{"label": "positive"}'` |
| **기대 결과** | 3개 evaluator 모두 실행됨. `exact_match: 1.0`, `json_validity: 1.0`, `latency_check: 1.0` (latency < 임계값). 총 실행 시간이 순차 실행 대비 유사하거나 빠름 (built-in은 오버헤드 무시) |
| **fixture/mock** | 없음 (순수 함수) |
| **엣지케이스** | evaluator 10개 동시 실행 |

#### 5.4.2 일부 evaluator 실패해도 나머지 정상 실행

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_continue_other_evaluators_when_one_fails` |
| **입력/설정** | evaluators: `[exact_match, custom_code(에러 발생), llm_judge]`. custom_code의 코드가 구문 에러 포함 |
| **기대 결과** | `exact_match`: 정상 스코어 반환. `custom_code`: `score=null`, 에러 메시지 기록. `llm_judge`: 정상 스코어 반환. 실험 자체는 계속 진행 (중단되지 않음) |
| **fixture/mock** | LiteLLM mock (Judge용), Docker sandbox mock (에러 반환) |
| **엣지케이스** | 모든 evaluator 실패 → 아이템을 "평가 실패"로 표시, 실험 계속 |

#### 5.4.3 모든 스코어가 Langfuse에 기록되는지 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_record_all_scores_to_langfuse_when_evaluation_complete` |
| **입력/설정** | evaluators: `[exact_match, json_validity, accuracy_judge]`. 모두 정상 스코어 반환 |
| **기대 결과** | `langfuse.score()` 호출 3회. 각 호출의 인자: `trace_id`, `name` (evaluator 이름), `value` (스코어), `data_type="NUMERIC"`. `accuracy_judge`의 경우 `comment`에 Judge reasoning 포함 |
| **fixture/mock** | Langfuse client mock (spy) |
| **엣지케이스** | evaluator 실패 시 해당 스코어의 Langfuse 기록 건너뜀 (null score는 기록하지 않거나 별도 표시) |

---

## Phase 6: 분석 테스트

### 6.1 ClickHouse 쿼리

#### 6.1.1 실험 간 요약 비교 — 정상

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_comparison_summary_when_runs_exist` |
| **입력/설정** | `POST /api/v1/analysis/compare` — `run_names: ["run_a", "run_b"]`. ClickHouse에 사전 삽입된 traces, observations, scores 데이터 |
| **기대 결과** | 응답 `comparison` 배열에 2개 항목. 각 항목: `run_name`, `model`, `prompt_version`, `metrics.sample_count`, `metrics.avg_latency_ms`, `metrics.p50_latency_ms`, `metrics.p90_latency_ms`, `metrics.p99_latency_ms`, `metrics.total_cost_usd`, `metrics.avg_input_tokens`, `metrics.avg_output_tokens`, `scores` (evaluator별 `avg`, `min`, `max`, `stddev`) |
| **fixture/mock** | ClickHouse mock (사전 정의된 쿼리 결과 반환) 또는 테스트용 ClickHouse 인스턴스에 데이터 삽입 |
| **엣지케이스** | 없음 |

#### 6.1.2 실험 간 요약 비교 — 빈 결과

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_empty_comparison_when_runs_not_found` |
| **입력/설정** | `run_names: ["nonexistent_run_a", "nonexistent_run_b"]` |
| **기대 결과** | 응답 `comparison` 배열이 빈 배열 `[]`. HTTP 200 (에러가 아님) |
| **fixture/mock** | ClickHouse mock — 빈 결과 반환 |
| **엣지케이스** | run_names에 존재하는 1개 + 존재하지 않는 1개 → 존재하는 것만 결과에 포함 |

#### 6.1.3 아이템별 상세 비교 — 페이지네이션

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_paginate_items_when_page_params_provided` |
| **입력/설정** | `POST /api/v1/analysis/compare/items` — `page: 1, page_size: 10`, 총 50개 아이템 |
| **기대 결과** | `items` 배열에 10개 항목. `total: 50`. `page: 1`. 각 아이템: `dataset_item_id`, `input`, `expected_output`, `results` (run별 output/score/latency/cost), `score_variance` |
| **fixture/mock** | ClickHouse mock |
| **엣지케이스** | `page: 6` (마지막 페이지 초과) → 빈 items. `page_size: 0` → 에러. `page_size: 1000` → 최대 제한 적용 |

#### 6.1.4 아이템별 상세 비교 — 정렬

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_sort_items_when_sort_params_provided` |
| **입력/설정** | `sort_by: "score_variance"`, `sort_order: "desc"` |
| **기대 결과** | 반환된 items가 score_variance 내림차순으로 정렬됨. 첫 번째 아이템의 score_variance가 가장 큼 |
| **fixture/mock** | ClickHouse mock |
| **엣지케이스** | `sort_order: "asc"`. 유효하지 않은 `sort_by` 필드 → 에러 또는 기본 정렬 |

#### 6.1.5 스코어 분포 (히스토그램 bins)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_return_histogram_bins_when_distribution_requested` |
| **입력/설정** | `GET /api/v1/analysis/scores/distribution` — `run_name`, `score_name: "exact_match"`, `bins: 10` |
| **기대 결과** | `distribution` 배열에 10개 항목. 각 항목: `bin_start`, `bin_end`, `count`. bin 범위가 0.0~1.0을 균등 분할 (0.0-0.1, 0.1-0.2, ..., 0.9-1.0). `statistics`: `mean`, `median`, `stddev`, `min`, `max` |
| **fixture/mock** | ClickHouse mock |
| **엣지케이스** | `bins: 1` → 전체 범위 하나의 bin. `bins: 100` → 100개 bin. 스코어가 모두 동일한 경우 (분산 0) |

#### 6.1.6 파라미터화 쿼리 검증 (SQL injection 방지)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_prevent_sql_injection_when_malicious_input_provided` |
| **입력/설정** | `run_names: ["run_a'; DROP TABLE traces; --"]`, `project_id: "'; DROP TABLE traces; --"` |
| **기대 결과** | 쿼리가 정상 실행됨 (악의적 입력이 파라미터로 바인딩되어 SQL 문법에 영향 없음). 결과는 빈 배열 (해당 이름의 run이 없으므로). 테이블 삭제 발생하지 않음 |
| **fixture/mock** | ClickHouse (실제 또는 mock, 쿼리 캡처 spy) |
| **엣지케이스** | 유니코드 injection 시도. 백슬래시 escape 시도. 매우 긴 파라미터 (10KB 문자열) |

#### 6.1.7 읽기 전용 계정 검증

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_reject_write_operations_when_readonly_account_used` |
| **입력/설정** | `labs_readonly` 계정으로 ClickHouse 연결 후 `INSERT INTO traces VALUES (...)` 시도 |
| **기대 결과** | ClickHouse 에러 발생 (권한 부족). SELECT 쿼리는 정상 동작 |
| **fixture/mock** | ClickHouse (실제 인스턴스, `labs_readonly` 계정) |
| **엣지케이스** | `DROP TABLE` 시도 → 실패. `CREATE TABLE` 시도 → 실패. `ALTER TABLE` 시도 → 실패 |

---

## Phase 7: Frontend 테스트

### 7.1 컴포넌트 단위 테스트 (vitest)

#### 7.1.1 ScoreBadge: 0.0~0.3 (낮은 스코어)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_show_rose_color_when_score_below_03` |
| **입력/설정** | `<ScoreBadge score={0.2} />` |
| **기대 결과** | 배지 배경색: `rose-900`. 텍스트 색: `rose-300`. 표시 텍스트: `"0.20"`. aria-label에 스코어 수치 포함 |
| **fixture/mock** | vitest + @testing-library/react |
| **엣지케이스** | `score=0.0` → rose 색상, `"0.00"` 표시. `score=0.3` → 경계값 (rose 또는 amber, 설계 기준에 따름) |

#### 7.1.2 ScoreBadge: 0.3~0.7 (중간 스코어)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_show_amber_color_when_score_between_03_and_07` |
| **입력/설정** | `<ScoreBadge score={0.5} />` |
| **기대 결과** | 배지 배경색: `amber-900`. 텍스트 색: `amber-300`. 표시 텍스트: `"0.50"` |
| **fixture/mock** | vitest + @testing-library/react |
| **엣지케이스** | `score=0.31`, `score=0.69` (경계 근처) |

#### 7.1.3 ScoreBadge: 0.7~1.0 (높은 스코어)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_show_emerald_color_when_score_above_07` |
| **입력/설정** | `<ScoreBadge score={0.92} />` |
| **기대 결과** | 배지 배경색: `emerald-900`. 텍스트 색: `emerald-300`. 표시 텍스트: `"0.92"` |
| **fixture/mock** | vitest + @testing-library/react |
| **엣지케이스** | `score=1.0` → `"1.00"`, emerald 색상. `score=null` → "N/A" 표시, neutral 색상. `score=undefined` → "N/A" 표시 |

#### 7.1.4 ModelSelector: 프로바이더 그룹핑

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_group_models_by_provider_when_rendered` |
| **입력/설정** | models: `[{id: "gpt-4o", provider: "azure"}, {id: "gemini-2.5-pro", provider: "google"}, {id: "claude-4.5-sonnet", provider: "anthropic"}]` |
| **기대 결과** | 드롭다운에 프로바이더별 그룹 헤더 표시: "Azure OpenAI", "Google Gemini", "Anthropic". 각 그룹 하위에 해당 프로바이더의 모델 나열 |
| **fixture/mock** | vitest + @testing-library/react, models 데이터 prop |
| **엣지케이스** | 프로바이더 1개만 있는 경우 → 그룹 헤더 표시. 빈 models 배열 → "사용 가능한 모델이 없습니다" 표시 |

#### 7.1.5 ModelSelector: 검색

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_filter_models_when_search_query_entered` |
| **입력/설정** | 검색어: `"gpt"` |
| **기대 결과** | `"gpt-4o"`, `"gpt-4.1"` 등 GPT 모델만 표시. `"gemini"`, `"claude"` 모델은 숨겨짐. 검색어 삭제 시 전체 목록 복원 |
| **fixture/mock** | vitest + @testing-library/react, userEvent |
| **엣지케이스** | 대소문자 무관 검색 (`"GPT"` → GPT 모델 표시). 매칭 결과 0건 → "검색 결과 없음" 표시. 프로바이더 이름으로 검색 (`"azure"`) → 해당 프로바이더 모델 표시 |

#### 7.1.6 PromptEditor: 변수 감지 ({{var}})

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_detect_variables_when_template_contains_placeholders` |
| **입력/설정** | 프롬프트 텍스트: `"입력: {{input_text}}\n규칙: {{rules}}\n포맷: {{output_format}}"` |
| **기대 결과** | 감지된 변수 목록: `["input_text", "rules", "output_format"]`. 각 변수에 대한 입력 폼이 자동 생성됨. 변수 이름이 폼 라벨에 표시 |
| **fixture/mock** | vitest + @testing-library/react |
| **엣지케이스** | 변수 없는 프롬프트 → 폼 없음. 중복 변수 (`{{var}}...{{var}}`) → 폼 1개만 생성. 잘못된 형식 (`{var}`, `{{ var }}`) → 설계에 따라 감지 여부 결정. 빈 변수 이름 `{{}}` → 무시 |

#### 7.1.7 ExperimentProgress: 상태별 UI

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_display_correct_ui_when_experiment_status_changes` |
| **입력/설정** | 5가지 상태 각각: `running`, `paused`, `completed`, `failed`, `cancelled` |
| **기대 결과** | `running`: amber dot + pulse 애니메이션 + 진행률 바 + "45/100 아이템 완료" 텍스트. `paused`: amber dot (pulse 없음) + "일시정지" 텍스트 + "재개" 버튼. `completed`: emerald dot + "완료" 텍스트 + 최종 통계 요약. `failed`: rose dot + "실패" 텍스트 + 에러 메시지 + "재시도" 버튼. `cancelled`: zinc dot + "취소됨" 텍스트 |
| **fixture/mock** | vitest + @testing-library/react |
| **엣지케이스** | progress 데이터가 null인 경우 → 로딩 스켈레톤 표시 |

---

### 7.2 E2E 테스트 (Playwright)

#### 7.2.1 로그인 → 프로젝트 선택 → 단일 테스트 → 결과 확인

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_complete_single_test_flow_when_user_executes_test` |
| **입력/설정** | 1) Auth callback URL로 JWT 전달하여 로그인. 2) 프로젝트 드롭다운에서 테스트 프로젝트 선택. 3) 프롬프트 에디터에 `"{{input_text}}의 감성을 분석하세요"` 입력. 4) 변수 `input_text`에 `"좋은 서비스입니다"` 입력. 5) 모델 `gpt-4o` 선택. 6) 실행 버튼 클릭 |
| **기대 결과** | 스트리밍 응답이 결과 패널에 실시간 표시. 응답 완료 후 메타데이터 (지연시간, 토큰 수, 비용) 표시. trace_id 존재 확인 |
| **fixture/mock** | Playwright browser context. Backend API mock 또는 실제 서버 |
| **엣지케이스** | 네트워크 지연 시 로딩 스피너 표시 확인 |

#### 7.2.2 데이터셋 업로드 → 배치 실험 → 결과 비교

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_complete_batch_experiment_flow_when_user_uploads_and_runs` |
| **입력/설정** | 1) 데이터셋 페이지 → 업로드 버튼 클릭. 2) CSV 파일 드래그앤드롭 (5행 테스트 데이터). 3) 컬럼 매핑 설정 (input_columns, output_column). 4) 미리보기 확인 → 업로드 완료. 5) 배치 실험 생성 → 프롬프트, 모델, evaluator 선택. 6) 실행 → 진행률 확인 → 완료. 7) 결과 비교 페이지 이동 → Run 선택 → 비교 차트 확인 |
| **기대 결과** | 데이터셋 업로드 완료 알림 표시. 실험 진행률 바가 0%에서 100%까지 증가. 결과 비교 페이지에 KPI 카드 (Best Score, Fastest, Cheapest) 표시. 아이템별 비교 테이블에 스코어와 색상 코딩 표시 |
| **fixture/mock** | Playwright. 테스트용 CSV fixture 파일. Backend mock 또는 실제 서버 |
| **엣지케이스** | 업로드 실패 시 에러 토스트 표시 확인 |

#### 7.2.3 키보드 단축키 (Cmd+Enter)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_execute_test_when_cmd_enter_pressed` |
| **입력/설정** | 단일 테스트 페이지에서 프롬프트 에디터에 포커스 후 `Cmd+Enter` (macOS) 또는 `Ctrl+Enter` (Windows/Linux) |
| **기대 결과** | 실행 버튼 클릭과 동일한 효과 — 테스트 실행 시작 |
| **fixture/mock** | Playwright |
| **엣지케이스** | 이미 실행 중일 때 `Cmd+Enter` → 중복 실행 방지 (버튼 비활성화 상태에서 단축키도 무시) |

#### 7.2.4 키보드 단축키 (Cmd+K)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_open_search_modal_when_cmd_k_pressed` |
| **입력/설정** | 아무 페이지에서 `Cmd+K` |
| **기대 결과** | 글로벌 검색 모달 열림. 검색어 입력 후 결과 표시 (프롬프트, 데이터셋, 실험). 결과 클릭 시 해당 페이지로 이동 |
| **fixture/mock** | Playwright. Backend search API mock |
| **엣지케이스** | `Esc` 키로 모달 닫기. 모달 열린 상태에서 `Cmd+K` 재입력 → 모달 닫기 (토글) |

#### 7.2.5 반응형 레이아웃 — Desktop (1440px)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_show_split_layout_when_viewport_is_desktop` |
| **입력/설정** | viewport: 1440x900 |
| **기대 결과** | Side Nav 표시 (아이콘 only, 56px). 단일 테스트: 좌우 분할 레이아웃 (설정 45%, 결과 55%). 비교 페이지: KPI 카드 3열, 차트/통계 6:4 비율 |
| **fixture/mock** | Playwright (`page.setViewportSize()`) |
| **엣지케이스** | 없음 |

#### 7.2.6 반응형 레이아웃 — Tablet (768px)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_show_stacked_layout_when_viewport_is_tablet` |
| **입력/설정** | viewport: 768x1024 |
| **기대 결과** | Side Nav 숨겨지고 햄버거 메뉴로 대체. 단일 테스트: 세로 스택 (설정 → 결과). 비교 페이지: KPI 카드 2열 또는 스크롤 가능 |
| **fixture/mock** | Playwright |
| **엣지케이스** | 없음 |

#### 7.2.7 반응형 레이아웃 — Mobile (375px)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_show_single_column_when_viewport_is_mobile` |
| **입력/설정** | viewport: 375x812 (iPhone X) |
| **기대 결과** | Side Nav 완전히 숨김, 하단 네비게이션 바로 대체 또는 햄버거 메뉴. 모든 콘텐츠 단일 컬럼. 터치 타겟 최소 44x44px. 수평 스크롤 없음 |
| **fixture/mock** | Playwright |
| **엣지케이스** | 가로 모드 (812x375) 테스트 |

#### 7.2.8 접근성 (axe-core 자동 검사)

| 항목 | 내용 |
|------|------|
| **테스트 이름** | `test_should_pass_wcag_aa_when_axe_core_scans_pages` |
| **입력/설정** | 주요 5개 페이지 각각에서 axe-core 실행: 1) 단일 테스트 페이지, 2) 배치 실험 목록 페이지, 3) 실험 생성 페이지, 4) 결과 비교 페이지, 5) 데이터셋 관리 페이지 |
| **기대 결과** | WCAG 2.1 AA 위반 0건. 구체적 검증: 모든 이미지에 alt 텍스트. 모든 폼 요소에 label 연결. 컬러 콘트라스트 비율 4.5:1 이상 (일반 텍스트), 3:1 이상 (대형 텍스트). 키보드 탐색 가능 (Tab 순서 논리적). ARIA role/속성 올바른 사용 |
| **fixture/mock** | Playwright + `@axe-core/playwright` |
| **엣지케이스** | 다크 모드에서의 콘트라스트 검증 (UI가 다크 모드 기본이므로 필수). 모달 열린 상태에서 접근성 검증. 동적 콘텐츠 (SSE 스트리밍 결과) 로딩 후 접근성 검증 |

---

## 공통 테스트 인프라

### Fixture 목록

| Fixture 이름 | 용도 | 사용 범위 |
|-------------|------|-----------|
| `fake_redis` | fakeredis 인스턴스, 테스트 간 격리 | Phase 4 (Redis 상태 관리) |
| `litellm_mock` | LiteLLM `acompletion()` mock | Phase 4, 5 (LLM 호출) |
| `langfuse_mock` | Langfuse SDK client mock (spy) | Phase 4, 5 (trace/score 기록) |
| `clickhouse_mock` | ClickHouse 쿼리 결과 mock | Phase 6 (분석 쿼리) |
| `jwt_token_factory` | role별 JWT 토큰 생성 (admin, user, viewer) | Phase 4, 5 (권한 검증) |
| `sample_dataset_items` | 테스트용 데이터셋 아이템 10개 | Phase 4 (배치 실험) |
| `sandbox_runner` | runner.py subprocess 래퍼 | Phase 5 (Custom Evaluator) |
| `sse_client` | SSE 스트림 파싱 헬퍼 | Phase 4 (스트리밍 응답) |
| `sample_csv_file` | 테스트용 CSV 파일 (5행) | Phase 7 (E2E 업로드) |
| `base64_test_image` | 1x1 PNG base64 인코딩 문자열 | Phase 4 (멀티모달) |

### Mock 전략

| 계층 | Mock 방식 | 사유 |
|------|-----------|------|
| LiteLLM | `unittest.mock.AsyncMock` | 외부 LLM 프로바이더 호출 차단, 결정적 응답 보장 |
| Langfuse SDK | `unittest.mock.MagicMock` (spy) | 호출 인자 검증, 부작용 없이 기록 확인 |
| Redis | `fakeredis.aioredis.FakeRedis` | 실제 Redis 없이 상태 관리 테스트, TTL 검증 시에만 실제 Redis 사용 |
| ClickHouse | `unittest.mock.MagicMock` | 쿼리 결과 사전 정의, SQL injection 방지 검증 시 쿼리 문자열 캡처 |
| Docker (sandbox) | subprocess 직접 실행 또는 `unittest.mock.patch` | runner.py 단위 테스트는 직접 실행, 통합 테스트는 Docker mock |
| JWT | `jose.jwt.encode()` 직접 생성 | role별 토큰 생성, 만료/무효 토큰 테스트 |

---

## 추가 엣지케이스 테스트

### EC.1 동시성 (Concurrency) 테스트 — 5개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_handle_concurrent_experiment_creation_when_multiple_requests` | 동일 프로젝트에서 동시에 실험 3개 생성 시 모두 고유 ID로 정상 생성 | mock_redis, LiteLLM mock |
| 2 | `test_should_not_corrupt_redis_state_when_concurrent_item_completions` | 동시에 10개 아이템 완료 이벤트 발생 시 completed_items 카운터 정확 | fakeredis 또는 실제 Redis |
| 3 | `test_should_handle_concurrent_pause_and_cancel_when_race_condition` | 동시에 pause와 cancel 요청 시 하나만 성공, 상태 일관성 유지 | Redis (실제) |
| 4 | `test_should_isolate_sse_streams_when_multiple_clients_connected` | 동일 실험에 SSE 클라이언트 3개 연결 시 각각 독립적으로 이벤트 수신 | AsyncClient 3개 |
| 5 | `test_should_handle_concurrent_dataset_uploads_when_same_project` | 동일 프로젝트에 동시에 CSV 2개 업로드 시 모두 정상 처리 | mock_langfuse |

### EC.2 데이터 경계값 (Data Boundaries) 테스트 — 9개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_handle_empty_string_output_when_llm_returns_nothing` | LLM 응답이 빈 문자열일 때 evaluator 정상 처리 | LiteLLM mock |
| 2 | `test_should_handle_100kb_prompt_when_large_template_provided` | 100KB 크기 프롬프트 텍스트 처리 시 메모리/성능 문제 없음 | Langfuse mock |
| 3 | `test_should_handle_unicode_emoji_when_variable_contains_special_chars` | 변수값에 이모지, 한자, 아랍어 등 포함 시 정상 치환 | 없음 |
| 4 | `test_should_handle_null_expected_output_when_dataset_item_has_no_expected` | expected_output이 null인 데이터셋 아이템으로 실험 실행 시 평가 건너뜀 또는 정상 처리 | mock_langfuse |
| 5 | `test_should_handle_zero_cost_when_llm_returns_zero_usage` | usage 토큰이 모두 0인 경우 비용 계산 정상 | LiteLLM mock |
| 6 | `test_should_handle_max_int_tokens_when_usage_extremely_large` | input_tokens = 2^31-1 등 극단적 값일 때 오버플로우 없음 | LiteLLM mock |
| 7 | `test_should_handle_negative_latency_when_clock_skew_occurs` | latency_ms가 음수로 계산되는 경우 0으로 보정 | 없음 |
| 8 | `test_should_handle_exactly_50mb_file_when_boundary_size_uploaded` | 정확히 50MB 파일 업로드 시 허용 (경계값) | jwt_user |
| 9 | `test_should_handle_exactly_10000_rows_when_boundary_count_uploaded` | 정확히 10,000행 CSV 업로드 시 허용 (경계값) | mock_langfuse, jwt_user |

### EC.3 네트워크 장애 (Network Failures) 테스트 — 6개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_return_502_when_litellm_connection_refused` | LiteLLM Proxy 연결 거부 시 적절한 에러 코드 반환 | LiteLLM mock (ConnectionRefusedError) |
| 2 | `test_should_continue_experiment_when_langfuse_intermittent_failure` | Langfuse가 5회 중 2회만 성공할 때 실험 계속 진행, 성공 건만 기록 | Langfuse mock (간헐적 에러) |
| 3 | `test_should_return_503_when_redis_connection_lost_mid_experiment` | 실험 중 Redis 연결 끊김 시 적절한 에러 반환 | mock_redis (연결 실패 시뮬레이션) |
| 4 | `test_should_timeout_gracefully_when_clickhouse_query_hangs` | ClickHouse 쿼리가 30초 이상 응답 없을 때 타임아웃 처리 | ClickHouse mock (지연) |
| 5 | `test_should_handle_sse_client_disconnect_when_browser_closed` | SSE 클라이언트가 중간에 연결 종료 시 서버 자원 정리 | AsyncClient |
| 6 | `test_should_retry_langfuse_flush_when_first_attempt_fails` | flush 실패 시 재시도 로직 동작 확인 | Langfuse mock (첫 flush 실패) |

### EC.4 리소스 제한 (Resource Limits) 테스트 — 4개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_enforce_max_concurrent_experiments_when_limit_reached` | 동시 실험 수 제한에 도달 시 새 실험 생성 거부 | mock_redis |
| 2 | `test_should_enforce_evaluator_5s_timeout_when_slow_code_provided` | 4.9초에 완료되는 코드는 성공, 5.1초 코드는 EVALUATOR_TIMEOUT | runner.py |
| 3 | `test_should_limit_sse_reconnection_when_max_retries_exceeded` | SSE 재연결 최대 횟수 초과 시 연결 종료 | AsyncClient |
| 4 | `test_should_limit_judge_prompt_length_when_input_extremely_large` | Judge 프롬프트가 모델 max_tokens를 초과할 때 적절한 에러 반환 | LiteLLM mock |

### EC.5 인증 엣지케이스 (Auth Edge Cases) 테스트 — 4개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_return_401_when_jwt_nbf_is_future` | `nbf` (Not Before) 클레임이 미래 시간인 JWT 사용 시 거부 | create_test_jwt |
| 2 | `test_should_return_401_when_jwt_has_duplicate_claims` | JWT payload에 중복된 claim이 있는 경우 처리 | create_test_jwt |
| 3 | `test_should_handle_role_change_when_jwt_refreshed_mid_session` | 세션 중 JWT가 갱신되어 role이 변경된 경우 새 role 적용 | create_test_jwt |
| 4 | `test_should_return_401_when_jwt_algorithm_none_attack` | `alg: "none"` 공격 시도 시 거부 | 직접 JWT 조작 |

### EC.6 파일 업로드 (File Upload) 엣지케이스 — 8개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_handle_bom_when_csv_has_utf8_bom` | UTF-8 BOM이 포함된 CSV 파일 정상 파싱 | jwt_user |
| 2 | `test_should_handle_crlf_when_csv_has_windows_line_endings` | Windows 줄바꿈(CRLF) CSV 파일 정상 파싱 | jwt_user |
| 3 | `test_should_handle_quoted_fields_when_csv_has_commas_in_data` | 데이터에 쉼표가 포함된 CSV (따옴표 감싸기) 정상 처리 | jwt_user |
| 4 | `test_should_handle_empty_columns_when_csv_has_trailing_commas` | 행 끝에 빈 컬럼이 있는 CSV 정상 처리 | jwt_user |
| 5 | `test_should_reject_when_file_extension_spoofed` | .csv 확장자이지만 내용이 바이너리인 파일 거부 | jwt_user |
| 6 | `test_should_handle_large_single_cell_when_csv_cell_exceeds_1mb` | 단일 셀이 1MB를 초과하는 CSV 처리 또는 적절한 에러 | jwt_user |
| 7 | `test_should_handle_duplicate_column_names_when_csv_headers_repeated` | 동일한 컬럼명이 2개인 CSV 파일 처리 | jwt_user |
| 8 | `test_should_handle_nested_json_when_jsonl_items_deeply_nested` | depth 20 이상의 중첩 JSON이 포함된 JSONL 파일 처리 | mock_langfuse, jwt_user |

### EC.7 실험 라이프사이클 (Experiment Lifecycle) 엣지케이스 — 7개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_handle_rapid_pause_resume_when_called_in_quick_succession` | 100ms 간격으로 pause/resume 반복 호출 시 상태 일관성 | Redis (실제) |
| 2 | `test_should_complete_in_progress_items_when_pause_called` | pause 호출 시 진행 중인 LLM 호출은 완료 후 중단 | LiteLLM mock (지연) |
| 3 | `test_should_preserve_progress_when_experiment_resumed_after_pause` | pause 후 resume 시 이전까지 완료된 아이템 상태 유지 | mock_redis |
| 4 | `test_should_handle_ttl_expiry_during_experiment_when_24h_exceeded` | 24시간 초과 실행 실험의 TTL 갱신 확인 | mock_redis |
| 5 | `test_should_handle_duplicate_retry_when_retry_called_twice` | retry-failed 2회 연속 호출 시 중복 실행 방지 | mock_redis |
| 6 | `test_should_handle_experiment_with_single_item_when_dataset_has_one` | 데이터셋 아이템 1개인 실험의 전체 플로우 정상 | mock_langfuse, LiteLLM mock |
| 7 | `test_should_handle_all_evaluators_null_when_every_evaluation_fails` | 모든 evaluator가 null 반환 시 아이템 상태와 avg_score 처리 | mock evaluator |

### EC.8 Custom Evaluator 추가 엣지케이스 — 9개

| # | 테스트 이름 | 설명 | fixture/mock |
|---|------------|------|-------------|
| 1 | `test_should_handle_evaluate_with_extra_args_when_function_signature_wrong` | `def evaluate(output):` (인자 부족) 호출 시 적절한 에러 | runner.py |
| 2 | `test_should_handle_memory_bomb_when_code_allocates_excessive_memory` | `"a" * (10**9)` 등 대량 메모리 할당 시 OOM 처리 | runner.py (Docker) |
| 3 | `test_should_handle_fork_bomb_when_code_attempts_process_fork` | `os.fork()` 시도 시 차단 (os 모듈 import 불가) | runner.py |
| 4 | `test_should_handle_file_write_when_code_attempts_filesystem_access` | `open("/etc/passwd", "r")` 시도 시 차단 | runner.py |
| 5 | `test_should_handle_recursive_evaluate_when_code_calls_itself` | evaluate 함수 내에서 재귀 호출 시 RecursionError 처리 | runner.py |
| 6 | `test_should_handle_eval_builtin_when_code_uses_eval` | `eval("__import__('os')")` 시도 시 차단 | runner.py |
| 7 | `test_should_handle_exec_builtin_when_code_uses_exec` | `exec("import os")` 시도 시 차단 | runner.py |
| 8 | `test_should_handle_float_precision_when_score_has_many_decimals` | `return 0.33333333333333337` 등 부동소수점 값 정상 처리 | runner.py |
| 9 | `test_should_handle_exception_in_evaluate_when_runtime_error_occurs` | `def evaluate(...): return 1/0` 시 ZeroDivisionError 적절히 래핑 | runner.py |
