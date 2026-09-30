"""User configuration: TOML file merged over built-in defaults."""
from __future__ import annotations

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
    decision_countdown: float = 12.0     # seconds to give an order before standing procedure applies
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
    "decision_countdown": _num(5, 30),
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
