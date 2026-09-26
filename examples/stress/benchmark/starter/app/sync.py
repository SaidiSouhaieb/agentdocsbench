"""Import organization members from a paged directory. See docs/migrations.md and related guides."""


def import_organization_users(client, cache, *, org_name: str, directory) -> dict:
    raise NotImplementedError("Import every paged user into a new organization.")
