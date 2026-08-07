#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical DFS entrypoint.

The implementation lives in ``dfs_core``.  This entrypoint installs the
project-wide ``special_opearte.operationN = [item, ...]`` contract before any
DFS export API is exposed, so imports and direct CLI execution behave exactly
the same.
"""

from __future__ import annotations

from typing import Any, Dict

import dfs_core as _core
import special_opearte_contract as _special


_core.DFS_RECORD_FIELDS = (
    "package_name",
    "main_page_name",
    "page_description",
    "path_snapshot",
    "special_opearte",
)
_core.SPECIAL_MANUAL_FIELD = _special.SPECIAL_MANUAL_FIELD
_core.normalize_special = _special.normalize_special_opearte
_core.normalize_special_opearte = _special.normalize_special_opearte
_core.format_special_step = _special.format_special_item
_core.build_special_operations = _special.build_special_opearte
_core.build_special_opearte = _special.build_special_opearte


def _format_dfs_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "package_name": str(record.get("package_name") or ""),
        "main_page_name": str(record.get("main_page_name") or ""),
        "page_description": str(record.get("page_description") or ""),
        "path_snapshot": [
            formatted
            for target in record.get("path_snapshot") or []
            if (formatted := _core.format_path_target(target))
        ],
        "special_opearte": _special.normalize_special_opearte(
            record.get("special_opearte")
            or record.get("special")
            or {}
        ),
    }


_core.format_dfs_record = _format_dfs_record

# Export the patched implementation as the public DFS module API.
from dfs_core import *  # noqa: E402,F401,F403


if __name__ == "__main__":
    _core.main()
