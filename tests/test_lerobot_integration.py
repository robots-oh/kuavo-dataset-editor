from types import SimpleNamespace

import pytest

from kuavo_dataset_editor.integrations.lerobot import LeRobotBackend, LeRobotCompatibilityError


class Writer:
    def add_frame(self, frame):
        pass

    def save_episode(self, parallel_encoding=True):
        self.parallel_encoding = parallel_encoding

    def finalize(self):
        pass


class Modern(Writer):
    @classmethod
    def create(cls, repo_id, fps, features, root, video_backend, rgb_encoder, encoder_threads=1, robot_type=None):
        return SimpleNamespace(codec=rgb_encoder.vcodec, threads=encoder_threads, root=root)


class Legacy(Writer):
    @classmethod
    def create(cls, repo_id, fps, features, root, video_backend, vcodec):
        return SimpleNamespace(codec=vcodec, root=root)


def test_modern_and_legacy_api_shapes():
    modern = LeRobotBackend(Modern, SimpleNamespace, 'v3.0')
    assert modern.create_writer(fps=30, features={}, root='output').codec == 'h264'
    assert modern.create_writer(fps=30, features={}, root='output').threads == 2
    legacy = LeRobotBackend(Legacy, codebase_version='v3.0')
    assert legacy.create_writer(fps=30, features={}, root='output').codec == 'libx264'
    writer = Writer()
    modern.save_episode(writer)
    assert writer.parallel_encoding is False


def test_unknown_schema_or_api_is_explained():
    with pytest.raises(LeRobotCompatibilityError, match='v4.0'):
        LeRobotBackend(Modern, SimpleNamespace, 'v4.0')
    class Changed(Writer):
        @classmethod
        def create(cls, repo_id, fps, features, root, video_backend, new_codec):
            pass
    with pytest.raises(LeRobotCompatibilityError, match='H264'):
        LeRobotBackend(Changed, codebase_version='v3.0')
