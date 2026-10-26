# Hugging Face dataset 공유

편집·bag import·로컬 export는 오프라인이다. Hub 미리보기/업로드만 격리된 온라인 worker에서
공식 `huggingface_hub.HfApi`를 사용한다. 실제 로봇 데이터 업로드는 자동 실행하지 않는다.

## 로그인

편집기를 실행하는 PC에서 저장소 쓰기 권한이 있는 계정으로 실행한다.

```bash
"$(dirname "$(bash run.sh --print-python)")/hf" auth login
"$(dirname "$(bash run.sh --print-python)")/hf" auth whoami
```

저장된 Hub 로그인 또는 앱 시작 전 주입한 `HF_TOKEN`을 사용한다. token을 CLI 인자, UI,
Git, project/job JSON, 스크린샷에 넣지 않는다. 조직 repo는 해당 조직 쓰기 권한도 필요하다.
`HF_HOME`을 변경한다면 로그인과 앱에서 같은 값을 쓴다. Export의 임시 HF cache는
별도 worker 안에만 적용되므로 기존 로그인 파일을 변경하지 않는다.

## UI 사용 순서

1. 로컬 Export를 완료한다. 추가/수정이라면 로컬에서 최종 dataset을 먼저 완성한다.
2. 완료된 작업의 **이 결과를 Hub 업로드 경로로 사용**을 누른다.
3. `owner/dataset-name`을 입력한다. 새 repo는 비공개 기본값이다.
4. **업로드 미리보기 (전송 안 함)**를 누른다. 이 단계는 Hub 상태만 읽는다.
5. 작업 목록에서 실제 공개 범위, 전송량, 전송/삭제/제외 목록을 확인한다.
6. **대상·공개 범위 확인 후 실제 업로드**를 누르고 확인한다.
7. 완료 결과의 URL과 commit SHA를 기록한다.

PC별 업로드 기본값은 Git에서 제외되는 `src/kuavo_dataset_editor/static/hub-target.local.json`에
`workspace`, `path`, `repo_id`, `private`로 지정할 수 있다. 해당 workspace에서 화면을 새로고침하면
빈 업로드 입력란에 반영된다. 이전한 저장소 이름을 `previous_repo_ids` 배열에 넣으면
새로고침으로 복원된 이전 대상도 새 경로로 바뀐다. 다른 저장소를 직접 선택한 값은 유지한다.
이 설정을 읽거나 로컬 Export를 완료하는 것만으로 Hub에 전송하지는 않는다.

**Hugging Face 작업**에서는 **업로드 미리보기 완료 · 전송 안 함**과 **실제 Hub 업로드 완료**를
별도 목록으로 표시한다. 가장 최근 작업이 있는 완료 목록이 먼저 나오고, 각 목록 안에서도
최근 작업부터 정렬한다. 시작 시각(KST)과 대상 저장소를
함께 보여준다. 두 완료 목록은 기본으로 펼쳐지고 **취소된 이전 작업**은 따로 접혀 있다.
목록을 펼치거나 접은 상태는 작업 목록이 자동 갱신되어도 유지된다.

외부 dataset 폴더를 직접 지정한다면 상위 폴더도 `--allow-root`로 허용한다.
전송 중 local append/replace와 충돌하지 않도록 같은 상위 폴더에 lock을 만든다.
Export 결과를 선택하는 일반 흐름에서는 출력 경로 허용이 이미 설정되어 있다.

기존 repo의 공개 범위는 바꾸지 않는다. 새 공개 repo를 원하면 비공개 체크를 해제한다.
영상에 사람·화면·시설 정보가 포함되는지, 재배포 권한이 있는지 직접 확인한다.
생성 card는 라이선스를 임의 지정하지 않는다. 배포 전에 소유자가 dataset card/라이선스를 작성한다.

## 전송 범위

| 항목 | 처리 |
| --- | --- |
| `meta/`의 JSON/JSONL/Parquet | 공식 metadata 전송 |
| `data/`의 Parquet | state/action/task 데이터 전송 |
| `videos/`의 MP4, `images/`의 PNG/JPEG | 영상/이미지 전송 |
| `editor/` 전체 | 제외: 절대 경로, 편집 이력, tag/TF/Quest sparse event sidecar 포함 |
| bag, cache, `.git`, 숨김 파일, 환경, 토큰 | 제외 |
| 심볼릭 링크 | 대상을 따라가지 않고 업로드 요청 거부 |
| 기존 Hub README/license/기타 파일 | 보존. README가 없으면 경로 없는 기본 card 생성 |

