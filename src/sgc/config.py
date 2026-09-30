"""User configuration: TOML file merged over built-in defaults."""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

DEFAULT_WEIGHTS = {
    "science": 1.0, "traffic": 1.2, "malp": 1.0, "failed_dial": 0.5,
    "code_red": 0.7, "friendly": 0.9, "kawoosh_hazard": 0.4,
}


@dataclass(frozen=True)
class Config:
    volume: float = 0.3
    sound: bool = True
    sound_pack: str = "synth"
    fps: int = 24
    speed: float = 1.0
    open_scale: float = 1.0
    canon_ratio: float = 0.6
    exit_duration: float = 6.0
    font_path: str | None = None
    graphics: str = "auto"
    transition_seconds: float = 10.0     # walking between the briefing room and the gate room
    game_pace: int | None = None         # real seconds per game hour; overrides the campaign's pace
    notify: bool = True                  # desktop notifications (notify-send) for game alarms
    legend: str = "bar"                  # game controls legend: "bar" | "full" | "off"
    event_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))


def _num(lo, hi):
    return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi


_VALIDATORS = {
    "volume": _num(0, 1),
    "sound": lambda v: isinstance(v, bool),
    "sound_pack": lambda v: v in ("synth", "freesound"),
    "fps": lambda v: isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= 60,
    "speed": _num(0.1, 10),
    "open_scale": _num(0.1, 10),
    "canon_ratio": _num(0, 1),
    "exit_duration": _num(0, 60),
    "font_path": lambda v: isinstance(v, str),
    "graphics": lambda v: v in ("auto", "kitty", "sixel", "iterm", "blocks"),
    "transition_seconds": _num(2, 30),
    "game_pace": lambda v: isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= 600,
    "notify": lambda v: isinstance(v, bool),
    "legend": lambda v: v in ("bar", "full", "off"),
}


def default_config_path() -> Path:
    return Path.home() / ".config" / "stargate-sgc" / "config.toml"


def load_config(path: Path | None) -> tuple[Config, list[str]]:
    """Load config from `path`, returning (config, warnings). Never raises."""
    path = path or default_config_path()
    if not path.exists():
        return Config(), []
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return Config(), [f"config {path}: could not read ({e}); using defaults"]

    warns: list[str] = []
    changes: dict = {}
    known = {f.name for f in fields(Config)}
    for key, value in data.items():
        if key not in known:
            warns.append(f"config: unknown key '{key}' ignored")
        elif key == "event_weights":
            if not isinstance(value, dict):
                warns.append("config: event_weights must be a table; ignored")
                continue
            weights = dict(DEFAULT_WEIGHTS)
            for name, w in value.items():
                if name not in DEFAULT_WEIGHTS:
                    warns.append(f"config: unknown event '{name}' ignored")
                elif not _num(0, 1000)(w):
                    warns.append(f"config: event weight {name}={w!r} invalid; using {DEFAULT_WEIGHTS[name]}")
                else:
                    weights[name] = float(w)
            changes["event_weights"] = weights
        elif not _VALIDATORS[key](value):
            warns.append(f"config: {key}={value!r} invalid; using default {getattr(Config(), key)!r}")
        else:
            changes[key] = value
    return replace(Config(), **changes), warns


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value))


def _trailing_comment(line: str) -> str:
    """Return a line's trailing `# ...` comment (with its leading whitespace), ignoring any
    `#` inside a quoted string. Returns "" if the line has no comment."""
    in_str = None
    hash_at = None
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == "\\" and in_str == '"':
                i += 1
            elif c == in_str:
                in_str = None
        elif c in ('"', "'"):
            in_str = c
        elif c == "#":
            hash_at = i
            break
        i += 1
    if hash_at is None:
        return ""
    start = hash_at
    while start > 0 and line[start - 1] in " \t":
        start -= 1
    return line[start:]


def save_setting(key: str, value, path: Path | None = None) -> None:
    """Set one top-level key in the config file, keeping every other line. Never raises."""
    path = path or default_config_path()
    try:
        lines = path.read_text().splitlines() if path.exists() else []
    except (OSError, UnicodeDecodeError):
        return
    line = f"{key} = {_toml_value(value)}"
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, old in enumerate(lines):
        if old.lstrip().startswith("["):         # top-level keys must come before the first table
            lines.insert(i, line)
            break
        if pattern.match(old):
            lines[i] = line + _trailing_comment(old)
            break
    else:
        lines.append(line)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass
