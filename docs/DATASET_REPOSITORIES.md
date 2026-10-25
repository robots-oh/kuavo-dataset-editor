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
UI의 **Export 대상** 기본값은 **현재 선택한 원본만**이다. **프로젝트 전체 라벨**을 선택하면
기존처럼 수집 batch를 한 번에 처리할 수 있다. CLI는 프로젝트 전체 라벨을 사용한다.

추가할 때 대상의 `editor/frame_mapping.parquet`를 기준으로 이미 저장된 원본 프레임을 건너뛴다.
같은 bag의 다른 구간은 추가할 수 있으며, 전부 중복이면 writer를 시작하지 않는다.
확인 창과 `editor/operation.json`의 `skipped_duplicate_frames`에 건너뛴 수를 기록한다.
원본 ID와 저장된 프로젝트/편집 이력의 등록 fingerprint를 비교하므로 같은 bag을 다시 import해
ID가 달라진 경우도 인식한다. fingerprint는 이름·크기·수정 시각 기준이며 전체 파일 내용 checksum은 아니다.
원본 매핑이 없는 외부 dataset의 과거 프레임은 판별할 수 없고, 기존 결과 안의 중복을 정리하지는 않는다.
이미 저장된 프레임의 task를 바꾸려면 추가가 아니라 기존 dataset 수정 모드를 사용한다.

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

## Export 속도와 episode 단위 이어가기

업데이트 후 **새로 시작한 Export**는 episode마다 공식 writer를 마감하고 checkpoint를 저장한다.
작업 목록에서 완료 episode 수를 확인할 수 있다. 실패/취소 후에는 **완료 episode부터 이어가기**를
누른다. CLI에서는 아래 명령을 사용할 수 있다.

```bash
bash run.sh resume-export <job-id> --workspace /data/editor-workspace
```

- 완료된 episode의 영상/data는 그대로 재사용한다. 중단됐던 episode는 첫 프레임부터 다시 처리한다.
- 재개는 **그 작업을 시작할 당시의 annotation과 명시적으로 선택한 누락 보정/제외 결정**을 사용한다.
  이후 편집한 annotation은 변경하지 않는다. 새 편집을 반영하려면 새 Export를 시작한다.
- 다른 저장/업로드가 진행 중이면 중복 실행하지 않는다. 종료된 PID의 이전 lock은 진단용으로
  `.stale-...`에 보존하고 회수한다. 소유자를 모르거나 다른 호스트의 잠금은 자동 회수하지 않는다.
- `jobs/<job-id>/export-checkpoint.json`과 `checkpoint-.../`를 보존해야 이어갈 수 있다.
  중단 시점의 미완료 파일은 같은 job의 `interrupted-.../`에 격리·보존하며 자동 삭제하지 않는다.
  checkpoint는 작은 metadata/매핑을 복사하고 완료 영상을 매번 복제하지 않는다.
- 작업 snapshot·원본/cache·실행 backend가 달라지거나 완료된 파일이 변경/누락되면 재사용하지 않는다.
  파일 목록·크기·수정시간을 확인하며 전체 영상에 대한 checksum 검사를 반복하지 않는다.
- 기존 버전의 job에는 checkpoint가 없으므로 새 Export가 필요하다. annotation/import를 다시 할 필요는 없다.
  기존 partial은 보존하고 lock은 위 소유자 정책에 따라 처리하며, 과거 partial을 자동 복구하지는 않는다.
- worker는 서버 터미널과 분리해서 실행한다. 브라우저/SSH 터널/서버를 닫아도 새 worker는 계속 실행하며,
  서버를 같은 workspace로 다시 열면 상태를 확인한다. OS 재부팅·OOM·강제 종료까지 막는 것은 아니다.
  업데이트 전에 이미 실행된 worker에는 소급 적용되지 않는다.

