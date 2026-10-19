# 새 PC에서 clone부터 실행까지

## 지원 기준과 준비물

- 잠금파일은 **Linux x86_64 또는 ARM64(aarch64) + glibc 2.28 이상 + Python 3.12 + CPU PyTorch**를 지원한다.
- Ubuntu 22.04/24.04용 OS 라이브러리 설치 보조 스크립트를 제공한다.
- GUI 데스크톱 세션이 필요하다. SSH만 있다면 웹 서버 모드와 터널을 사용한다.
- 초기 설치에는 인터넷과 수 GB의 여유 공간이 필요하다. 이후 편집·Export는 로컬에서 수행한다.
- CUDA, 로봇 PC, ROS workspace, 별도 LeRobot checkout, Node 빌드는 필요하지 않다.
- ARM64·Ubuntu 20.04는 웹/서버 모드를 사용한다. Windows/macOS는 이 설치기의 지원 대상이 아니다.

```bash
git clone https://github.com/robots-oh/kuavo-dataset-editor.git
cd kuavo-dataset-editor
bash setup.sh
# GUI가 있는 x86_64 PC:
bash desktop.sh
# SSH/ARM64 서버는 대신 bash setup.sh --headless 후 bash run.sh를 사용한다.
```

소스 저장소는 비공개다. 조직/저장소 접근 권한이 있는 GitHub 계정으로 먼저 로그인한다.
HTTPS clone에 인증이 필요하면 `gh auth login`과 `gh auth setup-git`을 사용하거나,
본인이 이미 설정한 GitHub SSH 인증으로 clone한다. 인증정보를 저장소 파일에 넣지 않는다.

`setup.sh`는 `.bootstrap`에 고정 uv를 준비하고 `uv.lock`을 변경하지 않고 동기화한다.
Python 3.12가 없으면 uv 관리 Python을 내려받는다. `sudo`/APT를 실행하거나 시스템 Python,
ROS/CUDA/JetPack을 바꾸지 않는다. bootstrap용 Python 3.8 이상과 venv 지원은 필요하다.
OS 라이브러리가 부족할 때만 시스템 설치 보조 스크립트의 내용을 검토하고 별도로 준비한다.

아키텍처별 `.venv-아키텍처-해시`에 환경을 분리하며, 기존 `.venv`와 workspace는 그대로 둔다.
기본은 Export 포함이다. 화면이 있는 x86_64에서는 Qt도 선택하며, ARM64/SSH에서는 웹 모드다.
GUI 없는 서버는 `bash setup.sh --headless`로 설치하고 `bash run.sh serve ...`로 실행한다.
Qt 명시 설치는 `--with-desktop`이며 잠금된 Qt wheel에는 glibc 2.34 이상이 필요하다.

진단에 성공해야 `.editor-python`의 실행 환경 선택을 바꾼다. `run.sh`와 `desktop.sh`는
이 선택을 읽으며 `bash run.sh --print-python`으로 실제 Python 경로를 확인할 수 있다.
진단 결과와 설치 정보는 선택된 환경의 `editor-diagnostics.json`, `editor-environment.json`에 남는다.
설치 실패 시 이전 선택을 유지한다. 실행 중인 서버는 자동 종료하지 않으므로 설치 후 편집기만
재시작해야 새 환경이 적용된다. 로봇 제어·수집은 재시작할 필요가 없다.

코드 변경 없이 잠금파일로 재설치하므로 LeRobot 최신 버전이 나와도 기본 환경은 바뀌지 않는다.
잠금파일이 소스 명세와 어긋나면 설치가 실패하며 임의 업데이트하지 않는다.
Python 버전과 바이너리는 PC별로 재생성된다. OS 라이브러리는 Git이 설치할 수 있는 Python
패키지가 아니므로 위 사전 준비가 별도로 필요하다.

## 데이터 열기

[폴더 탐색·여러 bag 선택·SSH 가져오기](DATA_IMPORT.md)로 개별 파일 경로 입력을 줄일 수 있다.

[화면으로 따라 하기](VISUAL_GUIDE.md)에 실제 실행 화면과 눌러야 할 버튼을 단계별로 정리했다.

처음에는 ‘작은 데모 열기’로 동작을 확인할 수 있다. 실제 bag은 PC로 별도 복사한다.
데스크톱 앱의 ‘Bag 선택’ 또는 ‘LeRobot 폴더 선택’ → ‘원본 불러오기’ → 완료 시
‘현재 프로젝트에 추가’ 순서다. 저장소에는 실제 데이터가 들어 있지 않다.

기존 프로젝트를 이어서 사용하려면 workspace도 별도 복사하고:

```bash
bash desktop.sh --workspace /data/editor-workspace --allow-root /data/robot-datasets
```

프로젝트는 원본/cache의 절대 경로를 포함한다. PC를 옮길 때 동일 경로를 유지하거나
원본을 새로 등록해야 한다. Git만 clone하면 기존 라벨·원본이 자동으로 생기지는 않는다.

## 확인 및 문제 해결

```bash
bash run.sh doctor --export --desktop
# 선택 사항: 임시 합성 데이터 20프레임을 Export하고 공식 로더로 다시 열기
bash run.sh doctor --smoke-export
```

- Qt가 display에 연결하지 못하면 GUI 세션에서 실행한다. SSH만으로 native 창은 보이지 않는다.
- Qt xcb 라이브러리 오류라면 위 OS 의존성 설치 스크립트를 확인한다.
- FFmpeg/torchcodec 오류는 임의로 torch만 업그레이드하지 말고 lock 환경을 다시 설치한다.
- 설치 도중 실패해도 원본 데이터는 바뀌지 않는다. 같은 `bash setup.sh`로 재시도한다.
- 기존 `.venv`나 공유 symlink는 수정하지 않고 새 프로젝트 전용 환경을 사용한다.
  `EDITOR_VENV_DIR="$PWD/.venv-new" bash setup.sh`로 독립 환경을 만들고
  `EDITOR_PYTHON="$PWD/.venv-new/bin/python" bash desktop.sh`로 사용한다.
- UI/읽기만 필요하면 `bash setup.sh --minimal`을 쓴다. 이 모드도 별도 환경을 선택하므로
  이전 Export 환경을 삭제하지 않는다. `--minimal --with-export`로 Export를 추가할 수 있다.
- `bash setup.sh --plan`은 설치 없이 자동 선택 결과만 보여준다. `--fresh`는 새 환경에 재설치한다.

버전 변경과 호환성 작업은 [LEROBOT_COMPATIBILITY.md](LEROBOT_COMPATIBILITY.md)를 따른다.
