"""DuckDB Quack Client implementation.

Connects to a remote Quack server, executes queries, and manages
attached databases on the server side.
"""

import atexit
import logging
from typing import Any, Optional

import duckdb

from duckdbcs.config import ClientConfig, ServerConfig, load_config, load_token

logger = logging.getLogger("duckdbcs.client")


# ---------------------------------------------------------------------------
# QuackResult — like DuckDB's relation but for remote server results
# ---------------------------------------------------------------------------


class QuackResult:
    """Query result from a remote Quack server.

    Wraps a local DuckDB relation and provides conversion methods similar to
    ``duckdb.DuckDBPyRelation``:

    - ``.fetchall()`` → list of Python objects (dicts)
    - ``.df()``       → Pandas DataFrame
    - ``.pl()``       → Polars DataFrame
    - ``.arrow()``    → Arrow Table (``pyarrow.Table``)
    - ``.fetchnumpy()`` → NumPy structured arrays (``dict[str, numpy.ndarray]``)
    - ``.show()``     → Print a formatted table
    - Iterable / indexable like ``list[dict]``
    """

    def __init__(self, relation: "duckdb.DuckDBPyRelation"):
        self._rel = relation
        self._columns = [desc[0] for desc in relation.description]
        self._cached_rows: Optional[list[dict]] = None

    @property
    def relation(self) -> "duckdb.DuckDBPyRelation":
        """Return the underlying DuckDB relation object."""
        return self._rel

    # ------------------------------------------------------------------
    # DuckDB-style output format conversions
    # ------------------------------------------------------------------

    def fetchall(self) -> list[dict]:
        """Fetch all rows as a list of Python dicts (column -> value)."""
        if self._cached_rows is None:
            rows = self._rel.fetchall()
            self._cached_rows = [dict(zip(self._columns, row)) for row in rows]
        return self._cached_rows

    def df(self):
        """Convert result to a Pandas DataFrame.

        Returns:
            ``pandas.DataFrame``
        """
        return self._rel.df()

    def pl(self):
        """Convert result to a Polars DataFrame.

        Returns:
            ``polars.DataFrame``
        """
        return self._rel.pl()

    def arrow(self):
        """Convert result to an Apache Arrow Table.

        Returns:
            ``pyarrow.Table``
        """
        return self._rel.arrow()

    def fetchnumpy(self):
        """Convert result to NumPy structured arrays.

        Returns:
            ``dict[str, numpy.ndarray]``
        """
        return self._rel.fetchnumpy()

    def show(self, max_rows: int = 10) -> None:
        """Print a formatted table to stdout (duckdb-style)."""
        self._rel.show(max_rows=max_rows)

    # ------------------------------------------------------------------
    # DuckDB-style chaining: ``.sql("SELECT ... FROM result")``
    # ------------------------------------------------------------------

    def query(self, sql: str) -> "QuackResult":
        """Run a query using this result as a named view ``result``.

        Example::

            r1 = client.sql("SELECT 42 AS n")
            r2 = r1.query("SELECT n + 1 AS m FROM result")
            r2.show()
        """
        return QuackResult(self._rel.query("result", sql))

    # ------------------------------------------------------------------
    # List-like protocol (backward compatibility)
    # ------------------------------------------------------------------

    def __iter__(self):
        return iter(self.fetchall())

    def __getitem__(self, index):
        return self.fetchall()[index]

    def __len__(self):
        return len(self.fetchall())

    def __bool__(self):
        """Truthy if there are rows."""
        return len(self.fetchall()) > 0

    def __repr__(self) -> str:
        rows = self.fetchall()
        if not rows:
            return "QuackResult([])"
        return f"QuackResult({len(rows)} rows, {len(self._columns)} cols)"

    def __str__(self) -> str:
        return self._rel.__str__()


# ---------------------------------------------------------------------------
# Module-level convenience: duckdbcs.sql() — like duckdb.sql() but for remote
# ---------------------------------------------------------------------------


