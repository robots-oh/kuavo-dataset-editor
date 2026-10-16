# Kuavo LeRobot 영상 데이터셋 편집기

로봇 PC 연결 없이 로컬에서 bag/LeRobot 데이터를 보고, 시간 구간별 task를 입력하고,
선택한 구간만 LeRobot 데이터셋으로 export하는 로컬 우선 도구다.
새 dataset 생성·기존 dataset 추가/수정과 선택적인 Hugging Face 업로드를 지원한다.
ROS master, Isaac Sim, Quest, 로봇 제어 프로세스가 필요하지 않다.

이 저장소는 독립 Python 패키지다. `kuavo_ros_application`이나 HumanoidScene checkout이
필요하지 않으며, 로봇 수집·제어 코드는 포함하지 않는다.

```text
kuavo-dataset-editor/
├── src/kuavo_dataset_editor/   # 편집·bag import·LeRobot export·데스크톱 창
├── tests/                     # 작은 합성/옵트인 검증
├── tools/bag_preview.py       # ROS master 없이 bag 영상 추출
├── docs/                     # 구조·개발·검증 기록
├── pyproject.toml            # Python 패키지 및 선택 의존성
└── setup.sh / desktop.sh / run.sh / package.sh
```

[새 PC 설치·실행](docs/GETTING_STARTED.md) · [LeRobot 업데이트](docs/LEROBOT_COMPATIBILITY.md) · [구조](docs/ARCHITECTURE.md) · [개발·패키징](docs/DEVELOPMENT.md) · [검증 범위](docs/VALIDATION.md)

[Dataset 이름 지정·기존 repo 추가/수정](docs/DATASET_REPOSITORIES.md)

[Hugging Face 업로드](docs/HUGGING_FACE.md) · [문서 사이트 / GitHub Pages 배포](docs/GITHUB_PAGES.md)

## 처음 사용하는 사람의 순서

1. 이 저장소를 clone/다운로드하고 `bash setup.sh` → `bash desktop.sh`를 실행한다.
2. 새 프로젝트에서 bag 또는 기존 LeRobot 폴더를 불러온다. 완료된 import를 프로젝트에 추가한다.
3. 영상을 보면서 종료점과 task를 적용한다. 다음 task는 직전 끝에서 시작하며, 드래그/시간 입력으로 수정할 수 있다.
4. Export에서 새 이름 또는 기존 dataset 폴더를 지정한다. 미지정 구간은 제외된다.
5. 로컬 결과를 확인한다. 공유가 필요할 때만 별도의 Hugging Face 업로드 미리보기 → 확인을 실행한다.

여러 bag은 하나의 프로젝트에 차례로 추가하고, 각 원본에 task를 붙인 뒤 한 번 Export한다.
서로 다른 원본/episode와 제외 구간 경계는 유지되며, 동시 촬영을 자동 동기화하거나 중복 제거하지 않는다.

English: A standalone, local-first ROS1 bag / LeRobot video timeline editor. Import recordings,
label and trim task intervals, export with official LeRobot writers, optionally sync a complete
dataset snapshot to Hugging Face. Linux x86_64 / Python 3.12; no ROS installation or robot connection required.

## 설치

현재 지원 환경은 Linux x86_64, Python 3.12, CPU PyTorch다. ROS/Isaac용 Python에 설치하지 않는다.
새 Ubuntu PC의 OS 라이브러리는 `bash tools/install_system_deps.sh`로 명시적으로 준비한다.
이 폴더에서 다음 명령을 실행하면 잠금파일로 편집기 전용 `.venv`와 필요한 의존성을 생성한다.

```bash
bash setup.sh
bash desktop.sh
```

데스크톱 창이 열린다. 웹 모드는 `bash run.sh` 후 `http://127.0.0.1:8765`를 연다.
처음 설치에만 인터넷이 필요하며, 편집·bag 읽기·export는 로컬 파일만 사용한다.
기본 설치에는 desktop과 export가 포함된다. 읽기/UI용 최소 환경만 필요하면 `bash setup.sh --minimal`을 쓴다.
CPU 전용 PyTorch를 먼저 설치하므로 GPU나 CUDA 설치가 필요하지 않다.

