"""
Example: Dynamic Database Attach
Demonstrates how to dynamically attach databases on the server side.

This shows the "Master Server" pattern where the server acts as a gateway,
attaching different database files on demand.

Usage:
    # Terminal 1: Start the server
    python examples/basic_server.py

    # Terminal 2: Run this client
    python examples/dynamic_attach.py
"""
import logging
import os
import tempfile
from duckdbcs import QuackClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ============================================================
# NOTE: Dynamic ATTACH works by having the client send an ATTACH
# command to the server via the server's query() table function.
# The server executes the ATTACH locally, making the database
# available as a catalog that all clients can see.
# ============================================================

def main():
    client = QuackClient(token="my_secure_token_123")

    # Or, auto-connect in one step:
    # client = QuackClient(token="my_secure_token_123", host="localhost", port=9494)

    try:
        client.connect(host="localhost", port=9494, attach_alias="remote_server")
        print("Connected to server.")

        # Check what databases are available on the server
        print(f"\nInitial databases: {client.list_databases()}")

        # --- Simulate: Create a sample database file for attaching ---
        # In production, this file would already exist on the server's filesystem.
        # Here we create one for demonstration purposes.
        import duckdb
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, "project_data.db")

        con = duckdb.connect(db_path)
        con.execute("CREATE TABLE users AS SELECT * FROM (VALUES (1, 'Alice'), (2, 'Bob')) t(id, name)")
        con.execute("CREATE TABLE logs AS SELECT * FROM (VALUES (1, 'login'), (2, 'logout')) t(id, action)")
        con.close()
        print(f"\nCreated sample database at: {db_path}")

        # --- Request the server to attach this database file ---
        alias = client.attach_remote(db_path, "project_data")
        print(f"Server attached '{db_path}' as '{alias}'.")

        # Now we can query the newly attached database
        results = client.query("SELECT * FROM project_data.users")
        print(f"\nUsers from dynamically attached DB: {results}")

        results = client.query("SELECT * FROM project_data.logs")
        print(f"Logs from dynamically attached DB: {results}")

        # The server now has this database in its catalog.
        # Other clients connected to the same server can also see it.
        print(f"\nDatabases after attach: {client.list_databases()}")

        # --- Cleanup: detach the database ---
        client.detach_remote("project_data")
        print(f"\nDetached 'project_data'. Databases: {client.list_databases()}")

        # Cleanup temp file
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
