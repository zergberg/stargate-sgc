"""A trade mission: needs a diplomatic specialty, ends the world in CONTACT."""
from __future__ import annotations

from .base import MissionType

TYPE = MissionType(name="trade", hours=30, needs="diplomatic", ends_as="contact")
