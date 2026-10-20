# Torso·랙 상대 base를 포함하는 Export

에디터 서버/Export를 실행하는 PC에 코드가 있어야 합니다. 브라우저만 여는 노트북에는
설치하지 않아도 됩니다. ROS master·로봇 제어 연결은 필요하지 않습니다.

## 사용 순서

1. 기존 workspace와 프로젝트를 그대로 엽니다. 다시 import하거나 annotation을 다시 할 필요가 없습니다.
2. Export의 **출력 구성 → 새 구성: state 24 / action 23**을 선택합니다.
3. **새 dataset repo 생성**에서 새 이름을 입력합니다. 기존 14차원 dataset에 새 차원을 추가하지 않습니다.
4. 기존 카메라/필수 데이터 누락이 있으면 보정·제외를 선택하고 Export합니다.
5. 완료된 작업의 **관절별 유효 정답 / 누락 요약**을 확인합니다.
6. 출력 폴더를 원본으로 불러오면 **신호 보기 → feature → 관절·차원**으로 값을 봅니다.
   유효하지 않은 값은 회색/빈 구간이며, `missing_reason.*` 신호에서 사유 코드를 확인할 수 있습니다.

기존 dataset의 차원을 유지하며 자르거나 task만 바꿀 때는 **기존 구성 유지**를 선택합니다.
`state 24 / 기존 action 유지`는 새 state와 이전 분리 action을 함께 쓰는 명시적 호환 모드입니다.
CLI/API의 기본값은 기존 호환성을 위해 `legacy`이며, GUI의 초기 선택은 새 구성입니다.

```bash
bash run.sh export /absolute/project.json /absolute/output \
  --schema kuavo_wholebody_v1 --repo-name team/rack-box-v2
```

## 차원·이름·단위

범위는 Python의 `[start:end]`, 즉 끝 번호는 포함하지 않습니다.

| 그룹 | observation.state (24) | action (23) | 단위 |
| --- | --- | --- | --- |
| torso | `[0:4]` 실측 관절각 | `[0:4]` 목표 관절각 | rad |
| 왼팔 | `[4:11]` 실측 관절각 | `[4:11]` 목표 관절각 | rad |
| 오른팔 | `[11:18]` 실측 관절각 | `[11:18]` 목표 관절각 | rad |
| claw | `[18:20]` 실측 개방률 | `[18:20]` 목표 개방률 | 0 닫힘, 1 열림 |
| base | `[20:24]` rack 기준 x,y,sin(yaw),cos(yaw) | `[20:23]` vx,vy,yaw_rate | m / m·s⁻¹, rad·s⁻¹ |

torso 순서는 `knee_joint`, `leg_joint`, `waist_pitch_joint`, `waist_yaw_joint`입니다.
양팔은 각각 `zarm_l1_joint`…`zarm_l7_joint`, `zarm_r1_joint`…`zarm_r7_joint`입니다.
`meta/info.json`의 feature `names`에 각 값의 이름을 붙이고,
`meta/kuavo_schema.json`에 그룹 범위·단위·원본 토픽·좌표계·mask 규칙을 저장합니다.
이미지는 기존 카메라 feature를 그대로 유지합니다.

관절 state는 `/sensors_data_raw.joint_data.joint_q[0:18]`을 사용합니다.
torso action은 `/joint_cmd.joint_q[0:4]`(rad)을 우선 사용하고, 토픽이 없으면
`/lb_leg_traj.position`(degree → rad)을 사용합니다. 어떤 경로였는지 보고서에 남깁니다.
후자는 요청한 trajectory target이며 actuator-level 명령과 같다고 가정하지 않습니다.
양팔 명령은 `/kuavo_arm_traj.position`을 degree → rad 변환합니다.
claw는 이름을 left/right 순서로 맞춘 뒤 `/100`, base 명령은 `/cmd_vel`에서 가져옵니다.
실측 관절각을 누락된 목표 명령으로 대신 저장하지 않습니다.

## 유효성·누락 사유

