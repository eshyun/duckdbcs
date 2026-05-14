"""Configuration management for DuckDB Quack client/server.

Config priority (highest to lowest):
1. CLI arguments (--token, --host, --port, etc.)
2. Environment variables (QUACK_TOKEN, QUACK_HOST, etc.)
3. Persistent config file (~/.config/duckdbcs/config.toml)
4. .quack_secret file (token only)
5. Built-in defaults
"""

import os
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TOKEN_ENVVAR = "QUACK_TOKEN"
DEFAULT_TOKEN_FILE = ".quack_secret"
CONFIG_DIR = Path.home() / ".config" / "duckdbcs"
CONFIG_FILE = CONFIG_DIR / "config.toml"


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    """Configuration for a Quack server."""
    host: str = "0.0.0.0"
    port: int = 9494
    token: Optional[str] = None
    allow_other_hostname: bool = False
    disable_ssl: bool = False
    attach_on_startup: list[dict] = field(default_factory=list)
    # attach_on_startup: [{"path": "/data/db.db", "alias": "mydb"}, ...]

    @classmethod
    def defaults(cls) -> "ServerConfig":
        return cls()

    def merge_env(self) -> "ServerConfig":
        """Override defaults with environment variables."""
        self.host = os.getenv("QUACK_HOST", self.host)
        self.port = int(os.getenv("QUACK_PORT", str(self.port)))
        self.token = os.getenv("QUACK_TOKEN", self.token)
        self.allow_other_hostname = (
            os.getenv("QUACK_ALLOW_EXTERNAL", str(self.allow_other_hostname)).lower() == "true"
        )
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ServerConfig":
        return cls(
            host=d.get("host", "0.0.0.0"),
            port=d.get("port", 9494),
            token=d.get("token"),
            allow_other_hostname=d.get("allow_other_hostname", False),
            disable_ssl=d.get("disable_ssl", False),
            attach_on_startup=d.get("attach_on_startup", []),
        )


@dataclass
class ClientConfig:
    """Configuration for a Quack client connection."""
    host: str = "localhost"
    port: int = 9494
    token: Optional[str] = None
    disable_ssl: bool = False
    attach_alias: str = "remote_server"
    attach_on_startup: list[dict] = field(default_factory=list)
    # attach_on_startup: [{"path": "/data/db.db", "alias": "mydb"}, ...]


    @property
    def uri(self) -> str:
        return f"quack:{self.host}:{self.port}"

    @classmethod
    def defaults(cls) -> "ClientConfig":
        return cls()

    def merge_env(self) -> "ClientConfig":
        """Override defaults with environment variables."""
        self.host = os.getenv("QUACK_REMOTE_HOST", self.host)
        self.port = int(os.getenv("QUACK_REMOTE_PORT", str(self.port)))
        self.token = os.getenv("QUACK_TOKEN", self.token)
        self.attach_alias = os.getenv("QUACK_ATTACH_ALIAS", self.attach_alias)
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ClientConfig":
        return cls(
            host=d.get("host", "localhost"),
            port=d.get("port", 9494),
            token=d.get("token"),
            disable_ssl=d.get("disable_ssl", False),
            attach_alias=d.get("attach_alias", "remote_server"),
            attach_on_startup=d.get("attach_on_startup", []),
        )


# ---------------------------------------------------------------------------
# Full config (TOML file model)
# ---------------------------------------------------------------------------

@dataclass
class DuckDBConfig:
    """Represents the entire ~/.config/duckdbcs/config.toml file."""
    server: ServerConfig = field(default_factory=ServerConfig.defaults)
    client: ClientConfig = field(default_factory=ClientConfig.defaults)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "DuckDBConfig":
        """Load config from TOML file. Returns defaults if file doesn't exist."""
        path = path or CONFIG_FILE
        config = cls()

        if not path.exists():
            return config

        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)

            if "server" in data:
                config.server = ServerConfig.from_dict(data["server"])
            if "client" in data:
                config.client = ClientConfig.from_dict(data["client"])

        except (tomllib.TOMLDecodeError, OSError) as exc:
            import logging
            logging.getLogger("duckdbcs.config").warning(
                "Failed to load config from %s: %s", path, exc
            )

        return config

    def resolve_token(self) -> Optional[str]:
        """Resolve token with full priority chain."""
        # 1. env var
        env_token = os.environ.get(DEFAULT_TOKEN_ENVVAR)
        if env_token:
            return env_token
        # 2. config file
        if self.server.token:
            return self.server.token
        if self.client.token:
            return self.client.token
        # 3. .quack_secret file
        secret_file = Path.cwd() / DEFAULT_TOKEN_FILE
        if secret_file.exists():
            try:
                return secret_file.read_text().strip()
            except OSError:
                pass
        return None


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def load_config() -> DuckDBConfig:
    """Load config from persistent file + environment overrides."""
    config = DuckDBConfig.load()
    config.server.merge_env()
    config.client.merge_env()
    return config


def save_config(config: DuckDBConfig, path: Optional[Path] = None) -> None:
    """Save config to TOML file.
    
    Only saves non-default values to keep the file minimal.
    """
    path = path or CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# DuckDB Quack Client/Server configuration")
    lines.append("# Priority: CLI args > env vars > this file > defaults")
    lines.append("")

    # Server section
    s = config.server
    if any([s.host != "0.0.0.0", s.port != 9494, s.token, s.allow_other_hostname, s.attach_on_startup]):
        lines.append("[server]")
        if s.host != "0.0.0.0":
            lines.append(f'host = "{s.host}"')
        if s.port != 9494:
            lines.append(f"port = {s.port}")
        if s.token:
            lines.append(f'token = "{s.token}"')
        if s.allow_other_hostname:
            lines.append("allow_other_hostname = true")
        for attach in s.attach_on_startup:
            alias_str = f', alias = "{attach["alias"]}"' if attach.get("alias") else ""
            lines.append(f'[[server.attach_on_startup]]')
            lines.append(f'path = "{attach["path"]}"')
            if attach.get("alias"):
                lines.append(f'alias = "{attach["alias"]}"')
        lines.append("")

    # Client section
    c = config.client
    if any([c.host != "localhost", c.port != 9494, c.token, c.attach_alias != "remote_server", c.attach_on_startup]):
        lines.append("[client]")
        if c.host != "localhost":
            lines.append(f'host = "{c.host}"')
        if c.port != 9494:
            lines.append(f"port = {c.port}")
        if c.token:
            lines.append(f'token = "{c.token}"')
        if c.attach_alias != "remote_server":
            lines.append(f'attach_alias = "{c.attach_alias}"')
        for attach in c.attach_on_startup:
            lines.append(f'[[client.attach_on_startup]]')
            lines.append(f'path = "{attach["path"]}"')
            if attach.get("alias"):
                lines.append(f'alias = "{attach["alias"]}"')
        lines.append("")

    path.write_text("\n".join(lines))


def show_config_path() -> Path:
    """Return the config file path for display purposes."""
    return CONFIG_FILE


def load_token() -> Optional[str]:
    """Load token from anywhere (env > config file > .quack_secret)."""
    return load_config().resolve_token()
