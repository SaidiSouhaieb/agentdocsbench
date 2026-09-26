# Organization import

`app.sync.import_organization_users(client, cache, *, org_name, directory)` copies every user from a paged directory into a new organization.

`directory.list_page(*, cursor, page_size)` returns a `Page`, the same shape as `client.users.list`. Use `page_size=2` and follow `next_cursor` until it is `None`. See `pagination.md`.

Call `list_page` through `app.retries.call_with_retry`. Retry only the failures allowed by `retries.md`. If the directory raises `NimbusAuthError`, do not retry and do not create an organization. Raise `AppError("auth_failed", str(exc), 401)` from `app.errors`. See `errors.md` and `authentication.md`.

After every page has been read:

1. `client.organizations.create(name=org_name)`.
2. For each user, in page order, `client.organizations.add_member(org.id, user_id=user.id, role="member")`. The users already exist on `client`.
3. `cache.invalidate(f"user:{user.id}")` for each imported user. See `caching.md`.

Return:

```python
{
    "organization_id": org.id,
    "imported_user_ids": [user.id, ...],
    "member_count": len(imported_user_ids),
}
```

`member_count` matches the number of users across all pages. Do not hard-code that number.
