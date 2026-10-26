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
        assert {key.toString() for key in window.refresh_action.shortcuts()} == {'F5', 'Ctrl+R'}
        ready = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            window.page.runJavaScript(
                "!document.getElementById('refresh').disabled && document.getElementById('projectSelect').options.length>0",
                ready.append)
            if True in ready:
                break
            time.sleep(0.05)
        assert True in ready, 'Initial project loading must finish before refreshing'
        # Refresh sees projects created elsewhere and keeps an unsubmitted draft.
        drafted = []
        window.page.runJavaScript(
            "document.getElementById('task').value='unsubmitted task';"
            "document.getElementById('repoName').value='draft-output';true", drafted.append)
        deadline = time.monotonic() + 3
        while not drafted and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        request = urllib.request.Request(backend.url + '/api/projects',
            data=json.dumps({'name': 'new date project'}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=3) as response:
            assert json.load(response)['name'] == 'new date project'
        window.refresh()
        refreshed = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            window.page.runJavaScript(
                "Boolean(document.getElementById('refresh') && "
                "Array.from(document.querySelectorAll('#projectSelect option')).some(x=>x.text==='new date project') && "
                "document.getElementById('task').value==='unsubmitted task' && "
                "document.getElementById('repoName').value==='draft-output' && "
                "document.getElementById('importPath').value===" + json.dumps(str(selected / 'sample.bag')) + ")",
                refreshed.append)
            if True in refreshed:
                break
            time.sleep(0.05)
        if True not in refreshed:
            diagnostics = []
            window.page.runJavaScript(
                "JSON.stringify({task:document.getElementById('task').value,"
                "repo:document.getElementById('repoName').value,path:document.getElementById('importPath').value,"
                "toast:document.getElementById('toast').textContent,"
                "projects:Array.from(document.querySelectorAll('#projectSelect option')).map(x=>x.text),"
                "draft:sessionStorage.getItem('editorRefresh')})", diagnostics.append)
            deadline = time.monotonic() + 3
            while not diagnostics and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
            pytest.fail('Refresh did not restore its draft: ' + str(diagnostics))
        assert all(not p['clips'] for p in backend.app.state.store.projects())
        window.loaded(False)
        assert window.refresh_action.isEnabled(), 'Refresh must also recover a failed page load'
        window.loaded(True)
        if os.environ.get("EDITOR_DESKTOP_SCREENSHOT"):
            assert window.grab().save(os.environ["EDITOR_DESKTOP_SCREENSHOT"])
        # Test teardown avoids the user-facing close-confirmation dialog.
        window.hide()
        from shiboken6 import delete
        delete(window)
    finally:
        backend.stop()
    assert not backend.thread.is_alive()


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_DESKTOP') != '1', reason='Opt-in Qt clip menu test')
def test_clip_context_menu_deletes_only_clicked_track_and_undoes(tmp_path):
    from PySide6.QtWidgets import QApplication
    from kuavo_dataset_editor.desktop import create_window
    from kuavo_dataset_editor.core import new_project, label
    from kuavo_dataset_editor.annotations import edit_annotation
    from kuavo_dataset_editor.sources import demo_source, CatalogSource, fingerprint
    from shiboken6 import delete

    app = QApplication.instance() or QApplication([])
    backend = LocalBackend(tmp_path / 'workspace')
    window = None
    try:
        catalog = tmp_path / 'workspace/catalog'
        demo_source(catalog, 'a')
        project = new_project('clip menu test')
        project['sources'] = [CatalogSource(catalog).descriptor('a')]
        project = label(project, 'a', 0, 0, 5, 'pick')
        project = label(project, 'a', 0, 5, 10, 'place')
        project = edit_annotation(project, {'operation': 'label', 'track': 'goal',
            'source_id': 'a', 'episode_id': 0, 'start_frame': 0, 'end_frame': 10, 'task': 'move box'})
        project = backend.app.state.store.save(project, -1)
        pid = project['project_id']
        stamp = fingerprint(catalog)
        backend.start()
        window = create_window(backend)
        window.show()

        def js(script):
            result = []
            window.page.runJavaScript(script, result.append)
            deadline = time.monotonic() + 3
            while not result and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert result, script
            return result[-1]

        def until(predicate):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                time.sleep(0.02)
            pytest.fail('Clip menu operation did not finish: ' + str(js(
                "JSON.stringify({toast:document.getElementById('toast').textContent,"
                "menuHidden:document.getElementById('clipContextMenu').hidden,"
                "save:document.getElementById('saveState').textContent,"
                "goalCount:document.querySelectorAll('#goalTrack .clip').length,"
                "stepCount:document.querySelectorAll('#track .clip').length})")))

        def current():
            return backend.app.state.store.get(pid)

        until(lambda: js("Boolean(document.querySelector('#track .clip') && !document.getElementById('refresh').disabled)"))
        for selector, removed, kept, select_first in [
            ('#goalTrack .clip', 'goals', 'clips', False),
            ('#track .clip .handle', 'clips', 'goals', False),
            ('#goalTrack .clip', 'goals', 'clips', True),
        ]:
            if select_first:
                js("document.querySelector(" + json.dumps(selector) + ").dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}))")
            before_clock = js("document.getElementById('clock').textContent")
            js("document.getElementById('task').value='keep this draft'")
            # A real contextmenu DOM event must suppress the native browser menu.
            assert js("(()=>{const el=document.querySelector(" + json.dumps(selector) + ");"
                "el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:2}));"
                "return el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,button:2,clientX:400,clientY:400}));})()") is False
            assert js("document.getElementById('clipContextMenu').hidden") is False
            assert js("document.getElementById('clock').textContent") == before_clock
            assert js("document.getElementById('task').value") == 'keep this draft'
            before = current()
            count_script = "document.querySelectorAll(" + json.dumps('#goalTrack .clip' if removed == 'goals' else '#track .clip') + ").length"
            js("document.getElementById('deleteContextClip').click()")
            until(lambda: len(current()[removed]) == len(before[removed]) - 1
                  and js(count_script) == len(before[removed]) - 1)
            assert current()[kept] == before[kept]
            assert current()['sources'] == before['sources']
            assert js("document.getElementById('task').value") == ('' if select_first else 'keep this draft')
            js("document.getElementById('undo').click()")
            until(lambda: len(current()[removed]) == len(before[removed])
                  and js(count_script) == len(before[removed]))
            assert current()['clips'] == before['clips'] and current()['goals'] == before['goals']
            js("document.getElementById('task').value='keep this draft'")
        assert fingerprint(catalog) == stamp
    finally:
        if window is not None:
            window.hide()
            delete(window)
        backend.stop()