## 별도 데스크톱 앱

기존 영상 편집 화면을 Qt/WebEngine 전용 창에 담는다. 외부 브라우저나 서버 터미널을 따로
열 필요가 없으며, 편집·변환 엔진은 웹 버전과 동일하다. 완전히 새 네이티브 타임라인을
별도로 구현한 것은 아니다. Linux의 GUI 세션에서 실행한다. SSH만 있는 headless PC에서는
기존 웹 서버 + SSH 터널을 사용하거나 GUI PC로 데이터를 옮긴다.

```bash
bash setup.sh --with-export --with-desktop
bash desktop.sh --workspace /data/editor-workspace --allow-root /data/robot-datasets
```

서버는 앱 내부에서 localhost 임의 포트로 자동 시작하고 종료한다. 기존 웹 서버를 종료하거나
8765 포트를 차지하지 않는다. 같은 workspace를 지정하면 기존 프로젝트·라벨을 그대로 연다.
두 창에서 같은 프로젝트를 동시에 수정하면 기존 revision 충돌 처리로 다시 열어야 한다.

- 상단 ‘Bag 선택 / LeRobot 폴더 선택’: OS 파일 선택창으로 입력 경로를 채운다.
  선택한 파일의 상위 폴더 또는 선택한 데이터셋 폴더에만 접근을 추가 허용한다.
  이 허용 목록은 workspace의 `desktop-roots.json`에 기록해 재실행 후에도 유지한다.
  **선택만으로 import하지 않으며**, 기존 ‘원본 불러오기’를 눌러 시작한다.
- ‘출력 경로 선택’: 새 생성은 상위 폴더/이름을, 추가·수정은 기존 LeRobot 폴더를 고른다.
  선택만으로 저장하지 않으며 ‘미리보기 후 Export’로 진행한다. 기존 데이터셋 변경은 복구본을 보존한다.
- ‘프로젝트 JSON’: OS 저장 대화상자로 내려받는다. `F11`은 앱 전체화면이다.
- 앱 종료 시 저장하지 않은 입력을 확인한다. 적용한 라벨은 보존하고 import/export worker는
  강제 종료하지 않는다. 앱을 다시 열어 같은 workspace에서 작업 상태를 확인할 수 있다.

Linux 실행 아이콘도 선택적으로 생성할 수 있다. 기존 파일은 덮어쓰지 않는다.

```bash
bash run.sh desktop --workspace /data/editor-workspace --allow-root /data/robot-datasets \
  --write-shortcut /absolute/path/KuavoDatasetEditor.desktop
```

파일 관리자에 따라 ‘실행 허용’이 필요하다. 앱 메뉴에 등록하려면 출력 위치를
`~/.local/share/applications/KuavoDatasetEditor.desktop`로 지정한다.
아이콘은 현재 Python과 소스의 절대 경로를 사용하므로 다른 PC로 옮긴 뒤 다시 생성한다.
PySide6/WebEngine 추가 설치 공간은 개발 PC에서 약 673 MiB였다(버전에 따라 달라짐).
UI에도 별도 엔진 프로세스와 메모리가 추가되지만 주된 CPU/디스크 작업은 기존 import/export다.
학습용 GPU나 ROS는 필요하지 않다. 완성 바이너리/AppImage 설치 패키지는 아직 제공하지 않는다.

창·task 버튼·경로 입력·내장 서버의 작은 임시 프로젝트 확인:

```bash
EDITOR_TEST_DESKTOP=1 PYTHONPATH=src "$EDITOR_PYTHON" -m pytest -q tests/test_desktop.py
```

별도 Python 환경을 이미 준비했다면 `EDITOR_PYTHON`으로 지정할 수 있다.
공식 LeRobot 소스 checkout을 사용할 때는 `LEROBOT_SOURCE=/absolute/path/lerobot/src`를 지정한다.
확인한 공식 코드 commit은 `d20a4538016d9fe0374a34011aebc1c8cddf3e0d`이며 dataset 형식은 v3.0이다.
서로 다른 PC에서 실행 환경을 재현하려면 같은 LeRobot 버전을 사용한다.
해당 소스는 0.6.2 개발 버전이며 PyPI 공개 버전과 다를 수 있어 pyproject의 공식 commit 아카이브와 uv.lock으로 고정한다.

