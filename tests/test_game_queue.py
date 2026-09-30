from sgc.game.schedule import QueueItem


def test_a_queue_item_is_a_row_of_when_what_and_status():
    item = QueueItem("dial:uav:P3X-774", "dial_out", "1", "UAV → P3X-774", "WAITING FOR THE GATE", (0, 0),
                     cancellable=True, movable=True)
    assert item.cells == ("1", "UAV → P3X-774", "WAITING FOR THE GATE") and item.reason == ""
    assert not QueueItem("team:SG-3", "team", "D4 18:00", "SG-3 INJURED", "BACK D4 18:00", (1, 5400)).cancellable
