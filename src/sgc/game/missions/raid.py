"""A raid: needs a combat specialty, ends the world SURVEYED."""
from __future__ import annotations

from .base import MissionType

TYPE = MissionType(name="raid", hours=18, needs="combat")
