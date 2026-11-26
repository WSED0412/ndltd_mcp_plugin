"""Entry point: local stdio transport only (local use by design)."""
from .server import mcp, start_bridge_early


def main() -> None:
    start_bridge_early()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
