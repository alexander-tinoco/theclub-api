"""Exceptions shared across services."""


class DataIntegrityError(Exception):
    """An invariant that should always hold didn't (e.g. an authenticated
    user with no wallet or no active seed pair — both are created at
    registration, in Phase 4 and Phase 5). Should never happen under normal
    circumstances: if it does, it's a bug elsewhere in the system, not
    something the client could trigger. Used instead of `assert` because
    Python drops `assert` under `-O`/`PYTHONOPTIMIZE` — a business
    invariant shouldn't depend on how the interpreter was invoked.
    """
