import random

from sgc.game import clock, factions
from sgc.game.state import new_campaign


def camp(diff="officer"):
    return new_campaign("campaign", diff, 5)


def pending(c, fid):
    return [e for e in c.events if e.kind == "faction_action" and e.data["faction"] == fid]


def test_stages_follow_the_thresholds():
    c = camp()
    f = c.factions["apophis"]
    for attention, stage in [(0, "unaware"), (19, "unaware"), (20, "curious"), (49, "curious"), (50, "hostile"),
                             (79, "hostile"), (80, "seeking"), (100, "seeking")]:
        f.attention = attention
        assert factions.stage(f) == stage


def test_attention_gains_scale_with_difficulty_and_clamp():
    for diff, expected in [("recruit", 8), ("officer", 10), ("commander", 13)]:
        c = camp(diff)
        factions.adjust_attention(c, "sokar", 10)
        assert c.factions["sokar"].attention == expected and c.factions["sokar"].quiet_since == c.now
    c = camp()
    factions.adjust_attention(c, "sokar", 500)
    assert c.factions["sokar"].attention == 100
    factions.adjust_attention(c, "sokar", -500)
    assert c.factions["sokar"].attention == 0


def test_crossing_into_curious_schedules_one_action_and_stays_silent_for_an_unknown_goauld():
    c = camp()
    assert factions.adjust_attention(c, "apophis", 25) == []
    [ev] = pending(c, "apophis")
    assert c.now + 72 * 60 <= ev.due <= c.now + 120 * 60
    factions.adjust_attention(c, "apophis", 5)
    assert len(pending(c, "apophis")) == 1


def test_a_known_goaulds_stage_change_is_logged_in_words():
    c = camp()
    factions.know(c, "apophis", "the Jaffa")
    assert factions.adjust_attention(c, "apophis", 50) == ["INTEL: APOPHIS HAS PUT A PRICE ON THE SG TEAMS"]
    assert factions.words(c, "apophis") == "has put a price on the SG teams"


def test_allies_have_no_attention_and_goauld_no_trust():
    c = camp()
    assert factions.adjust_attention(c, "tokra", 30) == [] and c.factions["tokra"].attention == 0
    assert factions.adjust_trust(c, "apophis", 30) == [] and c.factions["apophis"].trust == 0


def test_trust_moves_quietly_until_the_ally_is_known():
    c = camp()
    assert factions.adjust_trust(c, "tokra", 30) == [] and c.factions["tokra"].trust == 30
    assert factions.know(c, "tokra", "allied intelligence") == [
        "NEW FACTION ON FILE: THE TOK'RA — FROM ALLIED INTELLIGENCE"]
    assert factions.know(c, "tokra", "the Jaffa") == [] and c.factions["tokra"].source == "allied intelligence"
    assert factions.adjust_trust(c, "tokra", 25) == ["THE TOK'RA: NOW FRIENDLY"]
    assert factions.words(c, "tokra") == "friendly"
    factions.adjust_trust(c, "tokra", -200)
    assert c.factions["tokra"].trust == 0 and factions.words(c, "tokra") == "wary"


def test_texts_name_a_faction_only_once_it_is_known():
    c = camp()
    assert factions.bind(c, "heruur") == {"faction": "a Goa'uld", "faction_id": "heruur"}
    assert factions.display(c, "tollan") == "strangers"
    factions.know(c, "heruur", "the locals")
    assert factions.display(c, "heruur") == "Heru'ur"


def test_attention_decays_one_a_day_after_a_quiet_day():
    c = camp()
    factions.adjust_attention(c, "yu", 22)
    c.minutes += clock.DAY - 1
    assert factions.decay(c) == [] and c.factions["yu"].attention == 22
    c.minutes += 1
    factions.decay(c)
    assert c.factions["yu"].attention == 21
    c.minutes += clock.DAY
    factions.decay(c)
    assert c.factions["yu"].attention == 20
    r = camp("recruit")
    factions.adjust_attention(r, "yu", 30)                 # 22.5 rounds to 23
    r.minutes += clock.DAY
    factions.decay(r)
    assert r.factions["yu"].attention == 21


