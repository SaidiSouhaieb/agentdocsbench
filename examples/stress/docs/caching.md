# Caching

`nimbus_sdk.MemoryCache(clock=None)` stores values in memory.

- `cache.get(key)` returns the value, or `None` when the key is missing or expired.
- `cache.set(key, value, ttl_seconds)` stores it at `clock()`.
- An entry is expired when `clock() >= stored_at + ttl_seconds`.
- `cache.invalidate(key)` removes it immediately.
- `clock` is a zero-argument callable returning a number. Do not sleep.

`app.cache.get_user(client, cache, user_id)`:

1. Use the key `user:{user_id}`.
2. If the cache returns a dictionary, return that dictionary.
3. Otherwise call `client.users.get(user_id)`.
4. Store `{"id", "name", "email", "metadata"}` with `ttl_seconds=60`. `metadata` is a new `dict`.
5. Return that dictionary.

See `users.md` for `client.users.get`.
