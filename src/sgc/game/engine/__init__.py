"""The game engine package: see core.py's module docstring for the overview. Composed from mixins, one
per section of what used to be a single 1,793-line engine.py; engine/__init__.py's only job is to build
Engine from them and re-export the names the rest of the codebase reads off `engine` today."""
from __future__ import annotations

import sys as _sys

from . import core
from .core import (CAPTURED, CHECKIN_LINE_MINUTES, CoreMixin, DESTROYED, DETAIL, DRONE_HOME_MINUTES,
                    EXTENDED, FOLLOWED, INTEL_ROLL, MALP_FEED_S, MISS, PLANNABLE, REPAIR, SALVAGE, SEARCH,
                    SEEN, SHARE_ODDS, UAV_CRUISE, UAV_FEED_S, UAV_RAIL, UPLINK_FEED_S, UPLINK_HOURS,
                    UPLINK_ODDS, team_label, uplink_odds, uplink_outcome)
from .ending import EndingMixin
from .scene import SceneMixin


class Engine(CoreMixin, EndingMixin, SceneMixin):
    pass


# The test suite monkeypatches a handful of these as plain module "constants" through this package
# (`eng = sgc.game.engine`; e.g. `monkeypatch.setattr(eng, "MISS", ...)`), expecting the same effect as
# when `engine.py` was a single module. But `from .core import MISS` above only copies the *current*
# value into this package's own namespace — the mixins' methods still read the bare global from
# `core.py`'s own namespace, which a plain `setattr(engine, ...)` never touches. Forward any such write
# to every submodule that already defines that name, so the patch reaches the code that reads it. (Add
# further submodules to _OWNERS here as later tasks move mixins and their constants out of core.py.)
_OWNERS = (core,)


class _PackageModule(_sys.modules[__name__].__class__):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for owner in _OWNERS:
            if hasattr(owner, name):
                owner.__dict__[name] = value


_sys.modules[__name__].__class__ = _PackageModule
