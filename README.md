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

## Status

Early development. API is not yet stable.

## License

[Apache-2.0](LICENSE)
