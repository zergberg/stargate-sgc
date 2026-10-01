"""The mission-type registry: one MissionType per file, replacing the MISSION_HOURS / MISSION_NEEDS /
CONTACT_TYPES / EXACT_TYPES constants once scattered across engine.py, and planner.TYPE_SCORE."""
from __future__ import annotations

from .base import MissionType, NO_TARGET

MISSION_TYPES = ("survey", "contact", "trade", "raid", "study", "rescue", "recover", "mine", "aid")

from . import aid, contact, mine, raid, recover, rescue, study, survey, trade  # noqa: E402

REGISTRY: dict[str, MissionType] = {
    mt.name: mt for mt in (survey.TYPE, contact.TYPE, trade.TYPE, raid.TYPE, study.TYPE, rescue.TYPE,
                            recover.TYPE, mine.TYPE, aid.TYPE)
}


def get(name: str) -> MissionType:
    return REGISTRY[name]
