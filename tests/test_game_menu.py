from sgc.game.menu import Menu


def test_main_menu_numbers_and_quit():
    m = Menu(has_save=False)
    assert m.title == "STARGATE COMMAND" and m.items() == ["AMBIENCE", "MISSIONS", "QUIT"]
    assert m.key("1") == ("ambient",)
    assert m.key("3") == ("quit",)
    assert m.key("7") is None


def test_new_game_path_mode_difficulty_pace():
    m = Menu(has_save=False)
    assert m.key("down") is None and m.key("enter") is None and m.screen == "missions"
    assert m.items() == ["NEW GAME", "BACK"]
    m.key("1")
    assert m.screen == "mode" and m.items() == ["CAMPAIGN", "SANDBOX", "BACK"]
    m.key("2")
    assert m.screen == "difficulty"
    m.key("3")
    assert m.screen == "pace" and m.title == "PACE" and len(m.items()) == 4
    assert m.key("3") == ("new", "sandbox", "commander", "busy")


def test_pace_defaults_to_standard_with_enter_on_the_second_item():
    m = Menu(has_save=False, screen="missions")
    for k in ("1", "1", "2", "down"):
        m.key(k)
    assert m.key("enter") == ("new", "campaign", "officer", "standard")


def test_back_walks_up_through_pace_and_difficulty():
    m = Menu(has_save=False, screen="missions")
    for k in ("1", "1", "1"):
        m.key(k)
    assert m.screen == "pace"
    m.key("q")
    assert m.screen == "difficulty"
    m.key("4")
    assert m.screen == "mode"


def test_continue_only_with_a_save_and_back_navigation():
    m = Menu(has_save=True, screen="missions")
    assert m.items()[0] == "CONTINUE" and m.key("1") == ("continue",)
    m.key("2")
    assert m.screen == "overwrite"
    m.key("1")
    assert m.screen == "mode"
    assert m.key("up") is None and m.sel == 2          # wraps to BACK
    assert m.key("enter") is None and m.screen == "missions"
    m.back()
    assert m.screen == "main"
    assert m.back() == ("quit",)


def test_notice_clears_when_moving_screens():
    m = Menu(has_save=False, screen="missions")
    m.notice = "SAVE DAMAGED — STARTING FRESH"
    m.key("1")
    assert m.notice == ""


def test_new_game_over_a_save_asks_before_overwriting():
    m = Menu(has_save=True, screen="missions")
    m.key("2")
    assert m.screen == "overwrite" and m.title == "OVERWRITE SAVE?"
    assert m.items() == ["START OVER", "BACK"]
    assert m.key("1") is None and m.screen == "mode"
    m.key("2")
    m.key("1")
    assert m.key("1") == ("new", "sandbox", "recruit", "relaxed")


def test_overwrite_back_and_q_return_to_missions():
    m = Menu(has_save=True, screen="missions")
    m.key("2")
    assert m.key("2") is None and m.screen == "missions"
    m.key("2")
    assert m.key("q") is None and m.screen == "missions"
