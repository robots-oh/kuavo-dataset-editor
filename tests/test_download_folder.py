"""Folder-only browsing and settings never move bags, edit labels, or start a job."""
import json
from copy import deepcopy

import pytest

from kuavo_dataset_editor.app import create_app
from kuavo_dataset_editor.core import EditError
from kuavo_dataset_editor.file_browser import browse_local


def test_directories_filtered_before_pagination(tmp_path):
    root=tmp_path/'allowed'; root.mkdir()
    for i in range(205): (root/f'{i:03d}.bag').write_bytes(b'bag')
    (root/'z-folder').mkdir()
    dataset=root/'dataset'; (dataset/'meta').mkdir(parents=True)
    (dataset/'meta/info.json').write_text('{}')
    (root/'.hidden').mkdir()
    (root/'outside').symlink_to(tmp_path)
    result=browse_local([root],root,directories_only=True,limit=1)
    assert result['total']==2 and result['next_offset']==1
    assert result['entries'][0]['name']=='dataset'
    next_page=browse_local([root],root,directories_only=True,offset=1)
    assert [entry['name'] for entry in next_page['entries']]==['z-folder']
    with pytest.raises(EditError,match='허용'):
        browse_local([root],root/'outside',directories_only=True)


def test_download_settings_saved_without_import_or_annotation_change(tmp_path):
    ssd=tmp_path/'SSD folder'; ssd.mkdir()
    bag=ssd/'already-received.bag'; bag.write_bytes(b'original bag untouched')
    workspace=tmp_path/'workspace'
    app=create_app(workspace,[ssd])
    endpoints={route.name:route.endpoint for route in app.routes if hasattr(route,'endpoint')}
    project=endpoints['create']({'name':'existing annotations'})
    before=deepcopy(app.state.store.get(project['project_id']))
    starts=[]
    app.state.jobs.start=lambda *args: starts.append(args) or {'id':'fake'}
    assert endpoints['browse_folders']()['roots']==[str(workspace),str(ssd)]
    assert endpoints['browse_folders'](str(ssd))['entries']==[]
    settings=endpoints['set_ssh_settings']({'download_dir':str(ssd),'transport':'stream','token':'NEVER_SAVE'})
    assert settings['download_dir']==str(ssd) and settings['server_hostname']
    assert settings['default_download_dir']==str(workspace/'downloads')
    saved=json.loads((workspace/'ssh-transfer-settings.json').read_text())
    assert saved=={'download_dir':str(ssd),'transport':'stream'}
    assert not starts and app.state.store.get(project['project_id'])==before
    assert bag.read_bytes()==b'original bag untouched'
    for path in (tmp_path/'outside',ssd/'missing',bag):
        with pytest.raises(EditError): endpoints['set_ssh_settings']({'download_dir':str(path)})
        assert json.loads((workspace/'ssh-transfer-settings.json').read_text())==saved
    result=endpoints['set_ssh_settings']({'download_dir':''})
    assert result['download_dir']==str(workspace/'downloads')
    assert result['transport']=='stream' and not (workspace/'downloads').exists()
    assert not starts


def test_changed_preference_does_not_retarget_existing_job(tmp_path,monkeypatch):
    from kuavo_dataset_editor import ssh_sources
    monkeypatch.setattr(ssh_sources,'host_aliases',lambda:['fixture-host'])
    a,b=tmp_path/'ssd-a',tmp_path/'ssd-b'; a.mkdir(); b.mkdir()
    app=create_app(tmp_path/'workspace',[a,b])
    routes={route.name:route.endpoint for route in app.routes if hasattr(route,'endpoint')}
    pid=routes['create']({'name':'project'})['project_id']
    payloads=[]
    app.state.jobs.start=lambda kind,payload: payloads.append(payload) or {'id':'fake'}
    routes['set_ssh_settings']({'download_dir':str(a)})
    routes['start_ssh_import'](pid,{'host':'fixture-host','paths':['/bags/first.bag']})
    routes['set_ssh_settings']({'download_dir':str(b)})
    assert payloads[0]['download_dir']==str(a)
    routes['start_ssh_import'](pid,{'host':'fixture-host','paths':['/bags/second.bag']})
    assert payloads[1]['download_dir']==str(b)
