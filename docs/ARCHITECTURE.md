# Package boundaries

This repository contains the offline dataset editor only. It does not contain or
start the robot's VR collection, ROS controllers, Isaac Sim, or training jobs.

```text
ROS1 bag ── import/read-only ──┐
                              ├─ source/cache ─ project clips/tasks ─ export plan
existing LeRobot dataset ─────┘                                  │
                                                               ▼
                                                   new LeRobot v3 dataset
```

`desktop.py` hosts the same static UI as `app.py` in Qt/WebEngine, starts a private
loopback backend on an OS-assigned port, and provides native path/download dialogs.
The browser/server mode is still supported. Neither mode changes the source data.

| Module | Responsibility |
|---|---|
| `core.py` | Frame intervals, labels, trim/split, gaps, episode/export plan |
| `store.py` | SQLite project revisions |
| `sources.py` | Read-only catalog and LeRobot adapters |
| `bag.py`, `ros1_reader.py` | ROS1 import and manufacturer message compatibility |
| `profiles/` | Robot-specific topic, units, sampling, feature mappings |
| `exporter.py` | Official LeRobot writer, new output, provenance, small reload |
| `integrations/lerobot.py` | Upstream writer/loader capability adapter and version provenance |
| `doctor.py` | Installed dependency checks and optional disposable export smoke |
| `jobs.py` | Import/export workers and durable job logs/status |
| `app.py`, `static/` | Shared editing UI and local API |
| `desktop.py` | Native window and dialogs, not a second editing engine |

## Data ownership

The code repository is independent of the runtime workspace and original data.
Use `--workspace` to open an existing workspace without moving it. Use
`--allow-root` to grant external data directories explicitly. Desktop file-dialog
selections can also grant access, remembered in `desktop-roots.json` in the workspace.

Do not put bags, videos, caches, model weights, project databases, datasets, exports,
credentials, or a virtual environment in Git. A source-only bundle is not a data
backup. The current project format contains absolute source paths: when moving
projects between PCs, preserve those paths or re-register the sources.

## Semantics

- Intervals are half-open frame indices `[start, end)`.
- Unlabeled frames are excluded; a task is not implicitly propagated into gaps.
- Adjacent labeled clips can share an episode, with task changes per frame.
- A temporal gap or an original episode boundary always starts a new episode.
- Optional feature gaps use validity masks, not fabricated measurements.
- Required numeric gaps must be excluded or resolved through the input profile.
- Tag/TF/session events remain sparse provenance annotations.
- Train/validation/test assignment is by original recording, not arbitrary clips.
- A policy-specific combined arm/gripper/base vector is a separate training adapter.
- Depth editing, mixed-FPS auto-resampling, Hub upload, and training are not included.
