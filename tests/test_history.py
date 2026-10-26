from copy import deepcopy

from fastapi.testclient import TestClient

from kuavo_dataset_editor.annotations import edit_annotation
from kuavo_dataset_editor.app import create_app
from kuavo_dataset_editor.core import label, new_project


def fixture(tmp_path):
    app=create_app(tmp_path/'workspace')
    p=new_project('undo test')
    p['sources']=[{'source_id':'a','kind':'catalog','path':str(tmp_path/'source'),
        'fps':30,'fingerprint':{'value':'a'},'origin_bag_time_ns':1792656486431530713,
        'episodes':[{'episode_id':0,'num_frames':120}]}]
    p=label(p,'a',0,0,120,'carry')
    p=edit_annotation(p,{'operation':'label','track':'goal','source_id':'a',
        'episode_id':0,'start_frame':0,'end_frame':120,'task':'Move the box.'})
    p=app.state.store.save(p,-1)
    return app,TestClient(app),p


def exclude(client,p):
    response=client.post(f"/api/projects/{p['project_id']}/edit",json={
        'operation':'exclude','expected_revision':p['revision'],'source_id':'a',
        'episode_id':0,'start_frame':30,'end_frame':60})
    assert response.status_code==200
    return response.json()


def test_undo_redo_exclusion_does_not_round_trip_original_timestamps(tmp_path):
    app,client,original=fixture(tmp_path)
    pid=original['project_id'];url=f'/api/projects/{pid}'
    excluded=exclude(client,original)
    rounded=deepcopy(original)
    rounded['sources'][0]['origin_bag_time_ns']=int(float(rounded['sources'][0]['origin_bag_time_ns']))
    # Reproduce the rejected old browser payload, without weakening the guard.
    assert client.put(url,json={'project':rounded,'expected_revision':excluded['revision']}).status_code==409
    restored=client.put(url,json={'restore_revision':original['revision'],
        'expected_revision':excluded['revision']})
    assert restored.status_code==200
    restored=restored.json()
    for key in ('clips','goals','exclusions','tasks','sources'):
        assert restored[key]==original[key]
    assert restored['sources'][0]['origin_bag_time_ns']==1792656486431530713
    redone=client.put(url,json={'restore_revision':excluded['revision'],
        'expected_revision':restored['revision']})
    assert redone.status_code==200
    for key in ('clips','goals','exclusions','sources'):
        assert redone.json()[key]==excluded[key]
    assert app.state.store.get(pid,original['revision'])==original


def test_history_preserves_later_imports_their_labels_and_running_jobs(tmp_path):
    app,client,original=fixture(tmp_path)
    current=exclude(client,original)
    added={**deepcopy(current['sources'][0]),'source_id':'b','fingerprint':{'value':'b'}}
    current['sources'].append(added)
    current=label(current,'b',0,0,90,'imported task')
    current['source_splits']={'a':'train','b':'validation'}
    current['name']='current project name'
    current=app.state.store.save(current,current['revision'])
    app.state.store.put_job({'id':'extract','status':'running'})
    kept=[c for c in current['clips'] if c['source_id']=='b']
    response=client.put(f"/api/projects/{original['project_id']}",json={
        'restore_revision':original['revision'],'expected_revision':current['revision']})
    assert response.status_code==200
    p=response.json()
    assert p['sources']==current['sources'] and p['name']==current['name']
    assert [c for c in p['clips'] if c['source_id']=='a']==original['clips']
    assert [c for c in p['clips'] if c['source_id']=='b']==kept
    assert p['tasks'][kept[0]['task_id']]=='imported task'
    assert p['source_splits']=={'a':'unspecified','b':'validation'}
    assert app.state.store.job('extract')['status']=='running'


def test_history_keeps_revision_conflicts_and_source_edit_protection(tmp_path):
    app,client,original=fixture(tmp_path)
    current=exclude(client,original)
    pid=original['project_id'];url=f'/api/projects/{pid}'
    stale=client.put(url,json={'restore_revision':0,'expected_revision':0})
    assert stale.status_code==409 and '다른 창' in stale.json()['detail']
    for revision in (-1,1.5,True,None,99):
        assert client.put(url,json={'restore_revision':revision,
            'expected_revision':current['revision']}).status_code==409
    changed=deepcopy(current);changed['sources'][0]['path']='/elsewhere'
    assert client.put(url,json={'project':changed,'expected_revision':current['revision']}).status_code==409
    assert app.state.store.get(pid)==current
