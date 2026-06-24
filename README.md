# brickvar

Resolve configuration variables for Databricks jobs from literals, environment
variables, and Azure Key Vault secrets, and substitute them into JSON config files.

`brickvar` reads a JSON "variables" file in which each entry is one of:

- a **literal** string (which may reference other variables with `${VAR}`),
- an **environment variable** reference — `{"env": "NAME"}`, or
- a **Databricks / Azure Key Vault secret** — `{"scope": ..., "key": ..., "base"?: ...}`,
  read through the Databricks `dbutils.secrets` API.

Resolution is two-pass, so a secret's `scope`/`key` can themselves reference
already-resolved literal or environment values. It can also substitute `${VAR}`
placeholders into any JSON file, leaving unknown placeholders intact.

## Install

```bash
pip install brickvar
```

## Usage

```python
from brickvar import configure_json

# Read a JSON file and substitute its ${VAR} placeholders from a variables file,
# in one call. dbutils is provided by the Databricks runtime and is required only
# when the variables file contains Key Vault secret entries.
spec = configure_json("spec.json", dbutils=dbutils, var_filepath="variables.json")
```

For finer-grained control, use the `ConfigManager` class directly:

```python
from brickvar import ConfigManager

cfg = ConfigManager(dbutils=dbutils)

# Resolve a variables file to a dict.
variables = cfg.read_variables("variables.json")

# Read a JSON file and substitute its ${VAR} placeholders from a variables file.
spec = cfg.read_json("spec.json", "variables.json")
```

Example `variables.json`:

```json
{
  "SECRET_SCOPE": { "env": "SECRET_SCOPE" },
  "HOST": "example.documents.azure.us",
  "CLIENT_ID": { "scope": "${SECRET_SCOPE}", "key": "SP-CLIENT-ID" },
  "STORAGE": { "scope": "kv", "key": "ACCOUNT", "base": "abfss://data@{}/curated" }
}
```

- `HOST` is a literal.
- `SECRET_SCOPE` comes from the `SECRET_SCOPE` environment variable.
- `CLIENT_ID` is a secret whose scope is filled from the resolved `SECRET_SCOPE`.
- `STORAGE` is a secret wrapped by its `base` format string.

## Development

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

[Apache-2.0](LICENSE)
