# 공용 측정 워커

**현재 팀원 실행 방식:** 노트북의 본인 홈 폴더에 저장소를 클론한 뒤 직접 측정합니다. [개인 폴더 측정 실행 안내](docs/personal-run.md)를 먼저 확인하세요. 아래 `pp8` 명령과 서비스 설정은 기존 공용 큐 방식의 운영 안내이며, 개인 직접 실행에 필요한 절차는 아닙니다.

YAML 요청을 제출하면 단일 Linux 워커가 llama-bench를 순차 실행하고 결과를 요청별로 저장합니다. SSH 연결을 끊어도 systemd 서비스는 계속 요청을 처리합니다. 현재는 TPS·준비 시간·별도 모델 로딩 시간·CPU·클럭·메모리·디스크 읽기·페이지 폴트를 기록합니다. 메모리·저장장치 제한, 온도와 TTFT는 별도 작업입니다.

## 팀원 사용 방법

Tailscale 연결 후 본인 계정으로 노트북에 SSH 접속합니다. 호스트 주소는 팀 내부 안내를 확인하세요. 처음 그룹에 추가됐다면 SSH에 다시 접속해야 합니다.

```sh
pp8 submit baseline
pp8 submit threads
pp8 submit baseline --threads 4 --repeat 5 --prompt 512 --gen 128
pp8 submit baseline --threads 1 2 4 8 12 16 --repeat 5
pp8 submit baseline --model Llama-3.2-3B-Instruct-Q8_0.gguf
pp8 submit ~/my-request.yaml
pp8 status
```

기본 모델은 Llama 3.2 3B Instruct Q4_K_M입니다. 관리자가 `/srv/pp8/settings.yaml`의 `default_model`을 지정하면 baseline/threads 예시의 모델을 덮어쓰고, `--model`을 주면 그 요청에만 적용합니다. 사용자 YAML의 model은 기본 모델 설정으로 덮어쓰지 않습니다. Q8_0 등 다른 모델은 관리자가 먼저 models_dir에 다운로드해야 합니다.

baseline 기본 조건은 12스레드, Prefill 512토큰, Decode 128토큰, 내부 반복 10회입니다. `threads`는 1·2·4·8·12·16스레드, 내부 반복 5회와 순서 섞기를 사용하는 예시입니다. 장비가 바뀌면 조건도 조정하세요.

출력되는 전체 요청 ID로 상태와 결과를 조회합니다. 아래의 `여기에_전체_요청ID`는 출력된 실제 ID로 바꿉니다. 사용자명만 넣는 것이 아닙니다.

```sh
pp8 status 여기에_전체_요청ID
pp8 result 여기에_전체_요청ID
```

상태는 queue(대기), running(실행), done(전체 성공), partial(일부 조건 실패), failed(전체 실패·오류·중단)입니다. `result`는 결과 경로와 meta.json, CSV, 오류 정보를 출력합니다. 실행 중에는 아직 완성되지 않은 결과일 수 있습니다.

현재 pp와 tg는 분리 측정합니다. `--repeat`는 llama-bench 내부 반복이며 프로세스를 새로 시작하는 독립 반복 횟수가 아닙니다. 반복 1회의 표준편차 0은 안정성을 검증한 결과가 아닙니다. 실험 중 별도 벤치마크를 직접 실행하지 말고 요청으로 제출하세요. 팀원은 `pp8 worker`를 실행할 필요가 없습니다.

## 파일 구성

[모델 로딩 시간 측정](docs/model-loading.md)은 time으로 로딩 전용 프로세스 경과시간을 재고 기존 준비 시간과 별도로 model_loading.csv에 저장합니다.
기본은 TPS 이후 콜드 캐시 독립 반복입니다. 기존 명령과 ./summary로 사용합니다.

```text
measurement/
  src/team.py             제출·조회·단일 큐 워커
  src/worker.py           조건 조합별 llama-bench 실행
  requests/               3B 요청 예시
  tests/                  큐·실패·결과 저장 테스트
  settings.example.yaml   기계별 설정 예시
  deploy/                 서비스 및 기존 환경 전환 스크립트
```

