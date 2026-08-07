from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel

from settings_ui_manual_recorder import (
    load_json,
    load_navigation_graph,
    navigation_graph_path,
    now_iso,
    save_current_path_session,
    save_json,
    save_navigation_graph,
)

DEFAULT_SETTINGS_PROFILE_ID = "default"
SETTINGS_PROFILE_REGISTRY = "settings_profiles.json"


class SettingsProfileManager:
    """Maintain request-scoped settings-version/device profiles."""

    def __init__(self, base_work_dir: Path) -> None:
        self.base_work_dir = Path(base_work_dir)
        self.navigation_dir = self.base_work_dir / "outputs" / "navigation"
        self.registry_path = self.navigation_dir / SETTINGS_PROFILE_REGISTRY
        self.profiles_dir = self.base_work_dir / "config_profiles"

    def _default_profile(self) -> Dict[str, Any]:
        return {
            "profile_id": DEFAULT_SETTINGS_PROFILE_ID,
            "name": "默认配置",
            "settings_version": "",
            "device_model": "",
            "parent_profile_id": "",
            "created_at": "",
            "updated_at": "",
            "is_default": True,
        }

    def _load_profiles(self) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []
        if self.registry_path.exists():
            document = load_json(self.registry_path)
            raw_profiles = document.get("profiles", [])
            if isinstance(raw_profiles, list):
                profiles = [
                    dict(item)
                    for item in raw_profiles
                    if isinstance(item, dict) and str(item.get("profile_id") or "")
                ]
        by_id = {str(item["profile_id"]): item for item in profiles}
        default = by_id.get(DEFAULT_SETTINGS_PROFILE_ID, self._default_profile())
        default["profile_id"] = DEFAULT_SETTINGS_PROFILE_ID
        default["is_default"] = True
        ordered = [default]
        ordered.extend(
            item for item in profiles
            if str(item.get("profile_id")) != DEFAULT_SETTINGS_PROFILE_ID
        )
        return ordered

    def _save_profiles(self, profiles: List[Dict[str, Any]]) -> None:
        save_json({
            "schema_version": 1,
            "updated_at": now_iso(),
            "profiles": profiles,
        }, self.registry_path, "设置版本/机型配置索引")

    def profile_work_dir(self, profile_id: str) -> Path:
        profile_id = str(profile_id or DEFAULT_SETTINGS_PROFILE_ID)
        if profile_id == DEFAULT_SETTINGS_PROFILE_ID:
            return self.base_work_dir
        if not re.fullmatch(r"profile_[0-9a-f]{12}", profile_id):
            raise ValueError(f"配置 ID 非法：{profile_id}")
        return self.profiles_dir / profile_id

    def get_profile(self, profile_id: str) -> Dict[str, Any]:
        for profile in self._load_profiles():
            if str(profile.get("profile_id")) == profile_id:
                return profile
        raise ValueError(f"配置不存在：{profile_id}")

    def list_profiles(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for profile in self._load_profiles():
            profile_id = str(profile["profile_id"])
            work_dir = self.profile_work_dir(profile_id)
            graph = load_navigation_graph(work_dir)
            result.append({
                **profile,
                "work_dir": str(work_dir),
                "graph_path": str(navigation_graph_path(work_dir)),
                "page_count": len(graph.get("states", {}) or {}),
                "transition_count": len(graph.get("transitions", []) or []),
            })
        return result

    def create(self, *, name: str, settings_version: str, device_model: str, parent_profile_id: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        settings_version = str(settings_version or "").strip()
        device_model = str(device_model or "").strip()
        parent_profile_id = str(parent_profile_id or DEFAULT_SETTINGS_PROFILE_ID).strip()
        if not settings_version:
            raise ValueError("设置版本不能为空")
        if not device_model:
            raise ValueError("机型不能为空")
        profiles = self._load_profiles()
        if not any(str(item.get("profile_id")) == parent_profile_id for item in profiles):
            raise ValueError(f"继承来源配置不存在：{parent_profile_id}")
        duplicate = next((
            item for item in profiles
            if str(item.get("settings_version") or "").casefold() == settings_version.casefold()
            and str(item.get("device_model") or "").casefold() == device_model.casefold()
        ), None)
        if duplicate:
            raise ValueError(f"该设置版本和机型已经存在：{duplicate.get('name') or duplicate['profile_id']}")

        profile_id = f"profile_{uuid.uuid4().hex[:12]}"
        created_at = now_iso()
        profile = {
            "profile_id": profile_id,
            "name": name or f"{settings_version} · {device_model}",
            "settings_version": settings_version,
            "device_model": device_model,
            "parent_profile_id": parent_profile_id,
            "created_at": created_at,
            "updated_at": created_at,
            "is_default": False,
        }
        source_work_dir = self.profile_work_dir(parent_profile_id)
        target_work_dir = self.profile_work_dir(profile_id)
        source_graph_path = navigation_graph_path(source_work_dir)
        if not source_graph_path.exists():
            raise ValueError(f"继承来源还没有配置文件：{source_graph_path}")
        source_graph = load_navigation_graph(source_work_dir)
        inherited_graph = json.loads(json.dumps(source_graph, ensure_ascii=False))
        inherited_graph["settings_profile"] = {
            key: profile[key]
            for key in ("profile_id", "name", "settings_version", "device_model", "parent_profile_id")
        }
        save_navigation_graph(inherited_graph, target_work_dir)

        source_paths = source_work_dir / "outputs" / "navigation" / "settings_navigation_paths.json"
        target_paths = target_work_dir / "outputs" / "navigation" / "settings_navigation_paths.json"
        if source_paths.exists():
            target_paths.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_paths, target_paths)
        root_page = str(inherited_graph.get("traversal_config", {}).get("root_page") or "Pages_root")
        save_current_path_session(target_work_dir, root_page)
        profiles.append(profile)
        self._save_profiles(profiles)
        return profile


class CreateSettingsProfileRequest(BaseModel):
    name: str = ""
    settings_version: str
    device_model: str
    parent_profile_id: str = DEFAULT_SETTINGS_PROFILE_ID
