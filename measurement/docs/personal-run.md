# 팀원 개인 폴더 측정 실행 안내

작성일: 2026-10-27

각자 PC에서 Tailscale로 공용 Linux 노트북에 접속한 뒤, 노트북 안의 본인 홈 폴더에 코드를 클론해 실행합니다. 개인 PC에 Ubuntu를 설치할 필요는 없습니다.

코드·설정·결과는 개인별로 관리하고, 모델과 llama-bench 파일은 노트북에 준비된 공용 파일을 읽습니다. 이 방법에서는 공용 큐나 상시 워커, pp8 명령을 사용하지 않습니다.

## 1. Tailscale 연결 및 SSH 접속

PC에서 Tailscale을 연결하고 Windows 명령 프롬프트 또는 터미널에서 실행합니다. `본인계정`은 본인의 계정명으로, `노트북주소`는 팀 내부에서 공유한 Tailscale 주소로 바꿔주세요.

```cmd
ssh 본인계정@노트북주소
```

| 이름 | 계정 |
| --- | --- |
| 지영인 | yeongin |
| 강주원 | juwon |
| 정승호 | seungho |
| 최정원 | jungwon |

예를 들어 강주원 계정은 다음과 같습니다.

```cmd
ssh juwon@노트북주소
```

비밀번호는 터미널에 입력합니다. 입력 중 문자가 보이지 않는 것은 정상입니다. 접속 후 프롬프트가 `본인계정@yeongin-laptop:~$` 형태로 바뀌면, 아래 명령은 모두 그 SSH 터미널에서 실행합니다.

## 2. 본인 홈 폴더에 저장소 클론

처음 사용하는 경우:

```bash
cd ~
git clone https://github.com/pproject8/p-project8.git
cd ~/p-project8/measurement
```

이미 `~/p-project8`에 클론했다면 다시 클론하지 않고 이동합니다.

```bash
cd ~/p-project8/measurement
```

`~`는 로그인한 본인의 홈 폴더입니다. 예를 들어 juwon은 `/home/juwon/p-project8`, seungho는 `/home/seungho/p-project8`에서 작업합니다.

## 3. 개인 Python 실행 환경 준비

노트북에서 확인한 방식입니다. pip를 별도 설치하지 않고 시스템에 설치된 PyYAML을 사용합니다.

```bash
python3 -m venv --without-pip --system-site-packages .venv
.venv/bin/python -c "import yaml; print('PyYAML 정상:', yaml.__version__)"
```

PyYAML 버전이 출력되면 정상입니다. `.venv` 활성화 명령은 필요 없습니다. 아래에서도 `.venv/bin/python`을 직접 사용합니다.

## 4. 개인 설정과 짧은 테스트 요청 생성

`~/p-project8/measurement`에서 아래 전체를 한 번에 붙여넣습니다. 마지막 `PY` 줄까지 포함합니다.

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import yaml

home = Path.home()
settings_path = Path("settings.yaml")

if settings_path.exists():
    print("기존 settings.yaml 유지")
else:
    settings = {
        "bench_path": "/srv/pp8/llama/llama-b11706/llama-bench",
        "models_dir": "/srv/pp8/models",
        "work_dir": str(home / "pp8-work"),
        "default_model": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "condition_timeout_seconds": 3600,
    }
    settings_path.write_text(
        yaml.safe_dump(settings, sort_keys=False),
        encoding="utf-8",
    )
    settings_path.chmod(0o600)
    print("개인 settings.yaml 생성 완료")

requests = home / "pp8-requests"
requests.mkdir(exist_ok=True)
smoke = requests / "smoke-3b.yaml"

if not smoke.exists():
    request = {
        "name": "personal-smoke-3b",
        "model": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "repeat": 1,
        "threads": [12],
        "prompt_tokens": [32],
        "gen_tokens": [8],
    }
    smoke.write_text(
        yaml.safe_dump(request, sort_keys=False),
        encoding="utf-8",
    )

