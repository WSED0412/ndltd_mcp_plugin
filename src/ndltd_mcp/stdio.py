"""Entry point: local stdio transport only (local use by design)."""
from .server import mcp


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
