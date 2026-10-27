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
| meta.json | 모델 해시, 실행 환경, 성공·실패 개수, 측정 코드 Git 커밋 |
| request.yaml | 실행에 사용한 요청 원본 |
| run.log | 실행 명령과 실패 시 오류 기록 |

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

- 현재 개인 실행에는 공통 잠금이나 공용 큐를 적용하지 않았습니다. 서로 실행하면 측정이 동시에 돌아갈 수 있으므로 시간을 조율합니다.
- 같은 노트북의 CPU·메모리·저장장치를 공유합니다. 개인 폴더만 분리되어 있다고 측정 간 간섭이 사라지지는 않습니다.
- 개인 직접 실행 중에는 SSH 창을 유지합니다. 중단하려면 `Ctrl+C`를 사용합니다.
- 이 안내에서는 `pp8 submit`, `pp8 worker` 명령을 사용하지 않습니다.
- 현재 측정 지표는 Prefill·Decode 처리량입니다. 자원 제한·자원 모니터링·로딩 시간·TTFT 측정은 추후 추가합니다.
- `jungwon` 계정에서 이 방식으로 3B 실제 실행을 확인했습니다. 다른 팀원 계정에서 권한 오류가 나면 임의로 권한을 넓히지 말고 오류 내용을 공유해주세요. 그룹 권한이 적용되지 않았다면 SSH에 새로 접속한 뒤 다시 확인합니다.
