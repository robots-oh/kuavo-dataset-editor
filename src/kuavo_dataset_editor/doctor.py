"""Opt-in dependency diagnostics; never opens real bags or robot connections."""
import importlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

from .integrations.lerobot import LeRobotBackend, package_version


def diagnose(*, export=False, desktop=False, smoke_export=False):
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "executable": sys.executable, "checks": {}, "errors": []}
    modules = ["fastapi", "uvicorn", "numpy", "PIL", "pyarrow", "av", "rosbags"]
    if desktop:
        modules += ["PySide6.QtWidgets", "PySide6.QtWebEngineWidgets"]
    for name in modules:
        try:
            importlib.import_module(name)
            report["checks"][name] = "import-ok"
        except (ImportError, OSError) as exc:
            report["errors"].append(f"{name}: {exc}")
    report["versions"] = {name: package_version(name) for name in
                          ("fastapi", "uvicorn", "numpy", "Pillow", "pyarrow", "av", "rosbags", "PySide6")}
    if export or smoke_export:
        try:
            backend = LeRobotBackend()
            report["lerobot"] = backend.describe()
            import av
            av.Codec("libx264", "w")
            report["checks"]["h264"] = "encoder-available"
            if smoke_export:
                from .core import label, new_project
                from .exporter import export_dataset
                from .sources import demo_source
                with tempfile.TemporaryDirectory(prefix="kuavo-editor-doctor-") as directory:
                    root = Path(directory)
                    source = demo_source(root / "source", "sample")
                    project = new_project("Disposable dependency smoke test")
                    project["sources"] = [source]
                    project = label(project, "sample", 0, 2, 8, "approach")
                    project = label(project, "sample", 0, 8, 12, "grasp")
                    project = label(project, "sample", 0, 20, 30, "carry")
                    result = export_dataset(project, root / "output", root / "job", lambda *_: None)
                    report["checks"]["smoke_export"] = {"frames": result["frames"], "episodes": result["episodes"]}
        except Exception as exc:
            report["errors"].append(f"LeRobot export: {type(exc).__name__}: {exc}")
    report["status"] = "ok" if not report["errors"] else "error"
    return report


def run(args):
    if args.smoke_export:
        # Upstream cache constants are evaluated on first import. Configure them
        # before importing LeRobot so this disposable test never needs a home cache.
        with tempfile.TemporaryDirectory(prefix="kuavo-editor-doctor-cache-") as directory:
            names = {"HF_HOME": directory + "/hf", "HF_DATASETS_CACHE": directory + "/datasets"}
            previous = {key: os.environ.get(key) for key in names}
            os.environ.update(names)
            try:
                report = diagnose(export=args.export, desktop=args.desktop, smoke_export=True)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
    else:
        report = diagnose(export=args.export, desktop=args.desktop)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).expanduser().write_text(text + "\n")
    print(text)
    if report["errors"]:
        raise SystemExit(1)
