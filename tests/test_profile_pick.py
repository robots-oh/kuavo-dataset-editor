"""Bag profile chosen by file name unless the user edited it."""
from kuavo_dataset_editor.profile_pick import default_profile, for_path, load_all


def test_rwh_auto_bags_get_rwh_profile():
    assert for_path('/x/2026-10-26/rwh_auto_20261026_101027.bag')['name'].startswith('Kuavo 5W rwh_auto')
    assert for_path('/x/rwh_auto_1.bag', default_profile())['name'].startswith('Kuavo 5W rwh_auto')


def test_other_bags_and_edited_profiles_unchanged():
    assert for_path('/x/vr_trial_164_20261023.bag') == default_profile()
    edited = {**default_profile(), 'fps': 15}
    assert for_path('/x/rwh_auto_1.bag', edited) is edited


def test_all_profiles_listed():
    assert {'kuavo_5w', 'rwh_auto'} <= set(load_all())
