from copy import deepcopy
from pathlib import Path

from kuavo_dataset_editor.core import new_project, label
from kuavo_dataset_editor.sources import fingerprint
from kuavo_dataset_editor.store import Store
from kuavo_dataset_editor.relink_originals import relink, bag_fingerprint


def test_exact_relink_preserves_annotations_and_old_history(tmp_path):
    bags = tmp_path / 'bags'; bags.mkdir()
    bag = bags / 'test.bag'; bag.write_bytes(b'test metadata identity')
    cache = tmp_path / 'cache'; cache.mkdir()
    assert bag_fingerprint(bag) == fingerprint(bag)
    workspace = tmp_path / 'workspace'
    store = Store(workspace)
    project = new_project('relocation')
    project['sources'] = [{'source_id': 'a', 'kind': 'catalog', 'path': str(cache),
        'original_path': '/old-workstation/test.bag', 'fingerprint': fingerprint(bag),
        'fps': 30, 'episodes': [{'episode_id': 0, 'num_frames': 100}]}]
    project = label(project, 'a', 0, 0, 50, 'pick')
    original = deepcopy(store.save(project, -1))
    store.db.close()
    plan = relink(workspace, [bags])
    assert plan['unique_bags_relinked'] == 1
    assert Store(workspace).get(project['project_id']) == original
    result = relink(workspace, [bags], apply=True)
    assert result['descriptor_paths_relinked'] == 1
    reopened = Store(workspace)
    actual = reopened.get(project['project_id'])
    assert actual['sources'][0]['original_path'] == str(bag)
    assert actual['clips'] == original['clips'] and actual['tasks'] == original['tasks']
    assert actual['revision'] == 1
    assert reopened.get(project['project_id'], 0) == original
    assert relink(workspace, [bags], apply=True)['changes'] == []


def test_different_fingerprint_never_matches_filename_only(tmp_path):
    bags = tmp_path / 'bags'; bags.mkdir()
    bag = bags / 'test.bag'; bag.write_bytes(b'wrong bag')
    cache = tmp_path / 'cache'; cache.mkdir()
    store = Store(tmp_path / 'workspace')
    project = new_project('mismatch')
    project['sources'] = [{'source_id': 'a', 'kind': 'catalog', 'path': str(cache),
        'original_path': '/old-workstation/test.bag', 'fingerprint': {'method': 'manifest-size-mtime-sha256', 'value': 'wrong'},
        'fps': 30, 'episodes': [{'episode_id': 0, 'num_frames': 100}]}]
    original = store.save(label(project, 'a', 0, 0, 50, 'pick'), -1)
    result = relink(tmp_path / 'workspace', [bags], apply=True)
    assert result['changes'] == [] and result['skipped'][0]['reason'] == 'no_exact_fingerprint'
    assert store.get(project['project_id']) == original