코드는 Git으로 관리합니다. 모델과 llama.cpp 바이너리, 머신별 실제 설정, 공용 큐, 원시 측정 결과는 노트북에 남겨둡니다. 모델 파일을 Git에 올리지 않습니다.

```text
/srv/pp8/repo/             이 저장소의 clone
/srv/pp8/settings.yaml     기존 설정 유지
/srv/pp8/models/           GGUF
/srv/pp8/llama/            llama-bench와 라이브러리
/srv/pp8/work/             queue/running/done/failed/results
/srv/pp8/backups/          전환 전 코드·설정·서비스·실행 상태 백업
```

결과의 `meta.json`에는 모델 SHA256, 환경, 성공/실패 개수와 함께 `pipeline_commit` 및 `pipeline_dirty`를 남깁니다. CSV의 `build`는 llama.cpp 커밋이며 파이프라인 커밋과 별개입니다. 실행 중 코드를 갱신하면 서로 다른 모듈 버전이 섞일 수 있으므로 반드시 워커를 멈춘 뒤 업데이트합니다. Git 밖에서 실행하면 파이프라인 커밋은 null입니다.

## CPU 사용률 기록

기존 실행 명령과 YAML 요청을 그대로 사용합니다. Linux에서는 각 조건의 llama-bench PID를 약 1초마다 관찰하여 결과 폴더에 `monitor.csv`를 자동 생성합니다. 추가 패키지나 sudo 권한은 필요 없습니다. 1초보다 짧은 프로세스도 종료 전 최종 관측값을 남기며, 마지막 관측 구간은 1초보다 짧을 수 있습니다.

| 열 | 의미 |
| --- | --- |
| request_id, order | 요청 ID와 조건 실행 순서. results.csv와 연결 |
| test, threads, repeat | pp/tg 요청 조건, 지정 스레드 수, 내부 반복 횟수 |
| pid, timestamp | 관찰 대상 프로세스와 시간대가 포함된 관측 시각 |
| elapsed_s | 조건 실행 시작 기준 경과 시간 |
| interval_s | 해당 CPU 사용률을 계산한 실제 관측 구간 길이 |
| cpu_percent | 구간 CPU 사용률. 논리 CPU 1개를 완전히 쓰면 100% |
| cpu_time_s | 프로세스의 누적 user+system CPU 시간 |
| cpu_freq_avg_mhz, cpu_freq_min_mhz, cpu_freq_max_mhz | 해당 관측에서 읽힌 논리 CPU 클럭의 평균·최소·최대 (MHz) |
| cpu_freq_mhz_by_cpu | 논리 CPU 번호별 MHz 값의 JSON. 미수집 CPU는 null |
| cpu_freq_cpus_total, cpu_freq_cpus_read | 관측 대상 논리 CPU 수와 읽기 성공 개수 |
| cpu_freq_source, cpu_freq_status | scaling_cur_freq 출처와 ok/partial/unavailable/process_exited 상태 |

여러 스레드를 사용하면 cpu_percent가 100%를 넘을 수 있습니다. 예를 들어 400%는 해당 구간에 논리 CPU 약 4개 분량의 CPU 시간을 사용했다는 뜻이며, 물리 코어 4개에 고정됐다는 의미는 아닙니다. 전체 노트북 CPU 사용률이 아니라 해당 PID의 모든 스레드가 사용한 값이고 별도 자식 프로세스는 포함하지 않습니다.

**관측 범위:** pp/tg라는 표시는 그 조건을 실행하는 프로세스의 구분입니다. 모델 로딩·워밍업·모든 내부 반복과 종료 구간을 포함하며, llama-bench가 보고하는 순수 계산 시간만을 분리한 CPU 기록은 아닙니다. 반복별 CPU 기록이나 초당 순간 최대치를 뜻하지 않습니다. 샘플링 자체의 작은 실행 부하도 있습니다.

