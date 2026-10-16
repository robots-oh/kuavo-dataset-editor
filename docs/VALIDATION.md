# Bounded validation record

As of 2026-10-15 (Asia/Seoul):

## Fresh dependency installation

A source-only temporary copy (no developer venv/source symlinks) was installed using
setup.sh and the committed uv.lock. Python 3.12.15 was downloaded automatically;
88 packages including CPU torch 2.9.1, torchcodec 0.8.1, Qt 6.12, and the official
LeRobot 0.6.2 commit archive installed in a new isolated environment. No existing
LeRobot checkout or developer site-packages was used. Dependency diagnostics passed.
The optional doctor smoke check exported 20 frames into two episodes and reopened
them with the official loader. Ten default Python tests passed; native-window and
separate export pytest tests remained opt-in. This was a clean environment on the
development Linux host, not an assertion that every OS/PC has been tested.

- Core interval tests cover adjacent task changes, unlabeled gaps, overlap handling,
  source preservation, project revisions, and source-level train/test assignment.
- A small synthetic ROS1 bag checks custom message definitions, three RGB cameras,
  action unit conversion, optional marker absence, and optional-feature masks.
- A 20-frame official LeRobot writer/loader round trip checks two output episodes.
- Native-window smoke checks use a disposable empty workspace: Qt frontend load,
  four task candidate buttons, native-selected path entry, and backend shutdown.
- A real bag imported at 30 FPS was tested through the same export worker used by
  the editor, using a separate test project (the user's existing labels were kept).

## Named dataset update smoke checks (2026-10-16)

A disposable synthetic source was exported with a selected repo_id (8 frames),
appended using the official resume API (12 total frames, original rows/tasks kept),
then loaded with its original task clips and edited/replaced (10 frames).
Previous dataset fingerprints matched the saved backups. Source relocation preserved
saved clips/tasks; interrupted writes preserved the target, and an injected final
rename failure restored it. A localhost API check verified automatic task import and
required confirmation. The native Qt check verified all three storage choices.
These mutation checks used temporary data only, not the user's real dataset.

## Hub publication and documentation checks (2026-10-16)

Four mocked Hub tests covered private-by-default creation, explicit confirmation,
allowlisted file publication (no editor sidecars, hidden files or symlinks),
existing snapshot deletion lists, preserving visibility/cards, remote HEAD and
local-manifest changes, shared write locks, and API payload field filtering.
No real Hub authentication, repository creation or upload was executed.
The site builder rendered nine pages from README/docs; internal links and generated
heading anchors were checked. On 2026-10-16, the private GitHub repository was
created and pushed, the minimal suite passed on GitHub Actions, and a manually
triggered Pages build/deployment succeeded. The public documentation URL returned
HTTP 200; no recordings, project databases or environments were published.
The combined bounded suite passed 18 tests (one optional Qt check skipped), including
the official writer new/append/replace roundtrips. Frontend range/task tests passed.
The generated site was also viewed in a localhost browser to check the guide layout.

## Illustrated workflow guide (2026-10-16)

Ten actual browser screenshots were captured in an isolated temporary workspace.
Real head/left-wrist/right-wrist images came from a separate copy of the earlier
8-second smoke dataset, with user approval to show experiment footage publicly.
An example labeling workflow and local export were executed only in this copy;
the active project, original dataset and bag were not edited. Existing-dataset
write modes and Hub fields were shown as configuration examples, without running
those mutations or performing a Hub login/upload. Guide instructions are examples,
not verified semantic labels for the observed robot motion.
Numbered callouts are rendered separately from the original JPEG captures.

## Earlier real-data smoke export

Provisional instructions were assigned for pipeline testing, not semantic accuracy:

| Source time | Instruction |
|---|---|
| 1–3 s | Align the robot with the rack. |
| 3–5 s | Pick up the box from the rack. |
| 6–8 s | Carry the box to the front of the conveyor. |
| 8–10 s | Put down the box. |

Result: 240 frames (8 seconds), 2 episodes, 4 tasks, 3 RGB cameras, approximately
26 MiB. The 5–6 second gap was excluded and split the output into separate episodes.
The exporter reopened first/last frames with the official LeRobot loader. Every
exported row's task index was resolved against the task metadata and checked against
its intended instruction. State/action/validity values were compared at eight clip
boundary samples, and all three output videos decoded at first/last frames.
The original bag's size/mtime and original project's serialized labels were unchanged.

This does not assert that a full recording has been exported, that provisional task
labels describe the observed motion, or that a downstream policy has been trained.
The real source has missing required arm commands in the first two frames; those
frames were not selected. Full-recording exports must handle their own invalid frames.

Real recordings, project databases, export outputs, and machine-specific file paths
are deliberately not published with this record.
