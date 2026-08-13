# brickvar

Resolve configuration variables for Databricks jobs from literals, environment
variables, and Azure Key Vault secrets, and substitute them into JSON config files.

`brickvar` reads a JSON "variables" file in which each entry is one of:

- a **literal** string (which may reference other variables with `${VAR}`),
- **`null`**, which substitutes a JSON `null` into the config file,
- an **environment variable** reference — `{"env": "NAME"}`,
- a **Databricks / Azure Key Vault secret** — `{"scope": ..., "key": ..., "base"?: ...}`,
  read through the Databricks `dbutils.secrets` API, or
- a **counter sequence** — `{"seq": ..., "count": ..., "start"?: ..., "step"?: ...,
  "sep"?: ..., "as"?: ...}`, which expands one variable into many numbered values.

Resolution is two-pass, so a secret's `scope`/`key` can themselves reference
already-resolved literal or environment values. It can also substitute `${VAR}`
placeholders into any JSON file, leaving unknown placeholders intact.

A `null` variable substitutes a real JSON `null`. Because substitution is textual,
its placeholder must be a complete string value — `"${VAR}"` becomes `null` (quotes
and all). A null variable embedded in a larger string (`"prefix-${VAR}"`) cannot
become null and is left intact with a warning.

### Counter sequences

`seq` is a `str.format` template whose `{i}` field carries any zero-padding, and is
itself `${VAR}`-substituted first so it may reference an earlier variable. The counter
runs `count` values (required, positive) from `start` (default `1`) in steps of `step`
(default `1`). `as` picks the output form:

| entry | result |
| --- | --- |
| `{"seq": "ABD{i:02d}", "count": 3}` | `"ABD01, ABD02, ABD03"` — one string joined with `sep` (default `", "`) |
| `{"seq": "ABD{i:02d}", "count": 3, "as": "array"}` | `["ABD01", "ABD02", "ABD03"]` — **spliced** into the enclosing JSON array |

With `"as": "array"`, a config `["OTHER", "${TABLE_IDS}"]` becomes
`["OTHER", "ABD01", "ABD02", "ABD03"]` — sibling elements, not a nested sub-array.
Like a `null` variable, it acts only on a complete `"${VAR}"` string value; embedded in
a larger string it is left intact with a warning.

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

To merge several config files (and/or several variables files) in one call, use
`configure_jsons`:

```python
from brickvar import configure_jsons

# Variables files are merged *before* resolution, so a variable in one file may
# reference one defined in an earlier file. The config files are deep-merged: nested
# objects merge key by key, lists concatenate (a later file's items are appended),
# and a scalar leaf takes the last file's value (an override that discards a
# differing value is logged as a warning).
spec = configure_jsons(
    ["base.json", "prod.json"],
    dbutils=dbutils,
    var_filepaths=["base.variables.json", "prod.variables.json"],
)
```

### Default variables

Both functions accept a `defaults` mapping of variable specs supplied in code. It is a
**base layer**: a name a variables file also defines takes the file's value, and one no
file defines is added. Entries take the same forms as a file's, and `defaults` may be
used on its own, with no variables file at all:

```python
# HOST comes from variables.json if it defines one, otherwise from the default below;
# PORT is not in the file, so the default applies.
spec = configure_json(
    "spec.json",
    dbutils=dbutils,
    var_filepath="variables.json",
    defaults={"HOST": "localhost", "PORT": "443"},
)

# Defaults alone — no variables file.
spec = configure_json("spec.json", defaults={"HOST": "localhost"})
```

Because defaults are merged first, a file entry may reference a default with `${VAR}`,
while a default that references a file-only variable keeps its placeholder intact. A
variables file that redefines an already-supplied name is logged as a warning, whether
that name came from a default or from an earlier variables file.

For finer-grained control, use the `VariableResolver` class directly:

```python
from brickvar import VariableResolver

cfg = VariableResolver(dbutils=dbutils)

# Resolve a variables file to a dict (defaults are optional here too).
variables = cfg.read_variables("variables.json", defaults={"PORT": "443"})

# Read a JSON file and substitute its ${VAR} placeholders from a variables file.
spec = cfg.read_json("spec.json", "variables.json")
```

Example `variables.json`:

```json
{
  "SECRET_SCOPE": { "env": "SECRET_SCOPE" },
  "HOST": "example.documents.azure.us",
  "PROXY": null,
  "CLIENT_ID": { "scope": "${SECRET_SCOPE}", "key": "SP-CLIENT-ID" },
  "STORAGE": { "scope": "kv", "key": "ACCOUNT", "base": "abfss://data@{}/curated" }
}
```

- `HOST` is a literal.
- `SECRET_SCOPE` comes from the `SECRET_SCOPE` environment variable.
- `PROXY` is `null` — a `"${PROXY}"` placeholder becomes a JSON `null`.
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
