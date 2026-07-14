# brickvar — project context

`brickvar` is a small, standalone, public Python package that resolves configuration
variables for Databricks jobs (literals, environment variables, and Azure Key Vault
secrets via `dbutils.secrets`) and substitutes them into JSON config files. It has
**no runtime dependencies** — `dbutils` is injected by the caller.

> Internal/process notes and references to private repos live in `CLAUDE.local.md`
> (gitignored), not here.

## Public API

- **`VariableResolver`** — `read_variables(filepath)`, `read_json(filepath, var_filepath=None)`,
  and `read_jsons(filepaths, var_filepaths=None)` (since 0.0.4 — merges multiple files).
  (Renamed from `ConfigManager`, since 0.0.4 — no back-compat alias.)
- Module-level helper **`unresolved_variables(content, provided)`**.
- Package-level convenience **`configure_json(filepath, *, dbutils=None, var_filepath=None)`**
  (since 0.0.2) — instantiates a `VariableResolver` and calls `read_json` in one step.
- Package-level **`configure_jsons(filepaths, *, dbutils=None, var_filepaths=None)`**
  (since 0.0.4) — merges several config files and/or variables files in one call. Variables
  files are merged **before** resolution (so a variable may reference one from an earlier
  file; name conflicts: later file wins, with a warning). Config files are **deep**-merged
  (since 0.0.5): nested objects merge key by key, lists **concatenate** (later file's items
  appended), and a scalar leaf takes the **last** file's value. A key given incompatible
  shapes by two files (e.g. a list vs. an object) raises `ValueError` — except a JSON `null`
  opposite a list/object, which is allowed as a last-wins override (with a warning). Logging
  by conflict kind: list append and equal-value redefinition → `info`; any override that
  discards a differing value (scalar, or null↔container) → `warning`. It is a thin wrapper over `VariableResolver.read_jsons`, where
  the merge logic lives (reusing the `_resolve_variables` / `_substitute` / `_deep_merge`
  internals), mirroring `configure_json` → `read_json`.

Prefer `configure_json` for the common single-file case and `configure_jsons` for the
multi-file case; use `VariableResolver` directly when you need `read_variables` or want to
reuse an instance.

### Variables-file entry forms

Each entry maps a name to one of:

- a **literal** string (may reference earlier variables with `${VAR}`),
- **`null`** (since 0.0.3) — substitutes a real JSON `null`. The placeholder must be a
  complete string value `"${VAR}"`; an embedded `"prefix-${VAR}"` can't become null and is
  left intact with a warning,
- an **environment variable** — `{"env": "NAME"}`,
- a **Databricks / Azure Key Vault secret** — `{"scope": ..., "key": ..., "base"?: ...}`,
  read via `dbutils.secrets`.
- a **counter sequence** (since 0.0.6) — `{"seq": ..., "count": ..., "start"?: ..., "step"?: ...,
  "sep"?: ..., "as"?: ...}`. `seq` is a `str.format` template whose `{i}` counter carries any
  zero-padding (`"ABD{i:02d}"` → `ABD01`); it is `${VAR}`-substituted first, so it may reference an
  earlier variable. The counter runs `count` values from `start` (default 1) in steps of `step`
  (default 1). **`as`** (since 0.0.7) selects the output form: `"string"` (the default) joins the
  items with `sep` (default `", "`) into one delimited **string**, e.g.
  `{"seq": "ABD{i:02d}", "count": 3}` → `"ABD01, ABD02, ABD03"`; `"array"` **splices** the items as
  sibling elements into the enclosing JSON array (`sep` unused) — so a config
  `["OTHER", "${TABLE_IDS}"]` with `{"seq": "ABD{i:02d}", "count": 3, "as": "array"}` becomes
  `["OTHER", "ABD01", "ABD02", "ABD03"]`, **not** a nested sub-array. Like a `null` variable, an
  array acts only on a **complete** `"${VAR}"` string value; embedded in a larger string it's left
  intact with a warning. A missing `count`, a non-positive `count`, a negative `step`, or an `as`
  other than `"string"`/`"array"` raises `ValueError`; an unexpected key logs an error (like
  `env`/secret entries).

Resolution is two-pass, so a secret's `scope`/`key` can reference already-resolved literal
or environment values. Unknown `${VAR}` placeholders are left intact.

## Repo & layout

- `https://github.com/amida-tech/brickvar` (public, Apache-2.0).
- src layout: `src/brickvar/`, `pyproject.toml`, `tests/`.
- Default branch **`develop`**; releases flow `develop` → `main`. Both branches are
  protected (1 approval required).

## Current status

- `pytest`: **55 passing**. `python -m build` + `twine check dist/*`: passing.
- Version bumped to **0.0.7** (release in progress): `seq` gains an **`as`** key — `"array"`
  splices a sequence as sibling elements into the enclosing JSON array (vs. the default
  `"string"`, a delimited string). Acts like `null`: only on a complete `"${VAR}"` value.
  Also: `seq` `count` must now be **positive** (0 raises, was allowed in 0.0.6).
- Latest on PyPI: **0.0.6** (https://pypi.org/project/brickvar/) — `seq` counter-sequence
  variable type (a single delimited string from one variable), plus raise-on-invalid-shape
  validation for secret and mutually-exclusive `env`/`seq` entries.
- Prior release 0.0.5 — deep-merge for `read_jsons` / `configure_jsons` (dicts merge, lists
  concatenate, scalars last-wins, mismatched containers raise, null↔container is a warned
  last-wins override).

## Dev setup

```bash
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Releasing

Publishing to PyPI uses **Trusted Publishing (OIDC)** — no stored token. A published
GitHub Release triggers `.github/workflows/release.yml` (build → `twine check` → publish
via `pypa/gh-action-pypi-publish`). The `pypi` environment has a required-reviewer gate, so
the publish job pauses until approved.

To cut a release:

1. Bump the version in **one place**: `__version__` in `src/brickvar/__init__.py`.
   `pyproject.toml` reads it dynamically (`[tool.setuptools.dynamic] version = {attr = ...}`).
   PyPI rejects re-uploading an existing version, so always bump first.
2. Land the bump on `main` (via `develop` → `main`).
3. Publish a **GitHub Release** tagged `vX.Y.Z` targeting `main` (drafting first lets you
   review notes; drafts do not trigger the workflow).
4. Approve the `pypi` deployment when the run pauses; confirm the version on PyPI.

## Open questions

- _(none open)_ — the `ConfigManager` → `VariableResolver` rename and the single-source
  version (dynamic in `pyproject.toml`) are both done on `feature/multi-file-and-api-cleanup`.
