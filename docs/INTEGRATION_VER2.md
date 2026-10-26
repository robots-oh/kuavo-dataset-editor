# RWH + ver2 통합 보고서 — 2026-10-26

## 기준과 반영 범위

- 보존된 현장 적용 전 tag: `editor_v1` → `558d4aa`.
- 검증된 RWH head: `8673189`; PR #1을 먼저 main에 merge한 commit: `0daad11`.
- 결합한 ver2 head: `4da721a` (2 commits).
- 통합 브랜치: `codex/editor-v2-integration`. 이 보고서 작성 시 통합 PR은 검토 대상이며 main 반영은 별도 승인 사항이다.
- 실제 rosbag·영상·annotation workspace, Up PC, 로봇 제어·ROS 설정은 수정하지 않았다.

## 가져온 기능

| 영역 | 통합 결과 |
| --- | --- |
| RWH import | 파일명 기반 rwh_auto profile 선택, 수동 profile 선택, 파일별 profile 유지 |
| RWH 변환 | helper 팔·그리퍼·계획 base 속도와 기록된 rack-relative pose 사용을 유지 |
| 라벨링 | 원본별 완료·불필요·필터, 우클릭 Step/Goal 라벨 삭제, 경계 스냅 개선 |
| 동작 요약 | 영상 프레임과 맞춘 양팔 목표/실측, 그리퍼, torso·base 상태 표시 |
| 새로고침 | 원본·재생 위치·미적용 입력·profile 선택을 같은 탭의 초안으로 보존 |
| 연결 프로젝트 | 명시적인 annotation_group에 한해 같은 원본의 라벨·상태·split 공유 |
| 작업 관리 | import/export/미리보기/실제 Hub 업로드 이력 분리 |
| Export | 현재 원본 기본값, 프로젝트 전체 선택, 추가 모드의 원본 프레임 중복 제외 |
| 저장 성능 | RWH frozen provenance 링크와 ver2의 checkpoint 사본 재사용 결합 |

## 충돌 해결과 추가 연결

5개 충돌 파일(`app.py`, `repositories.py`, `checkpoints.py`, `static/app.js`,
`test_repositories.py`)에서 어느 한쪽을 덮어쓰지 않고 양쪽 동작을 결합했다.

- 내부 선택은 `source_ids`로 통일했다. ver2의 `source_id`는 한 원소 목록으로 변환하며
  미리보기·worker·wholebody가 같은 대상을 사용한다. 두 필드가 충돌하면 명시적으로 알린다.
- `source_ids` 생략/null은 전체, 빈 목록은 잘못된 선택이다. 기존 dataset 수정은 전체 프로젝트를 사용한다.
- 라벨링 불필요는 원본·저장 라벨을 삭제하지 않는다. 출력 선택에서만 빠진다.
- 중복 판단은 실제 `editor/frame_mapping.parquet`의 프레임과 원본 등록 fingerprint를 사용한다.
  확인 창과 operation metadata에 건너뛴 프레임 수를 남긴다.
- RWH 그리퍼도 사용자가 확인한 **0=열림, 100=닫힘**으로 화면 개방도를 표시한다.
  RWH profile의 display 설정과 기존 helper 토픽 호환을 추가했다.
- 이미 필터링한 RWH 피드백을 기존 Leju 혼합 토픽의 echo 분리/재조회 경로에 넣지 않는다.
- frozen history/events는 RWH 방식으로 공유하고, 일반 metadata는 staging과 독립시킨 뒤
  이전 checkpoint 사본을 재사용한다. events 쓰기 전 detach 동작을 유지한다.
- CI가 새 테스트를 누락하지 않도록 `tests/test_*.mjs` 전체를 실행한다.
- 문서 사이트 생성 목록에 원본 상태·동작 요약 안내를 추가했다.

## 변경하지 않은 데이터 계약

