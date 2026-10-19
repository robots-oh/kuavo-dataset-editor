"""Isolated, architecture-aware installer. Bootstrap runs on Python >=3.8."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


UV_VERSION = "0.13.1"


def options(arguments, *, machine=None, system=None, libc=None, environ=None):
    machine = platform.machine() if machine is None else machine
    system = platform.system() if system is None else system
    libc = platform.libc_ver() if libc is None else libc
    environ = os.environ if environ is None else environ
    if system != "Linux" or machine not in ("x86_64", "aarch64"):
        raise ValueError("Supported: Linux x86_64 or aarch64 (ARM64). No system packages were changed.")
    if libc[0] != "glibc" or tuple(int(n) for n in libc[1].split(".")[:2]) < (2, 28):
        raise ValueError("This wheel environment requires glibc >=2.28; do not upgrade the robot OS for it.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimal", action="store_true", help="Import/edit only; explicit --with-* flags add features")
    parser.add_argument("--with-export", action="store_true")
    parser.add_argument("--with-desktop", action="store_true")
    parser.add_argument("--headless", action="store_true", help="Browser/server mode without Qt")
    parser.add_argument("--with-test", action="store_true")
    parser.add_argument("--with-docs", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="Keep existing environments and install into a new directory")
    parser.add_argument("--plan", action="store_true", help="Print selection without installing anything")
    args = parser.parse_args(arguments)
    desktop = args.with_desktop or (not args.minimal and not args.headless and machine == "x86_64"
                                   and bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY")))
    if args.headless and args.with_desktop:
        raise ValueError("Choose either --headless or --with-desktop.")
    if desktop and tuple(int(n) for n in libc[1].split(".")[:2]) < (2, 34):
        raise ValueError("The locked Qt desktop requires glibc >=2.34. Use --headless on this PC.")
    extras = []
    if not args.minimal or args.with_export:
        extras.append("export")
    if desktop:
        extras.append("desktop")
    if args.with_test:
        extras.append("test")
    if args.with_docs:
        extras.append("docs")
    return args, {"system": system, "machine": machine, "libc": list(libc), "extras": extras,
                  "python": "3.12", "torch_backend": "cpu"}


def clean_environment():
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "LEROBOT_SOURCE", "VIRTUAL_ENV", "CONDA_PREFIX",
                 "LD_LIBRARY_PATH", "LD_PRELOAD", "UV_PROJECT_ENVIRONMENT"):
        env.pop(name, None)
    env.update(OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2",
               UV_CONCURRENT_DOWNLOADS="2", UV_CONCURRENT_BUILDS="1", UV_CONCURRENT_INSTALLS="2")
    return env


def target_environment(root, selection, override=None):
    if override:
        target = Path(override).expanduser().absolute()
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            raise ValueError("EDITOR_VENV_DIR must be inside this editor checkout.")
        if target.is_symlink() or target.exists():
            raise ValueError("EDITOR_VENV_DIR must be a new editor-only directory, not an existing/shared environment.")
        return target
    signature = hashlib.sha256((root / "uv.lock").read_bytes() + json.dumps(selection, sort_keys=True).encode()).hexdigest()[:12]
    return root / (".venv-" + selection["machine"] + "-" + signature)


def run(root, arguments):
    args, selection = options(arguments)
    if args.plan:
        print(json.dumps(selection, indent=2))
        return
    if not (root / "uv.lock").is_file():
        raise ValueError("uv.lock missing; use a full checkout.")
    env = clean_environment()
    bootstrap = root / ".bootstrap"
    if bootstrap.is_symlink():
        raise ValueError("Refusing a shared/symlinked .bootstrap; the existing environment was not changed.")
    bootstrap.mkdir(exist_ok=True)
    import fcntl
    with (bootstrap / "setup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another editor setup is running in this checkout.")
        uv = bootstrap / "bin/uv"
        installed = (subprocess.run([str(uv), "--version"], capture_output=True, text=True, env=env).stdout.strip()
                     if uv.is_file() else "")
        if installed != "uv " + UV_VERSION:
            python = bootstrap / "bin/python"
            if not python.is_file():
                subprocess.run([sys.executable, "-I", "-m", "venv", str(bootstrap)], check=True, env=env)
            subprocess.run([str(python), "-I", "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
                            "uv==" + UV_VERSION], check=True, env=env)
        target = target_environment(root, selection, os.environ.get("EDITOR_VENV_DIR"))
        if args.fresh:
            import uuid
            target = target.with_name(target.name + "-" + uuid.uuid4().hex[:8])
        if target.is_symlink():
            raise ValueError("Refusing a symlinked target environment.")
        print("Platform:", selection, "\nEnvironment:", target, flush=True)
        report = target / "editor-environment.json"
        if not report.is_file():
            command = [str(uv), "sync", "--project", str(root), "--python", "3.12", "--locked",
                       "--no-default-groups"]
            for extra in selection["extras"]:
                command.extend(["--extra", extra])
            subprocess.run(command, check=True, env={**env, "UV_PROJECT_ENVIRONMENT": str(target)})
        doctor = [str(target / "bin/python"), "-I", "-m", "kuavo_dataset_editor", "doctor"]
        if "export" in selection["extras"]:
            doctor.append("--export")
        if "desktop" in selection["extras"]:
            doctor.append("--desktop")
        doctor.extend(["--output", str(target / "editor-diagnostics.json")])
        subprocess.run(doctor, check=True, env=env)
        # Switch only after successful checks; old .venv and active servers stay intact.
        diagnostics = json.loads((target / "editor-diagnostics.json").read_text())
        report.write_text(json.dumps({"selection": selection, "diagnostics": diagnostics}, indent=2) + "\n")
        pending = root / ".editor-python.tmp"
        pending.write_text(str(target.relative_to(root) / "bin/python") + "\n")
        pending.replace(root / ".editor-python")
        print("Ready. Existing annotations/workspace were not modified.\nRun: bash run.sh serve ...", flush=True)


if __name__ == "__main__":
    try:
        run(Path(__file__).resolve().parents[1], sys.argv[1:])
    except (ValueError, subprocess.CalledProcessError) as error:
        print("Setup failed:", error, "\nNo runtime switch was made. No sudo/apt commands were run.", file=sys.stderr)
        raise SystemExit(1)
