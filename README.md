# p-project8

2026 AI P-실무프로젝트 8팀 저장소.

> 주제 확정 전이다. 확정되면 프로젝트 소개를 여기에 채운다.

## 링크

| 무엇 | 어디 |
| --- | --- |
| 이슈 · 작업 관리 | [Issues](https://github.com/pproject8/p-project8/issues) · [Projects 보드](https://github.com/orgs/pproject8/projects/2) |
| Proposal · 진행 상황 (발표 때 공유) | [Wiki](https://github.com/pproject8/p-project8/wiki) |
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
