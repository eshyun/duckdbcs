"""DuckDB Quack Server implementation.

Starts a DuckDB instance as a Quack server that remote clients can connect to
over HTTP. Supports dynamic database file attachment and custom auth/authorization.
"""

import logging
import os
import signal
import time
from typing import Optional

import duckdb

from duckdbcs.config import (
    ServerConfig,
    load_config,
    load_token,
    DEFAULT_TOKEN_ENVVAR,
    DEFAULT_TOKEN_FILE,
)

logger = logging.getLogger("duckdbcs.server")


class QuackServer:
    """Quack protocol server wrapping a DuckDB instance.

    Usage:
        server = QuackServer(token="my_secret")
        server.start(host="0.0.0.0", port=9494)
        # ... do work ...
        server.stop()
    """

    def __init__(
        self,
        token: Optional[str] = None,
        connection: Optional["duckdb.DuckDBPyConnection"] = None,
    ):
        self._conn = connection or duckdb.connect(":memory:")
        # Token resolution: explicit > env > config file > .quack_secret
        self._token = token or load_token()
        self._config: Optional[ServerConfig] = None
        self._running = False
        self._attached_dbs: dict[str, str] = {}


    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Optional["ServerConfig"] = None) -> "QuackServer":
        """Create a server from a ServerConfig object (or load from persistent config).

        If a ``ServerConfig`` is provided, it will be used directly.
        Otherwise, the persistent config file (``~/.config/duckdbcs/config.toml``)
        is loaded and the ``server`` section is used.

        The returned server is already started and has all ``attach_on_startup``
        databases attached.

        Usage::

            from duckdbcs.server import QuackServer
            server = QuackServer.from_config()
            # server is running and all auto-attach DBs are attached
            print(server.status())
            server.close()
        """
        from duckdbcs.config import load_config as _load_config

        if config is None:
            cfg = _load_config()
            config = cfg.server

        if not config.token:
            token = load_token()
        else:
            token = config.token

        instance = cls(token=token)
        instance.start(
            host=config.host,
            port=config.port,
            allow_other_hostname=config.allow_other_hostname,
            disable_ssl=config.disable_ssl,
        )

        # Auto-attach databases specified in config
        for attach_entry in config.attach_on_startup:
            try:
                instance.attach_database(attach_entry["path"], attach_entry.get("alias"))
            except Exception as exc:
                logger.warning(
                    "Failed to auto-attach '%s': %s",
                    attach_entry.get("path"),
                    exc,
                )

        return instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        host: str = "0.0.0.0",
        port: int = 9494,
        allow_other_hostname: bool = False,
        disable_ssl: bool = False,
    ) -> dict:
        """Start the Quack server on the given host:port.

        Returns the server status dict containing listen URI, URL, and auth token.
        """
        if self._running:
            logger.warning("Server is already running.")
            return self._get_status()

        self._config = ServerConfig(
            host=host,
            port=port,
            token=self._token,
            allow_other_hostname=allow_other_hostname,
            disable_ssl=disable_ssl,
        )

        uri = f"quack:{host}:{port}"
        logger.info("Starting Quack server on %s ...", uri)

        try:
            self._conn.execute("LOAD quack;")

            result = self._conn.execute(
                "CALL quack_serve(?, token := ?, allow_other_hostname := ?)",
                [uri, self._token, allow_other_hostname],
            ).fetchone()

            self._running = True
            status = self._get_status()
            logger.info(
                "Quack server running — listen_uri=%s url=%s auth_token=%s",
                status.get("listen_uri"),
                status.get("url"),
                status.get("auth_token", "(set)"),
            )
            return status

        except duckdb.Error as exc:
            logger.error("Failed to start Quack server: %s", exc)
            raise RuntimeError(f"Could not start Quack server: {exc}") from exc

    def stop(self) -> None:
        """Stop the Quack server gracefully."""
        if not self._running or not self._config:
            logger.warning("Server is not running.")
            return

        uri = f"quack:{self._config.host}:{self._config.port}"
        logger.info("Stopping Quack server on %s ...", uri)

        try:
            self._conn.execute("CALL quack_stop(?);", [uri])
        except duckdb.Error as exc:
            logger.warning("Error during quack_stop: %s", exc)

        self._running = False
        logger.info("Quack server stopped.")

    def attach_database(self, path: str, alias: Optional[str] = None) -> str:
        """Attach a DuckDB database file on the server side.

        Once attached, clients can query tables via ``alias.table_name``.

        Args:
            path: Filesystem path to the DuckDB database file.
            alias: Catalog alias for the attached database. Defaults to the filename stem.

        Returns:
            The alias used.
        """
        if alias is None:
            alias = os.path.splitext(os.path.basename(path))[0]

        logger.info("Attaching database '%s' as '%s' ...", path, alias)
        self._conn.execute(f"ATTACH '{path}' AS {alias};")
        self._attached_dbs[alias] = path
        return alias

    def detach_database(self, alias: str) -> None:
        """Detach a previously attached database by its alias."""
        if alias not in self._attached_dbs:
            raise ValueError(f"Database alias '{alias}' is not attached.")

        logger.info("Detaching database '%s' ...", alias)
        self._conn.execute(f"DETACH {alias};")
        del self._attached_dbs[alias]

    def set_authentication(self, macro_sql: str) -> None:
        """Override the default authentication callback with a custom MACRO.

        The macro must accept (sid, client_token, server_token) -> BOOLEAN.

        Example:
            server.set_authentication(
                \"CREATE MACRO my_auth(sid, ct, st) AS (ct IN ('token1', 'token2'));\"
            )
        """
        self._conn.execute(macro_sql)
        macro_name = self._extract_macro_name(macro_sql)
        self._conn.execute(
            "SET GLOBAL quack_authentication_function = ?;", [macro_name]
        )
        logger.info("Authentication set to macro '%s'.", macro_name)

    def set_authorization(self, macro_sql: str) -> None:
        """Override the default authorization callback with a custom MACRO.

        The macro must accept (connection_id, query) -> BOOLEAN.

        Example:
            server.set_authorization(
                \"CREATE MACRO read_only(sid, q) AS regexp_matches(upper(trim(q)), '^(SELECT|FROM|WITH)\\\\b');\"
            )
        """
        self._conn.execute(macro_sql)
        macro_name = self._extract_macro_name(macro_sql)
        self._conn.execute(
            "SET GLOBAL quack_authorization_function = ?;", [macro_name]
        )
        logger.info("Authorization set to macro '%s'.", macro_name)

    def status(self) -> dict:
        """Return the current server status."""
        return self._get_status()

    def list_databases(self) -> list[str]:
        """Return the list of attached database aliases (server-side)."""
        return list(self._attached_dbs.keys())

    def close(self) -> None:
        """Stop the server (if running) and close the DuckDB connection."""
        if self._running:
            self.stop()
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_token(self) -> Optional[str]:
        """Try to load the token from environment or a secret file. (legacy)"""
        return load_token()

    def _get_status(self) -> dict:
        status = {
            "running": self._running,
            "attached_databases": self._attached_dbs.copy(),
        }
        if self._config:
            status.update({
                "listen_uri": f"quack:{self._config.host}:{self._config.port}",
                "url": f"http://{self._config.host}:{self._config.port}",
                "auth_token": self._config.token or "(auto-generated)",
                "allow_other_hostname": self._config.allow_other_hostname,
            })
        return status

    @staticmethod
    def _extract_macro_name(sql: str) -> str:
        """Extract the macro name from a CREATE MACRO statement."""
        import re
        match = re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?MACRO\s+(\w+)", sql, re.IGNORECASE)
        if match:
            return match.group(1)
        raise ValueError("Could not extract macro name from SQL.")

    @property
    def pid(self) -> Optional[int]:
        """Return the PID of the server process (useful in background mode)."""
        return os.getpid()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def run_server_forever(
    host: str = "0.0.0.0",
    port: int = 9494,
    token: Optional[str] = None,
    allow_other_hostname: bool = False,
    databases: Optional[list[tuple[str, str]]] = None,
):
    """Run a Quack server in the foreground until interrupted.

    Args:
        host: Host to bind to.
        port: Port to listen on.
        token: Authentication token. Auto-generated if not given.
        allow_other_hostname: Allow connections from non-localhost addresses.
        databases: Optional list of (path, alias) pairs to attach at startup.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    server = QuackServer(token=token)
    shutdown_event = False

    def _signal_handler(signum, frame):
        nonlocal shutdown_event
        logger.info("Received signal %s, shutting down ...", signum)
        shutdown_event = True

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        server.start(
            host=host,
            port=port,
            allow_other_hostname=allow_other_hostname,
        )

        # Attach any databases specified at startup
        if databases:
            for path, alias in databases:
                try:
                    server.attach_database(path, alias)
                except Exception as exc:
                    logger.error("Failed to attach '%s' as '%s': %s", path, alias, exc)

        logger.info("Server is ready. Press Ctrl+C to stop.")
        while not shutdown_event:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        server.close()
        logger.info("Server shut down complete.")
