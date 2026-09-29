import pytest

from setuptoollib.selection import dump_selection, parse_selection, plan_load


def test_round_trip_keeps_order():
    text = dump_selection(["b", "a"], ["c"])
    assert text.startswith("# Setup Tool selection")
    assert parse_selection(text) == (["b", "a"], ["c"])


def test_plan_handles_added_removed_and_unavailable_apps():
    plan = plan_load(
        selected=["zed", "gone", "mullvad"],
        unselected=["vlc", "also-gone"],
        known=["zed", "vlc", "mullvad", "brand-new"],
        checkable={"zed", "vlc", "brand-new"},
    )
    assert plan.check == {"zed"}  # never ticks anything the file didn't select
    assert plan.removed == ["gone", "also-gone"]
    assert plan.new == ["brand-new"]
    assert plan.unavailable == ["mullvad"]


def test_missing_lists_are_empty():
    assert parse_selection("selected:\n") == ([], [])


@pytest.mark.parametrize("text", [
    "- zed\n",  # not a mapping
    "foo: 1\n",  # no 'selected'
    "selected: zed\n",  # not a list
    "selected: [1, 2]\n",  # not strings
    "version: 99\nselected: []\n",  # newer format
    "selected: [\n",  # broken YAML
])
def test_rejects_bad_files(text):
    with pytest.raises(ValueError):
        parse_selection(text)