print("설정:", settings_path.resolve())
print("테스트 요청:", smoke)
PY
```

이미 설정 파일이 있다면 덮어쓰지 않습니다. 결과를 개인 공간에 저장하려면 기존 `settings.yaml`의 `work_dir`도 본인의 `/home/계정명/pp8-work`인지 확인하세요.

```bash
cat settings.yaml
```

개인 공간의 구성은 다음과 같습니다.

```text
~/p-project8/measurement/               개인 코드
~/p-project8/measurement/settings.yaml  개인 경로 설정
~/pp8-requests/                        개인 실험 요청
~/pp8-work/results/                    개인 측정 결과
```

`/srv/pp8/models`와 `/srv/pp8/llama`는 모델 및 실행 파일을 함께 읽기 위한 경로입니다. 결과 저장 위치나 공용 큐를 뜻하지 않습니다.

## 5. 짧은 3B 테스트

다른 팀원이 측정 중이지 않은지 확인한 뒤 실행합니다.

```bash
cd ~/p-project8/measurement
.venv/bin/python src/worker.py ~/pp8-requests/smoke-3b.yaml
```

예상 출력 형태:

```text
요청 ...-personal-smoke-3b: 총 2개 조건
[1/2] pp32 스레드 12 ...
[2/2] tg8 스레드 12 ...
결과 폴더: /home/본인계정/pp8-work/results/...
```

이 테스트는 실행 확인용으로 내부 반복이 1회입니다. 표준편차가 0이어도 측정이 안정적이라는 의미는 아닙니다.

## 6. 기본 조건 측정

```bash
cd ~/p-project8/measurement
.venv/bin/python src/worker.py requests/baseline.yaml
```

현재 기본 요청 조건:

| 항목 | 값 |
| --- | --- |
| 모델 | Llama 3.2 3B Instruct Q4_K_M |
| CPU 스레드 수 | 12 |
| Prefill 입력 토큰 | 512 |
| Decode 생성 토큰 | 128 |
| 조건별 내부 반복 | 10회 |

Prefill과 Decode는 별도로 실행됩니다. 반복 횟수는 llama-bench 내부 반복이며, 프로세스를 새로 시작하는 독립 실행 횟수가 아닙니다.

## 7. 조건을 바꿔서 실험

기본 요청을 개인 요청 폴더로 복사합니다. 아래 복사 명령은 처음 요청 파일을 만들 때 한 번만 실행하세요. 이미 수정한 파일에 다시 실행하면 내용이 덮어써집니다.

```bash
cp requests/baseline.yaml ~/pp8-requests/my-test.yaml
nano ~/pp8-requests/my-test.yaml
```

요청 파일 예시:

```yaml
name: my-test
model: Llama-3.2-3B-Instruct-Q4_K_M.gguf
repeat: 5
threads: [1, 2, 4, 8, 12, 16]
prompt_tokens: [512]
gen_tokens: [128]
```

| 설정 | 의미 |
| --- | --- |
| name | 결과 폴더 이름에 포함할 실험명. 영문·숫자·하이픈 사용 권장 |
| model | models_dir 안에 있는 실제 GGUF 파일명 |
| repeat | 조건별 내부 반복 횟수 |
| threads | 비교할 CPU 스레드 수 목록 |
| prompt_tokens | Prefill 입력 토큰 수 목록 |
| gen_tokens | Decode 생성 토큰 수 목록 |

nano에서 `Ctrl+O` → Enter로 저장하고 `Ctrl+X`로 종료합니다.

```bash
.venv/bin/python src/worker.py ~/pp8-requests/my-test.yaml
```

직접 실행은 요청 YAML의 `model`을 사용합니다. `settings.yaml`의 `default_model`만 변경해도 이 직접 실행의 요청 모델이 바뀌지는 않습니다. 다른 모델로 측정하려면 해당 GGUF가 준비되어 있어야 하고 요청 YAML의 `model`도 변경해야 합니다.

## 8. 결과 확인

```bash
ls -lt ~/pp8-work/results
```

터미널에 출력된 결과 폴더 안에서 다음 파일을 확인합니다.

| 파일 | 내용 |
| --- | --- |
| results.csv | Prefill·Decode 평균 속도와 표준편차, 조건 |
| loading.csv | 기존 프로세스 준비 시간 |
| model_loading.csv | 별도 프로세스의 모델 로드 함수 시간, 반복·캐시·읽기 방식 |
| monitor.csv | 시간별 CPU 사용률·논리 CPU별 클럭, 조건과 PID |
| meta.json | 모델 해시, 실행 환경, 성공·실패 개수, 측정 코드 Git 커밋 |
| request.yaml | 실행에 사용한 요청 원본 |
| run.log | 실행 명령과 실패 시 오류 기록 |

CPU 사용률은 추가 설치 없이 Linux에서 약 1초 간격으로 기록합니다. 논리 CPU 1개 사용량을 100%로 표시하므로 여러 스레드를 쓰면 100%를 넘을 수 있습니다. 모델 로딩·워밍업·내부 반복을 포함한 각 llama-bench 프로세스 전체 구간을 관찰하며, 순수 pp/tg 계산 구간만의 사용률은 아닙니다. 조건별 평균·관측 최대값은 meta.json의 cpu_monitor.conditions에서 확인합니다. 자세한 기준은 [CPU 기록 설명](../README.md#cpu-사용률-기록)을 참고하세요.

클럭은 MHz 단위의 드라이버 보고값입니다. 해당 시점 메인 스레드가 실행될 수 있는 논리 CPU들의 평균·최소·최대와 CPU별 원본을 함께 기록합니다. 쉬는 CPU도 포함되므로 작업 중인 코어만의 클럭 평균으로 해석하지 않습니다. 종료 후 최종 행의 클럭은 빈 칸이며 수집 상태도 함께 남깁니다. 같은 실행 명령을 유지하며 추가 설치나 온도 수집은 없습니다.

예를 들어 결과 폴더 이름이 `20261027-043320-personal-smoke-3b`라면 다음처럼 읽습니다. 실제 출력된 폴더 이름으로 바꿔주세요.

```bash
cat ~/pp8-work/results/20261027-043320-personal-smoke-3b/results.csv
cat ~/pp8-work/results/20261027-043320-personal-smoke-3b/meta.json
```

## 9. 다음에 다시 실행할 때

최초 설정을 다시 만들 필요 없이 SSH 접속 후 다음만 실행하면 됩니다.

```bash
cd ~/p-project8/measurement
.venv/bin/python src/worker.py requests/baseline.yaml
```

개인 요청으로 실행하려면:

```bash
.venv/bin/python src/worker.py ~/pp8-requests/my-test.yaml
```

## 10. Git 코드 업데이트

본인의 측정이 끝난 뒤 실행합니다.

```bash
cd ~/p-project8
git status --short
git pull --ff-only
```

직접 수정한 코드가 있어 업데이트 오류가 나면 강제로 덮어쓰지 말고 내용을 공유해주세요. 개인 settings.yaml은 Git에서 제외되며, 위 안내대로 만든 개인 요청과 결과도 저장소 밖에 있습니다.

## 사용 시 참고

- [모델 로딩 측정](model-loading.md)은 기존 명령으로 TPS 이후 자동 실행됩니다. 기본 콜드 캐시 독립 반복이며 최초 g++·고정 버전 헤더 다운로드가 필요합니다. 준비 시간과 구분해 ./summary에서 확인합니다.

- 현재 개인 실행에는 공통 잠금이나 공용 큐를 적용하지 않았습니다. 서로 실행하면 측정이 동시에 돌아갈 수 있으므로 시간을 조율합니다.
- 같은 노트북의 CPU·메모리·저장장치를 공유합니다. 개인 폴더만 분리되어 있다고 측정 간 간섭이 사라지지는 않습니다.
- 개인 직접 실행 중에는 SSH 창을 유지합니다. 중단하려면 `Ctrl+C`를 사용합니다.
- 이 안내에서는 `pp8 submit`, `pp8 worker` 명령을 사용하지 않습니다.
- 현재는 TPS·준비 시간·별도 모델 로딩 시간·CPU·클럭·메모리·디스크 읽기·페이지 폴트를 기록합니다. 온도·자원 제한·TTFT는 별도 작업입니다.
- `jungwon` 계정에서 이 방식으로 3B 실제 실행을 확인했습니다. 다른 팀원 계정에서 권한 오류가 나면 임의로 권한을 넓히지 말고 오류 내용을 공유해주세요. 그룹 권한이 적용되지 않았다면 SSH에 새로 접속한 뒤 다시 확인합니다.


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
