"""Keep the test run away from the real setup: its own token directory, and any free port for the bridge."""
import os
import tempfile

os.environ["NDLTD_HOME"] = tempfile.mkdtemp(prefix="ndltd_mcp_test_")
os.environ["NDLTD_BRIDGE_PORT"] = "0"
