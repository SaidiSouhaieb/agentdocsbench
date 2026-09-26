"""Cached user reads. See docs/caching.md."""


def get_user(client, cache, user_id: str) -> dict:
    raise NotImplementedError("Read a user through the documented cache.")
