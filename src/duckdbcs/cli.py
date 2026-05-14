

"""DuckDB Quack CLI — command-line interface for server and client.

Usage:
    duckdbcs server start --host 0.0.0.0 --port 9494 --token "my_token"
    duckdbcs client connect localhost --port 9494 --token "my_token"
    duckdbcs client query "SELECT 42"
    duckdbcs config show
    duckdbcs config set server.host 0.0.0.0
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from duckdbcs.client import QuackClient
from duckdbcs.config import (
    load_config,
    save_config,
    show_config_path,
    DuckDBConfig,
)
from duckdbcs.server import run_server_forever

app = typer.Typer(
    name="duckdbcs",
    help="DuckDB Quack Client/Server — remote protocol for concurrent access",
    no_args_is_help=True,
)
server_app = typer.Typer(name="server", help="Manage a Quack server", no_args_is_help=True)
client_app = typer.Typer(name="client", help="Connect to a Quack server and run queries", no_args_is_help=True)
config_app = typer.Typer(
    name="config",
    help="Manage persistent configuration (~/.config/duckdbcs/config.toml)",
    no_args_is_help=True,
)

app.add_typer(server_app)
app.add_typer(client_app)
app.add_typer(config_app)

logger = logging.getLogger("duckdbcs.cli")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
    )


def _auto_connect(
    client: QuackClient,
    host: str = "localhost",
    port: int = 9494,
    token: Optional[str] = None,
    alias: str = "remote_server",
) -> None:
    """Connect if not already connected."""
    if not client.status()["connected"]:
        client.connect(host=host, port=port, attach_alias=alias)


def _print_results(results) -> None:
    """Pretty-print query results as a table.

    Accepts ``list[dict]``, ``QuackResult``, or any iterable of dicts.
    """
    if not results:
        print("(no results)")
        return
    columns = list(results[0].keys())
    col_widths = {
        c: max(len(str(c)), max((len(str(r[c])) for r in results), default=0))
        for c in columns
    }
    header = " | ".join(c.ljust(col_widths[c]) for c in columns)
    sep = "-+-".join("-" * col_widths[c] for c in columns)
    print(header)
    print(sep)
    for row in results:
        print(" | ".join(str(row[c]).ljust(col_widths[c]) for c in columns))


def _resolve_token(value: Optional[str]) -> Optional[str]:
    """Resolve token: explicit > env var > persistent config > .quack_secret."""
    if value:
        return value
    import os
    env_token = os.environ.get("QUACK_TOKEN")
    if env_token:
        return env_token
    cfg = load_config()
    return cfg.resolve_token()


def _parse_attach_pairs(pairs: Optional[list[str]]) -> list[tuple[str, str]]:
    """Parse --attach path:alias pairs."""
    result: list[tuple[str, str]] = []
    if not pairs:
        return result
    for pair in pairs:
        if ":" in pair:
            path, alias = pair.split(":", 1)
            result.append((path, alias))
        else:
            result.append((pair, Path(pair).stem))
    return result


# ===========================================================================
# CONFIG COMMANDS
# ===========================================================================


@config_app.command("show", help="Show current configuration")
def config_show():
    """Display the config file path and its current contents."""
    path = show_config_path()
    print(f"Config file: {path}")
    if path.exists():
        print(f"\n--- {path} ---")
        print(path.read_text().strip())
    else:
        print("(file does not exist yet - using defaults)")

    cfg = load_config()
    print(f"\n--- Effective config (env + file merged) ---")
    print(f"Server token: {'***set***' if cfg.server.token else '(not set)'}")
    print(f"Server host: {cfg.server.host}")
    print(f"Server port: {cfg.server.port}")
    print(f"Server allow_other_hostname: {cfg.server.allow_other_hostname}")
    print(f"Client host: {cfg.client.host}")
    print(f"Client port: {cfg.client.port}")
    print(f"Client attach_alias: {cfg.client.attach_alias}")
    resolved_token = cfg.resolve_token()
    print(f"Resolved token: {'***set***' if resolved_token else '(not set)'}")
    if cfg.server.attach_on_startup:
        print(f"\nServer auto-attach databases (from config):")
        for entry in cfg.server.attach_on_startup:
            print(f"  {entry.get('alias', '?')}: {entry.get('path', '?')}")
    if cfg.client.attach_on_startup:
        print(f"\nClient auto-attach databases (from config):")
        for entry in cfg.client.attach_on_startup:
            print(f"  {entry.get('alias', '?')}: {entry.get('path', '?')}")

@config_app.command("init", help="Create a default config file")
def config_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
):
    path = show_config_path()
    if path.exists() and not force:
        print(f"Config already exists at {path}")
        print("Use --force to overwrite.")
        raise typer.Exit(code=1)
    cfg = DuckDBConfig()
    save_config(cfg)
    print(f"Created default config at {path}")


@config_app.command("set", help="Set a config value")
def config_set(
    key: str = typer.Argument(..., help="Config key (e.g., server.host, client.port)"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a configuration value and persist it.

    Key format: <section>.<field>
    Sections: server, client

    Examples:
      config set server.port 9494
      config set server.token my_secure_token
      config set client.host myserver.local
    """
    if "." not in key:
        print("Error: key must be in format <section>.<field> (e.g., server.host)")
        raise typer.Exit(code=1)

    section, field = key.split(".", 1)
    existing = load_config()
    cfg = DuckDBConfig()
    cfg.server = existing.server
    cfg.client = existing.client

    # Type coercion
    if value.lower() in ("true", "false"):
        typed_value = value.lower() == "true"
    else:
        try:
            typed_value = int(value)
        except ValueError:
            typed_value = value

    if section == "server":
        if hasattr(cfg.server, field):
            setattr(cfg.server, field, typed_value)
        else:
            print(f"Error: unknown server field '{field}'")
            raise typer.Exit(code=1)
    elif section == "client":
        if hasattr(cfg.client, field):
            setattr(cfg.client, field, typed_value)
        else:
            print(f"Error: unknown client field '{field}'")
            raise typer.Exit(code=1)
    else:
        print(f"Error: unknown section '{section}'. Use 'server' or 'client'.")
        raise typer.Exit(code=1)

    save_config(cfg)
    path = show_config_path()
    print(f"Set config.{key} = {typed_value}")
    print(f"Saved to {path}")


