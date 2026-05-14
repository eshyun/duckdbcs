"""Tests for duckdbcs.config module."""

import os
import tempfile
from pathlib import Path


from unittest.mock import patch

import pytest


from duckdbcs.config import (
    ClientConfig,
    DuckDBConfig,
    ServerConfig,
    load_config,
    load_token,
    save_config,
    show_config_path,
)


class TestServerConfig:
    def test_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9494
        assert cfg.token is None
        assert cfg.allow_other_hostname is False
        assert cfg.disable_ssl is False
        assert cfg.attach_on_startup == []

    def test_from_dict(self):
        d = {
            "host": "127.0.0.1",
            "port": 9999,
            "token": "secret123",
            "allow_other_hostname": True,
            "disable_ssl": True,
            "attach_on_startup": [{"path": "/data/db.db", "alias": "mydb"}],
        }
        cfg = ServerConfig.from_dict(d)
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9999
        assert cfg.token == "secret123"
        assert cfg.allow_other_hostname is True
        assert cfg.disable_ssl is True
        assert cfg.attach_on_startup == [{"path": "/data/db.db", "alias": "mydb"}]

    def test_from_dict_partial(self):
        d = {"host": "0.0.0.0"}
        cfg = ServerConfig.from_dict(d)
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9494  # default
        assert cfg.token is None

    def test_merge_env(self, monkeypatch):
        monkeypatch.setenv("QUACK_HOST", "192.168.1.1")
        monkeypatch.setenv("QUACK_PORT", "7777")
        monkeypatch.setenv("QUACK_TOKEN", "env_token")
        monkeypatch.setenv("QUACK_ALLOW_EXTERNAL", "true")

        cfg = ServerConfig()
        cfg.merge_env()
        assert cfg.host == "192.168.1.1"
        assert cfg.port == 7777
        assert cfg.token == "env_token"
        assert cfg.allow_other_hostname is True

    def test_to_dict(self):
        cfg = ServerConfig(host="0.0.0.0", port=9494, token="tok")
        d = cfg.to_dict()
        assert d["host"] == "0.0.0.0"
        assert d["port"] == 9494
        assert d["token"] == "tok"


class TestClientConfig:
    def test_defaults(self):
        cfg = ClientConfig()
        assert cfg.host == "localhost"
        assert cfg.port == 9494
        assert cfg.token is None
        assert cfg.disable_ssl is False
        assert cfg.attach_alias == "remote_server"
        assert cfg.attach_on_startup == []

    def test_uri_property(self):
        cfg = ClientConfig(host="example.com", port=9494)
        assert cfg.uri == "quack:example.com:9494"

    def test_from_dict(self):
        d = {
            "host": "server.local",
            "port": 9494,
            "token": "tok",
            "disable_ssl": True,
            "attach_alias": "my_server",
            "attach_on_startup": [{"path": "/data/db.db", "alias": "db1"}],
        }
        cfg = ClientConfig.from_dict(d)
        assert cfg.host == "server.local"
        assert cfg.token == "tok"
        assert cfg.disable_ssl is True
        assert cfg.attach_alias == "my_server"

    def test_merge_env(self, monkeypatch):
        monkeypatch.setenv("QUACK_REMOTE_HOST", "remote.local")
        monkeypatch.setenv("QUACK_REMOTE_PORT", "8888")
        monkeypatch.setenv("QUACK_TOKEN", "env_tok")
        monkeypatch.setenv("QUACK_ATTACH_ALIAS", "env_alias")

        cfg = ClientConfig()
        cfg.merge_env()
        assert cfg.host == "remote.local"
        assert cfg.port == 8888
        assert cfg.token == "env_tok"
        assert cfg.attach_alias == "env_alias"


class TestDuckDBConfig:
    def test_defaults(self):
        cfg = DuckDBConfig()
        assert isinstance(cfg.server, ServerConfig)
        assert isinstance(cfg.client, ClientConfig)

    def test_load_nonexistent_file(self):
        """Loading a non-existent file should return defaults."""
        fake_path = Path("/nonexistent/path/config.toml")
        cfg = DuckDBConfig.load(fake_path)
        assert cfg.server.host == "0.0.0.0"
        assert cfg.client.host == "localhost"

    def test_save_and_load(self, tmp_path):
        """Round-trip: save config, then load it back."""
        config_path = tmp_path / "config.toml"
        cfg = DuckDBConfig()
        cfg.server.host = "127.0.0.1"
        cfg.server.port = 9999
        cfg.server.token = "saved_token"
        cfg.server.attach_on_startup = [{"path": "/data/db.db", "alias": "mydb"}]
        cfg.client.host = "myserver.local"
        cfg.client.attach_alias = "my_alias"

        save_config(cfg, config_path)
        assert config_path.exists()

        loaded = DuckDBConfig.load(config_path)
        assert loaded.server.host == "127.0.0.1"
        assert loaded.server.port == 9999
        assert loaded.server.token == "saved_token"
        assert loaded.server.attach_on_startup == [{"path": "/data/db.db", "alias": "mydb"}]
        assert loaded.client.host == "myserver.local"
        assert loaded.client.attach_alias == "my_alias"

    def test_resolve_token_priority(self, monkeypatch, tmp_path):
        """Token resolution: env > config file > .quack_secret."""
        # Create a .quack_secret file
        secret_file = tmp_path / ".quack_secret"
        secret_file.write_text("file_token")

        # Create a config file with a token
        config_path = tmp_path / "config.toml"
        cfg = DuckDBConfig()
        cfg.server.token = "config_token"
        save_config(cfg, config_path)

        # Without env var, should resolve from config file
        monkeypatch.delenv("QUACK_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        resolved = DuckDBConfig.load(config_path).resolve_token()
        assert resolved == "config_token"

        # With env var, should take precedence
        monkeypatch.setenv("QUACK_TOKEN", "env_token")
        resolved = DuckDBConfig.load(config_path).resolve_token()
        assert resolved == "env_token"


class TestLoadToken:
    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("QUACK_TOKEN", "env_secret")
        assert load_token() == "env_secret"

    def test_quack_secret_file(self, monkeypatch, tmp_path):
        monkeypatch.delenv("QUACK_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        secret_file = tmp_path / ".quack_secret"
        secret_file.write_text("file_secret")
        with patch("duckdbcs.config.CONFIG_FILE", tmp_path / "config.toml"):
            assert load_token() == "file_secret"

    def test_no_token_available(self, monkeypatch, tmp_path):
        monkeypatch.delenv("QUACK_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        with patch("duckdbcs.config.CONFIG_FILE", tmp_path / "config.toml"):
            assert load_token() is None


class TestSaveConfig:
    def test_save_creates_directory(self, tmp_path):
        """save_config should create parent directories."""
        nested = tmp_path / "sub" / "dir" / "config.toml"
        cfg = DuckDBConfig()
        save_config(cfg, nested)
        assert nested.exists()

    def test_save_minimal(self, tmp_path):
        """Saving defaults should produce a minimal file."""
        config_path = tmp_path / "config.toml"
        cfg = DuckDBConfig()
        save_config(cfg, config_path)
        content = config_path.read_text()
        # Should have header but no server/client sections (all defaults)
        assert "DuckDB Quack" in content

    def test_show_config_path(self):
        path = show_config_path()
        assert str(path).endswith(".config/duckdbcs/config.toml")
