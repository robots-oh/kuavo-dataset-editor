"""Light native-host checks; never open or edit the user's real dataset."""
import os
import json
import time
import urllib.request

import pytest

from kuavo_dataset_editor.desktop import LocalBackend, write_shortcut


def test_shortcut_preserves_workspace_and_does_not_overwrite(tmp_path):
    target = tmp_path / "Editor.desktop"
    write_shortcut(target, tmp_path / "workspace with spaces", [tmp_path / "data"])
    text = target.read_text()
    assert "Type=Application" in text and "Terminal=false" in text
    assert '"desktop" "--workspace"' in text
    assert str(tmp_path / "workspace with spaces") in text
    assert target.stat().st_mode & 0o111
    with pytest.raises(FileExistsError):
        write_shortcut(target, tmp_path, [])


@pytest.mark.skipif(os.environ.get("EDITOR_TEST_DESKTOP") != "1", reason="Opt-in local socket and Qt smoke test")
def test_native_window_and_backend(tmp_path):
    from PySide6.QtWidgets import QApplication
    from kuavo_dataset_editor.desktop import create_window

    app = QApplication.instance() or QApplication([])
    backend = LocalBackend(tmp_path / "workspace")
    try:
        backend.start()
        with urllib.request.urlopen(backend.url + "/api/projects", timeout=3) as response:
            assert response.read() == b"[]"
        selected = tmp_path / "chosen-data"
        selected.mkdir()
        backend.grant_directory(selected)
        assert selected.resolve() in backend.app.state.allowed_roots
        window = create_window(backend)
        window.show()
        result = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if not result or result[-1] != 4:
                window.page.runJavaScript("document.querySelectorAll('#taskPresets button').length", result.append)
            else:
                break
            time.sleep(0.05)
        assert 4 in result, "Frontend modules and task presets must load in Qt"
        options = []
        window.page.runJavaScript("JSON.stringify(Array.from(document.querySelectorAll('#exportMode option')).map(x=>x.value))", options.append)
        deadline = time.monotonic() + 3
        while not options and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert options and json.loads(options[0]) == ['new', 'append', 'replace']
        hub = []
        window.page.runJavaScript("JSON.stringify({private:document.getElementById('hubPrivate').checked,button:document.getElementById('hubPlan').textContent})", hub.append)
        deadline = time.monotonic() + 3
        while not hub and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert hub and json.loads(hub[0])['private'] is True
        assert '미리보기' in json.loads(hub[0])['button']
        window.set_path("importPath", str(selected / "sample.bag"))
        values = []
        window.page.runJavaScript("document.getElementById('importPath').value", values.append)
        deadline = time.monotonic() + 3
        while not values and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert values == [str(selected / "sample.bag")]
        if os.environ.get("EDITOR_DESKTOP_SCREENSHOT"):
            assert window.grab().save(os.environ["EDITOR_DESKTOP_SCREENSHOT"])
        # Test teardown avoids the user-facing close-confirmation dialog.
        window.hide()
        from shiboken6 import delete
        delete(window)
    finally:
        backend.stop()
    assert not backend.thread.is_alive()
