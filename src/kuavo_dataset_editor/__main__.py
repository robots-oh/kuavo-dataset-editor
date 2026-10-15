import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Offline Kuavo / LeRobot video dataset editor")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Open a browser editor; localhost by default")
    serve.add_argument("--workspace", default="workspace")
    serve.add_argument("--allow-root", action="append", default=[])
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    desktop = sub.add_parser("desktop", help="Open the standalone desktop window with its own local backend")
    desktop.add_argument("--workspace", default="workspace")
    desktop.add_argument("--allow-root", action="append", default=[])
    desktop.add_argument("--write-shortcut", metavar="PATH", help="Write a Linux .desktop launcher instead of opening a window")
    inspect = sub.add_parser("inspect-bag", help="Print topics without publishing anything")
    inspect.add_argument("path")
    child = sub.add_parser("_worker", help=argparse.SUPPRESS)
    child.add_argument("directory")
    export = sub.add_parser("export", help="Export a saved project JSON using the same core as the UI")
    export.add_argument("project")
    export.add_argument("target")
    export.add_argument("--jobs-root", default="workspace/jobs")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    if args.command == "desktop":
        from .desktop import launch, write_shortcut
        if args.write_shortcut:
            write_shortcut(args.write_shortcut, args.workspace, args.allow_root)
        else:
            launch(args.workspace, args.allow_root)
    elif args.command == "serve":
        import uvicorn
        from .app import create_app
        print(f"Dataset editor: http://{args.host}:{args.port}")
        print("Original sources are read-only. This process does not control the robot.")
        uvicorn.run(create_app(args.workspace, args.allow_root), host=args.host, port=args.port, access_log=False)
    elif args.command == "inspect-bag":
        from .bag import inspect_bag
        print(json.dumps(inspect_bag(Path(args.path).expanduser()), indent=2))
    elif args.command == "_worker":
        from .jobs import worker
        worker(args.directory)
    elif args.command == "export":
        from .jobs import JobManager
        manager = JobManager(Path(args.jobs_root).parent)
        project = json.loads(Path(args.project).read_text())
        print(json.dumps(manager.start("export", {"project": project, "target": str(Path(args.target).resolve())}), indent=2))


if __name__ == "__main__":
    main()
