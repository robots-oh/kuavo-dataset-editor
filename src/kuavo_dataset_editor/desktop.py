"""Thin Qt desktop host. Editing and export still use the shared offline engine."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time


class LocalBackend:
    """Own exactly one loopback server on an OS-assigned port (no 8765 conflict)."""

    def __init__(self, workspace, allowed_roots=()):
        import uvicorn
        from .app import create_app

        self.roots_file = Path(workspace).expanduser().resolve() / "desktop-roots.json"
        remembered = []
        if self.roots_file.is_file():
            try:
                saved = json.loads(self.roots_file.read_text())
                if isinstance(saved, list):
                    remembered = [p for p in saved if isinstance(p, str) and Path(p).is_absolute()]
            except (ValueError, OSError):
                print("Warning: desktop-roots.json could not be read; select the data folder again.", file=sys.stderr)
        self.app = create_app(workspace, [*allowed_roots, *remembered])
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, access_log=False, log_level="warning", timeout_graceful_shutdown=2,
        ))
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True,
            name="editor-desktop-backend",
        )

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("데스크톱 편집기의 로컬 서버를 시작하지 못했습니다.")
            time.sleep(0.02)

    def grant_directory(self, directory):
        path = Path(directory).expanduser().resolve()
        if not any(path.is_relative_to(root) for root in self.app.state.allowed_roots):
            self.app.state.allowed_roots.append(path)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.roots_file.parent, delete=False) as stream:
                json.dump([str(root) for root in self.app.state.allowed_roots], stream)
                temporary = stream.name
            os.replace(temporary, self.roots_file)

    def stop(self):
        # Import/export workers are independent; never kill them or other servers.
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        self.socket.close()


def write_shortcut(output, workspace, allowed_roots):
    """Create an explicit, relocatable-by-regeneration Linux launcher; no shell eval."""
    entry = Path(__file__).resolve().parents[2] / "run.sh"
    arguments = ["/usr/bin/env", f"EDITOR_PYTHON={sys.executable}"]
    if os.environ.get("LEROBOT_SOURCE"):
        arguments.append(f"LEROBOT_SOURCE={os.environ['LEROBOT_SOURCE']}")
    arguments += ["/bin/bash", str(entry), "desktop", "--workspace", str(Path(workspace).resolve())]
    for root in allowed_roots:
        arguments += ["--allow-root", str(Path(root).expanduser().resolve())]

    def quote(value):
        value = value.replace("%", "%%")
        for char in ("\\", '"', "`", "$"):
            value = value.replace(char, "\\" + char)
        if "\n" in value or "\r" in value:
            raise ValueError("Launcher paths cannot contain newlines")
        return '"' + value + '"'

    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Do not silently replace an existing user launcher.
    with target.open("x") as stream:
        stream.write("[Desktop Entry]\nType=Application\nName=Kuavo Dataset Editor\n"
                     "Comment=Offline ROS bag and LeRobot video editor\n"
                     "Icon=video-x-generic\nTerminal=false\nCategories=AudioVideo;Development;\n"
                     + "Exec=" + " ".join(quote(a) for a in arguments) + "\n")
    target.chmod(0o755)
    print(f"Created desktop launcher: {target}")


def create_window(backend):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox, QToolBar
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    class LocalPage(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            # The desktop host is not a general-purpose web browser.
            return (url.scheme() == "http" and url.host() == "127.0.0.1"
                    and url.port() == QUrl(backend.url).port()) or url.toString() == "about:blank"

    class EditorWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Kuavo Dataset Editor — Desktop")
            self.resize(1480, 960)
            self.view = QWebEngineView(self)
            self.page = LocalPage(self.view)
            self.view.setPage(self.page)
            self.setCentralWidget(self.view)
            self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
            self.page.fullScreenRequested.connect(self.fullscreen_request)
            self.page.profile().downloadRequested.connect(self.download)
            self.view.loadFinished.connect(self.loaded)
            self.bar = QToolBar("데이터 선택", self)
            self.bar.setMovable(False)
            self.addToolBar(self.bar)
            self.add_action("Bag 선택…", self.choose_bag)
            self.add_action("LeRobot 폴더 선택…", self.choose_dataset)
            self.add_action("출력 경로 선택…", self.choose_output)
            self.add_action('SSH 저장 폴더…', self.choose_download)
            self.add_action("작업 폴더 열기", self.open_workspace)
            self.add_action("앱 전체화면", self.toggle_fullscreen, "F11")
            self.statusBar().showMessage("로컬 엔진 준비 완료 · 원본 읽기 전용")
            self.view.setUrl(QUrl(backend.url))

        def add_action(self, text, callback, shortcut=None):
            item = QAction(text, self)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            item.triggered.connect(callback)
            self.bar.addAction(item)

        def loaded(self, ok):
            self.bar.setEnabled(ok)
            if not ok:
                self.statusBar().showMessage("화면을 불러오지 못했습니다. 앱을 종료한 뒤 다시 실행하세요.")

        def set_path(self, field, path):
            # Native selection fills a visible input only; never starts import/export.
            self.page.runJavaScript(
                "{const input=document.getElementById(" + json.dumps(field) + ");"
                "if(input){input.value=" + json.dumps(path) + ";"
                "input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();}}"
            )
            self.statusBar().showMessage("경로를 입력했습니다. 화면의 불러오기/Export 버튼으로 진행하세요.")

        def choose_bag(self):
            paths, _ = QFileDialog.getOpenFileNames(self, "ROS1 bag 선택 (여러 개 가능)", "", "ROS1 bag (*.bag)")
            if paths:
                self.set_import_paths(paths)

        def set_import_paths(self, paths):
            for path in paths:
                backend.grant_directory(Path(path).parent)
            self.page.runJavaScript("window.dispatchEvent(new CustomEvent('editor-import-selection',{detail:" + json.dumps(paths) + "}));")
            self.statusBar().showMessage(f"{len(paths)}개 선택했습니다. 화면의 원본 불러오기로 순차 처리하세요.")

        def choose_dataset(self):
            path = QFileDialog.getExistingDirectory(self, "LeRobot 데이터셋 루트 선택")
            if path:
                backend.grant_directory(path)
                self.set_path("importPath", path)

        def choose_output(self):
            self.page.runJavaScript("JSON.stringify({mode:document.getElementById('exportMode').value,name:document.getElementById('repoName').value})", self.output_dialog)

        def choose_download(self):
            path = QFileDialog.getExistingDirectory(self, 'SSH bag을 내려받을 로컬/SSD 폴더 선택')
            if path:
                backend.grant_directory(path)
                self.set_path('pickerDownloadDir', path)

        def output_dialog(self, state):
            choice = json.loads(state) if state else {"mode": "new", "name": ""}
            if choice["mode"] != "new":
                path = QFileDialog.getExistingDirectory(self, "추가/수정할 기존 LeRobot dataset 루트 선택")
                if path:
                    backend.grant_directory(Path(path).parent)
                    self.set_path("exportPath", path)
                return
            parent = QFileDialog.getExistingDirectory(self, "새 데이터셋을 저장할 상위 폴더 선택")
            if not parent:
                return
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "새 데이터셋 폴더 이름", "폴더 이름", text=choice["name"].split("/")[-1] or "edited-dataset")
            if not ok:
                return
            if not name.strip() or name in (".", "..") or Path(name).name != name:
                QMessageBox.information(self, "폴더 이름", "경로가 아닌 새 폴더 이름을 입력하세요.")
                return
            target = Path(parent) / name
            if target.exists():
                QMessageBox.information(self, "기존 폴더", "원본 보호를 위해 존재하지 않는 새 폴더를 지정하세요.")
                return
            backend.grant_directory(parent)
            self.set_path("repoName", choice["name"] if choice["name"].split("/")[-1] == name else name)
            self.set_path("exportPath", str(target))

        def open_workspace(self):
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(backend.app.state.store.root)))

        def download(self, request):
            filename, _ = QFileDialog.getSaveFileName(self, "프로젝트 JSON 저장", request.downloadFileName())
            if not filename:
                request.cancel()
                return
            path = Path(filename)
            request.setDownloadDirectory(str(path.parent))
            request.setDownloadFileName(path.name)
            request.accept()

        def fullscreen_request(self, request):
            request.accept()
            self.bar.setVisible(not request.toggleOn())
            if request.toggleOn():
                self.showFullScreen()
            else:
                self.showNormal()

        def toggle_fullscreen(self):
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()

        def closeEvent(self, event):
            # Drafts are not saved annotations. Do not silently discard a typed task/range.
            answer = QMessageBox.question(
                self, "편집기 종료", "적용하지 않은 task·구간 입력은 저장되지 않습니다. 종료할까요?\n"
                "저장된 라벨은 유지되며, 진행 중인 import/export는 계속됩니다.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                event.accept()
            else:
                event.ignore()

    return EditorWindow()


def launch(workspace, allowed_roots=()):
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SystemExit("데스크톱 화면이 없습니다. GUI가 있는 로컬 PC에서 실행하세요. SSH 전용 환경은 serve + 터널을 사용하세요.")
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except ImportError as exc:
        raise SystemExit("데스크톱 의존성이 없습니다. bash setup.sh --with-desktop 을 실행하세요.") from exc
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Kuavo Dataset Editor")
    backend = LocalBackend(workspace, allowed_roots)
    window = None
    try:
        backend.start()
        window = create_window(backend)
        window.show()
        print(f"Desktop ready · workspace={Path(workspace).resolve()} · backend={backend.url}", flush=True)
        app.exec()
    finally:
        if window is not None:
            # Delete the page before QApplication destroys its default profile.
            from shiboken6 import delete
            delete(window)
        backend.stop()