- 기존 VR bag과 RWH 자동수집 bag의 토픽은 같다고 가정하지 않는다. 각 profile에 맞는 토픽을 읽는다.
- 검증된 RWH 팔·torso·base 변환, rack pose 경로, 단위 변환·허용 시간·validity 처리와 출력 순서는 유지한다.
- wholebody의 state 24차원/action 23차원과 legacy 모드를 유지한다.
- RWH base action은 기존 profile대로 `/rwh/navigation_proxy/cmd_vel_plan`의 계획 속도다.
  기록된 rack-relative pose가 있으면 그 값을 사용하는 기존 RWH 경로도 유지한다.
- 그리퍼 화면 보정은 Export 숫자를 뒤집지 않는다. 기존 `/100` 출력은 0=열림·1=닫힘이지만,
  기존 feature 이름/단위에는 `open_fraction`이 남아 있다. 학습·배포 시 이름만 보고 방향을 반대로 해석하지 않는다.
- 오른손목 기본 180° 회전은 보기 전용이다. Export 영상은 회전하지 않는다.
- idle·실패·A-off 구간을 자동 제거하지 않고, 원본에 없는 명령이나 상태를 새로 만들어 채우지 않는다.

## 사용 시 주의

1. 여러 bag을 한 번에 Export하려면 **프로젝트 전체 라벨**을 선택한다. 기본은 현재 원본이다.
2. 추가 모드는 같은 프레임의 instruction을 갱신하지 않는다. 라벨 수정은 기존 수정 모드나 별도 dataset을 사용한다.
   동일 프레임의 Step와 Goal을 각각 학습하려면 서로 다른 dataset으로 Export한다.
3. fingerprint는 파일명·크기·수정 시각 기반이며 전체 내용 checksum이 아니다. 매핑 없는 외부 dataset의 중복은 판별할 수 없다.
4. 라벨링 완료는 수동 검토 표시다. 라벨을 고치면 해제되며, Export 성공으로 자동 완료되지 않는다.
5. 연결 프로젝트 간 공유는 annotation_group을 명시한 경우만 적용된다. 별도 프로젝트의 같은 bag을 자동 연결하지 않는다.
6. 일반 metadata와 달리 frozen history/events는 hard link다. 외부 프로그램으로 직접 수정하거나 독립적인 외부 백업으로 간주하지 않는다.
7. 배포할 때 진행 중인 작업을 마친 뒤 서버를 재시작하고 화면을 새로고침한다.
   기존 job은 시작 당시 snapshot을 사용한다. 버전 변경을 가로지른 중단 job의 재개 호환성은 보장하지 않는다.
8. 이 통합 작업은 실제 workspace를 열거나 이관하지 않았다. 업데이트 전 workspace와 프로젝트 JSON을 별도로 보존한다.

## 검증 범위

- 전체 Python: **158 passed, 10 skipped**. 실제 desktop/추가 writer 테스트 등 opt-in 및 docs 의존성 미설치 항목은 별도다.
- 작은 합성 데이터 공식 LeRobot 생성·추가·수정·중복 제외·중단 재개·wholebody: **38 passed**.
- JavaScript: **13개 테스트 파일 모두 통과**. 라벨링 경계·Undo/Redo·원본 선택·새로고침·작업 이력·RWH 그리퍼 표시 포함.
- 별도 opt-in desktop: **3 passed**. 임시 workspace에서 Qt offscreen 화면 로드·새로고침·클립 삭제/Undo 확인.
- 문서 생성 및 내부 링크·이미지 검사: **1 passed**. 새 상태·동작 요약·통합 보고서 페이지 포함.
- 시스템/로봇 PC에 패키지를 설치하지 않고 기존 로컬 런타임을 재사용했다. 테스트 출력은 임시 폴더에만 저장했다.
- 실제 장시간 VR/RWH bag의 전체 재변환이나 로봇 동작 검증, 대용량 속도 재측정은 하지 않았다.
