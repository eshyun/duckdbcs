"""
Example: Basic Quack Server
Start a Quack server that clients can connect to.

Usage:
    python examples/basic_server.py
"""
import logging
from duckdbcs import QuackServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Create a server with an explicit token
server = QuackServer(token="my_secure_token_123")

try:
    # Start the server (localhost-only for safety)
    status = server.start(host="0.0.0.0", port=9494, allow_other_hostname=True)
    print(f"Server status: {status}")

    # Attach a database file dynamically (optional)
    # server.attach_database("data/project_a.db", "proj_a")

    print("\nServer running. Press Ctrl+C to stop.")
    import time
    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\nShutting down...")
finally:
    server.close()
