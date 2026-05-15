"""Tests for duckdbcs.client module.

Uses mocking to avoid requiring a running Quack server.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from duckdbcs.client import QuackClient, QuackResult, sql


# ===========================================================================
# QuackResult tests
# ===========================================================================


class TestQuackResult:
    @pytest.fixture
    def mock_relation(self):
        rel = MagicMock()
        rel.description = [("n",), ("s",)]
        rel.fetchall.return_value = [(42, "hello"), (99, "world")]
        return rel

    def test_fetchall(self, mock_relation):
        result = QuackResult(mock_relation)
        rows = result.fetchall()
        assert rows == [
            {"n": 42, "s": "hello"},
            {"n": 99, "s": "world"},
        ]

    def test_fetchall_cached(self, mock_relation):
        """fetchall should cache results."""
        result = QuackResult(mock_relation)
        rows1 = result.fetchall()
        rows2 = result.fetchall()
        assert rows1 == rows2
        assert mock_relation.fetchall.call_count == 1

    def test_df(self, mock_relation):
        result = QuackResult(mock_relation)
        result.df()
        mock_relation.df.assert_called_once()

    def test_pl(self, mock_relation):
        result = QuackResult(mock_relation)
        result.pl()
        mock_relation.pl.assert_called_once()

    def test_arrow(self, mock_relation):
        result = QuackResult(mock_relation)
        result.arrow()
        mock_relation.arrow.assert_called_once()

    def test_fetchnumpy(self, mock_relation):
        result = QuackResult(mock_relation)
        result.fetchnumpy()
        mock_relation.fetchnumpy.assert_called_once()

    def test_show(self, mock_relation):
        result = QuackResult(mock_relation)
        result.show(max_rows=5)
        mock_relation.show.assert_called_once_with(max_rows=5)

    def test_query_chaining(self, mock_relation):
        """query() should create a new QuackResult from the chained query."""
        mock_relation.query.return_value = mock_relation
        result = QuackResult(mock_relation)
        chained = result.query("SELECT n + 1 FROM result")
        assert isinstance(chained, QuackResult)
        mock_relation.query.assert_called_once_with("result", "SELECT n + 1 FROM result")

    def test_iter(self, mock_relation):
        result = QuackResult(mock_relation)
        rows = list(result)
        assert rows == [
            {"n": 42, "s": "hello"},
            {"n": 99, "s": "world"},
        ]

    def test_getitem(self, mock_relation):
        result = QuackResult(mock_relation)
        assert result[0] == {"n": 42, "s": "hello"}
        assert result[1] == {"n": 99, "s": "world"}

    def test_len(self, mock_relation):
        result = QuackResult(mock_relation)
        assert len(result) == 2

    def test_bool_true(self, mock_relation):
        result = QuackResult(mock_relation)
        assert bool(result) is True

    def test_bool_false(self):
        rel = MagicMock()
        rel.description = [("n",)]
        rel.fetchall.return_value = []
        result = QuackResult(rel)
        assert bool(result) is False

    def test_repr(self, mock_relation):
        result = QuackResult(mock_relation)
        r = repr(result)
        assert "QuackResult" in r
        assert "2 rows" in r
        assert "2 cols" in r

    def test_repr_empty(self):
        rel = MagicMock()
        rel.description = [("n",)]
        rel.fetchall.return_value = []
        result = QuackResult(rel)
        assert repr(result) == "QuackResult([])"


# ===========================================================================
# QuackClient tests (mocked DuckDB connection)
# ===========================================================================


class TestQuackClientInit:
    def test_default_init(self):
        client = QuackClient(token="test_token")
        assert client._token == "test_token"
        assert client._attached is False
        assert client._auto_routing_enabled is True
        assert client._server_databases == set()

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("QUACK_TOKEN", "env_token")
        client = QuackClient()
        assert client._token == "env_token"

    def test_custom_connection(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        assert client._conn is mock_conn


class TestQuackClientConnect:
    def test_successful_connect(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        status = client.connect(host="localhost", port=9494, attach_alias="remote_server")

        assert client._attached is True
        assert status["connected"] is True
        assert status["attach_alias"] == "remote_server"
        mock_conn.execute.assert_any_call("LOAD quack;")

    def test_connect_already_attached(self):
        """When already connected, connect() should disconnect first then reconnect."""
        mock_conn = MagicMock()
        mock_rel = MagicMock()
        mock_rel.description = []
        mock_rel.fetchall.return_value = []
        mock_conn.execute.return_value = mock_rel
        mock_conn.sql.return_value = mock_rel

        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        status = client.connect(host="other-host", port=9494, attach_alias="remote_server")

        # Should have disconnected first (DETACH)
        detach_calls = [c for c in mock_conn.execute.call_args_list if "DETACH" in str(c)]
        assert len(detach_calls) >= 1
        # Should have loaded quack extension for the new connection
        mock_conn.execute.assert_any_call("LOAD quack;")
        # Should be connected with new config
        assert client._attached is True
        assert client._config.host == "other-host"
        assert status["connected"] is True
        assert status["host"] == "other-host"

    def test_connect_failure(self):
        mock_conn = MagicMock()
        import duckdb
        mock_conn.execute.side_effect = duckdb.Error("LOAD failed")
        client = QuackClient(token="tok", connection=mock_conn)
        with pytest.raises(RuntimeError, match="Could not connect"):
            client.connect()


class TestQuackClientDisconnect:
    def test_disconnect(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        client.disconnect()
        assert client._attached is False
        mock_conn.execute.assert_called_with("DETACH remote_server;")

    def test_disconnect_not_attached(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client.disconnect()  # Should not raise
        mock_conn.execute.assert_not_called()


class TestQuackClientQuery:
    def test_query_not_connected(self):
        client = QuackClient(token="tok")
        with pytest.raises(RuntimeError, match="Not connected"):
            client.query("SELECT 1")

    def test_query_simple(self):
        mock_conn = MagicMock()
        mock_rel = MagicMock()
        mock_rel.description = [("42",)]
        mock_rel.fetchall.return_value = [(42,)]
        mock_conn.sql.return_value = mock_rel

        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        result = client.query("SELECT 42")
        assert isinstance(result, QuackResult)
        assert result.fetchall() == [{"42": 42}]
        mock_conn.sql.assert_called_with("SELECT 42")

    def test_query_with_database_routing(self):
        mock_conn = MagicMock()
        mock_rel = MagicMock()
        mock_rel.description = [("n",)]
        mock_rel.fetchall.return_value = [(1,)]
        mock_conn.sql.return_value = mock_rel

        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        result = client.query("SELECT 1", database="remote_server")
        assert isinstance(result, QuackResult)
        # Should route through remote_server.query(...)
        mock_conn.sql.assert_called_once()
        call_arg = mock_conn.sql.call_args[0][0]
        assert "remote_server.query" in call_arg

    def test_query_auto_routing(self):
        mock_conn = MagicMock()
        mock_rel = MagicMock()
        mock_rel.description = [("n",)]
        mock_rel.fetchall.return_value = [(1,)]
        mock_conn.sql.return_value = mock_rel

        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"
        client._server_databases = {"analytics"}

        # SQL references a server-only database
        result = client.query("SELECT * FROM analytics.users")
        assert isinstance(result, QuackResult)
        mock_conn.sql.assert_called_once()
        call_arg = mock_conn.sql.call_args[0][0]
        assert "remote_server.query" in call_arg

    def test_query_fallback_on_local_failure(self):
        """When local query fails with 'does not exist', should retry via server."""
        import duckdb

        mock_conn = MagicMock()
        mock_rel = MagicMock()
        mock_rel.description = [("n",)]
        mock_rel.fetchall.return_value = [(1,)]

        # First call fails with duckdb.Error, second succeeds
        err = duckdb.Error("Catalog Error: Table with name test.table does not exist in schema main")
        mock_conn.sql.side_effect = [
            err,
            mock_rel,
        ]

        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        result = client.query("SELECT * FROM test.table")
        assert isinstance(result, QuackResult)
        assert mock_conn.sql.call_count == 2

class TestQuackClientExecute:
    def test_execute_not_connected(self):
        client = QuackClient(token="tok")
        with pytest.raises(RuntimeError, match="Not connected"):
            client.execute("CREATE TABLE t (x INT)")

    def test_execute_simple(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        result = client.execute("CREATE TABLE t (x INT)")
        mock_conn.execute.assert_called_with("CREATE TABLE t (x INT)")

    def test_execute_with_database(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        client.execute("CREATE TABLE t (x INT)", database="remote_server")
        mock_conn.execute.assert_called_once()
        call_arg = mock_conn.execute.call_args[0][0]
        assert "remote_server.query" in call_arg


class TestQuackClientStatelessQuery:
    def test_stateless_query(self):
        mock_conn = MagicMock()
        mock_rel = MagicMock()
        mock_rel.description = [("n",)]
        mock_rel.fetchall.return_value = [(42,)]
        mock_conn.sql.return_value = mock_rel

        client = QuackClient(token="tok", connection=mock_conn)
        result = client.stateless_query(
            host="localhost", port=9494, sql_str="SELECT 42"
        )
        assert isinstance(result, QuackResult)
        mock_conn.execute.assert_any_call("LOAD quack;")
        mock_conn.sql.assert_called_once()


class TestQuackClientRemoteManagement:
    def test_attach_remote(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"
        client._refresh_server_databases = MagicMock(return_value=set())

        alias = client.attach_remote("/data/db.db", alias="mydb")
        assert alias == "mydb"
        mock_conn.execute.assert_called_once()
        call_arg = mock_conn.execute.call_args[0][0]
        assert "remote_server.query" in call_arg
        assert "ATTACH" in call_arg

    def test_attach_remote_auto_alias(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        alias = client.attach_remote("/data/my_database.duckdb")
        assert alias == "my_database"

    def test_detach_remote(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"
        client._server_databases = {"mydb"}

        client.detach_remote("mydb")
        mock_conn.execute.assert_called()
        call_args = [c[0][0] for c in mock_conn.execute.call_args_list]
        assert any("DETACH mydb" in arg for arg in call_args)

    def test_list_databases(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("remote_server", ":memory:"),
            ("analytics", "/data/analytics.db"),
        ]
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        dbs = client.list_databases()
        assert len(dbs) == 2
        assert "remote_server (:memory:)" in dbs
        # Should route through the server catalog
        call_arg = mock_conn.execute.call_args[0][0]
        assert "remote_server.query" in call_arg

    def test_list_databases_failure(self):
        import duckdb
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = duckdb.Error("Server error")
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        with pytest.raises(RuntimeError, match="Could not list databases"):
            client.list_databases()

    def test_list_tables(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("main", "users"),
            ("main", "orders"),
        ]
        client = QuackClient(token="tok", connection=mock_conn)
        client._attached = True
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        tables = client.list_tables()
        assert len(tables) == 2
        assert tables[0] == {"schema": "main", "table": "users"}

    def test_status(self):
        client = QuackClient(token="tok")
        status = client.status()
        assert status["connected"] is False

        client._attached = True
        client._config = MagicMock()
        client._config.uri = "quack:localhost:9494"
        client._config.attach_alias = "remote_server"
        client._config.host = "localhost"
        client._config.port = 9494

        status = client.status()
        assert status["connected"] is True
        assert status["uri"] == "quack:localhost:9494"


class TestQuackClientLifecycle:
    def test_close(self):
        mock_conn = MagicMock()
        client = QuackClient(token="tok", connection=mock_conn)
        client.close()
        mock_conn.close.assert_called_once()

    def test_context_manager(self):
        mock_conn = MagicMock()
        with QuackClient(token="tok", connection=mock_conn) as client:
            assert client._token == "tok"
        mock_conn.close.assert_called_once()

    def test_sql_class_method(self):
        """duckdbcs.sql() should work as a one-shot convenience."""
        with patch("duckdbcs.sql") as mock_sql:
            mock_sql.return_value = "result"
            from duckdbcs import sql as module_sql
            r = module_sql("SELECT 1", token="tok")
            mock_sql.assert_called_once_with("SELECT 1", token="tok")
            assert r == "result"


class TestExtractDatabaseRefs:
    def test_simple_ref(self):
        refs = QuackClient._extract_database_refs("SELECT * FROM analytics.users")
        assert "analytics" in refs

    def test_multiple_refs(self):
        refs = QuackClient._extract_database_refs(
            "SELECT a.*, b.* FROM db1.table1 a JOIN db2.table2 b ON a.id = b.id"
        )
        assert "db1" in refs
        assert "db2" in refs

    def test_no_refs(self):
        refs = QuackClient._extract_database_refs("SELECT 42")
        assert refs == set()

    def test_three_part_ref(self):
        refs = QuackClient._extract_database_refs(
            "SELECT * FROM catalog.schema.table"
        )
        assert "catalog" in refs


class TestFindRouteCatalog:
    def test_no_routing_needed(self):
        client = QuackClient(token="tok")
        client._auto_routing_enabled = True
        client._server_databases = set()
        assert client._find_route_catalog("SELECT 1") is None

    def test_routing_needed(self):
        client = QuackClient(token="tok")
        client._auto_routing_enabled = True
        client._server_databases = {"analytics"}
        client._config = MagicMock()
        client._config.attach_alias = "remote_server"

        route = client._find_route_catalog("SELECT * FROM analytics.users")
        assert route == "remote_server"

    def test_routing_disabled(self):
        client = QuackClient(token="tok")
        client._auto_routing_enabled = False
        client._server_databases = {"analytics"}
        assert client._find_route_catalog("SELECT * FROM analytics.users") is None


# ===========================================================================
# sql() module-level function tests
# ===========================================================================


class TestSqlFunction:
    def test_sql_basic(self):
        """sql() should create a client, connect, query, close, and return a QuackResult."""
        import pyarrow as pa
        arrow_tbl = pa.table({"answer": [42]})
        with patch("duckdbcs.client.QuackClient") as MockClient:
            mock_instance = MagicMock()
            mock_query_result = MagicMock()
            mock_query_result.arrow.return_value = arrow_tbl
            mock_instance.query.return_value = mock_query_result
            MockClient.return_value = mock_instance

            result = sql("SELECT 42", token="tok")

            assert isinstance(result, QuackResult)
            assert result.fetchall() == [{"answer": 42}]
            MockClient.assert_called_once_with(token="tok")
            mock_instance.connect.assert_called_once_with(
                host="localhost",
                port=9494,
                attach_alias="remote_server",
                disable_ssl=False,
            )
            mock_instance.query.assert_called_once_with("SELECT 42", database=None)
            mock_instance.close.assert_called_once()

    def test_sql_with_database(self):
        import pyarrow as pa
        arrow_tbl = pa.table({"answer": [42]})
        with patch("duckdbcs.client.QuackClient") as MockClient:
            mock_instance = MagicMock()
            mock_query_result = MagicMock()
            mock_query_result.arrow.return_value = arrow_tbl
            mock_instance.query.return_value = mock_query_result
            MockClient.return_value = mock_instance

            sql("SELECT * FROM analytics.users", token="tok", database="analytics")

            mock_instance.query.assert_called_once_with(
                "SELECT * FROM analytics.users", database="analytics"
            )

    def test_sql_custom_host_port(self):
        import pyarrow as pa
        arrow_tbl = pa.table({"answer": [42]})
        with patch("duckdbcs.client.QuackClient") as MockClient:
            mock_instance = MagicMock()
            mock_query_result = MagicMock()
            mock_query_result.arrow.return_value = arrow_tbl
            mock_instance.query.return_value = mock_query_result
            MockClient.return_value = mock_instance

            sql("SELECT 1", host="server.example.com", port=9999, token="tok")

            mock_instance.connect.assert_called_once_with(
                host="server.example.com",
                port=9999,
                attach_alias="remote_server",
                disable_ssl=False,
            )

    def test_sql_cleanup_on_error(self):
        """sql() should close the client even if query fails."""
        with patch("duckdbcs.client.QuackClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.query.side_effect = RuntimeError("Query failed")
            MockClient.return_value = mock_instance

            with pytest.raises(RuntimeError, match="Query failed"):
                sql("SELECT 1", token="tok")

            mock_instance.close.assert_called_once()
