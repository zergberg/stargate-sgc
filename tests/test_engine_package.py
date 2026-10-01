"""Guards the engine package's monkeypatch-forwarding shim (engine/__init__.py's _PackageModule):
a plain `monkeypatch.setattr(eng, NAME, ...)` must reach every engine submodule that binds NAME as
its own module-level global, not just the submodules _OWNERS happened to list. See the shim's own
comment for why: a submodule that does `from .core import NAME` gets its own binding, which a
setattr on the package alone never touches."""
import pytest

from sgc.game import engine as eng
from sgc.game.engine import actions, alarms, core, drones, ending, operations, scenarios, scene, visuals

SUBMODULES = (core, actions, alarms, drones, ending, operations, scenarios, scene, visuals)

# The literal list of names the test suite patches through `eng` today (see the grep in the review
# that found this gap: every `monkeypatch.setattr(eng, ..., ...)` across tests/).
PATCHED_NAMES = ("SHARE_ODDS", "INTEL_ROLL", "MISS", "FOLLOWED")


def _owners(name: str) -> list:
    """Every engine submodule that binds `name` as a module-level global of its own."""
    return [m for m in SUBMODULES if hasattr(m, name)]


def test_every_patched_name_is_bound_in_some_submodule():
    """Drift guard: if a later refactor removes the last binding of one of these names (or renames
    it), this fails loudly instead of the forwarding shim silently becoming a no-op for it."""
    for name in PATCHED_NAMES:
        assert _owners(name), f"no engine submodule binds {name!r} any more"


def test_setattr_on_the_engine_package_forwards_to_every_owning_submodule():
    for name in PATCHED_NAMES:
        owners = _owners(name)
        originals = {m: getattr(m, name) for m in owners}
        sentinel = object()
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(eng, name, sentinel)
            for m in owners:
                assert getattr(m, name) is sentinel, (
                    f"{m.__name__} still reads its own {name!r} after patching eng.{name}")
            # Every other submodule is untouched: it either never had the name, or (this would be a
            # bug in the shim) still holds its own stale value instead of the sentinel.
            for m in SUBMODULES:
                if m not in owners:
                    assert not hasattr(m, name) or getattr(m, name) is not sentinel
        finally:
            mp.undo()
        for m in owners:
            assert getattr(m, name) == originals[m], f"{m.__name__}.{name} did not restore after undo()"
