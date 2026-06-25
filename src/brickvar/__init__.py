"""brickvar: resolve Databricks config variables and substitute them into JSON files."""

from brickvar.config import ConfigManager, configure_json

__all__ = ["ConfigManager", "configure_json"]
__version__ = "0.0.3"
