"""A mining mission: no specialty needed, ends the world SURVEYED, and ranks just under a study for
the planner."""
from __future__ import annotations

from .base import MissionType

TYPE = MissionType(name="mine", hours=48, planner_score=8)
