# 준비된 모델의 TTFT

기존 명령은 그대로 사용합니다.

```bash
cd ~/p-project8/measurement
.venv/bin/python src/worker.py requests/baseline.yaml
./summary
```

모든 llama-bench TPS 조건이 끝난 다음 별도 llama-server를 띄웁니다.
입력 길이(`prompt_tokens`)와 스레드(`threads`) 조합마다 워밍업 요청 1회를 제외하고
`repeat`번 측정합니다. `gen_tokens`는 TTFT 조건 수에 영향을 주지 않습니다.
서버는 스레드 조건마다 시작하며 127.0.0.1 임시 포트에서 CPU 전용·단일 슬롯으로 실행합니다.
모델이 준비된 상태의 측정이며 파일 캐시를 비우지 않습니다. TPS와 서버는 겹쳐 실행하지 않습니다.
다른 사용자의 실험과 겹치지 않도록 기존 시간 조율은 계속 필요합니다.
TTFT 조건은 YAML에 적힌 스레드·입력 길이 순서로 실행하며 중복 값은 한 번만 사용합니다.
기존 `shuffle`/`seed`는 TPS 조건에만 적용되고 TTFT 순서는 섞지 않습니다.

## 값의 의미

- `ttft_ms`: 요청 전송 직전부터 첫 생성 토큰 ID가 들어 있는 SSE 응답을 수신할 때까지의 monotonic 경과 시간입니다.
- 모델 로딩·서버 준비·워밍업·별도 토큰화는 제외합니다. 로컬 HTTP, 서버 처리, 입력 처리 및 샘플링·전송 비용을 포함합니다. 엔진 내부의 순수 계산 시간과 다릅니다.
- 빈 이벤트나 ping은 첫 토큰으로 세지 않습니다. 텍스트가 빈 문자열이어도 생성 토큰 ID가 있으면 셉니다.
- `/tokenize`로 `Hello world.`를 특수 토큰 포함 토큰화한 뒤 앞 토큰을 유지하고 나머지를 반복해 지정된 길이의 정수 배열을 만듭니다. 정확히 N개 토큰이며 실제 대화문이나 llama-bench의 무작위 입력과 같지 않습니다.
- 매 요청 `cache_prompt: false`를 보내고 최종 `timings.prompt_n`이 입력 길이와 같은지 확인합니다. b11706의 `tokens_cached`는 요청 후 슬롯 상태를 나타낼 수 있어 재사용 여부 검증에 사용하지 않습니다.
- 생성은 greedy·seed 1·`ignore_eos: true`·1토큰으로 고정합니다. 출력 길이의 영향이나 응답 품질을 평가하는 실험은 아닙니다.
- 반복은 준비된 서버에 보낸 독립 요청들입니다. 모델을 매번 재로딩한 독립 프로세스 반복이 아닙니다.

## 결과 파일

`ttft.csv`는 request_id, threads, prompt_tokens, repetition, status, ttft_ms,
first_token_id, evaluated_tokens, error를 저장합니다.
성공은 `ok`, 요청 실패는 `failed`, 서버 미설치·시작 실패 등은 `unavailable`입니다.
실패 시간은 0으로 채우지 않습니다. 실패하면 해당 서버를 중단하고 남은 조건은 미측정으로 기록합니다.

`meta.json`의 `ttft_measurement`와 `./summary`에는 성공한 표본의 평균·표본 표준편차·CV를 표시합니다.
표본 1개면 표준편차와 CV는 미기록입니다. 원시 값은 CSV, 서버 명령과 로그는 `ttft-server-t*.log`에 남습니다.
TTFT 성공/실패는 기존 TPS `ok/failed`와 별도 집계하므로 큐의 done 표시만으로 TTFT 성공을 판단하지 마세요.
기존 `monitor.csv`는 llama-bench 관측이며 TTFT 서버 자원 사용량은 포함하지 않습니다.
과거 결과에 TTFT가 없으면 요약에서 미기록으로 표시합니다.

## 설정

`settings.yaml`에서 `server_path`를 생략하면 `bench_path`와 같은 폴더의 `llama-server`를 찾습니다.
같은 llama.cpp 빌드를 사용하세요. 현재 검증 버전은 b11706 / f74e2fa6a입니다.
`ttft_startup_timeout_seconds`, `ttft_request_timeout_seconds`는 각각 기본 300초입니다.
서버는 정상 종료·실패·Ctrl+C 시 정리합니다.

기본적으로 TTFT도 실행되어 전체 소요 시간이 늘어납니다. TPS만 재려면 **요청 YAML**에 추가합니다.

```yaml
ttft: false
```

비활성화 시 CSV는 헤더만 생성됩니다. 추가 Python 패키지는 필요 없습니다.
공용 워커도 같은 run_request를 사용하므로 업데이트된 코드로 동작할 때 같은 기능을 사용합니다.
운영 중인 이전 서비스 코드는 자동 변경되지 않습니다.

`exp-run`, `exp-collect`, `exp-report`는 현재 저장소에 없습니다.
추가 파일을 무시하는 수집기라면 기존 TPS 수집은 유지되지만 TTFT 표·보고서 반영은 별도 수정이 필요합니다.
스킬의 엄격한 YAML 검증기가 있다면 `ttft` 키도 허용해야 합니다. 스킬 호환은 아직 검증하지 않았습니다.

참고: [동일 llama.cpp 버전 서버 API](https://github.com/ggml-org/llama.cpp/blob/f74e2fa6a/tools/server/README.md)
