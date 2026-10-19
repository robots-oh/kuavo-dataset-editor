# LeRobot 버전 관리와 확장

## 기본값: 검증한 환경 재현

`pyproject.toml`의 `[tool.uv.sources].lerobot` 한 곳에서 공식 commit의 소스 아카이브를
선택한다. 기본 commit은 `d20a4538016d9fe0374a34011aebc1c8cddf3e0d`이며 데이터 형식은 v3.0이다.
`uv.lock`은 아카이브 해시와 전체 전이 의존성을 기록한다. 기본 설치는 `uv sync --locked`다.
upstream 저장소의 개발용 CUDA index 설정을 물려받지 않도록 Git 개발 checkout 대신
공식 commit 소스 아카이브를 사용하며, CPU torch/torchvision index는 이 패키지가 지정한다.

Linux x86_64는 Torch 2.9.1 / torchvision 0.24.1 / torchcodec 0.8.1,
ARM64는 CPU Torch 2.11.0 / torchvision 0.26.0 / torchcodec 0.11.0을 함께 관리한다. 이것은
최신 버전을 쓰면 안 된다는 뜻이 아니라, 기본 설치가 예고 없이 바뀌지 않게 하는 것이다.

## 코드 경계

`src/kuavo_dataset_editor/integrations/lerobot.py`만 공식 writer/loader를 import한다.
exporter는 `create_writer`, `save_episode`, `load_dataset`, `describe` 계약을 사용한다.
현대적인 `rgb_encoder` 형태와 이전 `vcodec` 형태를 명시적으로 구분한다. 알 수 없는 API에
인자를 대충 생략하거나 새 데이터 형식을 v3처럼 취급하지 않는다.
연동 실패는 Export/진단에만 영향을 주며 로봇 제어나 데이터 수집을 차단하는 코드는 없다.

편집 구간·task·episode 규칙과 bag profile은 별개다. 따라서 writer API가 바뀌어도 우선
연동 어댑터만 갱신한다. LeRobot 저장 형식 자체가 바뀌면 `sources.LeRobotSource`의 입력
어댑터와 provenance/검증도 함께 갱신해야 한다. 미래 버전의 자동 호환을 보장하지 않는다.

Export의 `editor/report.json`에는 사용한 LeRobot/torch/torchcodec 버전, source commit,
API 형태, dataset 형식을 기록한다.

## 업데이트 후보를 별도 환경에서 확인

1. 새 Git branch에서 pyproject의 공식 archive commit URL을 바꾼다.
2. 필요하면 torch/vision/codec 조합과 Python/플랫폼 범위도 함께 조정한다.
3. `.bootstrap/bin/uv lock`으로 잠금파일을 갱신한다. 명세·lock diff를 함께 검토한다.
4. 기존 환경을 바꾸지 않고 후보 환경을 설치한다.

```bash
EDITOR_VENV_DIR="$PWD/.venv-candidate" bash setup.sh --with-test
env -u LEROBOT_SOURCE -u PYTHONPATH \
  .venv-candidate/bin/python -m kuavo_dataset_editor doctor --export --smoke-export
env -u LEROBOT_SOURCE -u PYTHONPATH EDITOR_TEST_EXPORT=1 \
  .venv-candidate/bin/python -m pytest -q tests/test_export.py tests/test_lerobot_integration.py
```

5. API 변경이 필요하면 어댑터와 작은 테스트를 갱신한다.
6. 작은 round trip을 통과한 뒤 명세·lock·어댑터·검증 기록을 같은 변경에 반영한다.

기본 테스트는 두 API 형태의 계약을 모의 객체로 확인한다. 실제 writer 검증은 고정 commit으로
20프레임 round trip을 수행한다. 이것을 모든 과거/미래 버전의 통합 테스트로 해석하면 안 된다.
## Episode checkpoint contract

The current pinned official `create`/`resume` API supports `streaming_encoding=True`.
The adapter enables it when available, retains H264 CRF/preset/thread settings and
falls back to the original image-based path when absent. Each output episode is
saved and finalized, then the next uses a fresh official resume writer. In the
pinned writer, resume allocates **new** data/video chunk files, rather than appending
to completed files. Small mutable metadata is journaled separately.

When updating LeRobot, run the opt-in `test_interrupted_export_reuses_completed_episode`
test as well as new/append/replace tests. It checks that completed video bytes and
mtimes do not change, the resume writer adds only the missing episode, and official
loading and frame/timestamp provenance remain correct. A new upstream version
must not start overwriting old chunks without updating this checkpoint strategy.
The resume identity includes backend versions, the frozen project/plan and source
file identities; a changed environment must not silently reuse incompatible work.
