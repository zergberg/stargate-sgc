from sgc.game.menu import Menu


def test_main_menu_numbers_and_quit():
    m = Menu(has_save=False)
    assert m.title == "STARGATE COMMAND" and m.items() == ["AMBIENCE", "MISSIONS", "QUIT"]
    assert m.key("1") == ("ambient",)
    assert m.key("3") == ("quit",)
    assert m.key("7") is None


def test_new_game_path_with_arrows_and_enter():
    m = Menu(has_save=False)
    assert m.key("down") is None and m.key("enter") is None and m.screen == "missions"
    assert m.items() == ["NEW GAME", "BACK"]
    m.key("1")
    assert m.screen == "mode"
    m.key("2")
    assert m.screen == "difficulty"
    assert m.key("3") == ("new", "endless", "commander")


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
    assert m.key("1") == ("new", "endless", "recruit")


def test_overwrite_back_and_q_return_to_missions():
    m = Menu(has_save=True, screen="missions")
    m.key("2")
    assert m.key("2") is None and m.screen == "missions"
    m.key("2")
    assert m.key("q") is None and m.screen == "missions"


def test_new_game_without_a_save_skips_the_confirmation():
    m = Menu(has_save=False, screen="missions")
    m.key("1")
    assert m.screen == "mode"
