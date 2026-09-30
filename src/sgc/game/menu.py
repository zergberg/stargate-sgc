"""The start menu's screens and navigation. Drawing is in screens.py."""
from __future__ import annotations

TITLES = {"main": "STARGATE COMMAND", "missions": "MISSIONS", "mode": "NEW GAME", "difficulty": "DIFFICULTY",
          "overwrite": "OVERWRITE SAVE?"}
MODES = {"CAMPAIGN · DEFEAT 3 SYSTEM LORDS": "campaign", "ENDLESS": "endless"}
DIFFICULTIES = ("RECRUIT", "OFFICER", "COMMANDER")
PARENT = {"missions": "main", "overwrite": "missions", "mode": "missions", "difficulty": "mode"}


class Menu:
    def __init__(self, has_save: bool, screen: str = "main"):
        self.has_save, self.screen = has_save, screen
        self.sel = 0
        self.mode = "campaign"
        self.notice = ""

    @property
    def title(self) -> str:
        return TITLES[self.screen]

    def items(self) -> list[str]:
        if self.screen == "main":
            return ["AMBIENCE", "MISSIONS", "QUIT"]
        if self.screen == "missions":
            return (["CONTINUE"] if self.has_save else []) + ["NEW GAME", "BACK"]
        if self.screen == "overwrite":
            return ["START OVER", "BACK"]
        if self.screen == "mode":
            return [*MODES, "BACK"]
        return [*DIFFICULTIES, "BACK"]

    def key(self, k: str) -> tuple | None:
        """Handle a key; returns an action tuple for the app, or None."""
        items = self.items()
        if k == "up":
            self.sel = (self.sel - 1) % len(items)
        elif k == "down":
            self.sel = (self.sel + 1) % len(items)
        elif k == "q":
            return self.back()
        elif k == "enter":
            return self._activate(items[self.sel])
        elif k.isdigit() and 1 <= int(k) <= len(items):
            self.sel = int(k) - 1
            return self._activate(items[self.sel])
        return None

    def back(self) -> tuple | None:
        if self.screen == "main":
            return ("quit",)
        self._go(PARENT[self.screen])
        return None

    def _go(self, screen: str) -> None:
        self.screen, self.sel, self.notice = screen, 0, ""

    def _activate(self, item: str) -> tuple | None:
        if item == "BACK":
            return self.back()
        if item == "AMBIENCE":
            return ("ambient",)
        if item == "QUIT":
            return ("quit",)
        if item == "CONTINUE":
            return ("continue",)
        if item == "MISSIONS":
            self._go("missions")
        elif item == "NEW GAME":
            self._go("overwrite" if self.has_save else "mode")
        elif item == "START OVER":
            self._go("mode")
        elif item in MODES:
            self.mode = MODES[item]
            self._go("difficulty")
        else:
            return ("new", self.mode, item.lower())
        return None
