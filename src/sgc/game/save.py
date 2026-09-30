"""The campaign save slot and the hall of records, under ~/.local/share/stargate-sgc/."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .state import Campaign, from_dict, to_dict, upgrade_v2

DATA_DIR = Path.home() / ".local" / "share" / "stargate-sgc"


def _write_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()                     # don't leave half a save behind (disk full, read-only)
        except OSError:
            pass
        raise


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _unique(path: Path) -> Path:
    """path, or path-1, path-2, ... if it (or they) already exist."""
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.name}-{i}")
        if not candidate.exists():
            return candidate
        i += 1


class Saves:
    def __init__(self, root: Path = DATA_DIR):
        self.root = Path(root)
        self.path = self.root / "campaign.json"
        self.records_path = self.root / "records.json"

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, c: Campaign) -> None:
        _write_atomic(self.path, to_dict(c))

    def load(self) -> tuple[Campaign | None, str]:
        """(campaign, notice). A save that can't be read is set aside and reported, never raised."""
        if not self.exists():
            return None, ""
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            data = None
        if isinstance(data, dict) and data.get("version") == 1:
            self._set_aside("v1")
            return None, "BUILD 1 SAVE SET ASIDE — START A NEW GAME"
        notice = ""
        try:
            if isinstance(data, dict) and data.get("version") == 2:
                data, notice = upgrade_v2(data), "STAGE 1 SAVE UPGRADED"
            return from_dict(data), notice
        except Exception:
            self._set_aside("bad")
            return None, "SAVE DAMAGED — STARTING FRESH"

    def _set_aside(self, tag: str) -> None:
        dest = _unique(self.path.with_name(f"campaign.json.{tag}-{datetime.now():%Y%m%d-%H%M%S}"))
        try:
            os.replace(self.path, dest)
        except OSError:
            pass

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def records(self, top: int = 5) -> list[dict]:
        try:
            recs = json.loads(self.records_path.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(recs, list):
            return []
        recs = [r for r in recs if isinstance(r, dict) and _is_int(r.get("surveyed")) and _is_int(r.get("days"))]
        return sorted(recs, key=lambda r: (-r["surveyed"], -r["days"]))[:top]

    def add_record(self, entry: dict) -> None:
        """Add a finished campaign: mode, difficulty, result, days and surveyed (worlds); the date is added here."""
        recs = self.records(top=50)
        recs.append({**entry, "date": f"{datetime.now():%Y-%m-%d}"})
        _write_atomic(self.records_path, recs)
