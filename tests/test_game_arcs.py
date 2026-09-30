from sgc.game import arcs, clock, world
from sgc.game.state import ARC_IDS, new_campaign


def camp():
    return new_campaign("campaign", "officer", 3)


def steps(c, aid):
    return [(e.due - c.now, e.data["stage"]) for e in c.events if e.kind == "arc_step" and e.data["arc"] == aid]


def test_the_arcs_match_the_saved_ids_and_their_places_exist():
    assert set(arcs.ARCS) == set(ARC_IDS)
    for a in arcs.ARCS.values():
        assert a.world in world.PLACES and a.faction in world.FACTION_IDS and a.stages
        assert a.endgame is None or 1 <= a.endgame <= len(a.stages)
    assert [a.id for a in arcs.ARCS.values() if a.major] == ["apophis"]


def test_start_enters_stage_one_once():
    c = camp()
    assert arcs.start(c, "apophis") == ["NEW LEAD: APOPHIS AND CHULAK",
                                        "NEW FACTION ON FILE: APOPHIS — FROM A NEW LEAD"]
    assert c.factions["apophis"].known
    st = c.arcs["apophis"]
    assert (st.state, st.stage, st.started) == ("active", 1, c.now) and steps(c, "apophis") == []
    assert arcs.start(c, "apophis") == []


def test_advancing_into_a_stage_with_a_step_schedules_it():
    c = camp()
    arcs.start(c, "apophis")
    lines = arcs.advance(c, "apophis")
    assert c.arcs["apophis"].stage == 2 and steps(c, "apophis") == [(48 * 60, 2)]
    assert lines == ["APOPHIS AND CHULAK: TEAL'C HAS TURNED ON APOPHIS"]
    arcs.advance(c, "apophis")
    assert c.arcs["apophis"].stage == 3 and steps(c, "apophis") == []          # stage 3 waits


def test_attention_at_80_jumps_to_the_endgame_with_a_visible_deadline():
    c = camp()
    arcs.start(c, "apophis")
    c.factions["apophis"].attention = 79
    assert arcs.on_attention(c, "apophis") == []
    c.factions["apophis"].attention = 80
    assert arcs.on_attention(c, "apophis")[0].startswith("APOPHIS AND CHULAK: TWO HA'TAKS")
    st = c.arcs["apophis"]
    assert st.stage == 4 and st.deadline == c.now + 120 * 60 and steps(c, "apophis") == [(120 * 60, 4)]
    assert arcs.on_attention(c, "apophis") == []
    assert arcs.stage_text(c, "apophis").endswith(f"Due {clock.short(st.deadline)}.")


def test_advancing_into_the_endgame_also_starts_the_countdown():
    c = camp()
    arcs.start(c, "apophis")
    for _ in range(3):
        arcs.advance(c, "apophis")
    assert c.arcs["apophis"].deadline == c.now + 120 * 60 and steps(c, "apophis") == [(120 * 60, 4)]
    assert arcs.advance(c, "apophis") == []                                     # nothing after the last stage


def test_dormant_arcs_ignore_attention():
    c = camp()
    c.factions["apophis"].attention = 90
    assert arcs.on_attention(c, "apophis") == [] and c.arcs["apophis"].stage == 0


def test_resolving_cancels_steps_and_counts_for_the_review():
    c = camp()
    arcs.start(c, "thor")
    arcs.advance(c, "thor")
    assert steps(c, "thor") == [(72 * 60, 2)]
    assert arcs.resolve(c, "thor") == ["RESOLVED: CIMMERIA AND THOR'S HAMMER"]
    assert c.arcs["thor"].state == "resolved" and c.arcs["thor"].ended == c.now and steps(c, "thor") == []
    assert c.ledger["arcs"] == 1 and arcs.advance(c, "thor") == [] and arcs.resolve(c, "thor") == []
    assert arcs.stage_text(c, "thor") == "Resolved."


def test_failing_the_major_arc_is_a_catastrophe_and_a_minor_one_is_not():
    c = camp()
    arcs.start(c, "tokra")
    assert arcs.fail(c, "tokra") == ["FAILED: THE TOK'RA"] and c.over is None
    assert c.arcs["tokra"].state == "failed" and arcs.stage_text(c, "tokra") == "Failed."
    arcs.start(c, "apophis")
    assert arcs.fail(c, "apophis") == ["FAILED: APOPHIS AND CHULAK"]
    assert c.over.startswith("Apophis's ha'taks") and c.ending == "fallen"


def test_sandbox_has_no_arcs():
    c = new_campaign("sandbox", "officer", 3)
    assert arcs.start(c, "apophis") == [] and arcs.on_attention(c, "apophis") == []
    assert arcs.stage_text(c, "apophis") == ""


def test_start_names_an_ally_it_puts_on_file():
    c = camp()
    lines = arcs.start(c, "tokra")
    assert lines[0] == "NEW LEAD: THE TOK'RA" and c.factions["tokra"].known
    assert arcs.start(c, "tokra") == []


def test_every_arc_log_line_fits_the_log():
    c = camp()
    for aid, arc in arcs.ARCS.items():
        lines = arcs.start(c, aid)
        for n in range(1, len(arc.stages) + 1):
            lines.append(arcs._line(arc, n))
        assert all(len(line) <= 66 for line in lines), [line for line in lines if len(line) > 66]


def test_arc_world_finds_listed_and_unlisted_worlds():
    c = camp()
    assert arcs.arc_world(c, "apophis").id == world.place_id("Chulak")
    assert arcs.arc_world(c, "tokra").id in c.unlisted


def test_starting_an_arc_files_its_faction_only_when_the_title_names_it():
    c = new_campaign("campaign", "officer", 5)
    arcs.start(c, "thor")
    assert not c.factions["asgard"].known
    lines = arcs.start(c, "tokra")
    assert c.factions["tokra"].known and any(l.startswith("NEW FACTION ON FILE") for l in lines)
    arcs.start(c, "apophis")
    assert c.factions["apophis"].known
