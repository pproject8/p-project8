# p-project8

2026 AI P-실무프로젝트 AI 2분반 8조(에잇비트) 저장소.

**Linux 환경 On-Device AI 워크로드 분석** (SK하이닉스 기업 수요 제안 주제). 리눅스 노트북에서 llama.cpp로 LLM을 CPU 추론하면서 CPU 스레드 수, 메모리 한도, 저장장치 읽기 대역폭을 하나씩 제한해 Loading, Prefill, Decode 단계별 병목을 측정한다. 측정 파이프라인과 데이터는 이 저장소에, 설계와 실험 결과는 [Wiki](https://github.com/pproject8/p-project8/wiki)에 정리한다.

## 공용 측정 코드

코드와 사용·설치·업데이트 안내는 [measurement/](measurement/README.md)에 있습니다. `pp8 submit baseline`으로 Llama 3.2 3B Q4_K_M 기준 요청을 제출하며, 단일 워커가 순서대로 실행합니다. 모델·머신별 설정·공용 큐·실험 결과는 노트북의 `/srv/pp8`에 별도 보관합니다.

현재는 Prefill·Decode 처리량 측정을 지원합니다. 자원 제한·모니터링·로딩 시간·TTFT 측정은 후속 구현 대상입니다.

## 링크

| 무엇 | 어디 |
| --- | --- |
| 이슈 · 작업 관리 | [Issues](https://github.com/pproject8/p-project8/issues) · [Projects 보드](https://github.com/orgs/pproject8/projects/2) |
| 프로젝트 소개, 실험 설계, 운영 방식 (발표 때 공유) | [Wiki](https://github.com/pproject8/p-project8/wiki) |
| 실험 결과 (실험 0 ~ 5 챕터) | [Wiki 실험 결과 목록](https://github.com/pproject8/p-project8/wiki#실험-결과) |
| 사전발표 영상 | [YouTube](https://www.youtube.com/watch?v=nq2-ds6317k) |
| 과목 안내 · 일정 · 회의록 | 팀 Notion |

## 작업 방식

- 할 일은 모두 **이슈**로 만든다. 템플릿은 기능 / 버그 / 실험 세 가지다. 템플릿으로 만든 이슈는 Projects 보드에 자동으로 올라간다.
- 보드의 Status 는 Backlog → Todo → In Progress → In Review → Done (안 하기로 하면 Canceled) 순서로 옮기고, Priority(Urgent·High·Medium·Low)와 Estimate(포인트)를 채운다.
- 이슈에는 `type:` 라벨 하나와 `area:` 라벨을 붙이고, 해당하는 **마일스톤**(사전발표 · 산학 멘토링 1 · 산학 멘토링 2 · 최종발표)을 지정한다.
- 큰 작업은 `type: epic` 이슈를 만들고 하위 이슈(Sub-issues)로 쪼갠다.
- 브랜치는 `{type}/{이슈번호}-{요약}` 형식으로 만든다. 예) `feat/12-login-form`
- PR 본문에 `Closes #이슈번호` 를 적어 merge 시 이슈가 닫히게 한다.
- 실험은 `실험` 템플릿으로 이슈를 열고, 결과를 Baseline 과 나란히 표로 남긴다. 최종발표의 성능 비교 근거가 된다.

### 라벨

| 라벨 | 의미 |
| --- | --- |
| `type: feature` / `bug` / `experiment` / `docs` / `refactor` / `chore` / `epic` | 이슈 종류 |
| `area: model` / `data` / `backend` / `frontend` / `infra` | 작업 영역 |
| `status: blocked` / `needs-discussion` | 막힘, 논의 필요 |

### 커밋

[Conventional Commits](https://www.conventionalcommits.org/ko/v1.0.0/) 를 따르고 subject 는 한국어로 쓴다.

```
feat(model): 베이스라인 학습 스크립트 추가
fix(backend): 업로드 파일 크기 제한이 적용되지 않던 문제 해결
```
