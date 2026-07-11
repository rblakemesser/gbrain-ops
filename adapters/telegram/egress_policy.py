"""Fixture-only retrieval policy contract.

This is deliberately an offline, pure policy helper. It proves the desired
fail-closed rule but is NOT yet wired into Hermes gateway/tool dispatch; Task 0
is not complete until that production seam is implemented and tested.
"""

from __future__ import annotations

from dataclasses import dataclass


APPROVED_SYNTHETIC_TELEGRAM_SOURCES = frozenset({"telegram-fixture"})


@dataclass(frozen=True)
class RetrievalContext:
    platform: str
    is_group: bool
    is_blake_authorized: bool


def can_retrieve_source(context: RetrievalContext, source_id: str) -> bool:
    """Allow only an approved synthetic Telegram source in operator-authorized contexts.

    Any group/topic context is denied before considering user authorization.
    This makes the default policy safe while a future real source and Hermes
    middleware are designed.
    """

    return (
        context.platform == "telegram"
        and not context.is_group
        and context.is_blake_authorized
        and source_id in APPROVED_SYNTHETIC_TELEGRAM_SOURCES
    )
