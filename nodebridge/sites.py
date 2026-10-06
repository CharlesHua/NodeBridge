"""Site profiles and optional portable, master-password-protected storage."""

from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from PySide6.QtCore import QStandardPaths


class LockedSiteStore(ValueError):
    """A master password is required before encrypted sites can be loaded."""


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
    MODES = ("plain", "master", "none")
    _CHECK = b"NodeBridge site passwords v2"

    def __init__(self, path: Path | None = None):
        if path is None:
            if getattr(sys, "frozen", False):
                path = Path(sys.executable).resolve().parent / "data" / "sites.json"
            else:
                config_dir = QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.GenericConfigLocation
                )
                path = Path(config_dir) / "NodeBridge" / "sites.json"
        self.path = path
        self.mode = "plain"
        self._cipher: Fernet | None = None
        self._salt: bytes | None = None

    def _read(self) -> dict:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") not in (1, 2) or not isinstance(data.get("sites"), list):
            raise ValueError("Unsupported site file format")
        if data["version"] == 2 and data.get("password_mode") not in self.MODES:
            raise ValueError("Unsupported password storage mode")
        return data

    @staticmethod
    def _derive(password: str, salt: bytes) -> Fernet:
        key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))
        return Fernet(base64.urlsafe_b64encode(key))

    def unlock(self, master_password: str) -> None:
        data = self._read()
        if data.get("password_mode") != "master":
            raise ValueError("站点资料未启用主密码。")
        try:
            salt = base64.b64decode(data["salt"], validate=True)
            cipher = self._derive(master_password, salt)
            if cipher.decrypt(data["check"].encode("ascii")) != self._CHECK:
                raise ValueError("主密码验证失败。")
        except (InvalidToken, KeyError, ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("主密码错误或站点资料已损坏。") from exc
        self._cipher, self._salt = cipher, salt

    def load(self) -> list[Site]:
        if not self.path.exists():
            self.mode = "plain"
            return []
        data = self._read()
        self.mode = data.get("password_mode", "plain")
        if self.mode == "master" and self._cipher is None:
            raise LockedSiteStore("请输入主密码以打开站点资料。")
        result = []
        for entry in data["sites"]:
            password = str(entry.get("password", ""))
            if self.mode == "master" and password:
                try:
                    password = self._cipher.decrypt(password.encode("ascii")).decode("utf-8")
                except (InvalidToken, UnicodeError, ValueError) as exc:
                    raise ValueError("站点密码无法解密；站点资料可能已损坏。") from exc
            elif self.mode == "none":
                password = ""
            result.append(Site(
                id=str(entry["id"]), name=str(entry["name"]), host=str(entry["host"]),
                port=int(entry["port"]), username=str(entry["username"]),
                key_file=str(entry.get("key_file", "")), password=password,
            ))
        return result

    def reset_encrypted_passwords(self) -> None:
        """Discard inaccessible passwords while retaining nonsecret site metadata."""
        data = self._read()
        if data.get("password_mode") != "master":
            raise ValueError("站点资料未启用主密码。")
        sites = [Site(
            id=str(entry["id"]), name=str(entry["name"]), host=str(entry["host"]),
            port=int(entry["port"]), username=str(entry["username"]),
            key_file=str(entry.get("key_file", "")),
        ) for entry in data["sites"]]
        self.save(sites, mode="none")

    def save(self, sites: list[Site], mode: str | None = None, master_password: str | None = None) -> None:
        mode = mode or self.mode
        if mode not in self.MODES:
            raise ValueError("不支持的密码保存模式。")
        cipher = None
        salt = None
        if mode == "master":
            if master_password is not None:
                if not master_password:
                    raise ValueError("主密码不能为空。")
                salt = os.urandom(16)
                cipher = self._derive(master_password, salt)
            elif self.mode == "master" and self._cipher is not None:
                cipher, salt = self._cipher, self._salt
            else:
                raise LockedSiteStore("请先设置主密码。")
        entries = []
        for site in sites:
            entry = asdict(site)
            if mode == "master" and site.password:
                entry["password"] = cipher.encrypt(site.password.encode("utf-8")).decode("ascii")
            elif mode == "none":
                entry.pop("password")
            entries.append(entry)
        payload: dict = {"version": 2, "password_mode": mode, "sites": entries}
        if mode == "master":
            payload["salt"] = base64.b64encode(salt).decode("ascii")
            payload["check"] = cipher.encrypt(self._CHECK).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.mode, self._cipher, self._salt = mode, cipher, salt
