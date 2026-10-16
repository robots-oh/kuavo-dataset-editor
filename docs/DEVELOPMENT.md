# Development

Use a dedicated Python 3.12 environment on Linux x86_64. Do not install into a ROS/Isaac environment.

```bash
bash setup.sh --with-test --with-docs
PYTHONPATH=src .venv/bin/python -m pytest -q tests
node tests/test_ranges.mjs
node tests/test_tasks.mjs
.venv/bin/python tools/build_docs.py
```

The default suite skips the opt-in Qt and official LeRobot export checks. To run
those small checks separately:

```bash
EDITOR_TEST_EXPORT=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_export.py
# A GUI session and local sockets are required for this native window test.
EDITOR_TEST_DESKTOP=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_desktop.py
```

GUI/worker smoke tests should use disposable workspaces. Never use an actively
annotated real project for mutation tests. Run the server and workers in the same
process namespace: a separate container/sandbox may make a live worker's PID look
dead to another server polling the shared workspace.

`LEROBOT_SOURCE=/absolute/path/to/lerobot/src` selects an existing official source
checkout. For local development, an ignored `.lerobot-source` symlink is supported.
Neither that link nor `.venv` belongs in Git or a portable source bundle.

Hub tests replace the entire HfApi boundary with a fake: no account, data upload,
remote repository creation, or token is needed. API tests use a local TestClient;
in a restricted execution sandbox allow local threading/sockets or run outside
that sandbox. Never turn a mock test into a real robot-dataset upload.

README/docs generate the [GitHub Pages site](GITHUB_PAGES.md). `docs` is an
optional dependency; site generation is not needed to run the editor.

## Distribution

`pyproject.toml` plus the committed `uv.lock` define reproducible source-checkout
installation, including the pinned official LeRobot archive and CPU torch packages.
Use setup.sh (not an unconstrained pip install) for the tested full environment.
`pyproject.toml` also defines the installable Python package and `kuavo-dataset-editor`
console entry point. Static assets and the default profile are package resources.
`desktop.sh` is a convenient source-checkout entry point, not a frozen executable.

```bash
.bootstrap/bin/uv build --wheel
bash package.sh
```

`package.sh` creates a source archive excluding the Git database, environment,
workspace, build products, and developer source symlinks. Do not add unrelated
private files to the checkout: review the archive before sharing it. Source/wheel
distribution does not bundle Qt or LeRobot dependencies; install with setup.sh.

There is no new licensing grant for this repository yet. Upstream libraries retain
their licenses; LeRobot and ROS libraries are dependencies, not vendored source trees.
