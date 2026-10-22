from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from kuavo_dataset_editor.annotations import edit_annotation,project_annotations,AnnotationLookup
from kuavo_dataset_editor.core import new_project,label,compile_selection,EditError
from kuavo_dataset_editor.sources import CatalogSource,demo_source,fingerprint
from kuavo_dataset_editor.repositories import prepare_export,with_existing_labels


@pytest.fixture
def hierarchy(tmp_path):
    root=tmp_path/'catalog';demo_source(root,'a')
    p=new_project('hierarchy');p['sources']=[CatalogSource(root).descriptor('a')]
    for a,b,text in [(0,3,'align'),(3,6,'pick'),(6,8,'carry'),(8,10,'place')]:p=label(p,'a',0,a,b,text)
    return edit_annotation(p,{'operation':'label','track':'goal','source_id':'a','episode_id':0,
                              'start_frame':0,'end_frame':10,'task':'Move box to conveyor.'})


def test_independent_tracks_and_projection(hierarchy):
    p=hierarchy;before=deepcopy(p)
    assert len(p['clips'])==4 and len(p['goals'])==1
    step=project_annotations(p,'step');goal=project_annotations(p,'goal')
    assert [c['task'] for c in compile_selection(step)['runs'][0]['clips']]==['align','pick','carry','place']
    assert [c['task'] for c in compile_selection(goal)['runs'][0]['clips']]==['Move box to conveyor.']
    assert compile_selection(goal)['kept_frames']==10
    assert project_annotations(goal,'goal')==goal
    assert p==before and len(goal['_annotation_original']['clips'])==4
    lookup=AnnotationLookup(goal)
    assert lookup.at('a',0,4)['step_task']=='pick'
    assert lookup.at('a',0,4)['goal_task']=='Move box to conveyor.'
    g=p['goals'][0]
    changed=edit_annotation(p,{'operation':'update_clip','track':'goal','clip_id':g['id'],
                              'start_frame':1,'end_frame':9,'task':'Updated goal'})
    assert changed['clips']==p['clips'] and changed['goals'][0]['start_frame']==1
    with pytest.raises(EditError):project_annotations(p,'invalid')
    with pytest.raises(EditError):edit_annotation(p,{'operation':'remove_clip','track':'step','clip_id':g['id']})


def test_goals_do_not_overlap_each_other_but_overlap_steps(hierarchy):
    p=edit_annotation(hierarchy,{'operation':'label','track':'goal','source_id':'a','episode_id':0,
                                'start_frame':10,'end_frame':15,'task':'Second box','include_unlabeled':True})
    assert compile_selection(project_annotations(p,'goal'))['num_episodes']==2
    with pytest.raises(EditError,match='겹치는'):
        edit_annotation(p,{'operation':'label','track':'goal','source_id':'a','episode_id':0,
                           'start_frame':9,'end_frame':12,'task':'overlap'})
    bad=deepcopy(p);bad['goals'][1]['start_frame']=9
    with pytest.raises(EditError,match='겹칩니다'):compile_selection(bad)


def test_gaps_explicit_exclusions_and_episode_boundaries(hierarchy):
    p=hierarchy
    # Removing an annotation is not permission to implicitly restore it via Goal.
    p=edit_annotation(p,{'operation':'remove_annotation','track':'step','source_id':'a','episode_id':0,'start_frame':3,'end_frame':6})
    assert compile_selection(project_annotations(p,'goal'))['kept_frames']==7
    assert compile_selection(project_annotations(p,'goal'))['num_episodes']==2
    p['goals'][0]['include_unlabeled']=True
    assert compile_selection(project_annotations(p,'goal'))['kept_frames']==10
    p['exclusions']=[{'id':'x','source_id':'a','episode_id':0,'start_frame':4,'end_frame':5}]
    assert compile_selection(project_annotations(p,'goal'))['kept_frames']==9
    assert compile_selection(project_annotations(p,'goal'))['num_episodes']==2
    p['exclusions']=[];p['clips'][-1]['new_episode']=True
    assert compile_selection(project_annotations(p,'goal'))['num_episodes']==2
    p=edit_annotation(p,{'operation':'remove_clip','track':'goal','clip_id':p['goals'][0]['id']})
    assert len(p['clips'])==3 and not p['goals']


