# CLI

The command is:

```text
python -m app.cli users list --directory DIRECTORY
```

`DIRECTORY` is a JSON file containing a list of objects with `id`, `name`, `email`, and `metadata`. Build `nimbus_sdk.User` objects from those objects, in file order. Construct a `NimbusClient` with `NimbusConfig(api_key="nim_local")` and call `client.users.seed(users)`.

Then list every user with the pagination rules in `pagination.md`: `page_size=2`, follow `next_cursor`, and do not hard-code the file length.

Print one line per user:

```text
{id} {email}
```

Use a single space. No header. Exit status 0.

`app.cli.main(argv=None)` parses those arguments. When `argv` is omitted, use `sys.argv[1:]`. Return the exit status. The module must call `main()` when executed as `__main__`.

Unknown commands should return status 2 and write a short message to stderr. This command does not read `config/app.json`.
