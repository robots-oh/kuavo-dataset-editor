from copy import deepcopy
import json
import numpy as np
import pytest

from kuavo_dataset_editor.core import EditError, compile_selection, label, new_project
from kuavo_dataset_editor.quality import corrected_row, inspect_quality, repair_index, resolve_quality
from kuavo_dataset_editor.sources import CatalogSource, demo_source, write_json


@pytest.fixture
def gaps(tmp_path):
    root = tmp_path / 'source'
    manifest = demo_source(root, 'a')
    state = np.load(root / 'state.npy')
    state[5, 0] = np.nan
    np.save(root / 'state.npy', state)
    action = np.load(root / 'action.npy')
    action[7, 0] = np.nan
    np.save(root / 'action.npy', action)
    for key in manifest['cameras'][:2]:
        manifest['images'][key][8] = None
    target = np.arange(120, dtype=np.int64) * 100_000_000
    stamps = target.copy()
    stamps[5], stamps[6] = -1, 650_000_000
    np.savez(root / 'time_mapping.npz', bag_time_ns=target, **{'observation.state/message_ns': stamps})
    manifest['time_mapping'] = 'time_mapping.npz'
    manifest['profile'] = {'signals': [{'key': 'observation.state', 'unit': 'rad'}]}
    write_json(root / 'catalog.json', manifest)
    source = CatalogSource(root).descriptor('a')
    project = new_project('disposable gap fixture')
    project['sources'] = [source]
    project = label(project, 'a', 0, 0, 12, 'test')
    return project, root


def approval(report, choice=None):
    return {'token': report['token'], 'decisions': {i['id']: choice or i['method'] for i in report['issues']}}


def test_counts_and_real_sample_times(gaps):
    project, _ = gaps
    before = deepcopy(project)
    report = inspect_quality(project)
    assert report['checked_frames'] == 12 and report['invalid_frames'] == 3
    assert len(report['issues']) == 4 and not report['ready']
    state = next(i for i in report['issues'] if i['feature'] == 'observation.state')
    assert state['missing_frames'] == 1 and state['duration_s'] == .1
    assert state['sample_gap_s'] == .25 and state['method'] == 'linear'
    assert inspect_quality(project)['token'] == report['token']
    assert project == before


def test_explicit_repairs_keep_raw_values_and_annotations(gaps):
    project, root = gaps
    before = deepcopy(project)
    report = inspect_quality(project)
    resolved, report = resolve_quality(project, approval(report))
    assert report['ready'] and resolved == before and project == before
    repairs = repair_index(report)
    adapter = CatalogSource(root)
    with np.load(root / 'time_mapping.npz') as times:
        row = corrected_row(adapter, 0, 5, repairs[('a', 0, 5)], times)
    # Actual sensor timestamps, not half-way between adjacent frame indices.
    expected = adapter.arrays['observation.state'][4, 0] + .4 * (adapter.arrays['observation.state'][6, 0] - adapter.arrays['observation.state'][4, 0])
    assert row['observation.state'][0] == pytest.approx(expected)
    assert row['observation.state'][1] == adapter.arrays['observation.state'][5, 1]
    held = corrected_row(adapter, 0, 7, repairs[('a', 0, 7)])
    assert held['action'][0] == adapter.arrays['action'][6, 0]
    assert held['action'][1] == adapter.arrays['action'][7, 1]
    image = corrected_row(adapter, 0, 8, repairs[('a', 0, 8)])
    assert np.array_equal(image['observation.images.head'], adapter.row(0, 7, camera_key='observation.images.head')['observation.images.head'])
    assert np.isnan(np.load(root / 'state.npy')[5, 0])
    assert json.loads((root / 'catalog.json').read_text())['images']['observation.images.head'][8] is None


def test_exclusion_is_export_only_and_counts_overlaps_once(gaps):
    project, _ = gaps
    before = deepcopy(project)
    report = inspect_quality(project)
    resolved, _ = resolve_quality(project, approval(report, 'exclude'))
    assert compile_selection(resolved)['kept_frames'] == 9
    assert compile_selection(resolved)['num_episodes'] == 3
    assert project == before


def test_missing_stale_or_invalid_approval_is_rejected(gaps):
    project, root = gaps
    report = inspect_quality(project)
    with pytest.raises(EditError, match='모든 누락'):
        resolve_quality(project, {'token': report['token'], 'decisions': {}})
    with pytest.raises(EditError, match='지원하지'):
        resolve_quality(project, approval(report, 'invent_video'))
    changed = deepcopy(project)
    changed['revision'] += 1
    with pytest.raises(EditError, match='바뀌었'):
        resolve_quality(changed, approval(report))
    values = np.load(root / 'action.npy'); values[6] += 1
    np.save(root / 'action.npy', values)
    with pytest.raises(EditError, match='바뀌었'):
        resolve_quality(project, approval(report))


def test_recording_start_cannot_be_extrapolated(gaps):
    project, root = gaps
    state = np.load(root / 'state.npy'); state[0] = np.nan
    np.save(root / 'state.npy', state)
    report = inspect_quality(project)
    first = next(i for i in report['issues'] if i['feature'] == 'observation.state' and i['start_frame'] == 0)
    assert first['method'] is None and first['left_frame'] is None
    choices = approval(report, 'exclude')
    choices['decisions'][first['id']] = 'linear'
    with pytest.raises(EditError): resolve_quality(project, choices)


def test_api_requires_review_before_job_and_keeps_project(gaps, tmp_path):
    from kuavo_dataset_editor.app import create_app
    project, _ = gaps
    app = create_app(tmp_path / 'workspace', [tmp_path])
    project = app.state.store.save(project, -1)
    endpoints = {r.name: r.endpoint for r in app.routes if hasattr(r, 'endpoint')}
    started = []
    app.state.jobs.start = lambda kind, payload: started.append(payload) or {'id': 'fixture'}
    body = {'mode': 'new', 'repo_name': 'review-fixture'}
    plan = endpoints['export_plan'](project['project_id'], body)
    assert not plan['quality']['ready']
    with pytest.raises(EditError, match='누락 점검'):
        endpoints['start_export'](project['project_id'], body)
    assert not started
    body['quality'] = approval(plan['quality'])
    endpoints['start_export'](project['project_id'], body)
    assert started[0]['options']['quality'] == body['quality']
    assert app.state.store.get(project['project_id']) == project