def test_legacy_project_and_global_exclude_api(hierarchy,tmp_path):
    from kuavo_dataset_editor.app import create_app
    from kuavo_dataset_editor.sources import write_json
    p=deepcopy(hierarchy);p.pop('goals');p.pop('exclusions')
    assert compile_selection(project_annotations(p,'step'))['kept_frames']==10
    assert compile_selection(project_annotations(p,'goal'))['kept_frames']==0
    app=create_app(tmp_path/'workspace',[tmp_path]);store=app.state.store
    p=store.save(hierarchy,-1);pid=p['project_id']
    routes={r.name:r.endpoint for r in app.routes if hasattr(r,'endpoint')}
    assert routes['config']()['annotation_tracks']==['step','goal']
    p=routes['edit'](pid,{'operation':'exclude','expected_revision':p['revision'],
                         'source_id':'a','episode_id':0,'start_frame':4,'end_frame':5})
    assert p['goals']==hierarchy['goals']
    assert prepare_export(p,tmp_path/'goal',{'annotation_track':'goal'})['kept_frames']==9
    saved=deepcopy(p)
    p=routes['edit'](pid,{'operation':'restore_exclusion','expected_revision':p['revision'],'exclusion_id':p['exclusions'][0]['id']})
    assert p['clips']==saved['clips'] and not p['exclusions']
    starts=[];app.state.jobs.start=lambda kind,payload:starts.append(payload) or {'id':'fake'}
    routes['start_export'](pid,{'repo_name':'goals','annotation_track':'goal'})
    assert starts[0]['options']['annotation_track']=='goal'
    # Same original recording always shares the split, irrespective of annotation level.
    assert project_annotations(p,'step')['source_splits']==project_annotations(p,'goal')['source_splits']


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT')!='1',reason='10-frame hierarchy official writer smoke')
def test_goal_step_exports_preview_and_reimport(hierarchy,tmp_path):
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.sources import LeRobotSource
    from kuavo_dataset_editor.data_preview import build_preview,preview_row,preview_key
    from kuavo_dataset_editor.repositories import task_names
    p=hierarchy;before=deepcopy(p);stamp=fingerprint(p['sources'][0]['path'])
    build_preview(p,tmp_path/'goal',{'annotation_track':'goal'},tmp_path/'preview',lambda *_:None)
    saved=json.loads((tmp_path/'preview/preview.json').read_text())
    assert saved['public']['annotation_track']=='goal'
    assert preview_row(saved,'a',0,4)['task']=='Move box to conveyor.'
    assert preview_key(p,tmp_path/'goal',{'annotation_track':'goal'})!=preview_key(p,tmp_path/'goal',{'annotation_track':'step'})
    for track in ['step','goal']:
        target=tmp_path/track
        result=export_dataset(p,target,tmp_path/('job-'+track),lambda *_:None,{'annotation_track':track})
        assert result['frames']==10 and result['episodes']==1
        source=LeRobotSource(target);names=task_names(target)
        tasks=[names[row['task_index']] for row in source.rows(0)]
        assert tasks[4]==('pick' if track=='step' else 'Move box to conveyor.')
        assert (target/'editor/annotations.json').is_file()
        imported=new_project('roundtrip');descriptor=source.descriptor('b');imported['sources']=[descriptor]
        imported=with_existing_labels(imported,descriptor)
        assert len(imported['clips'])==4 and len(imported['goals'])==1
        assert compile_selection(project_annotations(imported,'goal'))['kept_frames']==10
    assert p==before and stamp==fingerprint(p['sources'][0]['path'])


def test_wholebody_goal_projection(hierarchy,tmp_path):
    from test_wholebody import recording
    from kuavo_dataset_editor.data_preview import build_preview,preview_row
    from kuavo_dataset_editor.wholebody import FULL
    root=tmp_path/'robot';root.mkdir();p=recording(root)
    p=edit_annotation(p,{'operation':'label','track':'goal','source_id':'a','episode_id':0,
                         'start_frame':0,'end_frame':5,'task':'Full body goal'})
    build_preview(p,tmp_path/'out',{'schema':FULL,'annotation_track':'goal'},tmp_path/'preview',lambda *_:None)
    saved=json.loads((tmp_path/'preview/preview.json').read_text())
    row=preview_row(saved,'a',0,0)
    assert row['task']=='Full body goal'
    assert len([f for f in row['fields'] if f['feature']=='action'])==23