def test_a_known_goauld_calming_down_is_logged():
    c = camp()
    factions.know(c, "yu", "the Jaffa")
    factions.adjust_attention(c, "yu", 20)
    c.minutes += clock.DAY
    assert factions.decay(c) == ["INTEL: YU SHOWS NO SIGN OF KNOWING ABOUT EARTH"]


def test_the_next_action_depends_on_the_stage_and_lapses_when_unaware():
    c = camp()
    c.factions["baal"].attention = 60
    factions.schedule_next(c, "baal", random.Random(1))
    [ev] = pending(c, "baal")
    assert 36 * 60 <= ev.due - c.now <= 72 * 60
    c.factions["baal"].attention = 5
    factions.schedule_next(c, "baal", random.Random(1))
    assert pending(c, "baal") == []
    k = camp("commander")
    k.factions["baal"].attention = 85
    factions.schedule_next(k, "baal", random.Random(1))
    assert pending(k, "baal")[0].due - k.now <= round(36 * 60 * 0.8)


def test_ensure_action_is_deterministic_and_never_doubles_up():
    a, b = camp(), camp()
    for c in (a, b):
        c.factions["cronus"].attention = 30
        factions.ensure_action(c, "cronus")
        factions.ensure_action(c, "cronus")
    assert [e.due for e in pending(a, "cronus")] == [e.due for e in pending(b, "cronus")] and len(pending(a, "cronus")) == 1


def test_defcon_uses_only_what_the_sgc_knows():
    c = camp()
    c.factions["sokar"].attention = 90
    assert factions.threat(c) == 5
    factions.know(c, "sokar", "the Jaffa")
    assert factions.threat(c) == 2
    c.factions["sokar"].attention = 30
    assert factions.threat(c) == 4
    c.factions["sokar"].attention = 55
    assert factions.threat(c) == 3
    c.factions["sokar"].attention = 0
    c.arcs["apophis"].state, c.arcs["apophis"].deadline = "active", c.now + 600
    assert factions.threat(c) == 2


def test_owner_of_a_world():
    c = camp()
    chulak = next(w for w in c.worlds.values() if w.owner == "Apophis")
    assert factions.owner_of(c, chulak.id) == "apophis" and factions.owner_of(c, "nowhere") is None


def test_a_rising_stage_redraws_an_action_pending_too_far_off():
    a, b = camp(), camp()
    for c in (a, b):
        factions.adjust_attention(c, "apophis", 25)                 # curious: 72-120 h away
        [ev] = pending(c, "apophis")
        assert ev.due - c.now >= 72 * 60
        factions.adjust_attention(c, "apophis", 60)                 # seeking: 18-36 h
        [ev] = pending(c, "apophis")
        assert 18 * 60 <= ev.due - c.now <= 36 * 60
    assert [e.due for e in pending(a, "apophis")] == [e.due for e in pending(b, "apophis")]


def test_a_rising_stage_keeps_an_action_already_due_soon_enough():
    c = camp()
    c.factions["apophis"].attention = 30
    c.events.push(c.now + 10 * 60, "faction_action", {"faction": "apophis"})
    factions.adjust_attention(c, "apophis", 30)                     # hostile: within 72 h, 10 h is kept
    assert [e.due - c.now for e in pending(c, "apophis")] == [10 * 60]


def test_a_falling_stage_keeps_the_pending_action():
    c = camp()
    c.factions["apophis"].attention = 60
    c.events.push(c.now + 10 * 60, "faction_action", {"faction": "apophis"})
    factions.adjust_attention(c, "apophis", -20)
    assert [e.due - c.now for e in pending(c, "apophis")] == [10 * 60]