## 데이터 경로 지정

기본 접근 허용 경로는 편집기 `workspace/`뿐이다. 외부 데이터 폴더를 읽으려면 명시한다.
경로는 브라우저가 아닌 **서버를 실행한 로컬 PC의 경로**다.

```bash
bash run.sh serve \
  --workspace /data/editor-workspace \
  --allow-root /data/robot-datasets \
  --port 8765
```

UI의 ‘서버의 데이터 경로’에 `.bag` 파일 또는 `meta/info.json`이 있는 LeRobot 루트 폴더를 입력한다.
‘원본 불러오기’를 누른 뒤 작업 완료 시 ‘현재 프로젝트에 추가’를 누른다.
LeRobot 원본은 기존 task 구간도 자동으로 불러오므로 그대로 수정할 수 있다.
로봇 PC가 꺼져 있어도, 이미 복사한 bag으로 모든 작업을 할 수 있다.

Bag을 한 번 복사하는 예시는 다음과 같다. 이 작업은 수집 프로그램을 실행하지 않는다.

```bash
mkdir -p /data/robot-datasets
scp leju_kuavo@<UP_PC_IP>:/absolute/path/trial.bag /data/robot-datasets/
```

## 빠른 사용

1. ‘작은 데모 열기’ → 완료된 작업의 ‘현재 프로젝트에 추가’로 실제 데이터 없이 조작을 익힌다.
2. 영상 재생 또는 타임라인 클릭으로 이동한다. 여러 카메라는 같은 원본 frame 기준이다.
3. `I`로 시작점을, `O`로 현재 프레임까지의 끝점을 지정한다. 드래그 선택과 숫자 입력도 가능하다.
4. task 문장을 입력하고 ‘새 구간에 task 적용’을 누른다. 겹치면 교체 여부를 물어본다.
   적용 후 다음 구간의 시작점은 방금 지정한 끝점으로 자동 이동한다. 이후에는 영상을 보며 `O`로 끝점만 정하고 다음 task를 입력하면 된다.
   시작점을 바꾸려면 기존처럼 `I`, 시간/프레임 입력 또는 타임라인 드래그를 사용한다.
   기존 프로젝트/episode를 다시 열면 마지막으로 라벨이 지정된 구간의 끝에서 이어서 볼 수 있다.
   기존 clip 수정은 새 task 적용과 구분하며, 자동으로 다른 clip을 바꾸거나 빈 구간을 채우지 않는다.
5. clip을 선택한 뒤 `S`로 현재 위치에서 분할한다. 양끝 손잡이 또는 프레임 숫자로 trim한다.
6. ‘선택 clip 수정’으로 task/경계/새 episode 여부를 저장한다. 기존 task 목록도 재사용할 수 있다.
7. 버릴 부분을 선택하고 ‘선택 구간 라벨 제거 / 제외’를 누른다. 원본 데이터는 지워지지 않는다.
8. Export에서 새 생성/기존 추가/기존 수정 중 선택한다. 새 생성은 이름을 입력한다.
   ‘미리보기 후 Export’에서 최종 frame/episode 수와 복구본 여부를 확인하고 시작한다.

`Space`: 재생/정지. `←/→`: 한 프레임 이동. 재생 배속·확대·선택 구간 이어 보기를 제공한다.
새 구간 드래그와 clip 양끝 trim은 가까운 기존 경계에 8 화면 pixel 이내에서 맞춰진다.
충분히 멀면 자유롭게 지정되며, `Alt`를 누른 채 드래그하면 근처에서도 스냅하지 않는다.
드래그하는 동안 카메라 영상도 이동한다. 시작 경계는 첫 포함 frame, 끝 경계는 마지막 포함 frame을 보여준다.
입력란에서 타이핑할 때는 편집 단축키가 실행되지 않는다.
구간은 `[시작 포함, 끝 미포함)`이며 `O`는 현재 프레임도 포함하도록 끝점을 `현재+1`로 둔다.
편집은 SQLite 프로젝트 revision으로 자동 저장한다. Undo/Redo는 현재 브라우저 세션에서 사용할 수 있다.
서버 재시작 후에도 프로젝트와 이전 revision은 보존한다. 프로젝트 JSON도 내려받을 수 있다.

