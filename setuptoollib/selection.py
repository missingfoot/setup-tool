"""Saved selections: which apps are ticked, stored by app id so a file keeps
working as apps.yaml changes. Loading never ticks anything the file didn't
explicitly select - apps added since it was saved stay unticked, and ids that
no longer exist are skipped - and reports both so nothing changes silently."""
from __future__ import annotations

from dataclasses import dataclass

import yaml

FORMAT_VERSION = 1

HEADER = (
    "# Setup Tool selection - load it with the Load button.\n"
    "# Apps are listed by their id in apps.yaml.\n"
)


@dataclass(frozen=True)
class LoadPlan:
    check: set[str]  # ids to tick; everything else gets unticked
    removed: list[str]  # in the file, but no longer in apps.yaml
    new: list[str]  # in apps.yaml, but not in the file (left unticked)
    unavailable: list[str]  # selected in the file, but can't be installed here


def dump_selection(selected: list[str], unselected: list[str]) -> str:
    data = {"version": FORMAT_VERSION, "selected": selected, "unselected": unselected}
    return HEADER + yaml.safe_dump(data, sort_keys=False)


def parse_selection(text: str) -> tuple[list[str], list[str]]:
    """(selected, unselected) ids. Raises ValueError if it isn't a selection file."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ValueError(f"Not valid YAML: {err}") from err
    if not isinstance(data, dict) or "selected" not in data:
        raise ValueError("Not a Setup Tool selection file.")
    version = data.get("version", FORMAT_VERSION)
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ValueError(f"Saved by a newer Setup Tool (format {version}); update this one first.")

    def ids(key: str) -> list[str]:
        value = data.get(key) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"'{key}' must be a list of app ids.")
        return value

    return ids("selected"), ids("unselected")


def plan_load(
    selected: list[str], unselected: list[str], known: list[str], checkable: set[str]
) -> LoadPlan:
    """`known` is every app id in the current list, in order; `checkable` the
    ones that can be ticked on this machine."""
    known_set = set(known)
    mentioned = set(selected) | set(unselected)
    return LoadPlan(
        check={i for i in selected if i in checkable},
        removed=[i for i in dict.fromkeys(selected + unselected) if i not in known_set],
        new=[i for i in known if i not in mentioned],
        unavailable=[i for i in dict.fromkeys(selected) if i in known_set and i not in checkable],
    )