def sql(
    query: str,
    host: str = "localhost",
    port: int = 9494,
    token: Optional[str] = None,
    attach_alias: str = "remote_server",
    database: Optional[str] = None,
    disable_ssl: bool = False,
    verbose: bool = False,
) -> QuackResult:
    """Execute a SQL query against a remote Quack server in one shot.

    This is a convenience function similar to ``duckdb.sql()`` but for remote
    Quack servers. It creates a temporary client, connects, runs the query,
    and returns a ``QuackResult`` — all in one call.

    Args:
        query: SQL query to execute.
        host: Server hostname (default: localhost).
        port: Server port (default: 9494).
        token: Auth token. Falls back to ``QUACK_TOKEN`` env var, config file,
               or ``.quack_secret``.
        attach_alias: Local catalog alias (default: remote_server).
        database: Optional catalog to route the query through the server.
                  If not set, auto-routing is attempted.
        disable_ssl: Force plain HTTP.
        verbose: Enable verbose logging.

    Returns:
        ``QuackResult`` with format conversion methods::

            # Python objects
            sql("SELECT 42").fetchall()
            # Pandas DataFrame
            sql("SELECT 42").df()
            # Polars DataFrame
            sql("SELECT 42").pl()
            # Arrow Table
            sql("SELECT 42").arrow()
            # NumPy Arrays
            sql("SELECT 42").fetchnumpy()
            # Pretty print
            sql("SELECT 42").show()

    Examples:
        from duckdbcs import sql

        # One-shot query:
        results = sql("SELECT 42", token="my_token")
        print(results.fetchall())

        # Query a server-side attached database:
        results = sql("SELECT * FROM listings.market_listings LIMIT 10",
                      host="localhost", port=9494, token="my_token")
        df = results.df()  # Pandas DataFrame

        # Chaining:
        sql("SELECT 42 AS n").query("SELECT n + 1 FROM result").show()
    """
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    client = QuackClient(token=token)
    try:
        client.connect(host=host, port=port, attach_alias=attach_alias, disable_ssl=disable_ssl)
        result = client.query(query, database=database)
        # Eagerly materialize the result into a local in-memory connection
        # so the QuackResult survives client.close() below.
        # Use Arrow to transfer data across DuckDB connections (a DuckDBPyRelation
        # is tied to its creating connection and cannot be used by another one).
        local_conn = duckdb.connect(":memory:")
        rel = local_conn.from_arrow(result.arrow())
        return QuackResult(rel)
    finally:
        client.close()


# ===========================================================================
# QuackClient
# ===========================================================================


