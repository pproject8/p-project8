# 준비 시간과 모델 로딩 시간

기존 실행 명령과 결과 저장 위치를 사용합니다.

```bash
cd ~/p-project8/measurement
.venv/bin/python src/worker.py requests/baseline.yaml
./summary
```

| 파일/지표 | 측정 범위 |
|---|---|
| loading.csv / startup_to_warmup_s | 기존 llama-bench 실행 직전부터 워밍업 직전 메시지를 관찰할 때까지. 백엔드·모델·컨텍스트·스레드풀 준비 포함 |
| model_loading.csv / model_load_s | 기획서 6.1.2의 time 방식. 별도 로딩 전용 프로세스의 시작부터 종료까지 경과시간. 프로세스 시작·백엔드 초기화·모델 로드·모델 해제와 종료 정리 포함. 컨텍스트·토큰화·워밍업·추론 제외 |
| model_loading.csv / model_load_api_s | 보조 지표. 같은 프로세스 안에서 llama_model_load_from_file 호출 직전부터 반환 직후까지. 이전 PR의 주 지표였던 함수 시간 |

GNU `/usr/bin/time`의 `%e`(elapsed seconds)를 주 지표로 사용합니다. 이 빌드의 출력 해상도는 0.01초이므로 더 세밀한 시간 차이를 해석하지 않습니다. `model-loading.log`에 반복별 명령, 엔진 로그, 원본 `PP8_MODEL_LOAD_ELAPSED_S` 출력을 남깁니다. 함수 시간은 보조 지표로만 저장하며 주 지표 평균에 섞지 않습니다.

이 구간은 로딩만 수행하는 프로그램의 실행 시간이며 순수 디스크 읽기 시간은 아닙니다. mmap 사용 시 페이지 읽기가 이후 추론으로 지연될 수 있어 모델 전체를 RAM에 적재하는 시간으로 해석하면 안 됩니다. 계획서 6.1.2에 따라 TTFT와 함께 확인해야 합니다. 별도 프로세스에서 잰 로딩 값과 TTFT를 단순히 더해 하나의 요청 지연으로 만들지는 않습니다.

`llama-completion`의 최종 load time 로그는 첫 평가에서 갱신되어 추론 시간이 섞일 수 있어 사용하지 않습니다. 로딩 전용 도구는 모델 로드 후 즉시 모델을 해제하고 종료하며 컨텍스트나 첫 평가를 실행하지 않습니다.

## 독립 반복과 캐시

모든 TPS 조건이 끝난 뒤 별도 프로세스를 반복 실행합니다. 기본은 요청의 `repeat`회이며 `loading_repeat`로 별도 지정할 수 있습니다. 기획서 6.1.4에 따라 로딩 반복에서는 워밍업 표본을 버리지 않습니다. 모델/읽기 방식별로 반복하며 입력 길이·CPU 추론 스레드별 같은 로딩을 중복 측정하지 않습니다.

기본은 기획서 6.1.2의 `cold`입니다. **기존 TPS 구간의 캐시를 통제한다는 뜻은 아닙니다.** 각 별도 로딩 프로세스 직전에 관리자가 등록한 명령을 실행합니다.

```bash
sudo -n /usr/local/bin/pp8-drop-caches
```

자동 설치나 sudoers 변경은 하지 않습니다. 요청에 `cache: cold`를 명시했는데 명령이 없으면 측정을 시작하기 전에 검증 오류로 끝냅니다. 생략해서 기본값 cold가 쓰였거나 권한이 없으면 실패로 기록하고 warm으로 대체하지 않습니다. TPS 결과는 보존합니다. `warm` 옵션은 계획서의 콜드 로딩과 구분되는 탐색 조건이며 매 표본 직전에 모델 파일을 읽어 캐시를 데웁니다. DIO는 이 캐시를 우회할 수 있습니다.

