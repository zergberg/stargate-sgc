"""Test-wide safety: no test may raise a real desktop notification.

Alarms call `notify-send`. Tests that raise alarms, in process or through a pty, would otherwise flood the
desktop (parallel runs once took a GNOME session down). A silent `notify-send` goes first on PATH for the
whole run, so `shutil.which` finds it and every spawned sgc inherits it.
"""
import os
import stat

import pytest


@pytest.fixture(autouse=True, scope="session")
def _silent_notify_send(tmp_path_factory):
    bin_dir = tmp_path_factory.mktemp("fakebin")
    fake = bin_dir / "notify-send"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    old = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old}"
    yield fake
    os.environ["PATH"] = old