`meta.json`의 `cpu_monitor.conditions`에는 조건별 표본 수, 시간 가중 평균(`avg_cpu_percent`), 관측 구간 최대값(`max_cpu_percent`), 누적 CPU 시간과 상태를 저장합니다. 최대값은 관측 구간 평균 중 최대이며 순간 피크가 아닙니다. 종료 코드가 0이 아니거나 시간 초과된 조건도 수집된 CPU 기록은 보존합니다. `/proc` 관찰에 실패하면 상태와 오류를 기록하며 알 수 없는 사용률을 0으로 채우지 않습니다.

Windows 등 비Linux 환경에서는 기존 벤치마크 실행을 유지하지만 CPU 기록은 지원하지 않습니다. 이때 monitor.csv는 헤더만 생성되고 메타데이터에 unsupported로 표시됩니다. 과거 결과 폴더에 CPU 기록을 소급 생성하지 않습니다.

### CPU 클럭 기록과 해석

CPU 사용률과 같은 관측 행에서 `/sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq`를 읽고 kHz를 MHz로 변환합니다. 대상은 그 시점의 llama-bench 메인 스레드 affinity에 포함된 논리 CPU입니다. 코어 고정을 하지 않은 현재 장비에서는 보통 16개 전체가 대상이며, P/E코어와 쉬는 CPU도 섞입니다. 별도 스레드가 서로 다른 affinity를 가지면 그 스레드의 허용 CPU 집합까지 추적하지는 않습니다.

**클럭 평균은 실제 작업 코어만의 평균이나 사용률 가중 평균이 아닙니다.** 드라이버가 보고한 주파수 표본이고 정확한 순간 하드웨어 클럭 또는 프로세스의 유효 클럭으로 단정하지 않습니다. P/E코어 구분이나 개별 CPU의 경향은 `cpu_freq_mhz_by_cpu` 원본으로 확인합니다. 같은 주파수 정책을 공유하는 논리 CPU는 보고값이 중복될 수 있습니다. 값은 순차적으로 읽으므로 모든 CPU가 정확히 동일한 순간의 값은 아닙니다.

`meta.json`의 조건별 `frequency`에 유효 표본 수, `mean_sample_avg_mhz`(유효 관측별 CPU 평균의 단순 평균), `min_observed_mhz`, `max_observed_mhz`, 상태별 개수를 남깁니다. CPU 사용률 평균은 시간 가중 평균이지만 이 클럭 요약은 스냅샷 표본의 단순 평균입니다. 수집 실패는 빈 칸/null로 남기며 0MHz로 대체하지 않습니다.

프로세스가 끝난 뒤의 최종 행은 직전 구간 CPU 사용률은 남기되, 종료 후 유휴 클럭을 작업 중 클럭으로 오해하지 않도록 클럭은 비워두고 `process_exited`로 표시합니다. 1초보다 빨리 끝나는 실행은 클럭 표본이 없을 수 있습니다. 이전 monitor.csv의 열 이름은 유지하고 새 클럭 열만 뒤에 추가합니다. 온도·거버너·클럭 제한값은 변경하지 않습니다.

TPS와의 비교는 `request_id`와 `order`로 results.csv와 연결합니다. 클럭은 약 1초마다 관찰하지만 현재 TPS는 조건의 내부 반복 전체를 집계한 값이므로 초별 TPS 또는 각 반복의 정확한 계산 구간과 직접 대응하지 않습니다. 클럭이 낮을 때 TPS도 낮아지는지 탐색하는 자료이며, 이것만으로 속도 변화의 인과관계나 발열 원인을 확정하지 않습니다.