@config_app.command("path", help="Show config file path")
def config_path():
    print(show_config_path())


@config_app.command("remove", help="Remove the config file")
def config_remove(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    path = show_config_path()
    if not path.exists():
        print("Config file does not exist.")
        return
    if not yes:
        confirm = typer.confirm(f"Delete {path}?")
        if not confirm:
            print("Aborted.")
            return
    path.unlink()
    print(f"Removed {path}")


@config_app.command("edit", help="Open config file in default editor")
def config_edit():
    import os
    import subprocess

    path = show_config_path()
    if not path.exists():
        cfg = DuckDBConfig()
        save_config(cfg)
        print(f"Created default config at {path}")

    editor = os.environ.get("EDITOR", "nano")
    subprocess.call([editor, str(path)])


@config_app.command("add-attach", help="Add a database to auto-attach on server start")
def config_add_attach(
    path: str = typer.Argument(..., help="Path to the DuckDB database file"),
    alias: Optional[str] = typer.Option(None, "--as", help="Catalog alias"),
):
    """Add a database to the server's auto-attach list.

    Example:
      config add-attach /Users/euhyun/.cache/stock-data/listings.duckdb --as listings
      config add-attach /data/analytics.db --as analytics
    """
    cfg = load_config()
    if alias is None:
        alias = Path(path).stem

    # Check for duplicate alias
    for entry in cfg.server.attach_on_startup:
        if entry.get("alias") == alias:
            print(f"Alias '{alias}' already exists.")
            print(f"Use 'config remove-attach {alias}' first.")
            raise typer.Exit(code=1)

    cfg.server.attach_on_startup.append({"path": path, "alias": alias})
    save_config(cfg)
    print(f"Added auto-attach: {alias} -> {path}")
    print(f"This database will be attached automatically on 'duckdbcs server start'.")


@config_app.command("remove-attach", help="Remove a database from auto-attach list")
def config_remove_attach(
    alias: str = typer.Argument(..., help="Catalog alias to remove"),
):
    """Remove a database from the server's auto-attach list."""
    cfg = load_config()
    initial_len = len(cfg.server.attach_on_startup)
    cfg.server.attach_on_startup = [
        e for e in cfg.server.attach_on_startup if e.get("alias") != alias
    ]
    if len(cfg.server.attach_on_startup) == initial_len:
        print(f"No auto-attach entry found with alias '{alias}'.")
        raise typer.Exit(code=1)
    save_config(cfg)
    print(f"Removed auto-attach: {alias}")



@config_app.command("add-client-attach", help="Add a database to client auto-attach on connect")
def config_add_client_attach(
    path: str = typer.Argument(..., help="Path to the DuckDB database file on the server"),
    alias: Optional[str] = typer.Option(None, "--as", help="Catalog alias"),
):
    cfg = load_config()
    if alias is None:
        alias = Path(path).stem
    for entry in cfg.client.attach_on_startup:
        if entry.get("alias") == alias:
            print(f"Client alias '{alias}' already exists.")
            print(f"Use 'config remove-client-attach {alias}' first.")
            raise typer.Exit(code=1)
    cfg.client.attach_on_startup.append({"path": path, "alias": alias})
    save_config(cfg)
    print(f"Added client auto-attach: {alias} -> {path}")
    print(f"This database will be attached automatically on 'duckdbcs client connect'.")


@config_app.command("remove-client-attach", help="Remove a database from client auto-attach list")
def config_remove_client_attach(
    alias: str = typer.Argument(..., help="Catalog alias to remove"),
):
    cfg = load_config()
    initial_len = len(cfg.client.attach_on_startup)
    cfg.client.attach_on_startup = [
        e for e in cfg.client.attach_on_startup if e.get("alias") != alias
    ]
    if len(cfg.client.attach_on_startup) == initial_len:
        print(f"No client auto-attach entry found with alias '{alias}'.")
        raise typer.Exit(code=1)
    save_config(cfg)
    print(f"Removed client auto-attach: {alias}")

# ===========================================================================
# SERVER COMMANDS
# ===========================================================================


@server_app.command("start", help="Start the Quack server")
def server_start(
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to"),
    port: int = typer.Option(9494, "--port", help="Port to listen on"),
    token: Optional[str] = typer.Option(
        None, "--token", help="Auth token (default: QUACK_TOKEN env or auto-generated)",
    ),
    allow_external: Optional[bool] = typer.Option(
        None, "--allow-external/--no-allow-external",
        help="Allow non-localhost connections (requires reverse proxy). Default: from config file or False.",
    ),
    attach: Optional[list[str]] = typer.Option(
        None, "--attach", metavar="PATH:ALIAS",
        help="Attach a DB at startup (format: path:alias, can be repeated)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    """Start the Quack server.

    Auto-attach databases from two sources:
    1. Persistent config file (config add-attach ...)
    2. CLI --attach arguments (override/add to config)
    """
    _setup_logging(verbose)
    cfg = load_config()
    token = _resolve_token(token)

    # Resolve allow_external: CLI explicit > config file > default False
    if allow_external is None:
        allow_external = cfg.server.allow_other_hostname

    # Load auto-attach databases from persistent config (reuse cfg)
    config_databases = [
        (e["path"], e["alias"])
        for e in cfg.server.attach_on_startup
    ]
    

    # CLI --attach args supplement config file entries
    cli_databases = _parse_attach_pairs(attach) if attach else []
    databases = config_databases + cli_databases

    run_server_forever(
        host=host,
        port=port,
        token=token,
        allow_other_hostname=allow_external,
        databases=databases,
    )


@server_app.command("stop", help="Stop the Quack server")
def server_stop():
    print("Use the Python API to stop the server:")
    print("  from duckdbcs import QuackServer")
    print("  with QuackServer(token='...') as server:")
    print("      server.start()")
    print("      server.stop()")


@server_app.command("status", help="Show server status")
def server_status():
    print("Use the Python API to check server status:")
    print("  from duckdbcs import QuackServer")
    print("  with QuackServer(token='...') as server:")
    print("      server.start()")
    print("      print(server.status())")


@server_app.command("attach", help="Attach a database file (from Python API)")
def server_attach(
    path: str = typer.Argument(..., help="Path to the DuckDB database file"),
    alias: Optional[str] = typer.Option(None, "--as", help="Catalog alias"),
):
    print("Use the Python API to attach databases:")
    print("  from duckdbcs import QuackServer")
    print("  with QuackServer(token='...') as server:")
    print("      server.start()")
    print(f"      server.attach_database('{path}', '{alias or Path(path).stem}')")


@server_app.command("detach", help="Detach a database (from Python API)")
def server_detach(alias: str = typer.Argument(..., help="Catalog alias to detach")):
    print("Use the Python API to detach databases:")
    print("  from duckdbcs import QuackServer")
    print("  with QuackServer(token='...') as server:")
    print("      server.start()")
    print(f"      server.detach_database('{alias}')")


# ===========================================================================
# CLIENT COMMANDS
# ===========================================================================




@client_app.command("connect", help="Connect to a Quack server via ATTACH")
def client_connect(
    host: str = typer.Argument("localhost", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    alias: str = typer.Option("remote_server", "--alias", help="Local catalog alias"),
    attach: Optional[list[str]] = typer.Option(
        None, "--attach", metavar="PATH:ALIAS",
        help="Request server to attach a DB after connect (format: path:alias, can be repeated)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        client.connect(host=host, port=port, attach_alias=alias)
        print(f"Connected to Quack server at {host}:{port} as '{alias}'.")
        print(f"Tables are accessible via: {alias}.<table_name>")

        # Load client auto-attach databases from config
        cfg = load_config()
        config_attach_list = [
            (e["path"], e["alias"])
            for e in cfg.client.attach_on_startup
        ]

        # CLI --attach args supplement config file entries
        cli_attach_list = _parse_attach_pairs(attach) if attach else []
        all_attaches = config_attach_list + cli_attach_list

        for path, alias_name in all_attaches:
            try:
                result = client.attach_remote(path, alias_name)
                print(f"  Attached: {result} -> {path}")
            except Exception as attach_exc:
                print(f"  Failed to attach '{path}' as '{alias_name}': {attach_exc}", file=sys.stderr)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()
@client_app.command("query", help="Execute a SELECT query")
def client_query(
    sql: str = typer.Argument(..., help="SQL query"),
    database: Optional[str] = typer.Option(None, "--database", help="Database alias"),
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        results = client.query(sql, database=database)
        _print_results(results)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("execute", help="Execute a SQL statement (INSERT, UPDATE, CREATE, etc.)")
def client_execute(
    sql: str = typer.Argument(..., help="SQL statement"),
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        client.execute(sql)
        print("Statement executed successfully.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("stateless", help="Execute a stateless query (no ATTACH needed)")
def client_stateless(
    sql: str = typer.Argument(..., help="SQL query"),
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        results = client.stateless_query(host=host, port=port, sql=sql, token=token)
        _print_results(results)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("attach", help="Request server to attach a database file")
def client_attach(
    path: str = typer.Argument(..., help="Path on the server filesystem"),
    alias: Optional[str] = typer.Option(None, "--as", help="Catalog alias"),
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        result = client.attach_remote(path, alias)
        print(f"Server attached '{path}' as '{result}'.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("detach", help="Request server to detach a database")
def client_detach(
    alias: str = typer.Argument(..., help="Catalog alias to detach"),
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        client.detach_remote(alias)
        print(f"Server detached '{alias}'.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("tables", help="List tables on the server")
def client_tables(
    database: Optional[str] = typer.Option(None, "--database", help="Database alias"),
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        tables = client.list_tables(database=database)
        if tables:
            print(f"{'Schema':<20} {'Table':<30}")
            print("-" * 50)
            for t in tables:
                print(f"{t['schema']:<20} {t['table']:<30}")
        else:
            print("(no tables found)")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("databases", help="List databases visible on the server")
def client_databases(
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        dbs = client.list_databases()
        if dbs:
            for db in dbs:
                print(db)
        else:
            print("(no databases)")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("disconnect", help="Disconnect from the server")
def client_disconnect(
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        client.disconnect()
        print("Disconnected.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


@client_app.command("status", help="Show connection status")
def client_status(
    host: str = typer.Option("localhost", "--host", help="Server hostname"),
    port: int = typer.Option(9494, "--port", help="Server port"),
    token: Optional[str] = typer.Option(None, "--token", help="Auth token"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
):
    _setup_logging(verbose)
    token = _resolve_token(token)
    client = QuackClient(token=token)
    try:
        _auto_connect(client, host, port, token)
        status = client.status()
        for k, v in status.items():
            print(f"{k}: {v}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(code=1)
    finally:
        client.close()


# ===========================================================================
# Entry point
# ===========================================================================

def main() -> None:
    """Typer CLI entry point."""
    app()


if __name__ == "__main__":
    main()
