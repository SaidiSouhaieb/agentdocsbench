# Pagination

`client.users.list(*, cursor=None, page_size=2)` returns a `Page`.

- `page.items` is a tuple of `User` objects in directory order.
- `page.next_cursor` is a string when another page exists, otherwise `None`.
- The default and required application page size is 2. Pass `page_size=2` on every call.
- Start with `cursor=None`. Pass `next_cursor` back as `cursor` until it is `None`.

`app.users.list_all_users(client)` returns a list of dictionaries, one per user, in that order:

```python
{"id": user.id, "name": user.name, "email": user.email, "metadata": dict(user.metadata)}
```

Do not stop after the first page. Do not hard-code user ids.
