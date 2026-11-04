# 메모리 한도와 OOM 기록 (구현 초안)

관련 작업: [#43](https://github.com/pproject8/p-project8/issues/43).
실제 노트북에서 systemd/cgroup 한도와 스왑 제한을 확인했고, 3B 모델의 none 읽기 방식에서
0.8배 한도의 OOM과 이어지는 2배 조건의 정상 실행을 확인했습니다.
요청 YAML의 load_mode 연결과 외부 exp 검증기 수정은 아래 설명처럼 남아 있습니다.

## 요청과 호환

기존 실행 명령은 유지합니다. 한도를 주지 않은 요청은 기존 results.csv 열과 실행 경로를 유지합니다.
한도를 지정하려면 개인 요청 YAML에 다음처럼 모델 **파일 크기의 배수**를 적습니다.

```yaml
name: memory-pilot
model: Llama-3.2-3B-Instruct-Q4_K_M.gguf
threads: [12]
prompt_tokens: [512]
gen_tokens: [128]
repeat: 5
memory_max: [0.8, 1.0, 1.25, 2.0]
condition_timeout_seconds: 1800
```

`memory_max`는 양수 배수의 목록입니다. 페이지 단위로 올림한 바이트를 MemoryMax에 적용합니다.
스레드·pp/tg별 모든 배수 조합을 측정합니다. 요청의 condition_timeout_seconds는 설정 파일보다 우선합니다.
물리 RAM을 바꾸거나 메모리를 미리 예약하는 기능이 아닙니다.

## 실행 구조

1. 조건마다 무작위 이름의 사용자 systemd scope를 MemoryMax, MemorySwapMax=0, Delegate=yes로 준비합니다.
2. 작은 Python scope holder를 유지하여 벤치마크 종료 후에도 memory.events를 읽을 수 있게 합니다.
3. 워커의 직접 자식인 exec gate가 사용자 systemd의 AttachProcessesToUnit API로 해당 cgroup에 들어가
   실제 memory.max, memory.swap.max와 소속을 확인한 뒤 llama-bench로 exec합니다.
4. 기존 monitor는 같은 PID를 관찰합니다. systemd-run 자체를 벤치마크로 잘못 관찰하지 않습니다.
5. 종료 후 cgroup OOM 카운터를 읽고 해당 scope를 정리합니다. timeout/Ctrl+C도 정리를 시도합니다.

한도는 RSS만의 한도가 아닙니다. 커널이 이 cgroup에 계상한 파일 캐시·커널 메모리와 scope holder도 포함됩니다.
holder의 작은 메모리와 gate의 초기 Python 실행 비용도 있습니다. monitor.csv는 holder를 제외한
벤치마크 PID(초기의 gate 포함)의 사용량입니다. 따라서 제한 실행과 기존 직접 실행의 준비 시간은
구조가 조금 다릅니다. 메모리 페이지의 기존 소유 cgroup, 상위 cgroup 한도도 결과에 영향을 줄 수 있습니다.

사용자 systemd와 memory 컨트롤러 위임, busctl이 필요합니다. 사용자 systemd가 Delegate=yes인 scope로 프로세스를 옮깁니다.
권한이나 실제 한도 확인에 실패하면 setup_error를 기록하며 **제한 없이 대신 실행하지 않습니다**.
자동 sudo나 머신 전역 설정 변경은 없습니다.

## 저장 값과 해석

한도 요청에만 results.csv 뒤에 다음 열을 추가합니다.

| 열 | 의미 |
|---|---|
| memory_max | 모델 파일 크기 대비 요청 배수 |
| memory_max_bytes | 페이지 단위 올림한 적용 한도 |
| status | ok, oom, timeout, setup_error, failed, failed_unknown_oom |
| returncode | 벤치마크/gate 종료 코드. timeout 강제 종료도 가능한 경우 기록 |
| oom_kill_delta | 이 조건의 memory.events oom_kill 증가량. 미수집은 빈 값 |

실패·OOM·timeout도 CSV에 한 행 남기며 TPS는 비워 둡니다. 다음 조건은 계속 실행합니다.
`meta.json`의 memory_limit.conditions에는 cgroup 경로·unit·바이트 한도·swap 한도·
실행 전후 memory.events·오류·종료 코드를 저장합니다. run.log에도 증거를 남기고,
systemd 준비 로그는 memory-scope-N.log에 기록합니다. `./summary`에서도 한도와 상태를 확인합니다.

SIGKILL(-9)만으로 OOM이라고 판정하지 않습니다. 조건 cgroup의 oom_kill 증가와 비정상 종료가
함께 있어야 oom으로 표시합니다. cgroup이 먼저 사라지는 등 카운터를 읽지 못하면
failed_unknown_oom이며 OOM으로 단정하지 않습니다. timeout은 별도 상태로 유지하고 관측 카운터는 보존합니다.

## 아직 연결하지 않은 부분

- #42의 load_mode 기능은 현재 main에 없습니다. 제한 요청에 load_mode를 넣으면 현재는 검증 오류로 막습니다.
  #42 병합 후 조합 로직을 연결하고 YAML 전체 경로로 다시 검증해야 합니다.
  엔진 명령에 -lm none을 직접 전달한 하위 실행 경로의 OOM/후속 조건 검증은 완료했습니다.
- 별도 TTFT PR #55와 독립 브랜치입니다. 메모리 제한 요청에서는 무제한 TTFT 서버가 실행되지 않도록
  TTFT를 비활성화합니다. 명시적인 ttft: true는 오류로 막습니다. 제한된 TTFT는 별도 구현 대상입니다.
- 외부 exp-shared/scripts/validate_request.py와 exp-* 스킬은 저장소에 없어 수정·호환 검증하지 못했습니다.
  memory_max와 condition_timeout_seconds를 허용하고 실패 CSV 행에서 빈 TPS를 처리하도록 맞춰야 합니다.
- 실제 노트북에서 scope 소속·한도·스왑0·카운터 유지·OOM 후 다음 실행과 timeout/Ctrl+C 정리를 검증했습니다.
  #42 YAML 연결·외부 스킬 호환 완료 전까지 초안 상태입니다.

참고: [systemd 자원 제어](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html),
[Linux cgroup v2 memory](https://docs.kernel.org/admin-guide/cgroup-v2.html).
