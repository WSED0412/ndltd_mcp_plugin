"""Entry point: local stdio transport only (local use by design)."""
from .server import mcp


def main() -> None:
    # The bridge port is not taken at start-up: the first tool call binds it and it is released when idle, so
    # several Claude windows can each run an ndltd-mcp without blocking one another (see bridge.IDLE_RELEASE).
    mcp.run("stdio")


if __name__ == "__main__":
    main()