| feature | 타입 | 차원 | 의미 |
| --- | --- | --- | --- |
| `validity.observation.state` | bool | 24 | 해당 입력값을 실제로 사용할 수 있는가 |
| `validity.action` | bool | 23 | 해당 목표값을 loss 정답으로 쓸 수 있는가 |
| `missing_reason.observation.state` | uint8 | 24 | 입력 누락 사유 |
| `missing_reason.action` | uint8 | 23 | 목표 누락 사유 |

`true/1 = 유효`, `false/0 = 미기록·제외`입니다. 각 feature의 names는 대응하는 값과 동일합니다.
유효성 0인 값의 숫자 0은 **파일 저장용 placeholder이지 0 rad/정지 명령이 아닙니다**.
torso 목표가 없어도 torso 실측값이 있으면 state의 torso는 유효하게 남습니다.

사유 코드는 메타데이터에 한 번만 설명합니다. 프레임마다 문자열을 반복 저장하지 않습니다.

| 코드 | 의미 |
| --- | --- |
| 0 | 유효 |
| 1 | 토픽 미기록 |
| 2 | 해당 시점 샘플 없음/허용 시간 초과 |
| 3 | 기록된 메시지의 차원·값 오류 |
| 4 | 랙 태그 부착 좌표 보정 정보 없음 |
| 5 | 신뢰할 다중 태그 관측 구간 없음 |
| 6 | 해당 시점의 recorded TF 없음 |

양팔의 기존 필수 값·영상 gap에 대한 사용자 승인 보간/hold/제외 흐름은 그대로 유지됩니다.
승인된 보정은 원본을 바꾸지 않고 적용하며 `editor/source_quality.json`에 남습니다.
새로 추가하는 차원의 미기록 값은 자동 보간하거나 실측을 명령으로 복사하지 않습니다.

`editor/mask_summary.json`은 차원별 valid/masked 개수와 무정답 차원을 저장합니다.
정규화용 global `meta/stats.json`과 episode metadata의 값 통계는 유효한 값만으로 다시 계산합니다.
이 과정은 숫자 parquet만 읽으며 영상을 다시 디코딩하지 않습니다.
정답이 하나도 없는 차원의 mean=0/std=1/min=-1/max=1/quantile 기본값은
로더 호환용 중립값일 뿐, 측정된 통계가 아닙니다. 실제 개수는 summary의 `valid_counts=0`을 확인하세요.
LeRobot의 scalar `count`는 전체 프레임 수로 유지되며 차원별 유효 개수가 아닙니다.

## 랙 상대 base의 오프라인 재구성

선택 clip만이 아니라 **원본 bag 전체**의 마커·TF를 읽고, 마지막에 선택 프레임만 Export합니다.
따라서 clip 바깥/뒤쪽의 좋은 관측도 앞부분 역산에 사용할 수 있습니다.
기존 catalog의 영상을 재추출하거나 annotation을 변경하지 않습니다.

1. `/dataset/base_pose_in_rack/status`의 기록된 `rack_tags` 부착 좌표를 읽습니다.
   없는 구 bag은 명시적 calibration JSON을 줄 수 있습니다. 현재 RWH 설정을 과거 bag에 몰래 적용하지 않습니다.
2. `/rwh/tag_detections`(없으면 `/tag_detections`)에서 보정 목록의 `parent=rack` 태그만 사용합니다.
   박스 tag 7은 rack 보정 목록에 넣지 마세요. 정상 기록된 목록에는 box tag가 없습니다.
3. 각 관측의 header 시각에서 recorded TF로 camera → odom 변환합니다. 최신 TF로 대신하지 않습니다.
4. 평면 간격 20cm 이상인 서로 다른 랙 태그 2개 이상으로 XY/yaw를 맞추고 RMS 5cm 초과는 제외합니다.
5. 같은 1초 창의 3개 이상 프레임에서 중앙값/원형 평균과 outlier 처리를 거쳐 anchor를 만듭니다.
6. 여러 좋은 창의 anchor를 시간 보간하고, 첫/마지막 관측 전후에는 끝 anchor와 odom을 사용해 전파합니다.
   odom timestamp는 bag receipt 시각의 인접 `/odom` 메시지 header 시각으로 맞춥니다.
7. `odom <- base_link` TF를 이용해 rack 기준 base의 x,y,sin(yaw),cos(yaw)를 계산합니다.
   `/odom`의 child가 `base_footprint`여도 그 값을 `base_link`로 잘못 간주하지 않습니다.

