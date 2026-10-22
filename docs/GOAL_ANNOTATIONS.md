# Goal / Step 두 계층 annotation

하나의 프로젝트에서 짧은 **Step(세부 동작)**과 긴 **Goal(완료할 전체 작업)**을 함께 관리한다.
원본 bag·영상·state/action을 복제해서 따로 annotation할 필요는 없다.
기존 프로젝트의 Step은 그대로 유지되고 Goal은 빈 트랙으로 시작한다. Goal 입력은 강제가 아니다.

## 가장 빠른 사용 순서

1. 지금처럼 Step을 저장한다. 타임라인 위쪽은 Goal, 아래쪽은 Step이다.
2. 영상에서 작업 한 번에 해당하는 시작/끝을 정하고 **선택 범위로 Goal 초안**을 누른다.
   또는 **현재 시점의 연속 Step 묶기**로 인접한 Step 전체를 초안으로 가져온다.
3. 보라색 Goal 편집 모드에서 문장·범위를 확인하고 **새 Goal에 적용**을 누른다.
   초안 버튼만으로는 저장하지 않는다. 연속 Step이 여러 작업 반복을 포함하면 반드시 범위를 줄인다.
4. Goal 바 또는 Goal 목록을 클릭하면 기존과 동일하게 시작/끝 시간·프레임·양끝 드래그로 수정한다.
   드래그 중 영상이 갱신되고 Step/Goal 경계에 맞춤된다. Alt를 누르면 경계 맞춤을 해제한다.
5. 오른쪽 **학습용 instruction 트랙**에서 Step 또는 Goal을 선택한다.
   **데이터 미리보기 (저장 안 함)**로 실제 선택 instruction·프레임·episode 수를 확인한 다음 Export한다.

편집 중인 트랙과 Export할 트랙은 별도 선택이다. Goal을 편집했다고 Export가 자동으로 Goal로 바뀌지는 않는다.

## 문장과 연결 관계

전체 작업 후보도 중간/상단 선반 × 왼쪽/오른쪽 4개다. 좌우는 랙 정면을 바라본 기준이다.

예:

- Goal: `Move the box from the left side of the middle shelf of the rack to the front of the conveyor.`
- Step: 랙 정렬 → 중간 선반 왼쪽 상자 집기 → 운반 → 내려놓기

초안 범위에 위치가 지정된 상자 집기 instruction이 하나만 있으면 해당 위치의 Goal 후보를 제안한다.
위치가 여러 개이거나 알 수 없으면 일반 문장을 넣으므로 사람이 문장을 확인한다.
내려놓는 목적지나 완료 조건이 다르면 문장을 직접 수정한다.

Step이 Goal 범위에 완전히 들어가면 자동으로 연결 표시된다. Goal 경계를 수정하면 연결도 다시 계산된다.
Goal 없는 Step 또는 경계를 걸친 Step은 표시만 하며 Step 저장·Export를 차단하지 않는다.
Goal 목록 아래의 Step 버튼을 누르면 해당 Step 편집으로 이동한다.
같은 트랙 안에서는 겹치는 구간을 교체 확인하거나 경계를 조정해야 하지만, 두 트랙 간 겹침은 정상이다.

## 라벨 제거와 데이터 제외의 차이

- **현재 트랙의 선택 구간 라벨만 제거**: 다른 트랙은 유지한다.
- **데이터 제외 · 두 트랙 공통**: Step 라벨을 제거하고 공통 제외 구간도 기록한다. Goal 범위를 넓혀도 이 데이터는 Export에 포함되지 않는다.
- 공통 제외는 타임라인의 붉은 사선과 아래 제외 목록으로 확인한다. **제외 해제**로 되돌릴 수 있지만, 제거된 Step 문장은 자동 복원하지 않는다. 즉시 Undo하면 원래 annotation까지 복원한다.

Goal은 기본적으로 **그 안에서 Step 라벨이 있는 부분만** Export한다. 따라서 기존 Step의 미지정/제거 구간을 조용히 되살리지 않는다.
Step이 없는 전이 동작도 포함하려면 해당 Goal의 **Step 미지정 구간도 포함**을 명시적으로 체크하고 저장한다.
이전 버전의 라벨 제거와 단순 미지정을 구분할 정보는 없으므로, 체크할 때 해당 구간 영상을 확인한다.
이 옵션도 새 공통 제외 구간은 덮어쓰지 않는다.

## Export와 episode

- Step Export: 프레임마다 해당 Step instruction을 사용한다. Goal 유무와 무관하다.
- Goal Export: 해당 Goal 전체에서 동일한 Goal instruction을 사용한다. Goal이 없는 구간은 포함하지 않는다.
- Goal 하나마다 별도 episode다. 서로 다른 Goal/원본 bag을 이어 붙이지 않는다.
- Goal 내부라도 데이터 제외·미포함 구간, Step의 명시적 새 episode 경계에서는 episode가 나뉜다. 빈 구간을 이어 붙이거나 action을 만들어내지 않는다.
- state/action schema·단위·validity·누락 사유는 instruction 트랙과 독립적이다.
- 비교 실험은 `box_step_v1`, `box_goal_v1` 같은 서로 다른 출력 이름을 권장한다.
  같은 원본 recording은 두 실험 모두 동일한 train/validation/test split을 사용한다.
- 이 기능은 모델의 action horizon·관측 history를 바꾸거나 상위 planner를 학습하지 않는다. 선택한 계층의 task 문장을 일반 LeRobot task로 내보낸다.

CLI도 동일하다:

```bash
bash run.sh export project.json /data/box-goal-v1 \
  --annotation-track goal --schema kuavo_wholebody_v1
```

## 저장·복구·다른 PC

Goal/Step/공통 제외는 기존 SQLite project revision에 함께 저장된다. Undo/Redo와 프로젝트 JSON에도 함께 들어간다.
Export에는 `editor/annotations.json`, `editor/project.json`에 계층 snapshot이 남고,
`editor/frame_mapping.parquet`에 선택 트랙·원본 Step/Goal ID와 문장이 남는다. 프레임 조회는 정렬된 인덱스를 사용한다.
추가/수정 Export 시 이전 metadata는 기존 history/복구본 정책으로 보존한다.

이 에디터로 Export한 데이터셋을 다시 가져오면 Export에 실제 남은 프레임 범위에서 두 계층을 복원한다.
잘려서 내보내지 않은 프레임은 복원할 수 없으므로 원본 프로젝트를 유지한다.
외부 LeRobot 또는 계층 metadata가 없는 기존 dataset은 기존 task_index를 Step으로 불러온다.

업데이트 후 진행 중인 작업이 끝나면 에디터 서버를 재시작하고 브라우저를 강력 새로고침한다.
기존 프로젝트를 새로 만들거나 annotation을 다시 작성할 필요는 없다.
