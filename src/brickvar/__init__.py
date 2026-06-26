"""brickvar: resolve Databricks config variables and substitute them into JSON files."""

from brickvar.config import VariableResolver, configure_json

__all__ = ["VariableResolver", "configure_json"]
__version__ = "0.0.4"
