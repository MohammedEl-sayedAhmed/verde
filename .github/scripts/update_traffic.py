#!/usr/bin/env python3
"""Merge GitHub traffic API snapshots into a cumulative store and emit shields badges.

The traffic API only returns the last 14 days, so we keep a per-day store keyed by
date and overwrite each returned day with its latest value — the current day's count
is still climbing, while past days are final. Totals are summed across all stored days.

Usage:
    update_traffic.py --store <dir> --clones <clones_api.json> --views <views_api.json>
"""

from __future__ import annotations

import argparse
import json
import pathlib


def _load(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _merge(api_path: pathlib.Path, store_path: pathlib.Path, key: str) -> int:
    """Fold a 14-day API snapshot into the cumulative per-day store; return the total."""
    api = json.loads(api_path.read_text())
    store = _load(store_path)
    for day in api.get(key, []):
        store[day["timestamp"][:10]] = {"count": day["count"], "uniques": day["uniques"]}
    store = dict(sorted(store.items()))
    store_path.write_text(json.dumps(store, indent=2) + "\n")
    return sum(entry["count"] for entry in store.values())


def _badge(path: pathlib.Path, label: str, message: int, color: str) -> None:
    """Write a shields.io endpoint badge JSON."""
    path.write_text(
        json.dumps(
            {"schemaVersion": 1, "label": label, "message": str(message), "color": color}
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, type=pathlib.Path)
    parser.add_argument("--clones", required=True, type=pathlib.Path)
    parser.add_argument("--views", required=True, type=pathlib.Path)
    args = parser.parse_args()

    args.store.mkdir(parents=True, exist_ok=True)
    clones = _merge(args.clones, args.store / "clones.json", "clones")
    views = _merge(args.views, args.store / "views.json", "views")
    _badge(args.store / "clones-badge.json", "clones", clones, "blue")
    _badge(args.store / "views-badge.json", "views", views, "blueviolet")
    print(f"cumulative clones={clones} views={views}")


if __name__ == "__main__":
    main()
