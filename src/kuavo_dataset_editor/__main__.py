import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Offline Kuavo / LeRobot video dataset editor")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Check installed dependencies; optional disposable 20-frame export")
    doctor.add_argument("--export", action="store_true")
    doctor.add_argument("--desktop", action="store_true")
    doctor.add_argument("--smoke-export", action="store_true")
    doctor.add_argument("--output")
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
    export.add_argument("--mode", choices=["new", "append", "replace"], default="new")
    export.add_argument("--repo-name")
    export.add_argument("--confirm-existing", action="store_true")
    export.add_argument('--schema',choices=['legacy','kuavo_wholebody_v1','kuavo_state24_v1'],default='legacy')
    export.add_argument('--annotation-track',choices=['step','goal'],default='step')
    export.add_argument('--rack-calibration',help='Optional JSON with rack_tags; recorded calibration is preferred')
    resume = sub.add_parser('resume-export', help='Resume an interrupted Export from its last complete episode')
    resume.add_argument('job_id')
    resume.add_argument('--workspace', default='workspace')
    hub = sub.add_parser("hub", help="Preview or explicitly publish a complete local dataset to Hugging Face")
    hub.add_argument("path")
    hub.add_argument("repo_id", help="owner/dataset-name")
    hub.add_argument("--public", action="store_true", help="New repos only; default private")
    hub.add_argument("--confirm", action="store_true", help="Publish the displayed plan; otherwise read-only preview")
    hub.add_argument("--sync-existing", action="store_true", help="Confirm complete snapshot replacement, not remote append")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "0" if args.command == "hub" else "1")
    if args.command == "doctor":
        from .doctor import run
        run(args)
    elif args.command == "desktop":
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
        print(json.dumps(manager.start("export", {"project": project, "target": str(Path(args.target).resolve()),
                                                  "options": {"mode": args.mode, "repo_name": args.repo_name,
                                                              'schema':args.schema,
                                                              'annotation_track':args.annotation_track,
                                                              'rack_calibration':json.loads(Path(args.rack_calibration).read_text()) if args.rack_calibration else None,
                                                              "confirm_existing": args.confirm_existing}}), indent=2))
    elif args.command == 'resume-export':
        from .jobs import JobManager
        print(json.dumps(JobManager(args.workspace).resume_export(args.job_id), indent=2, ensure_ascii=False))
    elif args.command == "hub":
        from .hub import prepare_upload, publish_upload
        plan = prepare_upload(args.path, args.repo_id, private=not args.public)
        print(json.dumps(plan, indent=2, ensure_ascii=False), flush=True)
        if args.confirm:
            print(json.dumps(publish_upload(plan, confirmed=True, sync_existing=args.sync_existing), indent=2))


if __name__ == "__main__":
    main()
