"""Per-site sources used to discover candidate SSH nodes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoverySettings:
    hosts_enabled: bool = True
    hosts_path: str = "/etc/hosts"
    ssh_enabled: bool = True
    ssh_user_path: str = "~/.ssh/config"
    ssh_system_path: str = "/etc/ssh/ssh_config"

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        return cls(**{
            name: (data.get(name, getattr(defaults, name)) is True if name.endswith("_enabled")
                   else str(data.get(name, getattr(defaults, name))))
            for name in ("hosts_enabled", "hosts_path", "ssh_enabled", "ssh_user_path", "ssh_system_path")
        })
