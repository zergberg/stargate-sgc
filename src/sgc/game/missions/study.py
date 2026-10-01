"""A study: needs a science specialty, ends the world SURVEYED, and is the planner's second choice
after a rescue."""
from __future__ import annotations

from .base import MissionType

TYPE = MissionType(name="study", hours=36, needs="science", planner_score=9)
