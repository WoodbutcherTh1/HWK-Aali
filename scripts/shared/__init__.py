"""Shared client modules for the Aali clients (Studio, Desktop, CLI).

Kept as a package (not a loose file) so ``from shared.updater import ...``
works from the repo checkout AND from a frozen build whose spec puts
``<repo>/scripts`` on ``pathex``.
"""

from __future__ import annotations

__all__ = ["updater"]