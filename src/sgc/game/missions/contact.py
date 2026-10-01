"""First contact: needs a diplomatic specialty, ends the world in CONTACT."""
from __future__ import annotations

from .base import MissionType

TYPE = MissionType(name="contact", hours=36, needs="diplomatic", ends_as="contact")
