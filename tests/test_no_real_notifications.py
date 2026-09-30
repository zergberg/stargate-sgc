import shutil


def test_notify_send_is_the_silent_one(_silent_notify_send):
    assert shutil.which("notify-send") == str(_silent_notify_send)