## 포함·제외와 episode 규칙

Task 입력란 아래 후보 버튼을 클릭하면 영어 instruction이 입력된다.
클릭만으로 기존 라벨이 바뀌거나 구간이 저장되지는 않는다. 새 구간 적용/선택 clip 수정으로 저장한다.

| 후보 | Instruction |
|---|---|
| 랙 정렬 | Align the robot with the rack. |
| 상자 집기 | Pick up the box from the rack. |
| conveyor 앞으로 옮기기 | Carry the box to the front of the conveyor. |
| 상자 놓기 | Put down the box. |

‘상자 놓기’는 목적지를 임의로 제한하지 않는 문장으로 둔다. 기존 수동 문장과 재사용 목록도 유지된다.

- 라벨이 없는 구간은 export에서 제외한다. 빈 task를 자동으로 직전 task로 채우지 않는다.
- 연속된 라벨 구간은 기본적으로 한 episode다. 같은 episode 안에서도 task가 프레임별로 바뀐다.
- 미지정 구간을 건너뛰거나 다른 원본 episode로 이동하면 반드시 별도 episode를 만든다.
- clip의 ‘새 episode 시작’으로 연속 동작도 분리할 수 있다.
- A-off, 실패, 대기, 초기 자세 복귀는 자동 제거하지 않는다. task를 붙이면 포함된다.
- tag-7이 없어도 RGB/state/action 수집 구간은 유지된다. 검출 메시지는 sparse annotation으로 보존한다.
- 성공 여부나 측정 state에서 누락된 action을 임의로 만들어 넣지 않는다.
- 원본 session의 모든 clip에 같은 train/validation/test 배정을 적용한다. 원본을 양쪽 split으로 나누지 않는다.

## Rosbag profile

기본 `profiles/kuavo_5w.json`은 5W 팔과 Leju claw, 선택적 base topic 매핑이다.
UI의 ‘Bag profile 설정’에서 topic/FPS/허용 오차/feature를 수정할 수 있다.
profile 변경은 데이터 변환 설정만 바꾸며 로봇 설정을 변경하지 않는다.

- 팔 state: `/sensors_data_raw`의 `joint_data.joint_q[4:18]`, rad.
- 팔 action: `/kuavo_arm_traj.position`, degree → rad로 한 번 변환.
- gripper: `data.name`으로 좌우 정렬, position 0–100을 개방도 0–1로 변환. command와 feedback은 별도 feature다.
- base command는 `/cmd_vel`의 vx/vy/yaw 속도이며, `/odom` 측정값과 구분한다.
- torso/Quest/TF/tag 이벤트는 별도 annotation으로 보존한다. torso 속도를 관절 action으로 바꾸지 않는다.
- 시간 기준은 bag receipt time이다. 각 signal/camera의 실제 message 시간 매핑도 저장한다.
- command는 과거 sample hold, state/RGB는 제한된 오차 내 nearest 매칭을 사용한다.
- 필수 topic이 없으면 profile 확인을 요청한다. 없는 선택적 topic은 import 보고서에 표시한다.
- 필수 state/action sample 누락은 invalid로 표시한다. 해당 영상을 보며 구간을 제외하거나 profile을 조정한다.
- 선택적 gripper/base topic의 일부 sample이 없으면 `validity.<feature>` mask를 함께 저장한다.
  mask가 false인 자리의 0은 저장용 placeholder이지 측정값이 아니다. 그 값을 학습에 쓰지 않도록 mask를 적용해야 한다.
  이 누락 때문에 A-off/대기 구간 전체를 자동 제거하거나 export를 막지 않는다.

