"""Fast paths must preserve images, reader validation and exact time windows."""
import io

import numpy as np
from PIL import Image
import pytest
from rosbags.rosbag1 import Reader

from test_bag import make_fixture_bag
from kuavo_dataset_editor.bag import import_bag
from kuavo_dataset_editor.bag_index import clear_index_cache
from kuavo_dataset_editor.frame_preview import PreviewCache, jpeg_preview
from kuavo_dataset_editor.image_store import unpack
from kuavo_dataset_editor.ros1_reader import Ros1Reader
from kuavo_dataset_editor.sources import CatalogSource, demo_source


def test_reader_snapshots_survive_close_and_have_independent_handles(tmp_path, monkeypatch):
    clear_index_cache()
    bag, _ = make_fixture_bag(tmp_path)
    calls = []
    original = Reader.open
    def opened(reader):
        calls.append(1); return original(reader)
    monkeypatch.setattr(Reader, 'open', opened)
    with Ros1Reader(bag) as first:
        expected = [(c.topic, stamp, bytes(raw)) for c, stamp, raw in first.messages()]
    with Ros1Reader(bag) as second:
        with Ros1Reader(bag) as third:
            assert third.reader.bio is not second.reader.bio
            assert len(list(third.messages())) == len(expected)
        assert [(c.topic, stamp, bytes(raw)) for c, stamp, raw in second.messages()] == expected
    assert len(calls) == 1
    bag.touch()
    with Ros1Reader(bag) as changed:
        assert len(list(changed.messages())) == len(expected)
    assert len(calls) == 2


def test_bounded_query_keeps_exact_boundaries_and_restores_indexes(tmp_path):
    clear_index_cache()
    bag, _ = make_fixture_bag(tmp_path)
    with Ros1Reader(bag) as reader:
        origin = reader.start_time
        connections = [c for c in reader.connections if c.topic == '/kuavo_arm_traj']
        full = [(t, bytes(b)) for _, t, b in reader.messages(connections)]
        bounded = [(t, bytes(b)) for _, t, b in reader.messages(connections, origin+200_000_000, origin+500_000_000)]
        assert bounded == full[2:5]
        assert reader.start_time == origin
        assert [(t, bytes(b)) for _, t, b in reader.messages(connections)] == full
        assert not list(reader.messages(connections, reader.end_time+1, reader.end_time+2))


def test_cached_reader_still_checks_message_md5_and_can_disable_cache(tmp_path, monkeypatch):
    clear_index_cache()
    bag, _ = make_fixture_bag(tmp_path)
    with Ros1Reader(bag): pass
    bad = Ros1Reader(bag)
    monkeypatch.setattr(bad.typestore, 'generate_msgdef', lambda *_: ('', 'not-the-recorded-digest'))
    with pytest.raises(ValueError, match='MD5'):
        bad.__enter__()
    monkeypatch.setenv('EDITOR_BAG_INDEX_CACHE_MB', '0')
    with Ros1Reader(bag) as reader:
        assert list(reader.messages())


def test_display_jpeg_fast_path_is_byte_exact_for_legacy_and_packs(tmp_path, monkeypatch):
    demo_source(tmp_path/'demo', 'demo')
    source = CatalogSource(tmp_path/'demo'); camera = source.cameras[0]
    path = source.root/source.manifest['images'][camera][2]
    monkeypatch.setattr(source, 'row', lambda *_a, **_k: pytest.fail('unnecessary pixel decoding'))
    assert jpeg_preview(source, 0, 2, camera) == path.read_bytes()
    bag, profile = make_fixture_bag(tmp_path)
    desc = import_bag(bag, tmp_path/'catalog', 'a', profile, lambda *_: None)
    source = CatalogSource(desc['path']); camera = source.cameras[0]
    name, offset, length = unpack(source.manifest['images'][camera][3])
    with (source.root/name).open('rb') as stream:
        stream.seek(offset); expected = stream.read(length)
    assert jpeg_preview(source, 0, 3, camera) == expected
    with pytest.raises(ValueError): jpeg_preview(source, 0, -1, camera)


def test_preview_cache_is_bounded_and_file_changes_invalidate(tmp_path):
    cache = PreviewCache(10)
    cache.put('a', b'123456'); cache.put('b', b'567890')
    assert cache.get('a') is None and cache.size == 6
    cache.put('huge', b'x'*20)
    assert cache.size <= 10 and cache.get('huge') is None
    demo_source(tmp_path/'demo', 'demo')
    source = CatalogSource(tmp_path/'demo'); camera = source.cameras[0]
    before = jpeg_preview(source, 0, 2, camera)
    path = source.root/source.manifest['images'][camera][2]
    Image.new('RGB', (1280, 720), 'red').save(path)
    after = jpeg_preview(source, 0, 2, camera)
    assert before != after
    assert Image.open(io.BytesIO(after)).size == (960, 540)
    assert Image.open(io.BytesIO(jpeg_preview(source, 0, 2, camera, True))).size == (640, 360)
    assert np.array(Image.open(io.BytesIO(after)))[0, 0, 0] > 200
