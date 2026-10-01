"""Victory, retiring, and the campaign ending — what today's engine.py called "the end"."""
from __future__ import annotations

from ... import sequences as sq
from ...model import Prompt, Step
from .. import clock, scoring

VICTORY_TEXT = ("Every threat the SGC uncovered has been dealt with. The President sends his thanks, and "
                "General Hammond asks whether you'll stay on.")


class EndingMixin:
    def _check_victory(self) -> None:
        c = self.c
        if c.won is not None or c.over or not scoring.victory(c):
            return
        c.won = c.now
        self._log("VICTORY — EVERY THREAT WE UNCOVERED HAS BEEN DEALT WITH")
        self._on_victory(c)
        self._raise({"type": "victory", "deadline": None, "title": "VICTORY", "text": VICTORY_TEXT})

    def retire(self) -> str:
        """Hand over command: the campaign ends and goes into the records."""
        if self.ended:
            return "THE CAMPAIGN IS OVER"
        self.c.ending, self.c.over = "retired", "You handed over command of the SGC."
        self._game_over()
        return "COMMAND HANDED OVER"

    def _game_over(self) -> None:
        if self.ended:
            return
        c = self.c
        self.ended = True
        c.alarms.clear()
        self._log(c.over.upper())
        ending = c.ending or "overrun"
        if ending == "retired":
            title = "VICTORY" if c.won is not None else "COMMAND HANDED OVER"

            def calm(s, p):
                s.status = "STANDING DOWN"
            self._show([Step(0, calm, "COMMAND HANDED OVER"), sq.hold(2.0)], urgent=True)
        else:
            title = "BASE OVERRUN" if ending == "overrun" else "EARTH HAS FALLEN"

            def red(s, p):
                s.alert, s.status = "red", "SGC OVERRUN" if ending == "overrun" else "EARTH HAS FALLEN"
            self._show([Step(0, red, "THE SGC HAS FALLEN", ("loop:klaxon",)), sq.hold(4.0)], urgent=True)
        self.prompt = Prompt(title, f"{c.over}\nDay {clock.day(c.minutes)}. "
                             f"Worlds surveyed: {c.record['surveyed']}. Teams lost: {c.record['teams_lost']}. "
                             f"Score: {scoring.score(c)}.",
                             [("Return to the briefing room", True)])
        self._on_end(c)