기본은 RGB 3개 카메라다. 다른 topic이나 카메라 수는 profile을 바꿔 사용한다.
Depth를 RGB로 잘못 인코딩하지 않도록 현재 RGB 편집 경로에서는 depth 영상 입력을 명시적으로 지원하지 않는다.
팔/gripper/base feature를 하나의 policy action 벡터로 묶는 것은 학습 policy별 별도 profile 작업이다.
원본 FPS가 다른 데이터셋을 하나의 export로 자동 재샘플링하지 않는다.

Bag은 rosbags가 파일 안의 message definition을 읽으므로 `kuavo_msgs`를 ROS workspace에 빌드할 필요가 없다.
ROS1의 `kError` 같은 소문자 상수를 사용하는 제조사 메시지도 로컬 어댑터에서 처리한다.
파싱 시 상수 이름만 정규화하고 원래 이름으로 복원해 MD5를 확인하므로 bag이나 on-wire 필드를 바꾸지 않는다.
import는 압축 RGB의 JPEG/PNG 원본 bytes를 그대로 보존하고, raw RGB만 PNG로 저장한다.
mmap용 숫자 배열도 별도 작업 cache에 생성한다. 원본보다 cache가 커질 수 있다.
큰 bag은 로컬 여유 공간을 확보하고 필요한 파일부터 불러오는 것이 좋다.

## 출력과 원본 보존

공식 LeRobot writer로 staging dataset을 만든다. 영상·state·action·task를 같은 frame 매핑으로 선택하고,
episode/frame/global index, task 목록과 통계를 새로 작성한다.
새 생성은 기존 경로를 덮어쓰지 않는다. 명시적인 기존 추가/수정 모드는 복구본을 보존한 뒤 교체한다.
원본 경로 하위 export나 관련 없는 dataset 교체는 허용하지 않는다.
미완료 출력은 `.partial` 폴더에 남기며 완료 결과와 구분된다.

출력의 `editor/`에는 다음 내용이 있다.

- `project.json`: 고정한 편집 snapshot.
- `export_plan.json`: 선택 구간과 출력 episode 구성.
- `frame_mapping.parquet`: 출력 frame → 원본 episode/frame/clip/task/split.
- `events.jsonl`: 선택 구간에 포함된 TF/tag/Quest/세션 이벤트와 출력 시간.
- `truncation.json`: 편집 때문에 원본 시작/끝이 잘렸는지. 성공/terminal 값은 원본 그대로 보존한다.
- `report.json`: writer 버전, 출력 수와 작은 loader round-trip 결과.
- `dataset.json`, `operation.json`, `history/`: dataset 이름, 저장 방식·복구본, 이전 editor 기록.

Export 시작 후 계속 편집해도 진행 중인 job의 라벨은 바뀌지 않는다.
출력은 처음/끝 frame만 공식 loader로 확인하며 전체를 다시 디코딩하지 않는다.
학습 시 action chunk는 episode 경계를 넘어가면 안 된다.
로컬 Export는 Hub에 접속하지 않는다. Hub 공유는 아래 별도 기능으로만 실행한다.
이 도구는 학습 실행이나 로봇 제어를 수행하지 않는다.

## Hugging Face에 공유하기 (선택)

편집기 PC의 터미널에서 한 번 로그인한다. UI, project JSON, job 요청에 token을 입력하지 않는다.

```bash
.venv/bin/hf auth login
.venv/bin/hf auth whoami
```

완료된 Export 작업의 **이 결과를 Hub 업로드 경로로 사용**을 누르고,
오른쪽에 `owner/dataset-name`을 입력한다. 새 repo 기본값은 비공개다.
**업로드 미리보기**는 로컬 파일 목록과 Hub 상태만 읽는다. 작업 목록에서
실제 공개 범위·파일 수·삭제 목록을 확인한 뒤 **실제 업로드**를 눌러야 전송한다.

- 기존 Hub dataset은 로컬 전체 결과와 동기화한다. 원격 데이터에 episode를 병합하지 않는다.
  먼저 로컬 append/replace로 완성한 dataset을 준비한다.