출처: [Linux CPUFreq 공식 문서](https://docs.kernel.org/admin-guide/pm/cpufreq.html#policy-interface-in-sysfs).

## 로컬 개발과 테스트

```sh
git clone https://github.com/pproject8/p-project8.git
cd p-project8
python3 -m venv .venv
.venv/bin/pip install -r measurement/requirements.txt
.venv/bin/python -m unittest discover -s measurement/tests -v
```

별도 개발 환경에서 직접 실행할 때는 settings.example.yaml을 measurement/settings.yaml로 복사해 자신의 절대 경로로 수정한 뒤 사용합니다. 실제 설정은 커밋하지 않습니다.

```sh
.venv/bin/python measurement/src/team.py --settings measurement/settings.yaml submit baseline
.venv/bin/python measurement/src/team.py --settings measurement/settings.yaml worker
```

공용 `/srv/pp8/work`를 지정한 설정으로 개발용 워커를 함께 실행하지 마세요. 같은 큐의 중복 워커는 Linux flock으로 차단하지만 직접 llama-bench를 실행하는 것까지 막지는 않습니다. 이 코드는 신뢰하는 팀 그룹에서 사용하는 방식이며 계정별 작업 파일 격리 시스템은 아닙니다.

## 기존 공용 환경의 Git 전환

아래 절차는 기존 `jungwon` 서비스 계정, `pp8` 그룹, 설정 및 모델·결과 폴더를 유지하는 노트북용입니다. 다른 장비에서는 서비스 계정과 경로를 먼저 조정하세요.

1. 기존 코드가 수정됐다면 먼저 diff를 확인합니다. 팀원에게 잠시 요청 제출을 중단하도록 안내합니다.
2. `/srv/pp8/repo`에 저장소를 clone하고 사용할 커밋을 확인합니다.
3. 기존 `/srv/pp8/settings.yaml`을 유지한 채 테스트와 짧은 3B 측정을 별도 임시 작업 폴더에서 확인합니다.
4. 대기·실행 중 요청이 없을 때 다음을 실행합니다.

```sh
sudo bash /srv/pp8/repo/measurement/deploy/migrate-existing.sh
systemctl status pp8-worker --no-pager
pp8 submit baseline --threads 12 --repeat 1 --prompt 32 --gen 8
pp8 status
```

전환 스크립트는 큐가 비었는지 확인하고, 기존 서비스·명령 래퍼와 코드·요청·설정·결과를 백업합니다. 서비스와 명령을 clone 경로로 바꾸며, 시작 실패 시 이전 서비스와 래퍼를 복구합니다. 설정·모델·결과와 이전 코드 디렉터리를 삭제하지 않습니다. 팀원이 전환 중 새 요청을 제출하지 않도록 협의해야 합니다.

다른 팀원 계정에서도 짧은 요청 제출과 결과 읽기를 확인하세요. Git 전환만으로 계정별 검증까지 완료되는 것은 아닙니다.

## 이후 코드 업데이트

진행 중 측정이 끝나고 큐가 비었는지 확인한 뒤 업데이트합니다. 서비스 시작 실패 시 이전 커밋으로 돌아가야 하므로 커밋 ID를 기록합니다.

```sh
pp8 status
sudo systemctl stop pp8-worker
cd /srv/pp8/repo
git rev-parse HEAD
git status --short
git pull --ff-only origin main
python3 -m unittest discover -s measurement/tests -v
sudo systemctl start pp8-worker
systemctl status pp8-worker --no-pager
```

로컬 수정이나 테스트 실패가 있으면 갱신을 중단하고 원인을 해결하세요. 임의의 강제 reset으로 덮어쓰지 않습니다. 워커 중단 시 남은 요청은 다음 시작 때 failed로 이동하며 자동 재시도하지 않습니다. 조건 시간 초과는 기본 3600초이고 설정의 condition_timeout_seconds로 변경할 수 있습니다.

## 수동 복구

전환 시 출력된 백업 디렉터리의 `pp8-worker.service`와 `pp8`를 각각 원래 위치에 복원한 후 daemon-reload와 start를 실행하면 됩니다. 기존 team-src는 복구 확인 전 삭제하지 않습니다. 결과 폴더를 과거 백업으로 덮어쓰면 새 결과를 잃을 수 있으므로 서비스 복구 과정에서는 결과를 복원하지 않습니다.


### 모델 준비 시간 기록

기존 실행 명령을 그대로 사용하면 결과 폴더에 `loading.csv`도 저장됩니다.
`startup_to_warmup_s`는 프로세스 실행 직전부터 llama-bench의 첫 워밍업 직전 진행 메시지를 관찰할 때까지의 시간(초)입니다. 프로세스 시작, 백엔드 및 모델 로딩, 컨텍스트와 스레드 풀 초기화를 포함합니다. 순수 파일 읽기 시간, TTFT, 첫 워밍업 계산 시간은 아닙니다.

이 값은 폴링 관찰 시점의 상한이며 `lower_bound_s`와 `upper_bound_s`로 관찰 범위를 함께 저장합니다. 약 50 ms 간격으로 확인하되 실제 간격은 시스템 부하에 따라 길어질 수 있습니다. 메시지가 없으면 `marker_missing`, 지원하지 않는 플랫폼은 `unavailable`로 표시하고 시간은 빈 값으로 둡니다. 실패/시간 초과에서도 이미 관찰한 값은 보존하며 성공 여부는 meta.json의 ok/failed와 run.log를 함께 확인합니다.

각 pp/tg 조건은 별도 프로세스이므로 준비 시간이 하나씩 기록됩니다. `repeat: 10`은 계산 반복 수이며 로딩 시간 표본 10개를 뜻하지 않습니다. OS 파일 캐시는 통제하지 않고, 실행 전에 모델 해시도 읽으므로 cold load 측정으로 해석하지 않습니다. 캐시를 비우거나 모델 로딩 방식, 기존 TPS/CPU 기록 형식을 변경하지 않습니다. 원본 진행 메시지는 run.log에 저장되고 요약은 meta.json의 loading_measurement에 저장됩니다. llama-bench는 `--progress` 옵션이 필요합니다.


### 메모리 사용량 기록

기존 실행 명령으로 `monitor.csv` 뒤에 다음 열도 저장합니다. 별도 패키지나 sudo 없이 Linux `/proc/PID/status`를 약 1초마다 읽습니다.

| 열 | 의미 |
| --- | --- |
| memory_rss_bytes | VmRSS: 현재 RAM에 상주하는 프로세스 메모리 |
| memory_hwm_bytes | VmHWM: 해당 시점까지 커널이 보고하는 RSS 최고값 |
| memory_vms_bytes | VmSize: 가상 주소 공간 크기이며 실제 RAM 사용량이 아님 |
| memory_swap_bytes | VmSwap: 비공유 익명 메모리의 스왑 사용량이며 shmem 스왑은 제외 |
| memory_source / memory_status | 출처 및 ok / partial / unavailable / process_exited 상태 |

단위는 bytes이며 proc의 kB에 1024를 곱합니다. MiB로 읽으려면 1,048,576으로 나눕니다. 누락/권한 오류/프로세스 종료는 빈 값으로 저장하고 실제 스왑 0과 구분합니다. Windows에서는 기존과 같이 모니터링이 지원되지 않으며 CSV는 헤더만 저장합니다.

RSS는 공유 라이브러리 및 mmap 파일의 상주 페이지를 포함합니다. 모델 파일 크기, 노트북 전체 사용량, cgroup의 memory.current와 다른 지표이며 자식 프로세스는 합산하지 않습니다. RSS 계수는 커널의 근사값이고 짧은 순간의 피크를 놓칠 수 있습니다.

`meta.json`의 `cpu_monitor.conditions[].memory`에 유효 RSS 표본의 단순 평균(mean_sample_rss_bytes), 관측 RSS 최대(max_observed_rss_bytes), 관측된 VmHWM의 최대(max_observed_hwm_bytes), 관측 스왑 최대 및 표본 수/상태 개수를 저장합니다. 평균은 시간 가중 평균이 아니며 두 최대값 모두 프로세스 전체 수명의 정확한 최대 사용량을 보장하지 않습니다. CPU/클럭/준비 시간 및 TPS 기록은 유지하며 메모리 제한이나 스왑 설정은 변경하지 않습니다.


### 디스크 읽기와 페이지 폴트 기록

기존 실행 명령을 유지하며 `monitor.csv`에 다음 네 누적 카운터를 추가합니다. 별도 패키지·sudo 없이 기존 CPU/메모리 관측과 함께 약 1초마다 읽습니다.

| 열 | 의미 |
| --- | --- |
| io_read_bytes | /proc/PID/io read_bytes: 해당 프로세스에 계상된 저장장치 계층 읽기 바이트 |
| io_rchar_bytes | /proc/PID/io rchar: read 계열 호출에서 읽은 바이트, 파일 캐시 적중도 포함 |
| minor_faults | /proc/PID/stat minflt: 디스크에서 페이지를 가져올 필요 없이 처리한 페이지 폴트 누적 횟수 |
| major_faults | /proc/PID/stat majflt: 디스크에서 페이지를 가져와야 했던 페이지 폴트 누적 횟수 |

각 열에 `_delta`(이전 유효 관측 이후 증가량), `_interval_s`(그 관측 간 실제 시간), `_per_s`(증가량/초), `_status`도 저장합니다. 읽기량은 bytes와 bytes/s, 폴트는 횟수와 횟수/s입니다. 첫 증가량은 프로세스 실행 전 0을 기준으로 계산합니다. 누락이 끼면 해당 카운터의 마지막 유효 관측부터 시간을 계산하며 누락을 0으로 대체하지 않습니다. 감소한 카운터는 counter_decreased로 표시합니다.

`meta.json`의 `cpu_monitor.conditions[].io_and_faults`에 카운터별 마지막 누적값, 관측 시각, 평균 및 관측 최대 속도, 상태 개수와 `final_observed`를 저장합니다. 종료 전 최종 카운터 읽기를 시도하되 이 장비에서는 종료 후 `/proc/PID/io`를 읽지 못할 수 있습니다. `final_observed: false`이면 전체 실행의 확정 총량이 아닌 마지막 관측 누적값입니다. 짧게 끝난 실행은 I/O 표본이 없을 수 있습니다. 정상/실패/시간 초과에서도 가능한 값은 보존합니다.

관측 대상은 로딩·워밍업·계산을 포함한 llama-bench 프로세스이며 모델 파일만의 I/O나 SSD 전체 사용량이 아닙니다. stat의 자식 폴트 필드는 제외하며 프로세스 트리를 별도 합산하지 않습니다. OS I/O 계상 규칙은 적용됩니다.

캐시가 데워져 있으면 `io_read_bytes`가 0이어도 정상입니다. 워커가 모델 해시를 먼저 읽어 캐시를 데울 수 있으며 이 읽기는 워커에 속하므로 llama-bench 카운터에는 포함하지 않습니다. rchar는 mmap 접근량 전체를 나타내지 않으므로 `rchar - read_bytes`를 캐시 적중량으로 계산하면 안 됩니다. major fault 횟수는 디스크 요청 수나 `횟수 × 페이지 크기`의 읽기량과 일치하지 않습니다. 캐시 비우기, 스왑 및 자원 제한 설정은 변경하지 않습니다.

정의 참고: https://docs.kernel.org/filesystems/proc.html


## 한 줄로 결과 요약 보기

SSH 접속 후 개인 클론의 measurement 폴더에서 실행합니다.

```bash
cd ~/p-project8/measurement
./summary
```

개인 settings.yaml의 work_dir에서 최근 결과(기본 워커가 생성한 시간순 요청 ID)를 찾아 TPS, 준비 시간, CPU 평균/관측 최대, 평균 보고 클럭, RSS 평균/관측 최대, 스왑, 디스크/read 계열 읽기량, minor/major 페이지 폴트를 출력합니다. 결과를 읽기만 하며 측정을 새로 실행하지 않습니다.

특정 결과는 요청 ID를 붙입니다.

```bash
./summary 20261102-005918-exp0-baseline
```

기존 클론은 먼저 `git pull --ff-only`로 업데이트합니다. 실행 파일 권한은 Git에 포함되어 있어 별도 설치·sudo·alias가 필요 없습니다. `.venv/bin/python`이 있으면 사용하고, 없으면 python3를 사용합니다(기존 PyYAML 필요). 다른 위치의 설정은 `./summary --settings /절대경로/settings.yaml`로 선택합니다. 개인 실행에서는 기존 공용 `/usr/local/bin/pp8 result`와 구분해서 사용합니다.

최근 결과가 미완료면 이전 결과로 자동 넘어가지 않고 안내합니다. 예전 결과에서 기록하지 않은 값은 미기록으로 표시합니다. 실패 조건과 마지막 I/O 관측 여부도 함께 표시합니다.
