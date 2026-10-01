"""An aid mission: needs a medical specialty, ends the world in CONTACT, and the planner's third choice."""
from __future__ import annotations

from .base import MissionType

TYPE = MissionType(name="aid", hours=30, needs="medical", ends_as="contact", planner_score=10)