class QuackClient:
    """Client for connecting to a DuckDB Quack server.

    Supports both stateless queries (via ``quack_query``) and full catalog
    attachment (via ``ATTACH``).

    Usage:
        client = QuackClient(token="my_secret")
        client.connect("localhost", 9494)
        result = client.query("SELECT 42")
        client.execute("CREATE TABLE t AS SELECT * FROM range(10)")
        client.disconnect()
    """

    def __init__(
        self,
        token: Optional[str] = None,
        connection: Optional["duckdb.DuckDBPyConnection"] = None,
        host: Optional[str] = None,
        port: int = 9494,
        attach_alias: str = "remote_server",
        disable_ssl: bool = False,
        auto_start_server: bool = False,
        auto_stop_server: bool = True,
    ):
        """Initialize a QuackClient.

        Args:
            token: Auth token. Falls back to ``QUACK_TOKEN`` env var, config file,
                   or ``.quack_secret``.
            connection: An existing DuckDB connection to use (creates one if not given).
            host: Server hostname. If provided, automatically connects to the server.
            port: Server port (default: 9494). Only used when ``host`` is given.
            attach_alias: Local catalog alias for the remote server (default: remote_server).
                          Only used when ``host`` is given.
            disable_ssl: Force plain HTTP (default: auto-detect). Only used when
                         ``host`` is given.
            auto_start_server: If True, automatically start a local Quack server when
                               connection to the specified host:port fails.
            auto_stop_server: If True (default), stop the auto-started server when the
                              client is closed. Only relevant when auto_start_server=True.
        """
        self._conn = connection or duckdb.connect(":memory:")
        # Auto-started server tracking
        self._auto_started_server: Optional["QuackServer"] = None  # noqa: F821
        self._auto_start_config: Optional[ServerConfig] = None
        self._auto_stop_server = auto_stop_server
        # Token resolution: explicit > env > config file > .quack_secret
        self._token = token or load_token()
        self._config: Optional[ClientConfig] = None
        self._attached = False
        # Auto-discovered server-side databases that need query routing
        self._auto_routing_enabled = True  # Set False to disable auto-routing
        self._server_databases: set[str] = set()

        # Auto-connect if host is provided
        if host is not None:
            try:
                self.connect(
                    host=host,
                    port=port,
                    attach_alias=attach_alias,
                    disable_ssl=disable_ssl,
                )
            except RuntimeError:
                if auto_start_server:
                    logger.info("Could not connect to server -- attempting auto-start...")
                    self._auto_start_server(
                        host=host,
                        port=port,
                        attach_alias=attach_alias,
                        disable_ssl=disable_ssl,
                    )
                else:
                    raise

    # ------------------------------------------------------------------
    # Auto-start server management
    # ------------------------------------------------------------------

    def _auto_start_server(
        self,
        host: str,
        port: int,
        attach_alias: str,
        disable_ssl: bool,
    ) -> None:
        """Auto-start a Quack server in-process, then connect to it.

        Uses the persistent config (if available) to configure the server,
        then starts it and connects the client to it.
        """
        # Load server config from config file / env
        cfg = load_config()
        server_cfg = cfg.server

        # Override with the host:port the client was trying to connect to
        server_cfg.host = "0.0.0.0" if host in ("localhost", "127.0.0.1") else host
        server_cfg.port = port
        server_cfg.token = self._token

        self._auto_start_config = server_cfg

        # Lazily import QuackServer to avoid circular imports
        from duckdbcs.server import QuackServer  # noqa: F811

        server = QuackServer(token=self._token)
        self._auto_started_server = server

        logger.info(
            "Auto-starting Quack server on %s:%d ...",
            server_cfg.host, server_cfg.port,
        )
        server.start(
            host=server_cfg.host,
            port=server_cfg.port,
            allow_other_hostname=server_cfg.allow_other_hostname,
            disable_ssl=server_cfg.disable_ssl,
        )

        # Auto-attach databases from config
        for attach_entry in server_cfg.attach_on_startup:
            try:
                server.attach_database(attach_entry["path"], attach_entry.get("alias"))
            except Exception as exc:
                logger.warning(
                    "Failed to auto-attach '%s' on auto-started server: %s",
                    attach_entry.get("path"),
                    exc,
                )

        # Now connect the client to this newly started server
        self.connect(
            host=host,
            port=port,
            attach_alias=attach_alias,
            disable_ssl=disable_ssl,
        )

        # Register cleanup for auto-stopped server
        if self._auto_stop_server:
            atexit.register(self._cleanup_auto_server)

    def _cleanup_auto_server(self) -> None:
        """Stop the auto-started server if one was started and auto-stop is enabled."""
        if self._auto_started_server is not None and self._auto_stop_server:
            logger.info("Stopping auto-started Quack server ...")
            try:
                self._auto_started_server.stop()
                self._auto_started_server.close()
            except Exception as exc:
                logger.warning("Error stopping auto-started server: %s", exc)
            self._auto_started_server = None

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Optional["ClientConfig"] = None) -> "QuackClient":
        """Create a client from a ClientConfig object (or load from persistent config).

        If a ``ClientConfig`` is provided, it will be used directly.
        Otherwise, the persistent config file (``~/.config/duckdbcs/config.toml``)
        is loaded and the ``client`` section is used.

        The returned client is already connected and has all ``attach_on_startup``
        databases attached remotely.

        Usage::

            from duckdbcs.client import QuackClient
            client = QuackClient.from_config()
            # client is connected and all auto-attach DBs are attached
            print(client.query("SELECT 42"))
            client.close()
        """
        from duckdbcs.config import load_config as _load_config

        if config is None:
            cfg = _load_config()
            config = cfg.client

        if not config.token:
            token = load_token()
        else:
            token = config.token

        instance = cls(token=token)
        instance.connect(
            host=config.host,
            port=config.port,
            attach_alias=config.attach_alias,
            disable_ssl=config.disable_ssl,
        )

        # Auto-attach databases specified in config
        for attach_entry in config.attach_on_startup:
            try:
                instance.attach_remote(attach_entry["path"], attach_entry.get("alias"))
            except Exception as exc:
                logger.warning(
                    "Failed to auto-attach '%s': %s",
                    attach_entry.get("path"),
                    exc,
                )

        return instance

    def connect(
        self,
        host: str = "localhost",
        port: int = 9494,
        attach_alias: str = "remote_server",
        disable_ssl: bool = False,
    ) -> dict:
        """Connect to a Quack server by attaching it as a remote catalog.

        Once connected, tables on the server are accessible as
        ``<attach_alias>.table_name``.

        If already connected, the previous connection is automatically
        disconnected before establishing the new one.

        Args:
            host: Server hostname or IP.
            port: Server port (default: 9494).
            attach_alias: Local catalog alias for the remote server.
            disable_ssl: Force plain HTTP (default: auto-detect).

        Returns:
            Client status dict.
        """
        if self._attached:
            logger.info("Already connected -- disconnecting first.")
            self.disconnect()

        self._config = ClientConfig(
            host=host,
            port=port,
            token=self._token,
            disable_ssl=disable_ssl,
            attach_alias=attach_alias,
        )

        uri = self._config.uri
        logger.info("Connecting to Quack server at %s as '%s' ...", uri, attach_alias)

        try:
            self._conn.execute("LOAD quack;")

            suffix = ", DISABLE_SSL true)" if disable_ssl else ")"
            attach_sql = "ATTACH '%s' AS %s (TOKEN '%s'%s" % (
                uri, attach_alias, self._token, suffix,
            )
            self._conn.execute(attach_sql)

            self._attached = True

            # Auto-discover server-side databases for routing
            self._refresh_server_databases()

            logger.info("Connected to Quack server at %s.", uri)
            return self.status()

        except duckdb.Error as exc:
            logger.warning("Failed to connect to Quack server: %s", exc)
            raise RuntimeError(f"Could not connect to Quack server at {uri}: {exc}") from exc

    def disconnect(self) -> None:
        """Detach from the Quack server."""
        if not self._attached or not self._config:
            logger.warning("Not connected.")
            return

        alias = self._config.attach_alias
        logger.info("Disconnecting from '%s' ...", alias)

        try:
            self._conn.execute(f"DETACH {alias};")
        except duckdb.Error as exc:
            logger.warning("Error during detach: %s", exc)

        self._attached = False
        self._server_databases.clear()
        logger.info("Disconnected.")

    # ------------------------------------------------------------------
    # Server-side database auto-detection & query routing
    # ------------------------------------------------------------------

    def _refresh_server_databases(self) -> set[str]:
        """Query the server for its attached databases and cache which are server-only.

        Databases that exist on the server but are NOT the local attach alias
        (``remote_server``) need their queries auto-routed through the server.
        """
        if not self._attached or not self._config:
            return set()

        attach_alias = self._config.attach_alias
        # Query the server for its databases
        query_sql = "SELECT database_name, path FROM duckdb_databases()"
        safe_sql = query_sql.replace(chr(39), chr(39) + chr(39))
        full_sql = f"FROM {attach_alias}.query('{safe_sql}')"

        try:
            result = self._conn.execute(full_sql)
            rows = result.fetchall()

            # Get the set of all databases visible locally
            local_result = self._conn.execute("SELECT database_name FROM duckdb_databases()")
            local_dbs = {row[0].lower() for row in local_result.fetchall()}

            # Databases that exist on the server but are NOT local
            server_only = set()
            for row in rows:
                db_name = row[0].lower()
                # If the database is not visible locally, it's server-only
                if db_name not in local_dbs and db_name != attach_alias.lower():
                    server_only.add(row[0])  # Keep original casing

            self._server_databases = server_only
            if server_only:
                logger.info(
                    "Auto-detected server-side databases that will be auto-routed: %s",
                    sorted(server_only),
                )
            return server_only
        except Exception as exc:
            logger.debug("Could not refresh server databases: %s", exc)
            return set()

    @staticmethod
    def _extract_database_refs(sql: str) -> set[str]:
        """Extract potential database/catalog references from SQL text.

        Looks for identifiers in the form ``database.table`` or ``database.schema.table``.
        """
        import re

        refs: set[str] = set()

        # Match ``identifier.identifier`` or ``identifier.identifier.identifier``
        for match in re.finditer(
            r"(?:^|[\s,;\(\)])([\w_]+)\.([\w_]+(?:\.\w+)?)",
            sql, re.IGNORECASE
        ):
            refs.add(match.group(1))

        return refs

    def _find_route_catalog(self, sql: str) -> Optional[str]:
        """Check if SQL references any server-only database and return the route catalog.

        Returns the attach_alias if routing is needed, None if SQL can run locally.
        """
        if not self._auto_routing_enabled or not self._server_databases:
            return None

        refs = self._extract_database_refs(sql)
        if not refs:
            return None

        # Normalize both sets for comparison
        refs_lower = {r.lower() for r in refs}
        server_lower = {s.lower() for s in self._server_databases}

        if refs_lower & server_lower:
            return self._config.attach_alias if self._config else None

        return None

    # ------------------------------------------------------------------
    # Core query / execute methods
    # ------------------------------------------------------------------

    def query(self, sql_str: str, database: Optional[str] = None) -> QuackResult:
        """Execute a SELECT query and return results as a ``QuackResult``.

        Args:
            sql_str: The SQL query string.
            database: Optional catalog alias to route the query through the server.
                      If not provided, auto-routing is attempted.

        Returns:
            ``QuackResult`` with format conversion methods::

                # Python objects
                results.fetchall()
                # Pandas DataFrame
                results.df()
                # Polars DataFrame
                results.pl()
                # Arrow Table
                results.arrow()
                # NumPy Arrays
                results.fetchnumpy()
                # Pretty print
                results.show()
                # Chaining (use as named view "result")
                results.query("SELECT ... FROM result")

        Examples:
            # Return a QuackResult -- just like duckdb.sql():
            result = client.query("SELECT 42")
            result.show()

            # Convert to various formats:
            df = client.query("SELECT * FROM data").df()
            py_result = client.query("SELECT * FROM data").fetchall()
        """
        if not self._attached or not self._config:
            raise RuntimeError("Not connected. Call connect() first.")

        try:
            if database:
                catalog = self._config.attach_alias
                safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                full_sql = f"FROM {catalog}.query('{safe_sql}')"
                logger.debug("Routing query through %s: %s", catalog, full_sql)
                rel = self._conn.sql(full_sql)
            else:
                route_catalog = self._find_route_catalog(sql_str)
                if route_catalog:
                    safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                    full_sql = f"FROM {route_catalog}.query('{safe_sql}')"
                    logger.debug("Auto-routing query through %s: %s", route_catalog, full_sql)
                    rel = self._conn.sql(full_sql)
                else:
                    try:
                        rel = self._conn.sql(sql_str)
                    except duckdb.Error as exc:
                        err_msg = str(exc)
                        if "does not exist" in err_msg and "schema" in err_msg.lower():
                            catalog = self._config.attach_alias
                            safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                            full_sql = f"FROM {catalog}.query('{safe_sql}')"
                            logger.info(
                                "Local query failed -- auto-rerouting through %s: %s",
                                catalog, full_sql,
                            )
                            self._refresh_server_databases()
                            rel = self._conn.sql(full_sql)
                        else:
                            raise exc

            return QuackResult(rel)

        except duckdb.Error as exc:
            err_msg = str(exc)
            if "does not exist" in err_msg and "schema" in err_msg.lower():
                hint = (
                    f"\n\nHint: The table or schema may exist on the server side. "
                    f"Try passing database=\"{self._config.attach_alias if self._config else 'remote_server'}\" "
                    f"to route the query through the server:\n"
                    f"  client.query(\"{sql_str}\", database=\"{self._config.attach_alias if self._config else 'remote_server'}\")"
                )
                logger.error("Query failed: %s%s", exc, hint)
                raise RuntimeError(f"Query execution failed: {exc}{hint}") from exc
            logger.error("Query failed: %s", exc)
            raise RuntimeError(f"Query execution failed: {exc}") from exc

    def execute(self, sql_str: str, database: Optional[str] = None) -> int:
        """Execute a SQL statement (INSERT, UPDATE, CREATE, etc.) on the server.

        Args:
            sql_str: The SQL statement.
            database: Optional catalog alias to route the statement through.
                      If not set, auto-routing is attempted.

        Returns:
            Number of rows affected (if available), or -1.
        """
        if not self._attached or not self._config:
            raise RuntimeError("Not connected. Call connect() first.")

        try:
            if database:
                catalog = self._config.attach_alias
                safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                full_sql = f"FROM {catalog}.query('{safe_sql}')"
                logger.debug("Routing execute through %s: %s", catalog, full_sql)
                self._conn.execute(full_sql)
            else:
                route_catalog = self._find_route_catalog(sql_str)
                if route_catalog:
                    safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                    full_sql = f"FROM {route_catalog}.query('{safe_sql}')"
                    logger.debug("Auto-routing execute through %s: %s", route_catalog, full_sql)
                    self._conn.execute(full_sql)
                else:
                    try:
                        self._conn.execute(sql_str)
                    except duckdb.Error as exc:
                        err_msg = str(exc)
                        if "does not exist" in err_msg and "schema" in err_msg.lower():
                            catalog = self._config.attach_alias
                            safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                            full_sql = f"FROM {catalog}.query('{safe_sql}')"
                            logger.info(
                                "Local execute failed -- auto-rerouting through %s: %s",
                                catalog, full_sql,
                            )
                            self._refresh_server_databases()
                            self._conn.execute(full_sql)
                        else:
                            raise exc

            # Try to get row count
            try:
                return self._conn.cursor.rowcount if hasattr(self._conn, 'cursor') and hasattr(self._conn.cursor, 'rowcount') else -1
            except Exception:
                return -1
        except duckdb.Error as exc:
            err_msg = str(exc)
            if "does not exist" in err_msg and "schema" in err_msg.lower():
                hint = (
                    f"\n\nHint: The table or schema may exist on the server side. "
                    f"Try passing database=\"{self._config.attach_alias if self._config else 'remote_server'}\" "
                    f"to route the statement through the server:\n"
                    f"  client.execute(\"{sql_str}\", database=\"{self._config.attach_alias if self._config else 'remote_server'}\")"
                )
                logger.error("Execute failed: %s%s", exc, hint)
                raise RuntimeError(f"Statement execution failed: {exc}{hint}") from exc
            logger.error("Execute failed: %s", exc)
            raise RuntimeError(f"Statement execution failed: {exc}") from exc

    def stateless_query(
        self,
        host: str,
        port: int,
        sql_str: str,
        token: Optional[str] = None,
        disable_ssl: bool = False,
    ) -> QuackResult:
        """Execute a stateless query (no ATTACH needed) via ``quack_query``.

        This does not require calling connect() first.

        Args:
            host: Server hostname.
            port: Server port.
            sql_str: SQL query to execute.
            token: Authentication token (defaults to the client token).
            disable_ssl: Force plain HTTP.

        Returns:
            ``QuackResult`` with format conversion methods.
        """
        uri = f"quack:{host}:{port}"
        token = token or self._token

        try:
            self._conn.execute("LOAD quack;")

            params = {"token": f"'{token}'"}
            if disable_ssl:
                params["disable_ssl"] = "true"

            params_str = ", ".join(f"{k} => {v}" for k, v in params.items())

            # Escape single quotes in SQL
            safe_sql = sql_str.replace("'", "''")
            query_sql = (
                f"FROM quack_query('{uri}', '{safe_sql}', {params_str})"
            )
            rel = self._conn.sql(query_sql)
            return QuackResult(rel)

        except duckdb.Error as exc:
            logger.error("Stateless query failed: %s", exc)
            raise RuntimeError(f"Stateless query failed: {exc}") from exc

    def attach_remote(self, path: str, alias: Optional[str] = None) -> str:
        """Ask the server to attach a database file on its side.

        Args:
            path: Path to the DuckDB database file on the server's filesystem.
            alias: Catalog alias. Defaults to the filename stem.

        Returns:
            The alias used.
        """
        if not self._attached or not self._config:
            raise RuntimeError("Not connected. Call connect() first.")

        if alias is None:
            import os
            alias = os.path.splitext(os.path.basename(path))[0]

        logger.info("Requesting server to attach '%s' as '%s' ...", path, alias)

        catalog = self._config.attach_alias
        attach_sql = f"ATTACH '{path}' AS {alias}"

        try:
            safe_sql = attach_sql.replace(chr(39), chr(39) + chr(39))
            self._conn.execute(f"FROM {catalog}.query('{safe_sql}')")
            logger.info("Server attached '%s' as '%s'.", path, alias)
            self._refresh_server_databases()
            return alias

        except duckdb.Error as exc:
            logger.error("Failed to attach remote database: %s", exc)
            raise RuntimeError(f"Could not attach '{path}' on server: {exc}") from exc

    def detach_remote(self, alias: str) -> None:
        """Ask the server to detach a database file on its side."""
        if not self._attached or not self._config:
            raise RuntimeError("Not connected. Call connect() first.")

        catalog = self._config.attach_alias
        detach_sql = f"DETACH {alias}"

        try:
            safe_sql = detach_sql.replace(chr(39), chr(39) + chr(39))
            self._conn.execute(f"FROM {catalog}.query('{safe_sql}')")
            logger.info("Server detached '%s'.", alias)
            self._server_databases.discard(alias)
            self._refresh_server_databases()

        except duckdb.Error as exc:
            logger.error("Failed to detach remote database: %s", exc)
            raise RuntimeError(f"Could not detach '{alias}' on server: {exc}") from exc

    def list_databases(self) -> list[str]:
        """List attached databases visible on the server."""
        if not self._attached or not self._config:
            raise RuntimeError("Not connected. Call connect() first.")

        try:
            catalog = self._config.attach_alias
            query_sql = "SELECT database_name, path FROM duckdb_databases()"
            safe_sql = query_sql.replace(chr(39), chr(39) + chr(39))
            full_sql = f"FROM {catalog}.query('{safe_sql}')"
            result = self._conn.execute(full_sql)
            rows = result.fetchall()
            return [f"{row[0]} ({row[1]})" for row in rows]
        except duckdb.Error as exc:
            logger.error("Failed to list databases: %s", exc)
            raise RuntimeError(f"Could not list databases on server: {exc}") from exc

    def list_tables(self, database: Optional[str] = None) -> list[dict]:
        """List tables in the specified catalog.

        Args:
            database: Database/catalog alias. If None, uses the connect alias.
                      If server-side, it will be queried through the server.

        Returns:
            List of {schema, table} dicts.
        """
        if not self._attached or not self._config:
            raise RuntimeError("Not connected. Call connect() first.")

        target = database or self._config.attach_alias
        try:
            if database:
                catalog = self._config.attach_alias
                sql_str = (
                    f"SELECT table_schema, table_name FROM {database}.information_schema.tables "
                    "WHERE table_type = 'BASE TABLE' OR table_type = 'VIEW'"
                )
                safe_sql = sql_str.replace(chr(39), chr(39) + chr(39))
                full_sql = f"FROM {catalog}.query('{safe_sql}')"
                result = self._conn.execute(full_sql)
            else:
                result = self._conn.execute(
                    f"SELECT table_schema, table_name FROM {target}.information_schema.tables "
                    "WHERE table_type = 'BASE TABLE' OR table_type = 'VIEW'"
                )
            return [
                {"schema": row[0], "table": row[1]}
                for row in result.fetchall()
            ]
        except duckdb.Error as exc:
            logger.error("Failed to list tables: %s", exc)
            raise RuntimeError(f"Could not list tables in '{target}': {exc}") from exc

    def status(self) -> dict:
        """Return the current client connection status."""
        status = {
            "connected": self._attached,
        }
        if self._config:
            status.update({
                "uri": self._config.uri,
                "attach_alias": self._config.attach_alias,
                "host": self._config.host,
                "port": self._config.port,
                "token_set": self._token is not None,
            })
        return status

    def sql(
        self,
        query: str,
        database: Optional[str] = None,
    ) -> QuackResult:
        """Run a query using this client's existing connection.

        This is an instance method that delegates to ``self.query()``,
        reusing the already-established connection.

        For a one-shot query (creates a temporary client), use
        ``duckdbcs.sql()`` instead.

        Args:
            query: SQL query to execute.
            database: Optional catalog to route the query through the server.

        Returns:
            ``QuackResult`` with format conversion methods.
        """
        return self.query(query, database=database)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Disconnect and close the local DuckDB connection."""
        if self._attached:
            self.disconnect()
        self._conn.close()
        self._cleanup_auto_server()
        logger.info("Client connection closed.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
