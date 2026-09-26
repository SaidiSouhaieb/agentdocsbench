"""Public records returned by the local Nimbus SDK."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class User:
    id: str
    name: str
    email: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Organization:
    id: str
    name: str


@dataclass(frozen=True)
class Membership:
    organization_id: str
    user_id: str
    role: str


@dataclass(frozen=True)
class Page:
    items: tuple[User, ...]
    next_cursor: str | None
