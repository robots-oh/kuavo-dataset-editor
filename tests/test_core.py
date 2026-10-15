from copy import deepcopy
import pytest
from kuavo_dataset_editor.core import EditError, compile_selection, frame_at, label, new_project, split_clip, subtract
from kuavo_dataset_editor.store import Store


def project():
    p = new_project("test")
    p["sources"] = [{"source_id": "a", "fps": 30, "fingerprint": {"value": "source-a"},
                     "episodes": [{"episode_id": 0, "num_frames": 1200}]}]
    return p


def test_time_and_continuity():
    p = project()
    for start, end, task in [(60, 300, "approach"), (300, 480, "grasp"), (720, 1050, "carry")]:
        p = label(p, "a", 0, start, end, task)
    plan = compile_selection(p)
    assert (plan["kept_frames"], plan["dropped_frames"], plan["num_episodes"]) == (750, 450, 2)
    assert [c["task"] for c in plan["runs"][0]["clips"]] == ["approach", "grasp"]
    assert frame_at("0.1000001", 30) == 4


def test_exclude_same_task_gap_and_split():
    p = label(project(), "a", 0, 0, 1200, "same")
    p["clips"] = subtract(p["clips"], "a", 0, 300, 400)
    assert compile_selection(p)["num_episodes"] == 2
    p = split_clip(p, p["clips"][0]["id"], 100)
    assert compile_selection(p)["num_episodes"] == 2
    p["clips"][-1]["new_episode"] = True
    assert compile_selection(p)["num_episodes"] == 3


def test_overlap_requires_explicit_replace_and_no_mutation():
    original = label(project(), "a", 0, 0, 300, "first")
    before = deepcopy(original)
    with pytest.raises(EditError):
        label(original, "a", 0, 100, 200, "second")
    result = label(original, "a", 0, 100, 200, "second", replace=True)
    assert original == before
    assert compile_selection(result)["kept_frames"] == 300
    assert len(result["clips"]) == 3
    with pytest.raises(EditError):
        label(original, "a", 0, 200, 301, "   ")


def test_revision_and_split_leakage(tmp_path):
    store = Store(tmp_path)
    p = store.save(project(), -1)
    stale = deepcopy(p)
    p["name"] = "changed"
    saved = store.save(p, 0)
    assert saved["revision"] == 1 and store.get(p["project_id"], 0)["name"] == "test"
    with pytest.raises(EditError):
        store.save(stale, 0)
    alias = {**p["sources"][0], "source_id": "alias"}
    p["sources"].append(alias)
    p["source_splits"] = {"a": "train", "alias": "validation"}
    with pytest.raises(EditError):
        compile_selection(p)