- 기존 원격의 오래된 `meta/`, `data/`, `videos/`, `images/` dataset 파일은 확인한 목록에 한해 같은 commit에서 삭제한다.
  기존 공개 범위와 README/license/기타 파일은 유지한다.
- `editor/` 전체(로컬 절대 경로·편집 이력·tag/TF/Quest sparse events 포함), bag, 환경, 인증정보는 업로드 대상이 아니다.
  sparse events도 필요한 협업자는 로컬 `editor/events.jsonl`을 별도로 검토·공유해야 한다.
- 전송은 하나의 commit으로 반영하며 version tag를 이동하지 않는다.
  결과에 표시된 **commit SHA를 LeRobot loader의 `revision`에 지정**한다.
- 대용량 전송은 진행 로그를 확인한다. 현재 앱은 파일별 재개 UI나 전송 도중 즉시 취소를 제공하지 않는다.
  실패 후 미리보기를 다시 생성해 재시도할 수 있다. 새 빈 Hub repo가 남을 수 있다.

자세한 UI/CLI 예시와 주의사항은 [Hub 가이드](docs/HUGGING_FACE.md)를 참고한다.

## 작업 관리와 batch

가져오기/export는 별도 프로세스다. UI를 닫아도 작업은 계속되고 작업 목록에서 상태와 로그를 볼 수 있다.
‘작업 취소’는 다음 처리 단위에서 적용된다. 원본과 완료된 결과는 삭제하지 않는다.
동시에 여러 파일의 import/export를 시작할 수 있다. 큰 bag 여러 개를 동시에 열면 disk/CPU 사용량이 늘어난다.
CLI export도 UI와 같은 구간 처리 코어를 사용한다.

```bash
bash run.sh inspect-bag /data/robot-datasets/trial.bag
bash run.sh export /data/project.json /data/new-edited-dataset --repo-name rack-box-v1 --jobs-root /data/editor-workspace/jobs
```

## 로컬 개발 후 다른 PC로 옮기기

Git에서 관리할 대상은 이 폴더의 소스/설정/문서다. `.venv`, workspace, bag, 영상은 Git에 올리지 않는다.
배포 시 같은 소스를 checkout하고 `bash setup.sh --with-export`로 환경을 새로 만든다.
Python 가상환경 디렉터리를 PC 간 복사하지 않는다.
`bash package.sh`로 소스 전용 tar.gz를 만들 수 있다. 환경·bag·workspace·인증정보는 포함하지 않는다.
배포 PC에서 압축을 푼 뒤 동일하게 `bash setup.sh --with-export`로 설치한다.
프로젝트를 이어서 편집하려면 workspace와 원본 데이터도 옮기고 원본의 절대 경로를 유지하는 것이 가장 간단하다.
현재 경로 재배치 wizard는 없으므로 새 PC에서 원본 위치를 다르게 바꾸려면 프로젝트 재등록이 필요하다.
로봇 PC에 옮겨도 별도 폴더에서 실행하며 ROS 수집기/제어기와 섞지 않는다.

서버를 원격 개발 PC에서 실행하면 SSH 터널로 로컬 브라우저를 연결한다.

```bash
ssh -N -L 8765:127.0.0.1:8765 <user>@<DEVELOPMENT_PC>
```

기본 bind는 localhost다. 인증 없이 공용 IP로 공개하지 않는다.

## 작은 검증만 수행하기

```bash
EDITOR_PYTHON=.venv/bin/python
bash setup.sh --with-test
PYTHONPATH=src "$EDITOR_PYTHON" -m pytest -q tests/test_core.py tests/test_bag.py
EDITOR_TEST_EXPORT=1 PYTHONPATH=src "$EDITOR_PYTHON" -m pytest -q tests/test_export.py
# 프런트엔드 경계 연산을 개발용 Node가 있을 때 확인 (실행 환경에는 Node 불필요)
node tests/test_ranges.mjs
```

수 초짜리 synthetic ROS1 bag과 20-frame export를 사용한다. 실제 로봇 동작이나 전체 bag 재검토는 하지 않는다.
공식 writer/loader를 재사용하며 제조사 변환기 전역 patch를 적용하지 않는다.
