"""Compatibility boundary for the official LeRobot dataset writer and reader."""
import importlib
from importlib.metadata import PackageNotFoundError, distribution, version
import inspect
import json
import re
from pathlib import Path


class LeRobotCompatibilityError(RuntimeError):
    pass


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


class LeRobotBackend:
    def __init__(self, dataset_class=None, encoder_class=None, codebase_version=None, runtime=None, heartbeat=lambda: None):
        self.runtime, self.heartbeat = runtime, heartbeat
        if dataset_class is None:
            try:
                module = importlib.import_module("lerobot.datasets.lerobot_dataset")
                dataset_class = module.LeRobotDataset
                try:
                    metadata = importlib.import_module("lerobot.datasets.dataset_metadata")
                    codebase_version = metadata.CODEBASE_VERSION
                except (ImportError, AttributeError):
                    codebase_version = module.CODEBASE_VERSION
                if "rgb_encoder" in inspect.signature(dataset_class.create).parameters:
                    encoder_class = importlib.import_module("lerobot.configs").RGBEncoderConfig
            except (ImportError, AttributeError, OSError) as exc:
                raise LeRobotCompatibilityError(
                    "현재 서버 환경에서 LeRobot/영상 라이브러리를 불러올 수 없습니다. "
                    "서버 PC에서 bash setup.sh --headless 후 bash run.sh doctor --export를 확인하고 "
                    "편집기 서버를 재시작하세요. workspace/annotation은 유지됩니다. "
                    f"원인: {exc}"
                ) from exc
        self.dataset_class = dataset_class
        self.encoder_class = encoder_class
        self.codebase_version = codebase_version
        self.parameters = inspect.signature(dataset_class.create).parameters
        missing = {"repo_id", "fps", "features", "root"} - self.parameters.keys()
        if missing or not all(callable(getattr(dataset_class, method, None)) for method in ("add_frame", "save_episode", "finalize")):
            raise LeRobotCompatibilityError("지원하지 않는 LeRobot writer 계약입니다. integrations/lerobot.py를 갱신하세요.")
        if codebase_version != "v3.0":
            raise LeRobotCompatibilityError(f"지원하지 않는 LeRobot 출력 형식 {codebase_version!r}. v3.0 어댑터를 사용하세요.")
        if "rgb_encoder" in self.parameters and encoder_class is not None:
            self.api_shape = "rgb_encoder"
        elif "vcodec" in self.parameters:
            self.api_shape = "vcodec"
        else:
            raise LeRobotCompatibilityError("H264 설정을 전달할 수 없는 LeRobot API입니다. 호환 어댑터 갱신이 필요합니다.")
        if "video_backend" not in self.parameters:
            raise LeRobotCompatibilityError("PyAV backend를 명시할 수 없는 LeRobot API입니다.")

    def create_writer(self, *, fps, features, root, robot_type=None, repo_id="local/edited_dataset"):
        arguments = dict(repo_id=repo_id, fps=fps, features=features, root=root, video_backend="pyav")
        if "robot_type" in self.parameters:
            arguments["robot_type"] = robot_type
        self._runtime_arguments(arguments, self.parameters, features)
        if self.api_shape == "rgb_encoder":
            arguments["rgb_encoder"] = self.encoder_class(vcodec="h264", crf=23, preset="veryfast")
        else:
            arguments["vcodec"] = "libx264"
        writer = self.dataset_class.create(**arguments)
        return self._lossless(writer, arguments, features)

    def _runtime_arguments(self, arguments, parameters, features):
        if self.runtime is None:
            from ..export_runtime import ExportRuntime
            self.runtime = ExportRuntime.choose(features)
        if 'encoder_threads' in parameters:
            arguments['encoder_threads'] = self.runtime.encoder_threads
        if 'streaming_encoding' in parameters:
            arguments['streaming_encoding'] = True
        if 'encoder_queue_maxsize' in parameters:
            arguments['encoder_queue_maxsize'] = self.runtime.encoder_queue_frames

    def _lossless(self, writer, arguments, features):
        if arguments.get('streaming_encoding') and any(ft['dtype'] == 'video' for ft in features.values()):
            from .streaming import LosslessStreamingEncoder
            internal = getattr(writer, 'writer', None)
            encoder = getattr(internal, '_streaming_encoder', None)
            if encoder is None:
                raise LeRobotCompatibilityError('공식 streaming writer 구조가 바뀌었습니다. 어댑터 갱신이 필요합니다.')
            internal._streaming_encoder = LosslessStreamingEncoder(encoder, self.heartbeat)
        return writer

    @staticmethod
    def video_counts(writer):
        encoder = getattr(getattr(writer, 'writer', None), '_streaming_encoder', None)
        return dict(getattr(encoder, 'last_counts', {}))

    @staticmethod
    def save_episode(writer):
        parameters = inspect.signature(writer.save_episode).parameters
        if "parallel_encoding" in parameters:
            writer.save_episode(parallel_encoding=False)
        else:
            writer.save_episode()

    def resume_writer(self, root, repo_id):
        resume = getattr(self.dataset_class, "resume", None)
        if not callable(resume):
            raise LeRobotCompatibilityError("이 LeRobot 버전은 resume API가 없습니다. 기본 lock 환경을 사용하세요.")
        parameters = inspect.signature(resume).parameters
        arguments = dict(repo_id=repo_id, root=root, video_backend="pyav")
        info = json.loads((Path(root) / "meta/info.json").read_text())
        codecs = {feature.get("info", {}).get("video.codec") for feature in info["features"].values()
                  if feature["dtype"] == "video"}
        if not codecs:
            codec = "h264"
        elif len(codecs) == 1 and next(iter(codecs)) in {"h264", "av1", "hevc"}:
            codec = {"h264": "h264", "av1": "libsvtav1", "hevc": "hevc"}[next(iter(codecs))]
        else:
            raise LeRobotCompatibilityError("기존 영상 codec이 누락되었거나 혼합/미지원 형식입니다. 새 dataset으로 변환 후 추가하세요.")
        if "rgb_encoder" in parameters and self.encoder_class is not None:
            arguments["rgb_encoder"] = (self.encoder_class(vcodec=codec, crf=23, preset="veryfast")
                                         if codec == "h264" else self.encoder_class(vcodec=codec, crf=30))
        elif "vcodec" in parameters:
            arguments["vcodec"] = "libx264" if codec == "h264" else codec
        else:
            raise LeRobotCompatibilityError("resume API의 codec 설정이 바뀌었습니다. 어댑터 갱신이 필요합니다.")
        self._runtime_arguments(arguments, parameters, info['features'])
        writer = resume(**arguments)
        return self._lossless(writer, arguments, info['features'])

    def load_dataset(self, root, repo_id="local/edited_dataset"):
        return self.dataset_class(repo_id, root=root, video_backend="pyav")

    def describe(self):
        revision = None
        try:
            direct = distribution("lerobot").read_text("direct_url.json")
            if direct:
                metadata = json.loads(direct)
                revision = metadata.get("vcs_info", {}).get("commit_id")
                if revision is None:
                    match = re.search(r"/archive/([0-9a-f]{40})\.tar\.gz$", metadata.get("url", ""))
                    revision = match.group(1) if match else None
        except (PackageNotFoundError, ValueError):
            pass
        return {"adapter": self.api_shape, "codebase_version": self.codebase_version,
                "lerobot": package_version("lerobot"), "lerobot_commit": revision,
                "torch": package_version("torch"), "torchcodec": package_version("torchcodec")}
