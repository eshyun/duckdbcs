"""
Example: Basic Quack Client
Connect to a Quack server and run queries.

Usage:
    # First start the server (in another terminal):
    #   python examples/basic_server.py
    #
    # Then run this client:
    #   python examples/basic_client.py
"""
import logging
from duckdbcs import QuackClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Create a client with the same token as the server
client = QuackClient(token="my_secure_token_123")

# Or, auto-connect in one step:
# client = QuackClient(token="my_secure_token_123", host="localhost", port=9494)

try:
    # Connect to the server
    client.connect(host="localhost", port=9494, attach_alias="remote_server")
    print(f"Status: {client.status()}")

    # List databases visible on the server
    dbs = client.list_databases()
    print(f"Databases: {dbs}")

    # List tables on the remote server
    tables = client.list_tables()
    print(f"Tables: {tables}")

    # Execute a simple query
    results = client.query("SELECT 42 AS answer, 'hello' AS greeting")
    print(f"Query result: {results}")

    # Create a table and insert data
    # (these operations happen on the server)
    # client.execute("CREATE TABLE remote_server.test AS SELECT * FROM range(5)")

    # Stateless query (no ATTACH needed, just send SQL)
    results = client.stateless_query("localhost", 9494, "SELECT 1 AS col")
    print(f"Stateless result: {results}")

except Exception as e:
    print(f"Error: {e}")
finally:
    client.close()
