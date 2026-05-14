"""Tests for duckdbcs.server module.

Uses mocking to avoid requiring a running DuckDB instance.
"""

from unittest.mock import MagicMock, patch

import pytest

from duckdbcs.server import QuackServer, run_server_forever


class TestQuackServerInit:
    def test_default_init(self):
        server = QuackServer(token="test_token")
        assert server._token == "test_token"
        assert server._running is False
        assert server._attached_dbs == {}

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("QUACK_TOKEN", "env_token")
        server = QuackServer()
        assert server._token == "env_token"

    def test_custom_connection(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        assert server._conn is mock_conn


class TestQuackServerStart:
    def test_successful_start(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = ("running",)
        server = QuackServer(token="tok", connection=mock_conn)

        status = server.start(host="0.0.0.0", port=9494)

        assert server._running is True
        assert status["running"] is True
        assert status["listen_uri"] == "quack:0.0.0.0:9494"
        assert status["auth_token"] == "tok"
        mock_conn.execute.assert_any_call("LOAD quack;")

    def test_start_already_running(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        server._running = True

        status = server.start()
        assert status["running"] is True
        # Should not call quack_serve again
        calls = [c for c in mock_conn.execute.call_args_list if "quack_serve" in str(c)]
        assert len(calls) == 0

    def test_start_failure(self):
        mock_conn = MagicMock()
        import duckdb
        mock_conn.execute.side_effect = duckdb.Error("LOAD failed")
        server = QuackServer(token="tok", connection=mock_conn)

        with pytest.raises(RuntimeError, match="Could not start"):
            server.start()


class TestQuackServerStop:
    def test_stop(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        server._running = True
        server._config = MagicMock()
        server._config.host = "0.0.0.0"
        server._config.port = 9494

        server.stop()
        assert server._running is False
        mock_conn.execute.assert_called_with("CALL quack_stop(?);", ["quack:0.0.0.0:9494"])

    def test_stop_not_running(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        server.stop()  # Should not raise
        mock_conn.execute.assert_not_called()


class TestQuackServerDatabaseManagement:
    def test_attach_database(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)

        alias = server.attach_database("/data/db.db", alias="mydb")
        assert alias == "mydb"
        assert server._attached_dbs == {"mydb": "/data/db.db"}
        mock_conn.execute.assert_called_with("ATTACH '/data/db.db' AS mydb;")

    def test_attach_database_auto_alias(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)

        alias = server.attach_database("/data/my_database.duckdb")
        assert alias == "my_database"

    def test_detach_database(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        server._attached_dbs = {"mydb": "/data/db.db"}

        server.detach_database("mydb")
        assert "mydb" not in server._attached_dbs
        mock_conn.execute.assert_called_with("DETACH mydb;")

    def test_detach_nonexistent(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)

        with pytest.raises(ValueError, match="not attached"):
            server.detach_database("nonexistent")

    def test_list_databases(self):
        server = QuackServer(token="tok")
        server._attached_dbs = {"a": "/path/a.db", "b": "/path/b.db"}
        assert set(server.list_databases()) == {"a", "b"}


class TestQuackServerAuth:
    def test_set_authentication(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)

        server.set_authentication(
            "CREATE MACRO my_auth(sid, ct, st) AS (ct IN ('tok1', 'tok2'));"
        )
        mock_conn.execute.assert_any_call(
            "SET GLOBAL quack_authentication_function = ?;", ["my_auth"]
        )

    def test_set_authorization(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)

        server.set_authorization(
            "CREATE MACRO read_only(sid, q) AS regexp_matches(upper(trim(q)), '^(SELECT|FROM|WITH)\\b');"
        )
        mock_conn.execute.assert_any_call(
            "SET GLOBAL quack_authorization_function = ?;", ["read_only"]
        )


class TestQuackServerStatus:
    def test_status_not_running(self):
        server = QuackServer(token="tok")
        status = server.status()
        assert status["running"] is False
        assert status["attached_databases"] == {}

    def test_status_running(self):
        server = QuackServer(token="tok")
        server._running = True
        server._config = MagicMock()
        server._config.host = "0.0.0.0"
        server._config.port = 9494
        server._config.token = "tok"
        server._config.allow_other_hostname = False
        server._attached_dbs = {"mydb": "/data/db.db"}

        status = server.status()
        assert status["running"] is True
        assert status["listen_uri"] == "quack:0.0.0.0:9494"
        assert status["auth_token"] == "tok"
        assert "mydb" in status["attached_databases"]


class TestQuackServerLifecycle:
    def test_close(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        server.close()
        mock_conn.close.assert_called_once()

    def test_close_stops_running_server(self):
        mock_conn = MagicMock()
        server = QuackServer(token="tok", connection=mock_conn)
        server._running = True
        server._config = MagicMock()
        server._config.host = "0.0.0.0"
        server._config.port = 9494

        server.close()
        assert server._running is False
        mock_conn.close.assert_called_once()

    def test_context_manager(self):
        mock_conn = MagicMock()
        with QuackServer(token="tok", connection=mock_conn) as server:
            assert server._token == "tok"
        mock_conn.close.assert_called_once()


class TestExtractMacroName:
    def test_simple(self):
        name = QuackServer._extract_macro_name(
            "CREATE MACRO my_auth(sid, ct, st) AS (ct IN ('tok1'));"
        )
        assert name == "my_auth"

    def test_or_replace(self):
        name = QuackServer._extract_macro_name(
            "CREATE OR REPLACE MACRO my_macro(x) AS (x + 1);"
        )
        assert name == "my_macro"

    def test_no_match(self):
        with pytest.raises(ValueError, match="Could not extract"):
            QuackServer._extract_macro_name("SELECT 1")


class TestFromConfig:
    def test_server_from_config(self, tmp_path):
        """from_config() should create a server from a config object."""
        from duckdbcs.config import ServerConfig

        config = ServerConfig(
            host="0.0.0.0",
            port=9494,
            token="cfg_token",
            allow_other_hostname=False,
            attach_on_startup=[{"path": "/data/db.db", "alias": "mydb"}],
        )

        with patch("duckdbcs.server.QuackServer.start") as mock_start:
            with patch("duckdbcs.server.QuackServer.attach_database") as mock_attach:
                mock_start.return_value = {"running": True}
                server = QuackServer.from_config(config)

                mock_start.assert_called_once_with(
                    host="0.0.0.0",
                    port=9494,
                    allow_other_hostname=False,
                    disable_ssl=False,
                )
                mock_attach.assert_called_once_with("/data/db.db", "mydb")
                server.close()


class TestRunServerForever:
    def test_run_server_forever_startup(self):
        """run_server_forever should start the server and attach databases."""
        with patch("duckdbcs.server.QuackServer") as MockServer:
            mock_instance = MagicMock()
            MockServer.return_value = mock_instance

            # start succeeds, then KeyboardInterrupt on time.sleep
            mock_instance.start.return_value = {"running": True}

            with patch("duckdbcs.server.time.sleep", side_effect=[None, KeyboardInterrupt()]):
                run_server_forever(
                    host="0.0.0.0",
                    port=9494,
                    token="tok",
                    allow_other_hostname=True,
                    databases=[("/data/db.db", "mydb")],
                )

            MockServer.assert_called_once_with(token="tok")
            mock_instance.start.assert_called_once_with(
                host="0.0.0.0",
                port=9494,
                allow_other_hostname=True,
            )
            mock_instance.attach_database.assert_called_once_with("/data/db.db", "mydb")
            mock_instance.close.assert_called_once()
