# Dataset 생성 · 추가 · 수정

여기서 dataset repo는 **로컬 LeRobot 폴더**다. 이 기능은 Hugging Face Hub 저장소를
자동 생성하거나 업로드하지 않는다. Hub 공유는 [별도의 확인형 업로드](HUGGING_FACE.md)로 수행한다.
이름은 `rack-box-v1` 또는 `team/rack-box-v1`
형식이며, 단일 이름에는 내부 repo_id로 `local/`을 붙인다.

## 1. 새 dataset repo 생성

1. bag 또는 LeRobot 원본을 프로젝트에 추가하고 구간/task를 지정한다.
2. Export의 저장 방식을 ‘새 dataset repo 생성’으로 선택한다.
3. 이름을 입력한다. 출력 경로는 선택 사항이다. 비우면 `workspace/exports/<이름의 마지막 부분>`이다.
4. ‘미리보기 후 Export’에서 경로·frame·episode 수를 확인한다.

이미 존재하는 폴더에는 새 생성으로 저장하지 않는다. 이름/공식 writer repo_id는
`editor/dataset.json`과 작업 결과에 기록한다. 출력 경로를 직접 정했다면 폴더 이름과
dataset 이름은 달라도 된다.

## 2. 기존 dataset repo에 추가

1. 추가할 bag/데이터를 프로젝트에 넣고 task 구간을 지정한다.
2. ‘기존 dataset repo에 추가’를 선택하고 기존 dataset 루트 폴더를 지정한다.
3. 변경 확인에 체크하고 미리보기의 ‘기존 유지 + 선택 구간 추가’ 수를 확인한다.

기존 모든 episode와 task는 유지하고 새 선택 구간을 새로운 episode로 추가한다.
대상 dataset 자체를 같은 프로젝트에 불러온 경우 그 원본의 clip은 자동으로 제외한다.
같은 bag/clip을 반복 Export하는 일반적인 중복은 자동 제거하지 않으므로 새 수집분만
선택하거나 수집 batch별 프로젝트를 사용한다.

기존/추가 데이터의 FPS와 feature schema(카메라/해상도·state/action 차원 등)가 같아야 한다.
추가는 v3.0과 기본 lock의 공식 `resume` API를 사용한다. 기존 영상 codec을 유지한다.
혼합 codec 또는 지원하지 않는 codec/metadata는 새 dataset으로 먼저 정리해야 한다.
H264 생성·추가·수정의 실제 작은 writer/loader 테스트를 수행했으며, 모든 외부 codec의
실행을 검증했다는 뜻은 아니다.

## 3. 기존 dataset repo 수정

1. 새 프로젝트에서 수정할 기존 LeRobot 폴더를 원본으로 불러온다.
2. 기존 프레임별 task가 연속 clip으로 자동 등록된다. 손대지 않은 구간은 그대로 포함된다.
3. task 변경·trim·분할·구간 제외를 수행한다.
4. ‘기존 dataset repo 수정’과 같은 원본 폴더를 선택한다.
5. 변경 확인 후 미리보기의 최종 frame/episode 수로 저장한다.

**현재 프로젝트에서 라벨이 남은 구간 전체가 수정 결과**다. 라벨을 제거한 구간은 삭제된
결과로 출력된다. 불러오지 않은 다른 dataset을 이 모드로 교체하지 않는다.
여러 원본을 프로젝트에 넣었다면 선택된 모든 원본의 clip이 결과에 포함된다.
따라서 한 dataset을 수정하려면 그 dataset 전용 프로젝트를 쓰는 것이 명확하다.

## 저장 과정과 복구

- 동일 부모 경로의 `.dataset.<job>.partial`에서 결과를 완성하고 공식 로더로 작은 확인을 한다.
- 기존 dataset을 `.dataset.backup-<job>`으로 이름 변경한 뒤 완성 결과를 원래 경로에 게시한다.
- 게시 rename 실패 시 원래 경로로 복구한다. 이전 dataset과 중간 partial은 자동 삭제하지 않는다.
- 기존 dataset의 card/license/Git metadata 등 추가 root 파일과 editor 이력을 보존한다.
  dataset card에 수동으로 적은 frame 수 등은 새 통계와 별도로 확인해야 한다.
- 진행 중인 다른 저장이나 기존 파일 변경이 감지되면 기존 데이터셋을 교체하지 않는다.
- 작업 목록에 결과 경로와 복구본 경로를 표시한다. 원래 bag은 변경하지 않는다.
- 기존 dataset을 참조하던 프로젝트는 **복구본의 원본 프레임을 계속 참조**하도록 경로와
  revision을 갱신하며, 사용자가 저장한 clip/task 내용은 유지한다. 미적용 입력을 확인한 뒤
  프로젝트를 다시 열고, 새 결과를 편집하려면 결과 dataset을 새로 불러온다.
- 부모 폴더에도 접근 권한이 필요하다. 웹 서버는 dataset 부모를 `--allow-root`로 허용하고,
  데스크톱은 상단 ‘출력 경로 선택’에서 저장 방식에 맞춰 폴더를 고르면 된다.

추가는 기존 dataset 전체를 staging에 복사한다. 큰 dataset은 추가 디스크 공간과 복사 시간이
필요하며, 복구본도 누적된다. 충분한 공간을 확보하고 복구본 정리는 사용자가 따로 결정한다.
같은 dataset에 다른 수집기/편집기를 동시에 쓰지 않는다. 비정상 강제 종료 시
`.dataset.editor-write.lock`, job 로그, partial/backup 위치를 먼저 확인하고 작업이 살아 있는 동안
lock을 지우지 않는다. `publication.json` 및 결과의 `editor/operation.json`이 복구 근거다.

## CLI

```bash
bash run.sh export /data/project.json /data/rack-box-v1 --repo-name team/rack-box-v1
bash run.sh export /data/new-clips.json /data/rack-box-v1 --mode append --confirm-existing
bash run.sh export /data/edited-existing.json /data/rack-box-v1 --mode replace --confirm-existing
```

CLI도 UI와 같은 worker/저장 계획을 사용한다. 편집 프로젝트가 같은 workspace에 있으면
`--jobs-root /data/editor-workspace/jobs`로 지정해야 저장 후 해당 프로젝트 경로도 갱신된다.