`provenance.base_pose_source`는 uint8 1차원이며 0=미계산, 1=첫 관측 이전 역산,
2=좋은 관측 창 사이, 3=마지막 관측 이후 전파입니다. 결과는 직접 관측과 구분됩니다.
보정/anchor/TF가 없으면 해당 state의 유효성은 0입니다. 수집/Export 전체를 그것만으로 막지 않습니다.

**전제:** recording 안에서 rack이 고정되어 있고 odom이 연속적이어야 합니다.
rack을 옮기거나 odom을 재설정한 bag은 별도 구간·보정 기준으로 나누어야 합니다.
단일 태그만 있는 구간은 새 anchor를 만들지 않으며 장시간 전파에는 odom drift가 남을 수 있습니다.
실물 정책 실행에서도 동일한 state 기준을 준비해야 합니다. 오프라인 역산이 실시간 초기 관측을 대신하지는 않습니다.

원본 fingerprint·선택·보정 승인·알고리즘을 키로 job 내부 계산을 캐시해 episode 재개 시 재사용합니다.
새 job은 다시 계산할 수 있습니다. 완성 dataset에는 reconstruction 보고서와 프레임별 source code가 포함되어
원본 bag/캐시 없이도 값·mask·의미를 읽을 수 있습니다.

구 bag에 별도 calibration을 주는 예:

```bash
bash run.sh export project.json /absolute/output --schema kuavo_wholebody_v1 \
  --rack-calibration /absolute/recording-rack-calibration.json
```

JSON은 `{"rack_tags":[{"id":0,"parent":"rack","x":0,"y":0.4,"z":1}, ...]}` 형태입니다.
예시 좌표를 실제 랙에 그대로 사용하지 말고, 해당 수집 당시의 보정 좌표를 사용하세요.
기록된 보정과 다르면 오류로 알립니다.

## 학습 연결: 자동 적용 아님

에디터는 training repo나 정책을 수정하지 않습니다. `validity.action` 저장만으로
LeRobot/GR00T가 자동으로 그 차원을 loss에서 제외한다고 가정하면 안 됩니다.
학습 data loader는 `action`과 **동일한 future timestep 인덱스**로 `validity.action` chunk도 모아야 합니다.
현재 프레임의 mask를 horizon 전체에 broadcast하면 안 됩니다.

예를 들어 두 feature에 동일한 `delta_timestamps`를 설정한 뒤 모델 processor가 만든
padding mask와 AND합니다. 모델 입력 관절 순서를 재배열했다면 mask도 똑같이 재배열해야 합니다.
다음은 trainer에 명시적으로 연결하는 예이며 자동 실행 코드는 아닙니다.

```python
# validity_chunk: [B, T, 23], action과 같은 미래 프레임에서 수집
# padding_mask: [B, T, max_action_dim], GR00T processor가 만든 마스크
valid = validity_chunk.to(device=padding_mask.device, dtype=torch.bool)
assert valid.shape[:2] == padding_mask.shape[:2]
mask = torch.zeros_like(padding_mask)
mask[..., :23] = padding_mask[..., :23] * valid
# 모델에 전달할 action_mask를 mask로 교체 (processor 실행 이후)
```

순수 NumPy 계약 helper는 `kuavo_dataset_editor.masking.chunk_loss_mask()`이며
차원·horizon·0/1 규칙을 검사합니다. 정규화/relative-action 변환도 명시적으로 맞춰야 합니다.
base 속도에서 state의 rack 위치를 빼거나 state24-only 모드의 state 앞 14개를 arm state로 쓰면 안 됩니다.

입력 state의 누락 mask는 action loss mask와 다릅니다. 입력 처리/모델 학습에 별도로 연결해야 하며,
연결하지 않은 채 missing-state placeholder를 실제 위치로 해석하지 마세요.
torso 정답을 제외하면 나머지는 학습 가능하지만 그 프레임에서 torso 동작을 배우지는 않습니다.
전체 action을 받는 flow/diffusion 모델에서 loss mask만으로 누락 차원의 모든 간접 영향이 사라지거나
강인성이 자동 향상된다고 보장할 수도 없습니다.
