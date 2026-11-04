# 준비 시간과 모델 로딩 시간

기존 실행 명령을 사용합니다.

```bash
cd ~/p-project8/measurement
.venv/bin/python src/worker.py requests/baseline.yaml
./summary
```

| 파일/지표 | 측정 범위 |
|---|---|
| loading.csv / startup_to_warmup_s | 기존 llama-bench 프로세스 실행 직전부터 워밍업 직전 메시지를 관찰할 때까지. 백엔드·모델·컨텍스트·스레드풀 준비 포함 |
| model_loading.csv / model_load_s | 별도 프로세스 안에서 llama_model_load_from_file 호출 직전부터 반환 직후까지. 백엔드 초기화·컨텍스트·토큰화·워밍업·첫 추론 제외 |

`llama-completion`의 최종 load time 로그를 그대로 쓰지 않습니다. 현재 버전은 첫 평가에서 그 시간을
갱신할 수 있어 계산 시간이 섞이기 때문입니다. C++ 도구가 직접 steady clock으로 API 경계를 잽니다.
mmap은 반환 후 추론 중에도 페이지를 읽을 수 있으므로 **모든 모델 페이지를 RAM으로 읽는 시간이나 TTFT와는 다릅니다**.

## 독립 반복과 캐시

모든 TPS 조건이 끝난 뒤 별도 로딩 프로세스를 반복 실행합니다. 기본은 요청의 repeat회이며
loading_repeat로 별도 지정할 수 있습니다. 입력 길이·CPU 스레드와 무관한 모델 로드 함수이므로
모델/읽기 방식별로 반복하고 pp/tg·스레드마다 같은 로딩을 중복 측정하지 않습니다.
로딩 반복에서는 워밍업 표본을 버리지 않습니다.

기본 cache는 계획서에 맞춰 cold입니다. **기존 TPS 구간의 캐시를 통제한다는 뜻은 아닙니다**.
각 별도 로딩 프로세스 실행 직전에 다음 명령으로 페이지 캐시 등을 비웁니다.

```bash
sudo -n /usr/local/bin/pp8-drop-caches
```

노트북 관리자가 등록한 전용 명령만 사용하며 자동 설치·sudoers 변경은 하지 않습니다.
도구가 없거나 권한이 없으면 로딩을 실패로 기록하고 warm으로 바꾸지 않습니다. 기존 TPS 결과는 보존합니다.
다른 llama-bench/server가 감지되면 캐시 비우기를 거부합니다. 공통 잠금 #26 전까지는 실행 시간 조율도 필요합니다.
캐시 비우기는 노트북 전체에 영향을 줍니다.

warm은 매 표본 직전에 모델 파일을 읽어 OS 캐시를 데웁니다. DIO는 이 캐시를 우회할 수 있습니다.

```yaml
load_mode: [mmap, none]  # 생략 시 auto, TPS 명령에도 -lm으로 전달
loading_repeat: 5       # 생략 시 repeat
cache: cold            # cold / warm, 생략 시 cold
```

load_mode를 명시한 요청에만 results.csv에 해당 열을 추가합니다. 기존 요청의 TPS 열은 유지합니다.
지원 값: auto, none, mmap, mlock, mmap+mlock, dio. mlock 실제 잠금 성공은 시스템 한도와 로그를 확인해야 합니다.
기본 cache/반복은 #42 제안의 warm/1 대신 사용자 요청인 계획서의 콜드 독립 반복에 맞춘 선택입니다.

## 결과

model_loading.csv에 읽기 방식, 캐시 상태, 반복 번호, 성공/실패, 초, 종료 코드, 오류를 저장합니다.
model_file_gb_per_s는 **파일 크기/로드 함수 시간의 비율**이며 실제 디스크 읽기 대역폭이 아닙니다.
meta.json의 model_loading과 ./summary에는 평균·표본 표준편차·CV·성공/실패 수를 표시합니다.
한 표본이면 SD/CV는 미기록이며 실패 시간을 0으로 채우지 않습니다.
원본 엔진 로그는 model-loading.log에 저장합니다. 과거 결과는 새 요약에서 로딩 미기록으로 표시합니다.

실행 시간이 늘어나므로 TPS만 필요하면 요청에 measure_loading: false를 넣습니다.
별도 TTFT/메모리 PR과는 독립이며 메모리 제한 요청은 무제한 로딩 도구를 실행하지 않도록 비활성화합니다.
제한된 모델 로딩·TTFT와의 통합 실행 순서는 후속 연결 대상입니다.

## 최초 도구 준비

Linux g++와 bench_path 옆의 libllama.so, 관련 라이브러리가 필요합니다.
최초 실행에서 ABI가 일치하는 llama.cpp f74e2fa6a의 헤더와 MIT LICENSE를 내려받아
개인 work_dir/tools에 도구를 컴파일합니다. 이후 같은 소스·라이브러리면 재사용합니다.
헤더 해시 manifest와 소스/libllama 해시를 저장합니다. bench --version으로 버전을 확인하고
다른 빌드는 ABI가 다른 구조체를 호출하지 않도록 미지원 처리합니다.
초기 인터넷·컴파일러·라이브러리가 없으면 로딩만 미측정 사유를 남기고 TPS를 보존합니다.

settings.yaml 선택 설정:

```yaml
drop_caches_path: /usr/local/bin/pp8-drop-caches
model_load_timeout_seconds: 300
```

외부 exp-* 스킬/exp-shared 검증기는 저장소에 없어 호환 검증하지 못했습니다.
새 YAML 키 허용과 model_loading.csv 수집·보고서 반영이 별도로 필요합니다.

참고: [고정 버전 모델 API](https://github.com/ggml-org/llama.cpp/blob/f74e2fa6a/include/llama.h),
[최초 평가 시 context load time 갱신](https://github.com/ggml-org/llama.cpp/blob/f74e2fa6a/src/llama-context.cpp).
