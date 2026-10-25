from copy import deepcopy
import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from kuavo_dataset_editor.core import EditError, label, new_project, compile_selection
from kuavo_dataset_editor.export_scope import select_source
from kuavo_dataset_editor.repositories import selected_project, prepare_export


def project():
    p = new_project('two recordings')
    p['sources'] = [dict(source_id=sid, kind='catalog', path='/cache/'+sid,
                         original_path='/bags/'+sid+'.bag', fps=30,
                         fingerprint={'method':'manifest-size-mtime-sha256','value':sid},
                         episodes=[{'episode_id':0,'num_frames':60}]) for sid in ('a','b')]
    p = label(p,'a',0,0,30,'pick')
    return label(p,'b',0,0,20,'place')


def provenance(root, p, rows):
    editor = root/'editor'
    editor.mkdir(parents=True)
    (editor/'project.json').write_text(json.dumps(p))
    pq.write_table(pa.Table.from_pylist(rows),editor/'frame_mapping.parquet')


def test_source_scope_preserves_original_annotations():
    p = project()
    p['goals'] = [{**p['clips'][0], 'id':'goal-a'}]
    p['exclusions'] = [{'source_id':'b','episode_id':0,'start_frame':5,'end_frame':7}]
    p['source_completed'] = {'a':True,'b':False}
    before = deepcopy(p)
    selected = select_source(p,'a')
    assert [s['source_id'] for s in selected['sources']] == ['a']
    assert len(selected['clips']) == len(selected['goals']) == 1
    assert selected['exclusions'] == [] and selected['source_completed'] == {'a':True}
    assert compile_selection(selected)['kept_frames'] == 30
    assert p == before
    assert select_source(p,None) == p
    with pytest.raises(EditError,match='현재 프로젝트'):
        select_source(p,'unknown')


def test_append_skips_overlaps_but_keeps_new_portions(tmp_path):
    p = project()
    provenance(tmp_path,p,[{'source_id':'a','source_episode':0,'source_frame':f}
                           for f in [*range(5,10),*range(20,25),*range(5,10)]])
    before = deepcopy(p)
    result = selected_project(select_source(p,'a'),tmp_path,'append')
    assert [(c['start_frame'],c['end_frame']) for c in result['clips']] == [(0,5),(10,20),(25,30)]
    assert result['_export_duplicate_frames'] == 10
    assert compile_selection(result)['num_episodes'] == 3
    assert p == before
    # Reapplying selection during whole-body conversion must be idempotent.
    repeated = selected_project(result,tmp_path,'append')
    assert repeated['clips'] == result['clips']
    assert repeated['_export_duplicate_frames'] == 10


def test_reimport_identity_uses_history_and_actual_output(tmp_path):
    saved = project()
    provenance(tmp_path,saved,[{'source_id':'a','source_episode':0,'source_frame':f} for f in range(30)])
    history = tmp_path/'editor/history/first'
    history.mkdir(parents=True)
    (history/'project.json').write_text(json.dumps(saved))
    (tmp_path/'editor/project.json').write_text(json.dumps(select_source(saved,'b')))
    incoming = select_source(saved,'a')
    incoming['sources'][0]['source_id'] = 'reimported'
    incoming['clips'][0]['source_id'] = 'reimported'
    incoming['sources'][0]['original_path'] = '/new-location/a.bag'
    result = selected_project(incoming,tmp_path,'append')
    assert result['clips'] == [] and result['_export_duplicate_frames'] == 30
    # Being registered in project.json is insufficient: b has never been written.
    assert compile_selection(selected_project(select_source(saved,'b'),tmp_path,'append'))['kept_frames'] == 20
    incoming['sources'][0]['fingerprint']['value'] = 'different-recording'
    assert len(selected_project(incoming,tmp_path,'append')['clips']) == 1


def test_all_existing_frames_stop_before_writer(tmp_path):
    p = project()
    provenance(tmp_path,p,[{'source_id':'a','source_episode':0,'source_frame':f} for f in range(30)])
    with pytest.raises(EditError,match='이미.*Export'):
        prepare_export(select_source(p,'a'),tmp_path,{'mode':'append'})


def test_api_preview_and_export_snapshots_share_source_scope(tmp_path,monkeypatch):
    from kuavo_dataset_editor.app import create_app
    from kuavo_dataset_editor.sources import demo_source
    app = create_app(tmp_path/'workspace',[tmp_path])
    endpoints = {route.name:route.endpoint for route in app.routes if hasattr(route,'endpoint')}
    p = new_project('api test')
    p['sources'] = [demo_source(tmp_path/sid,sid) for sid in ('a','b')]
    p = label(p,'a',0,1,6,'pick')
    p = label(p,'b',0,2,9,'carry')
    p = app.state.store.save(p,-1)
    captured = []
    def start(kind,payload):
        captured.append((kind,payload))
        return {'id':'fake-job'}
    monkeypatch.setattr(app.state.jobs,'start',start)
    body = {'source_id':'a','mode':'new','schema':'legacy','repo_name':'trial-a'}
    plan = endpoints['export_plan'](p['project_id'],body)
    assert plan['kept_frames'] == 5 and plan['source_names'] == ['a']
    endpoints['start_preview'](p['project_id'],body)
    endpoints['start_export'](p['project_id'],body)
    for kind,snapshot in captured:
        assert kind in ('export','export-preview')
        assert [s['source_id'] for s in snapshot['project']['sources']] == ['a']
        assert len(snapshot['project']['clips']) == 1
        assert snapshot['options']['source_id'] == 'a'
    batch = endpoints['export_plan'](p['project_id'],{**body,'source_id':None})
    assert batch['kept_frames'] == 12 and batch['source_names'] == ['a','b']
    assert app.state.store.get(p['project_id']) == p
    assert not (tmp_path/'workspace/exports/trial-a').exists()
    with pytest.raises(EditError,match='프로젝트 전체'):
        endpoints['export_plan'](p['project_id'],{**body,'mode':'replace'})


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT') != '1', reason='bounded official writer append test')
def test_writer_partial_append_and_repeat_are_duplicate_free(tmp_path):
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.sources import demo_source, fingerprint
    source = demo_source(tmp_path/'source','a')
    raw = tmp_path/'trial.bag'
    raw.write_bytes(b'fixture: original bag identity; legacy rows use the prepared catalog')
    source.update(original_path=str(raw),fingerprint=fingerprint(raw))
    p = new_project('one bag')
    p['sources'] = [source]
    first = label(p,'a',0,2,5,'pick')
    target = tmp_path/'output'
    export_dataset(first,target,tmp_path/'first',lambda *_:None,{'mode':'new'})
    addition = label(p,'a',0,3,8,'pick')
    plan = prepare_export(addition,target,{'mode':'append'})
    assert plan['skipped_duplicate_frames'] == 2 and plan['kept_frames'] == 3
    result = export_dataset(addition,target,tmp_path/'append',lambda *_:None,
                            {'mode':'append','confirm_existing':True})
    assert result['frames'] == 6 and result['frames_added'] == 3
    rows = pq.read_table(target/'editor/frame_mapping.parquet').to_pylist()
    assert [r['source_frame'] for r in rows] == list(range(2,8))
    assert len({(r['source_id'],r['source_episode'],r['source_frame']) for r in rows}) == 6
    before = fingerprint(target)
    with pytest.raises(EditError,match='이미.*Export'):
        export_dataset(addition,target,tmp_path/'repeat',lambda *_:None,
                       {'mode':'append','confirm_existing':True})
    assert fingerprint(target) == before and not (tmp_path/'repeat').exists()
