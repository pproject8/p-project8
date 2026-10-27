# 공용 측정 워커

YAML 요청을 제출하면 단일 Linux 워커가 llama-bench를 순차 실행하고 결과를 요청별로 저장합니다. SSH 연결을 끊어도 systemd 서비스는 계속 요청을 처리합니다. 현재는 Prefill·Decode 처리량 측정을 지원하며, 메모리·저장장치 제한, 자원 모니터링, 로딩 시간과 TTFT는 후속 구현 대상입니다.

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
