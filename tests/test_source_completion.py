from copy import deepcopy
import json

import pytest

from kuavo_dataset_editor.core import compile_selection
from test_history import fixture, exclude


def mark(client,p,completed=True):
    response=client.post(f"/api/projects/{p['project_id']}/edit",json={
        'operation':'source_completion','source_id':'a','completed':completed,
        'expected_revision':p['revision']})
    assert response.status_code==200
    return response.json()


def test_completion_is_persistent_manual_review_not_export_coverage(tmp_path):
    app,client,p=fixture(tmp_path)
    p=exclude(client,p)
    before=deepcopy(p)
    app.state.store.put_job({'id':'import-running','status':'running'})
    p=mark(client,p)
    assert p['source_completed']=={'a':True}
    assert p['sources']==before['sources'] and p['clips']==before['clips']
    assert p['goals']==before['goals'] and p['exclusions']==before['exclusions']
    assert compile_selection(p)['kept_frames']==compile_selection(before)['kept_frames']
    raw=app.state.store.db.execute('SELECT payload FROM projects WHERE id=?',(p['project_id'],)).fetchone()[0]
    assert json.loads(raw)['source_completed']=={'a':True}
    assert client.get(f"/api/projects/{p['project_id']}").json()['source_completed']=={'a':True}
    assert app.state.store.job('import-running')['status']=='running'
    assert mark(client,p,False)['source_completed']=={'a':False}


@pytest.mark.parametrize('operation', ['step','goal','exclude','restore_exclusion','split'])
def test_annotation_changes_reopen_only_the_affected_completed_source(tmp_path,operation):
    app,client,p=fixture(tmp_path)
    p['sources'].append({**deepcopy(p['sources'][0]),'source_id':'b','fingerprint':{'value':'b'}})
    p['source_completed']={'b':True}
    p=app.state.store.save(p,p['revision'])
    if operation=='restore_exclusion':p=exclude(client,p)
    p=mark(client,p)
    request={'expected_revision':p['revision'],'source_id':'a','episode_id':0,'start_frame':30,'end_frame':60}
    if operation in ('step','goal'):
        request.update(operation='label',track=operation,task='Updated instruction',replace=True)
    elif operation=='split':request.update(operation='split',clip_id=p['clips'][0]['id'],frame=60)
    elif operation=='restore_exclusion':request.update(operation=operation,exclusion_id=p['exclusions'][0]['id'])
    else:request['operation']=operation
    response=client.post(f"/api/projects/{p['project_id']}/edit",json=request)
    assert response.status_code==200
    assert response.json()['source_completed']=={'a':False,'b':True}


def test_completion_undo_redo_and_legacy_project(tmp_path):
    app,client,p=fixture(tmp_path)
    p.pop('source_completed')
    p=app.state.store.save(p,p['revision'])
    marked=mark(client,p)
    changed=exclude(client,marked)
    url=f"/api/projects/{p['project_id']}"
    undo=client.put(url,json={'restore_revision':marked['revision'],'expected_revision':changed['revision']})
    assert undo.status_code==200 and undo.json()['source_completed']['a'] is True
    redo=client.put(url,json={'restore_revision':changed['revision'],'expected_revision':undo.json()['revision']})
    assert redo.status_code==200 and redo.json()['source_completed']['a'] is False


def test_completion_rejects_unknown_sources_bad_values_and_stale_edits(tmp_path):
    app,client,p=fixture(tmp_path)
    url=f"/api/projects/{p['project_id']}/edit"
    request={'operation':'source_completion','source_id':'a','expected_revision':p['revision']}
    for value in ('true',1,None):
        assert client.post(url,json={**request,'completed':value}).status_code==409
    assert client.post(url,json={**request,'source_id':'missing','completed':True}).status_code==409
    assert client.post(url,json={**request,'expected_revision':-1,'completed':True}).status_code==409
    assert app.state.store.get(p['project_id'])==p


def mark_not_required(client,p,value=True):
    response=client.post(f"/api/projects/{p['project_id']}/edit",json={
        'operation':'source_not_required','source_id':'a','not_required':value,
        'expected_revision':p['revision']})
    assert response.status_code==200
    return response.json()


@pytest.mark.parametrize('has_labels',[False,True])
def test_not_required_persists_without_changing_labels_sources_jobs_or_export(tmp_path,has_labels):
    app,client,p=fixture(tmp_path)
    if not has_labels:
        p['clips']=[];p['goals']=[]
        p=app.state.store.save(p,p['revision'])
    p=mark(client,p)
    before=deepcopy(p)
    app.state.store.put_job({'id':'import-running','status':'running'})
    p=mark_not_required(client,p)
    assert p['source_not_required']=={'a':True}
    assert p['source_completed']=={'a':False}
    for key in ('sources','tasks','clips','goals','exclusions'):
        assert p[key]==before[key]
    plan=compile_selection(p);original_plan=compile_selection(before)
    assert {k:v for k,v in plan.items() if k!='project_revision'}=={
        k:v for k,v in original_plan.items() if k!='project_revision'}
    assert client.get(f"/api/projects/{p['project_id']}").json()['source_not_required']=={'a':True}
    stored=app.state.store.db.execute('SELECT payload FROM projects WHERE id=?',(p['project_id'],)).fetchone()[0]
    assert json.loads(stored)['source_not_required']=={'a':True}
    assert app.state.store.job('import-running')['status']=='running'
    unmarked=mark_not_required(client,p,False)
    assert unmarked['source_not_required']=={'a':False}
    assert unmarked['source_completed']=={'a':False}
    skipped=mark_not_required(client,unmarked)
    completed=mark(client,skipped)
    assert completed['source_completed']=={'a':True}
    assert completed['source_not_required']=={'a':False}


def test_not_required_history_supports_legacy_and_preserves_later_sources(tmp_path):
    app,client,p=fixture(tmp_path)
    p.pop('source_not_required')
    p=app.state.store.save(p,p['revision'])
    skipped=mark_not_required(client,p)
    current=deepcopy(skipped)
    current['sources'].append({**deepcopy(p['sources'][0]),'source_id':'b','fingerprint':{'value':'b'}})
    current['source_not_required']['b']=True
    current=app.state.store.save(current,current['revision'])
    url=f"/api/projects/{p['project_id']}"
    restored=client.put(url,json={'restore_revision':p['revision'],'expected_revision':current['revision']})
    assert restored.status_code==200
    assert restored.json()['source_not_required']=={'a':False,'b':True}
    redone=client.put(url,json={'restore_revision':skipped['revision'],'expected_revision':restored.json()['revision']})
    assert redone.status_code==200
    assert redone.json()['source_not_required']=={'a':True,'b':True}
    assert redone.json()['sources']==current['sources']
    assert 'source_not_required' not in app.state.store.get(p['project_id'],p['revision'])


def test_not_required_rejects_bad_sources_values_and_stale_edits(tmp_path):
    app,client,p=fixture(tmp_path)
    url=f"/api/projects/{p['project_id']}/edit"
    request={'operation':'source_not_required','source_id':'a','expected_revision':p['revision']}
    for value in ('true',1,None):
        assert client.post(url,json={**request,'not_required':value}).status_code==409
    assert client.post(url,json={**request,'source_id':'missing','not_required':True}).status_code==409
    assert client.post(url,json={**request,'expected_revision':-1,'not_required':True}).status_code==409
    assert app.state.store.get(p['project_id'])==p
