"""Compatibility boundary for the official LeRobot dataset writer and reader."""
import importlib
from importlib.metadata import PackageNotFoundError, distribution, version
import inspect
import json
import re


class LeRobotCompatibilityError(RuntimeError):
    pass


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


class LeRobotBackend:
    def __init__(self, dataset_class=None, encoder_class=None, codebase_version=None):
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
                    "LeRobot/영상 라이브러리를 불러올 수 없습니다. bash setup.sh 후 doctor --export를 확인하세요. "
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

    def create_writer(self, *, fps, features, root, robot_type=None):
        arguments = dict(repo_id="local/edited_dataset", fps=fps, features=features, root=root, video_backend="pyav")
        if "robot_type" in self.parameters:
            arguments["robot_type"] = robot_type
        if "encoder_threads" in self.parameters:
            arguments["encoder_threads"] = 2
        if self.api_shape == "rgb_encoder":
            arguments["rgb_encoder"] = self.encoder_class(vcodec="h264", crf=23, preset="veryfast")
        else:
            arguments["vcodec"] = "libx264"
        return self.dataset_class.create(**arguments)

    @staticmethod
    def save_episode(writer):
        parameters = inspect.signature(writer.save_episode).parameters
        if "parallel_encoding" in parameters:
            writer.save_episode(parallel_encoding=False)
        else:
            writer.save_episode()

    def load_dataset(self, root):
        return self.dataset_class("local/edited_dataset", root=root, video_backend="pyav")

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