파일 내용의 개인정보를 자동 검열하는 기능은 아니다.
tag-7 등이 필요한 내부 협업자는 로컬 `editor/events.jsonl`을 별도로 검토·공유한다.

## 기존 Hub dataset 갱신

원격 append가 아니라 **완성된 로컬 snapshot 동기화**다. 먼저 로컬 append/replace로 최종
dataset을 만든 뒤 업로드한다. 원격의 오래된 관리 대상 dataset 파일만 삭제 목록에 표시하고
같은 commit에서 제거한다. README/license 등 그 외 파일은 보존한다.
이전 Hub commit으로 이전 snapshot을 참조할 수 있다.

확인 후 원격 HEAD/공개 범위가 달라지면 미리보기를 다시 요구한다. 로컬은 파일명/크기/mtime
manifest와 편집기 저장 lock을 사용한다. 외부 프로그램이 같은 크기·mtime으로 변경한 것은
감지하지 못하므로 전송 중 dataset 폴더를 수정하지 않는다.

## CLI

첫 명령은 조회만 한다. `--confirm`이 있어야 외부 쓰기를 수행한다.

```bash
# Read-only preview
bash run.sh hub /data/exports/rack-box-v1 team/rack-box-v1

# New private repo: publish after reviewing the preview
bash run.sh hub /data/exports/rack-box-v1 team/rack-box-v1 --confirm

# Existing repo: synchronize the complete snapshot
bash run.sh hub /data/exports/rack-box-v1 team/rack-box-v1 --confirm --sync-existing

# Explicitly create a new public repo
bash run.sh hub /data/exports/public-v1 team/public-v1 --public --confirm
```

CLI는 해당 실행의 미리보기를 출력한 다음 확인 플래그에 따라 진행한다.
메타데이터와 영상은 하나의 commit으로 반영한다. 전송 중에는 기존 원격 snapshot이 유지된다.
새 repo 생성 후 실패하면 빈 repo가 남을 수 있다. 이미 전송한 chunk가 원격에 남을 수 있다.
현재 앱은 일반 크기의 단일 commit 게시용이다. 수만 개 파일/매우 큰 dataset은 Hub 제한에
걸릴 수 있다. 파일별 재개 UI·다중 commit·전송 중 즉시 취소는 제공하지 않는다.
실패하면 로그를 보고 새 미리보기를 생성해 재시도한다. 로컬 dataset은 유지된다.
commit 응답 직전 연결이 끊기면 원격에는 이미 반영됐을 수 있다. Hub commit 이력과 새 미리보기를
확인한 뒤 재시도한다. 앱을 닫아도 독립 업로드 worker는 계속 실행된다.

## LeRobot에서 읽기

기존 version tag를 이동하거나 새 tag를 자동 생성하지 않는다. 완료 결과의 **commit SHA**를 지정한다.

```python
from lerobot.datasets.lerobot_dataset import LeRobotDataset
dataset = LeRobotDataset("team/rack-box-v1", revision="<completed commit SHA>")
# Latest snapshot instead: revision="main"
```

비공개 dataset은 다운로드 PC에서도 읽기 권한 로그인이 필요하다.
policy별 action 구성과 validity mask 처리는 별도 학습 단계다.

## 문제 해결

- 로그인 없음: 같은 PC/사용자/HF_HOME에서 로그인 후 미리보기를 다시 생성한다.
- 권한/404: 소유자·조직 쓰기 권한·토큰 권한을 확인한다. 접근 불가 repo를 덮어쓰지 않는다.
- 로컬/원격 변경: 최종 결과 확인 후 새 미리보기를 생성한다.
- lock: 다른 저장/업로드 작업 완료를 기다린다. 죽은 프로세스라면 PID/job 로그를 확인한 후 남은 lock을 수동 복구한다.
- 업로드 후 이전 데이터가 로딩됨: 최신 완료 commit SHA를 `revision`에 지정한다.

공식 참고: [Hub SDK 업로드](https://huggingface.co/docs/huggingface_hub/en/guides/upload),
[CLI 인증](https://huggingface.co/docs/huggingface_hub/en/guides/cli#hf-auth-login),
[LeRobot](https://github.com/huggingface/lerobot).
