# TTFT (기획서 6.1.2)

기존 실행 명령으로 TPS 조건을 마친 뒤 TTFT를 측정합니다. `ttft.csv`, `meta.json`에 저장하고 `./summary`로 확인합니다. 요청에 `ttft: false`를 넣으면 생략합니다.

## 측정 구간

TTFT는 llama.cpp의 내부 timing 로그에서 **입력 평가 시간 + 첫 토큰 선택 시간**을 합한 ms 값입니다. CPU 전용 도구가 입력을 한 번 평가하고 greedy sampler로 첫 토큰 ID 하나를 얻은 직후 timing을 출력합니다. 두 번째 토큰을 계산하는 decode는 실행하지 않습니다. 일반적인 입력에서는 Prefill 계산 결과의 logits에서 첫 토큰을 선택하므로 decode 평균 시간을 별도로 더하지 않습니다. 입력이 1토큰이면 엔진이 입력 평가를 `eval time`으로 분류하므로 이 시간도 입력 평가에 포함합니다.

`llama_perf_context_print`와 `llama_perf_sampler_print` 원문을 반복별 로그에 남깁니다. 같은 카운터를 소수점 반올림 없이 `PP8_TTFT` 로그로 함께 저장하고 이 값을 CSV로 옮깁니다. `prefill_ms = prompt_eval_ms + eval_ms`, `first_token_ms = sampler time`, `ttft_ms = prefill_ms + first_token_ms`입니다. sampler time은 엔진 sampler 체인의 시간이며 전체 출력 문자열 변환이나 HTTP 전달 시간이 아닙니다.

모델 로드, 프로세스 시작, 컨텍스트 생성, 토큰 준비는 제외합니다. 이는 6.1.2의 로딩 제외 선택에 해당합니다. 입력 평가 직전부터 첫 토큰 ID를 얻을 때까지의 wall time은 진단용 `first_token_wall_ms`로 별도 기록하며 TTFT 평균에 섞지 않습니다. HTTP 서버는 사용하지 않습니다.

## 반복과 캐시

기획서 6.1.4에 따라 조건별 워밍업 1회를 버리고 `repeat`회 기록합니다. 워밍업과 각 반복은 모두 새 프로세스에서 모델과 컨텍스트를 새로 만듭니다. 각 기록은 해당 프로세스의 첫 평가이며 기존 KV 캐시나 앞선 요청을 재사용하지 않습니다. 워밍업은 별도 프로세스이므로 기록 대상 모델의 첫 평가를 미리 실행하지 않습니다.

OS 파일 캐시는 비우거나 강제로 데우지 않습니다(`cache=uncontrolled`). 앞선 TPS와 워밍업이 파일 캐시에 영향을 줄 수 있습니다. mmap 읽기가 첫 평가로 지연되면 엔진 timing에 포함될 수 있지만, 이 결과를 콜드 디스크 TTFT나 순수 디스크 읽기 시간으로 해석하면 안 됩니다. 파일 캐시를 비우는 로딩 실험과는 구분합니다. 기본 모델 읽기 방식은 엔진 기본값 `auto`입니다. 자원 제한이나 읽기 방식 조합은 별도 PR 통합 범위입니다.

## 입력과 조건

기획서 4.3, 6.1.1의 llama-bench 방식에 맞춰 `rand() % vocab_size`와 필요시 BOS를 사용한 합성 정수 토큰을 정확히 `prompt_tokens`개 만듭니다. 매 프로세스 `srand(1)`로 고정합니다. llama-bench의 난수 진행 상태와 완전히 같은 토큰열을 보장하지는 않습니다. 실제 문장이나 답변 품질을 평가하지 않습니다.

`threads`와 `prompt_tokens` 조합마다 측정하며 `gen_tokens`는 조건 수를 늘리지 않습니다. 생성은 항상 첫 토큰 하나입니다. `repeat`는 각 조건의 기록 횟수입니다. TTFT는 YAML 순서로 실행하며 기존 TPS의 `shuffle`, `seed`는 TTFT 순서를 바꾸지 않습니다. 논리 batch는 입력 길이, microbatch는 512, context는 입력 길이+32, CPU 스레드는 배치와 생성에 동일 적용합니다. TPS와 batch 설정까지 동일하다고 간주하지 않습니다.

## 환경과 실패

현재는 Linux의 llama.cpp `f74e2fa6a`(b11706) CPU 빌드와 그 옆의 `libllama.so`, `g++`가 필요합니다. 최초 실행 시 해당 커밋의 MIT 라이선스 헤더를 다운로드하고 개인 `work_dir/tools` 안에 도구를 빌드합니다. 이후 캐시를 사용합니다. 소스, 라이브러리, 헤더 SHA256을 메타데이터에 기록합니다. 다른 ABI는 추측해서 실행하지 않고 미지원으로 기록합니다.

`ttft_request_timeout_seconds`는 모델 로드부터 첫 토큰 측정까지 한 프로세스의 제한 시간입니다(기본 300초). 기존 `server_path`, `ttft_startup_timeout_seconds`는 더 이상 사용하지 않습니다. 이전 설정 파일에 남아 있어도 실행 명령을 바꿀 필요는 없습니다.

빌드나 설치 실패는 `unavailable`, 반복 중 실패는 `failed`, 제한 시간 초과는 `timeout`으로 남깁니다. 값은 빈칸이며 0ms로 채우지 않습니다. timeout과 Ctrl+C 시 자식 프로세스를 정리합니다. TTFT 실패는 기존 TPS 성공 여부와 별도입니다. `monitor.csv`도 기존 TPS 프로세스용이며 TTFT 자원 사용량은 포함하지 않습니다.

## 파일과 요약

- `ttft.csv`: 기존 열을 유지하고 측정 방법, Prefill, 첫 토큰 선택, wall time과 캐시·읽기 방식 열을 추가합니다.
- `ttft-t{threads}-p{prompt}-r{repetition}.log`: 반복별 엔진 timing 원문.
- `ttft-t{threads}-p{prompt}-warmup.log`: 평균에서 제외한 워밍업.
- `ttft-build.log`: 빌드 실패 확인용.
- `meta.json`: 측정 정의, 환경 해시, 성공 표본 평균·표본 표준편차·CV 및 실패 수.

`./summary`는 새 결과에 engine timing이라는 정의를 표시합니다. 이전 HTTP 방식 결과는 기존 정의로 표시하고 숫자를 재계산하거나 덮어쓰지 않습니다. 두 방식의 수치를 같은 정의의 반복 결과로 합치면 안 됩니다.
