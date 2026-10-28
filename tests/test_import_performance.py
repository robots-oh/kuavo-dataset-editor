"""Bounded fixtures for lossless packs, deferred events and shared import caches."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path

import numpy as np
import pytest

from test_bag import make_fixture_bag
from kuavo_dataset_editor.bag import import_bag
from kuavo_dataset_editor.image_store import image_present, open_image, unpack
from kuavo_dataset_editor.import_cache import ImportCache, valid_catalog
from kuavo_dataset_editor.sources import CatalogSource, fingerprint, write_json
from kuavo_dataset_editor.source_events import source_events


def test_packs_preserve_every_compressed_byte_and_preview(tmp_path):
    from kuavo_dataset_editor.ros1_reader import Ros1Reader
    bag, profile = make_fixture_bag(tmp_path)
    before = fingerprint(bag)
    desc = import_bag(bag, tmp_path/'catalog', 'a', profile, lambda *_: None)
    source = CatalogSource(desc['path'])
    assert 'images' not in desc and source.manifest['report']['stored_image_messages'] == 36
    assert len(list(source.root.iterdir())) < 30
    assert not list(source.root.glob('camera_*/*'))
    assert len(list(source.root.glob('*.frames'))) == 3
    counters = {topic: 0 for topic in profile['cameras'].values()}
    with Ros1Reader(bag) as reader:
        connections = [c for c in reader.connections if c.topic in counters]
        for connection, _, raw in reader.messages(connections=connections):
            msg = reader.deserialize(raw, connection.msgtype)
            key = next(k for k, topic in profile['cameras'].items() if topic == connection.topic)
            ref = source.manifest['images'][key][counters[connection.topic]]
            name, offset, length = unpack(ref)
            with (source.root/name).open('rb') as stream:
                stream.seek(offset)
                assert stream.read(length) == bytes(msg.data)
            counters[connection.topic] += 1
            assert image_present(source.root, ref)
            with open_image(source.root, ref) as image:
                assert image.size == (64, 48)
    assert valid_catalog(source.root, bag, profile)
    assert np.allclose(source.row(0, 3)['action'], np.pi/2)
    assert fingerprint(bag) == before


def test_events_are_deferred_lossless_and_interval_filtered(tmp_path, monkeypatch):
    from kuavo_dataset_editor import bag as adapter
    bag, profile = make_fixture_bag(tmp_path)
    profile['event_topics'] = ['/kuavo_arm_traj', '/missing']
    before = fingerprint(bag)
    with monkeypatch.context() as changes:
        changes.setattr(adapter, 'plain', lambda *_: pytest.fail('JSON conversion during Import'))
        desc = import_bag(bag, tmp_path/'catalog', 'a', profile, lambda *_: None)
    source = CatalogSource(desc['path'])
    assert not list(source.root.glob('events*'))
    assert desc['preparation'] == {'editing': 'ready', 'events': 'on_export'}
    assert desc['report']['deferred_event_counts'] == {'/kuavo_arm_traj': 12, '/missing': 0}
    events = list(source_events(source, [(.2, .5)]))
    assert [e['source_time_s'] for e, _ in events] == [.2, .3, .4]
    assert all(not coordinates and e['message']['position'] == [90.]*14 for e, coordinates in events)
    assert len(list(source_events(source))) == 12
    assert fingerprint(bag) == before
    bag.touch()
    with pytest.raises(ValueError, match='변경'):
        list(source_events(source))


def test_shared_cache_reuse_no_reextract_and_new_annotation_identity(tmp_path, monkeypatch):
    from kuavo_dataset_editor import bag as adapter
    from kuavo_dataset_editor.core import label, new_project
    from kuavo_dataset_editor.store import Store
    bag, profile = make_fixture_bag(tmp_path)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    cache = ImportCache(workspace)
    first = cache.obtain(bag, profile, 'a', lambda *_: None)
    original_manifest = (Path(first['path'])/'catalog.json').read_bytes()
    store = Store(workspace)
    project = new_project('labels'); project['sources'] = [first]
    project = store.save(label(project, 'a', 0, 0, 5, 'pick'), -1)
    with monkeypatch.context() as changes:
        changes.setattr(adapter, 'import_bag', lambda *_: pytest.fail('re-extraction'))
        second = cache.obtain(bag, profile, 'b', lambda *_: None)
    assert second['path'] == first['path'] and second['source_id'] == 'b'
    assert second['import_cache']['reused'] and not first['import_cache']['reused']
    assert store.get(project['project_id']) == project
    assert (Path(first['path'])/'catalog.json').read_bytes() == original_manifest
    changed = deepcopy(profile); changed['fps'] = 5
    third = cache.obtain(bag, changed, 'c', lambda *_: None)
    assert third['path'] != first['path'] and third['episodes'][0]['num_frames'] == 6


def test_truncated_cache_is_not_reused_or_overwritten(tmp_path):
    bag, profile = make_fixture_bag(tmp_path)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    cache = ImportCache(workspace)
    first = cache.obtain(bag, profile, 'a', lambda *_: None)
    pack = next(Path(first['path']).glob('*.frames'))
    pack.write_bytes(pack.read_bytes()[:20])
    assert not valid_catalog(first['path'], bag, profile)
    second = cache.obtain(bag, profile, 'b', lambda *_: None)
    assert second['path'] != first['path'] and not second['import_cache']['reused']
    assert pack.read_bytes() and pack.stat().st_size == 20
    assert valid_catalog(second['path'], bag, profile)


def test_two_concurrent_imports_share_one_complete_generation(tmp_path):
    bag, profile = make_fixture_bag(tmp_path)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    cache = ImportCache(workspace)
    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(lambda sid: cache.obtain(bag, profile, sid, lambda *_: None), ['a', 'b']))
    assert result[0]['path'] == result[1]['path']
    assert sorted(r['import_cache']['reused'] for r in result) == [False, True]
    assert len(list((workspace/'import-cache').glob('*/catalog.json'))) == 1


def test_cancelled_generation_is_retained_but_never_reused(tmp_path):
    from kuavo_dataset_editor.jobs import Cancelled
    bag, profile = make_fixture_bag(tmp_path)
    workspace = tmp_path/'workspace'; workspace.mkdir()
    cache = ImportCache(workspace)
    def cancel(fraction, _):
        if fraction >= .92:
            raise Cancelled()
    with pytest.raises(Cancelled):
        cache.obtain(bag, profile, 'a', cancel)
    partial = next(p for p in cache.root.iterdir() if p.is_dir())
    assert not (partial/'import-ready.json').exists()
    result = cache.obtain(bag, profile, 'b', lambda *_: None)
    assert result['path'] != str(partial) and partial.is_dir()
    assert not result['import_cache']['reused']


def test_unknown_catalog_version_is_not_silently_adopted(tmp_path):
    bag, profile = make_fixture_bag(tmp_path)
    desc = import_bag(bag, tmp_path/'catalog', 'a', profile, lambda *_: None)
    root = Path(desc['path']); c = json.loads((root/'catalog.json').read_text())
    c['import_version'] = 999
    write_json(root/'catalog.json', c)
    assert not valid_catalog(root, bag, profile)


def test_legacy_job_catalog_adoption_checks_all_referenced_images(tmp_path, monkeypatch):
    from kuavo_dataset_editor import bag as adapter
    bag, profile = make_fixture_bag(tmp_path)
    workspace = tmp_path/'workspace'
    root = workspace/'jobs'/('a'*32)/'legacy'
    import_bag(bag, root, 'old', profile, lambda *_: None)
    c = json.loads((root/'catalog.json').read_text())
    for camera, refs in c['images'].items():
        folder = root/f'old_{len(camera)}_{camera.replace(".", "_")}'
        folder.mkdir()
        for i, ref in enumerate(refs):
            name, offset, length = unpack(ref)
            with (root/name).open('rb') as stream:
                stream.seek(offset); data = stream.read(length)
            path = folder/f'{i}.jpg'; path.write_bytes(data)
            refs[i] = str(path.relative_to(root))
    c.pop('import_version'); c.pop('events_source'); c['events'] = 'events.jsonl.gz'
    with gzip.open(root/c['events'], 'wt') as stream:
        stream.write('')
    (root/'import-ready.json').unlink()
    write_json(root/'catalog.json', c)
    assert valid_catalog(root, bag, profile)
    cache = ImportCache(workspace)
    with monkeypatch.context() as changes:
        changes.setattr(adapter, 'import_bag', lambda *_: pytest.fail('legacy re-extraction'))
        adopted = cache.obtain(bag, profile, 'new', lambda *_: None)
    assert adopted['path'] == str(root) and adopted['import_cache']['reused']
    (root/c['images'][c['cameras'][0]][-1]).unlink()
    assert not valid_catalog(root, bag, profile)


def test_pack_path_and_bounds_are_checked(tmp_path):
    for ref in ['packed:../secret:0:5', 'packed:/etc/passwd:0:5', 'packed:x:-1:5',
                'packed:x:0:0', 'packed:x:0:99999999999', 'packed:broken']:
        assert not image_present(tmp_path, ref)


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT') != '1', reason='six-frame official writer smoke')
def test_packed_bag_to_official_lerobot_with_deferred_events(tmp_path):
    from kuavo_dataset_editor.core import label, new_project
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.sources import LeRobotSource
    from kuavo_dataset_editor.storage import dataset_event_files, open_events
    bag, profile = make_fixture_bag(tmp_path)
    profile['event_topics'] = ['/kuavo_arm_traj']
    desc = import_bag(bag, tmp_path/'catalog', 'a', profile, lambda *_: None)
    before = fingerprint(bag)
    project = new_project('packed official export'); project['sources'] = [desc]
    project = label(project, 'a', 0, 2, 5, 'align')
    project = label(project, 'a', 0, 5, 8, 'pick')
    result = export_dataset(project, tmp_path/'output', tmp_path/'job', lambda *_: None)
    assert result['frames'] == 6 and result['episodes'] == 1
    dataset = LeRobotSource(tmp_path/'output')
    assert len(dataset.rows(0)) == 6
    assert dataset.rows(0)[0]['task_index'] != dataset.rows(0)[3]['task_index']
    assert dataset.row(0, 5)['observation.images.head'].shape == (48, 64, 3)
    np.testing.assert_allclose(dataset.row(0, 3, cameras=False)['action'], np.pi/2)
    events = []
    for path in dataset_event_files(tmp_path/'output/editor'):
        with open_events(path) as stream:
            events.extend(json.loads(line) for line in stream)
    assert [e['source_time_s'] for e in events] == [.2, .3, .4, .5, .6, .7]
    assert all(e['message']['position'] == [90.]*14 for e in events)
    assert fingerprint(bag) == before