캐시를 비우기 전 `llama-bench`, `llama-server`, `model-load`, **`ttft-engine`**의 실행 여부를 확인합니다. 하나라도 실행 중이거나 검사에 실패하면 캐시를 비우지 않습니다. 이름 검사와 캐시 비우기 사이에 새 측정이 시작되는 경쟁 상태까지 막는 공통 잠금은 아니므로 #26 전까지 실행 시간 조율이 필요합니다. 캐시 비우기는 노트북 전체에 영향을 줍니다.

```yaml
load_mode: [mmap, none]  # 생략 시 auto, TPS 명령에도 -lm으로 전달
loading_repeat: 5       # 생략 시 repeat
cache: cold            # cold / warm, 생략 시 cold
```

지원 값은 auto, none, mmap, mlock, mmap+mlock, dio입니다. mlock 실제 잠금 성공은 시스템 한도와 엔진 로그로 확인합니다. `load_mode`를 명시한 요청에만 results.csv에 해당 열을 추가합니다. 기존 요청의 TPS 열은 유지합니다. #42 초안의 warm/1 대신 사용자의 계획서 정합성 요청에 따라 cold/repeat가 기본입니다.

## 결과와 호환

model_loading.csv의 기존 열을 유지하고 `measurement_method`, `model_load_api_s`를 추가합니다. 주 지표 `model_load_s`의 정의는 `gnu_time_load_process_v1`로 명시합니다. 실패 시간은 빈칸이며 0초로 채우지 않습니다. timeout·Ctrl+C 시 time과 로딩 자식 프로세스를 함께 종료합니다.

로딩의 예상하지 못한 오류(예: 헤더 다운로드 중 IncompleteRead)도 TPS 완료 상태를 되돌리지 않습니다. meta.json의 model_loading에 실패 사유를 기록하고 finished를 남겨 ./summary로 기존 TPS를 조회할 수 있습니다. 이미 끝난 로딩 표본은 CSV와 진행 메타데이터에 보존하고 완료 표본만 집계합니다.

로딩 도중 Ctrl+C를 누르면 model_loading.status는 interrupted로 남고 완료된 TPS를 조회할 수 있습니다. 큐에서도 TPS 성공이 있는 요청은 done(일부 TPS 실패가 있으면 partial)으로 보존한 뒤 중단을 다시 전달해 다음 요청을 시작하지 않습니다. finished는 로딩 성공을 보장하지 않으므로 model_loading.status도 함께 확인합니다. TPS 도중 중단은 이 처리 범위가 아니며 기존대로 미완료입니다.

meta.json과 ./summary에는 주 지표의 평균·표본 표준편차·CV·성공/실패 수를 표시합니다. 한 표본이면 SD/CV는 미기록입니다. 이전 함수 시간 방식의 결과는 원래 정의로 표시하고 수정하지 않습니다. 새 프로세스 경과시간과 이전 함수 시간을 같은 반복으로 합치거나 직접 개선율을 계산하지 않습니다.

`model_file_gb_per_s`는 파일 크기/프로세스 경과시간의 비율이며 실제 디스크 읽기 대역폭이 아닙니다. TPS만 필요하면 요청에 `measure_loading: false`를 넣습니다. monitor.csv는 기존 TPS용이며 별도 로딩 프로세스의 자원 사용량을 포함하지 않습니다.

## 최초 도구 준비

Linux GNU time(`/usr/bin/time`), g++, bench_path 옆 libllama.so가 필요합니다. 최초 실행에서 ABI가 일치하는 llama.cpp f74e2fa6a의 헤더와 MIT LICENSE를 내려받아 개인 work_dir/tools에 도구를 컴파일합니다. 같은 소스·라이브러리면 재사용합니다. 소스·라이브러리 해시와 헤더 manifest를 저장합니다. 다른 엔진 ABI는 미지원 처리하고 도구 준비 실패 시 TPS를 보존합니다.

```yaml
# settings.yaml 선택 설정
drop_caches_path: /usr/local/bin/pp8-drop-caches
model_load_timeout_seconds: 300
```

TTFT/메모리 PR과는 독립이며 메모리 제한 요청은 무제한 도구를 실행하지 않도록 로딩을 비활성화합니다. 제한 적용과 TTFT 통합 순서, 외부 exp-* 검증기 및 보고서 연결은 별도 작업입니다.
