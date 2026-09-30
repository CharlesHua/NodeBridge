"""Persistent site profiles in the user's configuration directory."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    host: str
    port: int
    username: str
    key_file: str = ""
    password: str = ""

    @classmethod
    def create(cls, name: str = "新站点") -> "Site":
        return cls(str(uuid.uuid4()), name, "", 22, "")


class SiteStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            config_dir = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.GenericConfigLocation
            )
            path = Path(config_dir) / "NodeBridge" / "sites.json"
        self.path = path

    def load(self) -> list[Site]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("sites"), list):
            raise ValueError("Unsupported site file format")
        result = []
        for entry in data["sites"]:
            result.append(Site(
                id=str(entry["id"]),
                name=str(entry["name"]),
                host=str(entry["host"]),
                port=int(entry["port"]),
                username=str(entry["username"]),
                key_file=str(entry.get("key_file", "")),
                password=str(entry.get("password", "")),
            ))
        return result

    def save(self, sites: list[Site]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "sites": [asdict(site) for site in sites]}
        temporary = self.path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