영상은 공식 streaming encoder API가 있으면 RGB → H264로 직접 인코딩하여 임시 PNG 저장/재읽기를 생략한다.
timestamp NPZ도 프레임마다 압축 해제하지 않고 한 번 읽어 사용한다. H264의 CRF 23/veryfast,
해상도, FPS는 유지한다(구 API는 기존 인코딩 경로로 호환).
`editor/report.json`의 `episode_timings`에는 frame 처리와 episode 마감 시간이 남는다.
이는 실제 데이터·CPU·저장장치에 따라 달라지므로 일정한 배속 향상을 보장하지 않는다.
추가 모드의 최초 전체 dataset 복사와 마지막 게시/작은 로더 확인 시간은 별도다.
기존 dataset 폴더를 복구본으로 교환하던 아주 짧은 게시 단계 자체가 강제 종료된 경우에는
`publication.json`, partial/backup과 원래 경로를 확인한 뒤 복구해야 한다. 임의로 lock/backup을 삭제하지 않는다.

### 프레임 누락 없는 bounded 병렬 처리

공식 실시간 streaming encoder는 큐가 밀리면 프레임을 버릴 수 있으므로, 이 편집기는
**인스턴스 단위 backpressure 어댑터**를 사용한다. 큐가 가득 차면 공간이 날 때까지 기다리며
취소 요청과 encoder 종료를 확인한다. 설치된 LeRobot 소스를 수정하거나 전역 monkey patch하지 않는다.
episode마다 카메라별 입력 frame 수와 실제 MP4 frame 수가 일치해야 checkpoint로 마감한다.
이 문서에서 lossless는 **frame drop이 없음**을 뜻하며, H264 자체가 무손실 codec이라는 뜻은 아니다.

- JPEG 디코딩은 여러 스레드로 미리 준비하지만 결과는 원래 frame/task 순서대로 writer에 전달한다.
  metadata writer는 하나이므로 episode/task 순서가 바뀌지 않는다.
- 기본 CPU 작업 예산은 사용 가능한 CPU의 절반(최대 8)이며, 디코딩/카메라 인코딩에 분배한다.
  이는 스레드 구성의 기준이지 OS 수준 CPU 사용량 제한이나 로봇 제어용 CPU 예약은 아니다.
- 기본 prefetch는 디코딩된 RGB 약 64 MiB, 카메라 큐 합계 약 48 MiB를 기준으로 제한한다.
  한 프레임 자체가 이보다 큰 경우 최소 한 프레임은 필요하며, 라이브러리 내부 메모리는 별도다.
- 반복 미리보기의 누락 보고서와 catalog JSON은 프로세스 내에서 제한적으로 캐싱한다.
  catalog·수치 배열·timestamp 또는 LeRobot metadata/parquet의 현재 파일 식별자·크기·mtime/ctime이
  달라지면 다시 검사한다. 보정/제외 **승인 자체는 캐싱하지 않는다**. 새 worker는 새로 검사한다.
  파일 트리 identity는 디렉터리 mtime만 믿고 생략하지 않고, `scandir`로 syscall 중복을 줄인다.

필요할 때만 서버 시작 전 아래 환경 변수로 조정한다. 기본값부터 사용하고 실제 report를 비교한다.

```bash
EDITOR_EXPORT_CPU_BUDGET=6 \
EDITOR_EXPORT_DECODE_WORKERS=2 \
EDITOR_EXPORT_ENCODER_THREADS=1 \
EDITOR_EXPORT_PREFETCH_FRAMES=4 \
bash run.sh serve --workspace /data/editor-workspace --allow-root /data/recordings
```

`EDITOR_EXPORT_ENCODER_QUEUE_FRAMES`로 카메라별 큐 크기도 지정할 수 있다.
`editor/report.json`의 `export_runtime`, `episode_timings[*].video_frames`, `frame_drop_policy`에
실제 설정과 인코딩 frame 수를 남긴다. CPU 스레드 설정 변경은 영상 품질/해상도/FPS 변경이 아니다.
프레임 드롭 방지 이전 checkpoint는 새 pipeline에서 자동 재사용하지 않는다. 새 Export를 시작한다.
