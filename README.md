# sgc: SGC Dialing Computer

An ambient Stargate SG-1 dialing computer for your terminal. It loops forever: dial an address, play an event at the destination, shut the gate down, pick the next address. The gate is drawn as an image and shown with your terminal's image support. The side screens, clock and a running log are drawn as text. It has sound, and it plays an animated shutdown when you quit.

## Events

- Science readouts
- SG teams walking through
- MALP recon with live telemetry
- Failed dials ("chevron seven will not lock")
- Code Red with the iris closing under impacts
- Friendly incoming wormholes with an IDC
- The occasional kawoosh vaporizing something left on the ramp

Addresses are a mix of about 30 canon ones (Abydos, Chulak, Dakara, Atlantis with 8 chevrons, Destiny with 9) and random `P3X-nnn` worlds.

## Install

Requires Python 3.12+. The only runtime dependency is Pillow.

```sh
git clone https://github.com/zergberg/stargate-sgc.git
cd stargate-sgc
python3 -m venv .venv            # add --without-pip and bootstrap pip if ensurepip is missing
.venv/bin/pip install -e '.[dev]'
ln -sf "$PWD/.venv/bin/sgc" ~/.local/bin/sgc
```

**Glyph font (recommended).** Download *Stargate SG-1 Address Glyphs* by Joy Anne Baker from [The Scifi World's font page](https://www.thescifiworld.net/fonts.htm), then run:

```sh
sgc --install-font            # finds the .ttf or .zip in ~/Downloads
sgc --install-font PATH       # or point it at the file
```

This installs it as `~/.local/share/fonts/stargate_sg1_adress_glyphs.ttf`. The font isn't bundled or downloaded automatically: its licence was never stated, and the site forbids redistribution and direct links to its files. Without it, the gate shows glyph numbers instead.

## Run

```sh
sgc                       # briefing-room menu: Ambience or Missions
sgc --ambient             # straight to the ambient dialing computer
sgc --missions            # straight to the missions menu (continue or new game)
sgc --pack freesound      # the other sound pack
sgc --event code_red      # ambient, starting with a specific event
sgc --graphics blocks     # force a graphics mode: kitty | sixel | iterm | blocks
sgc --no-sound
sgc --install-font        # install the glyph font you downloaded (see Install)
```

| Key | Action |
|---|---|
| `q` / Ctrl+C | animated shutdown; press again to quit immediately |
| `m` | mute / unmute |
| `+` / `-` | volume |
| `space` | skip to the next dial |
| `p` | pause |

## Missions (the game)

You command the SGC while sgc runs in a terminal pane. The game keeps its own clock and plays itself between your decisions: MALPs go out, teams check in, the gate spins. When something needs you, an alarm goes off. If you don't answer in time, your standing orders do.

### Starting

- **The menu:** `sgc` opens the briefing-room menu: Ambience, Missions or Quit. Missions offers Continue (when there's a save) and New Game. New Game over an existing save asks first (Start over or Back). Then pick a mode, a difficulty and a pace.
- **Modes:** Campaign places seven canon worlds (Chulak, Cimmeria, Kheb, K'tau, Langara, Tollana, Juna) among the 20 addresses of the Abydos cartouche, and the rest are generated. Sandbox generates everything but Abydos, and Goa'uld and Jaffa worlds are rarer. Story arcs and victory come in a later build, so for now both modes are open-ended.
- **Difficulty:**
  - Recruit, Officer or Commander.
  - Meter losses count ×0.5, ×1 or ×1.5, and gains ×1.25, ×1 or ×0.75.
  - Telemetry and reports get thinner as the difficulty goes up.
  - Only Recruit can pause: `p` in the gate room stops the clock.
- **Saving:**
  - The campaign saves after every event and decision to `~/.local/share/stargate-sgc/campaign.json`. It resumes exactly where it was, missions in flight included.
  - If a save fails (a full disk, a read-only home), the log reads `SAVE FAILED — <reason>` once and the game plays on. It tries again every 5 game minutes and logs `SAVE OK` when a save works again.
  - A save from build 1 is renamed `campaign.json.v1-<date>` and the menu says so. A damaged save is set aside the same way (`.bad-<date>`).
  - When the base falls, the save is deleted and the run goes into the hall of records on the menu (the top five, by worlds surveyed).

### The clock

- The header shows `DAY 3 · 14:30 SGC · DEFCON 5`. A campaign starts on day 1 at 08:00.
- Time only passes while sgc is open. Nothing happens while it's closed.
- The pace is chosen at the start and can be changed in the briefing room:

  | Pace | A game hour takes | An alarm waits |
  |---|---|---|
  | Relaxed | 2 minutes | 6 minutes |
  | Standard | 1 minute | 3 minutes |
  | Busy | 10 seconds | 1 minute |

- `game_pace` in the config sets the real seconds per game hour (5–600) for every campaign. The briefing room then shows the pace as locked.
- The clock waits while the gate is showing real traffic, so a result is never logged before you've seen it happen. This mostly slows Busy, in real time.
- Walking between the rooms doesn't stop the clock. Gate traffic that comes due during a walk plays after you arrive.

### Alarms

- An alarm sounds the klaxon and the terminal bell. It also sends a desktop notification through `notify-send`, if that's installed and `notify` is on.
- The header turns red (`!! ALARM · INCOMING`, `DEFCON 2`), and you're brought back to the gate room from wherever you are.
- You have 3 game hours to answer with `1`–`9`, and never less than 60 real seconds. The countdown ticks for its last 5 seconds.
- If you don't answer, the standing order for that situation is carried out and the log names it. A greyed-out option can't be chosen right now.
- **Typing when an alarm goes off:** your keys are held, so a half-typed word can't give an order. The log reads `ALARM — TYPING HELD TILL YOU STOP`.
  - The hold lasts until you stop typing for 1 second, or press Enter (which does nothing else).
  - Ctrl+C or Esc also ends the hold and throws away the half-typed note or search.
  - The alarm closes the Database but keeps it: `d` opens it again on the same tab, search and scroll, a half-typed search included.
  - After the hold, `1`–`9` answers the alarm.
- **Leaving an open alarm:** `b` won't take you out of the gate room while an alarm is open. The log reads `ALARM OPEN — GIVE AN ORDER FIRST`. After you answer, `b` takes you back to the same briefing-room screen, a half-typed note and all.
- **Hostiles on their heels:** a team coming home from a dangerous world (Unas, Jaffa or Goa'uld) has a 15% chance of being followed. You decide before they step through: open the iris briefly and risk a firefight, or keep it closed and risk losing the team.
- **Losing:** the base falls if a security breach hits while Security is at 0, if Jaffa win a gate-room firefight after you open the iris to an unknown code, or if a device you try to push back through the gate goes off.

### Standing orders

The first option is the default in each case. Change them in the briefing room.

| Situation | Options |
|---|---|
| Unknown IDC or no IDC | Keep the iris closed · Open for 30 seconds under guard |
| Our IDC, team reports hostiles following | Open briefly, then close · Keep closed until clear |
| A compromised or revoked IDC | Keep closed, revoke the code · Keep closed, say nothing |
| Object through the gate | Seal the level · Dial out, push it back through |
| Missed check-in | Send a MALP to search · Send the nearest available team · Wait 12 hours |
| Team under fire at a check-in | Recall them · Hold position · Reinforce if a team is available |
| Contact or trade offer during a check-in | Defer to the debrief · Accept if the team is diplomatic |

A missed check-in plays out as follows:

- **A MALP search** reports back in 1 game hour. The team is found safe 60% of the time, pinned down and injured 25%, and otherwise captured.
- **A team search** takes 6 game hours: found safe 80%, injured 15%, captured 5%.
- **Waiting 12 hours:** the team turns up 50% of the time, is captured 30%, and is lost otherwise.

### Worlds and drones

- **Worlds:**
  - An unexplored world is only glyphs and a designation like `P3X-866`. Its names stay hidden until intel reveals them: the locals, the ruins, the Jaffa, a UAV intercept and so on. A world can have several names.
  - Some addresses won't lock and are marked LOST.
  - A world counts as surveyed once, for good. Surveying a HOSTILE world counts too, though it stays HOSTILE.
- **Drones:**
  - You start with 4 MALPs and 2 UAVs. Stores hold at most 6 MALPs and 3 UAVs.
  - At midnight a MALP is delivered every day, and a UAV every third day, unless the fleet is already at the cap. Drones out in the field count towards the cap.
  - A MALP's telemetry comes back in 1–2 game hours, and a UAV's in 45–90 minutes. A UAV sees more (settlements and head counts), and sometimes catches a name over comms. It's also more likely to be shot down over Jaffa or Goa'uld worlds.
  - The UAV is its own aircraft, a small straight-wing drone with a pusher propeller:
    - **Launch:** it fires off a launch rail at the foot of the ramp and climbs into the wormhole.
    - **Home:** a recalled UAV flies back out of the gate nose first, lands on the ramp and rolls to a stop.
    - **Aerial feed:** its report plays on a monitor in the corner of the gate image. It shows the ground scrolling past, a crosshair, `REC ●` and its altitude and heading, and it boxes a contact when the UAV spots a settlement or a structure. The side screen reads out ALT, HDG, SPEED and FUEL, then what the UAV learned.
    - **Colours:** a world's ground always looks the same. It only takes on the world's colours once telemetry has told you its environment.
    - **Lost:** if the UAV is shot down or captured, the feed breaks up into static and `SIGNAL LOST`.
    - **Small panes:** when the gate image is under 160 pixels, the monitor is left out and the side screen still reads out.
  - Drones can be destroyed by a harsh world or captured by its garrison. Capture marks the world HOSTILE.
  - One that survives stays parked on the world, one drone per world. Recall it through the gate, or the next team there brings it home.
  - A drone that comes home to full stores is scrapped.
  - A search MALP stays on the world, unless a drone is already parked there. In that case it comes home.
- **The gate** does one thing at a time. Check-ins go first, then other incoming traffic, then your queued dial-outs.

### Teams

- **The teams:** SG-1 (every specialty), SG-2 (recon), SG-3 (combat) and SG-4 (science). Several can be out at once.
- **Missions:**
  - A world must be probed before a team can go.
  - A survey takes 20–29 game hours. A contact mission takes 31–43 hours. It needs a diplomat, so only SG-1 can run one, and only on a world where contact has been unlocked (Abydos from the start, others through locals or allies met on a mission).
  - SG-2 is a quarter faster than the others, and SG-1 an eighth.
- **Check-ins:** every 8 game hours, or 6 for SG-2. A check-in is missed 3–25% of the time, depending on how dangerous the world is. Each rank takes 5 points off that chance, and recon takes 5 more.
- **Rank:** Green, then Seasoned at 3 completed missions, Veteran at 8 and Elite at 15. A few check-in events count as one more mission.
  - Each rank adds 5% to a team's check-in and debrief odds, and one more chance of new addresses in a debrief.
  - A team that comes home injured drops a rank.
- **Statuses:** the briefing room and the Database show each team's status with its time left.

  | Status | Meaning |
  |---|---|
  | `BASE` | ready |
  | `AWAY: <world>` | on a mission (the Database shows `OFFWORLD`, with the world under LOCATION) |
  | `STOOD DOWN 11H` | a new IDC was issued: 12 game hours off duty, after a revoke or a rescue |
  | `INJURED 1D 20H` | 2 days in the infirmary |
  | `CAPTURED 4D` | presumed lost after 5 days |
  | `RE-FORMING 2D 5H` | a lost team's number is re-formed, Green, after 3 days |
  | `LOST` | lost, with no re-forming under way (rarely seen) |

  The gate room's team panel uses the same words in capitals, without the time left: `STOOD DOWN`, `INJURED`, `AWAY: ABYDOS`.

### The briefing room

Press `b` to walk up and `b` again to walk back down. The walk takes `transition_seconds`, 10 by default.

- A walk that starts while the gate is showing real traffic waits for it to finish. The idle scene is cut instead.
- `q` during the walk down logs `WALKING — Q AGAIN ON ARRIVAL`.

What's in the briefing room:

- **Dialing list:** every address with its status and any drone on it. An address offers:
  - MALP probe (with the number left)
  - UAV flight
  - Recall drone
  - Assign team
  - Add note
- **Assign a team:** shows all four teams. Unavailable ones are greyed out with the reason. Then pick the mission type.
- **Teams:** the roster. A team can have its IDC revoked and reissued, after a confirmation that says whether the team stands down.
  - A team at base stands down for 12 game hours.
  - An injured or re-forming team keeps the longer of its own timer and 12 hours. (The roster can't revoke a lost or re-forming team's code; a scenario can.)
  - A captured team's timer doesn't change, and the confirmation says `… DOES NOT STAND DOWN.` Neither does a team that's away.
- **Standing orders:** choose a situation to cycle through its options.
- **Pace:** Relaxed, Standard or Busy. It shows `PACE · LOCKED` when `game_pace` is set.

An action you can't take yet is greyed out and tells you why when you choose it.

### The SGC Database

Press `d` for the full-screen SGC Database. It has six tabs:

- **Addresses:** name, glyphs, status, last visit, flags and drone. The flags are `T` for a team there, `N` for notes and `L` for an address from intel.
- **World file:** names and where they came from, telemetry, mission options, reports and your notes.
- **Missions:** team, world, type, start, outcome, casualties and findings.
- **Teams:** specialty, rank, status, location and history.
- **Intel:** names learned, and leads to addresses not yet visited.
- **Queue:** what's scheduled, soonest first:
  - dial-outs waiting for the gate, in the order the gate will take them
  - drones through the gate, with the window their report is due in
  - missions in the field, with the next check-in and when the team is due home
  - teams stood down, injured, captured or re-forming, with when that ends

  Incoming wormholes and anything else you haven't been told about never show.

On the Addresses tab:

- `/` searches known names, designations and glyphs. Enter keeps the search; Ctrl+C or Esc clears it.
- `s` sorts by status or name.
- `f` filters by status: all, unexplored, probed, surveyed, contact, hostile or lost.

On the Queue tab:

- `x` cancels the selected dial-out. Press it again to confirm.
  - A MALP or UAV goes back to stores.
  - A departing team stands by at base, and its mission reads CANCELLED.
  - A recall is withdrawn, and the drone stays where it is.
  - A withdrawn search leaves the missing team to the 12-hour wait.

  Anything already through the gate can't be cancelled here, and the tab says why.
- `[` and `]` move the selected dial-out up or down the gate queue. Check-ins and other incoming traffic still go first. The new order is saved.
- `/` searches the rows' text.

On a small terminal the tabs stack into a single column.

An alarm closes the Database but keeps it, and `d` brings back the same tab, search and scroll. Closing it yourself with `q` starts it fresh next time.

### Controls

`?` cycles the legend from a one-line bar, to the full help, to off. The choice is saved as `legend` in the config.

- The gate room's full help replaces the side panel, except while an alarm is open. It lists every key, `p` (pause, Recruit only) and Ctrl+C / Esc (cancel typing) included.
- The briefing room always shows the bar.
- The Database's full legend adds a help block of every key above its bar. Its bar lists only the keys that work on the current tab.

| Key | Gate room |
|---|---|
| `1`–`9` | give an order when an alarm is up |
| `b` | walk up to the briefing room |
| `d` | open the SGC Database |
| `?` | legend: bar, full, off |
| `p` | pause (Recruit only) |
| `m` / `+` / `-` | mute, volume |
| `q` / Ctrl+C | save and shut down; press again to quit at once |

| Key | Briefing room |
|---|---|
| `↑` `↓` Enter, `1`–`9` | choose |
| `q` | back out of a list; at the top, walk back down |
| `b` | walk back down to the gate room |
| `d` | open the SGC Database |
| `?` | legend |
| `m` / `+` / `-` | mute, volume |
| Enter | save a note |
| Ctrl+C / Esc | discard a note and go back to its address |
| Ctrl+C | outside a note, save and shut down |

| Key | SGC Database |
|---|---|
| `←` `→` Tab | switch tabs |
| `↑` `↓` | select a row; scroll a world file |
| Enter | open the selected world's file (Addresses, Missions, Intel) |
| `/` `s` `f` | search, sort, filter the addresses (/ also searches the Queue) |
| `x` | cancel the selected dial-out on the Queue tab (press twice) |
| `[` `]` | move the selected dial-out up or down the gate queue |
| Ctrl+C / Esc | clear and close a search |
| `?` | legend: bar, full (with the help block), off |
| `m` / `+` / `-` | mute, volume |
| `q` | back to where you were |
| Ctrl+C | outside a search, save and shut down |

The menus use `↑` `↓` Enter or `1`–`9`, with `q` to go back (at the top, `q` shuts down).

Notes and searches take accented letters. A character that doesn't fit in one cell shows as `?`.

The idle scene: after 45 quiet seconds, the gate sometimes dials a science uplink to a world you know. It's only for show and changes nothing. Real traffic, an alarm or your walk cuts it.

### Writing scenarios

Scenarios are TOML files in `src/sgc/data/scenarios/`. Add your own in `~/.config/stargate-sgc/scenarios/`. A file with the same `id` replaces the built-in one.

A scenario is a small graph of nodes, starting at `start`:

- Each node has text: `full`, plus optional `partial` and `minimal` for harder difficulties.
- Each node has 1–4 choices and a `default` choice. A choice can have `requires`; the default can't.
- Each choice's outcome can do one of these:
  - `goto` another node
  - `end`
  - `roll` odds (1–99, with `mods`) into `win` and `lose` outcomes. Rolls are held to 5–95%, and a team adds 5% per rank on check-ins and debriefs.
- An outcome can also play a `visual` and apply `effects`.
- A node with one choice just happens. A node with more raises an alarm.
- Top-level keys: `id`, `kind`, `weight`, `when`, `visual`, `goauld`, `team`, `mission_type` and `on`.

Kinds:

- `incoming`: an unscheduled wormhole, every 36–96 game hours. The first comes 36–96 hours in.
  - `team = "compromised" | "captured" | "base" | "any"` picks a team for it.
  - `on = "team_return"` plays it as a team comes home from a dangerous world instead.
- `probe`: plays after a drone's telemetry, chosen by the world's traits in `when`. The text never names the drone ("Telemetry from…").
- `checkin` and `debrief`: during and after a mission. `mission_type = "survey" | "contact"` narrows them.

A node that answers to a standing order names its `situation`: `unknown_idc`, `hostiles_following`, `bad_idc`, `object`, `under_fire` or `contact_offer`. Its `default` must be that situation's first option, and it needs a choice keyed for every option.

Placeholders must be bound:

- `{world}` and `{designation}`: in probes, check-ins, debriefs and `team_return` scenarios.
- `{team}` and `{specialty}`: in check-ins, debriefs and `team_return` scenarios.
- `{team}` and `{captured_at}`: with `team = ...`.
- `{goauld}`: with `goauld = "any"`.

Visuals: `incoming`, `dial_out`, `iris_hold`, `arrival`, `team_return`, `firefight`, `firefight_win`, `bomb` and `asgard_beam`. Any ambient event name works too (`science`, `code_red`, `kawoosh_hazard`, …).

Effects:
- meters: `security -10`, `personnel +5`. `breach 20` lowers Security; if it's already 0, the base falls.
- teams:
  - `team {team} captured` (or `base`, `offworld`, `injured`, `lost`)
  - `idc {team} revoke`, `idc {team} compromise`
  - `xp {team} +1`, `recall {team}`, `reinforce {team}` (a free team joins for 6 hours)
- worlds:
  - `reveal address`
  - `reveal name {world} from locals`, with an optional `"A Name"` before `from`. The sources are `locals`, `ruins`, `jaffa`, `goauld`, `comms`, `allies` and `records`.
  - `unlock contact {world}`, `status {world} hostile`, `drone {world} lost` (or `captured`)
- other:
  - `schedule incoming in 6h`, `game_over <text>`
  - `gain ally.tokra` (allies: `tokra`, `asgard`, `tollan`, `nox`, `jaffa`; tech: `tech.zat`, `tech.naquadah_generator`, `tech.lrs`)
  - `use ally.asgard`, `use ally.nox` (single-use)
- any effect can end with `unless <flag>` (except `game_over`)

Conditions (used in `when`, and most also in a choice's `requires` or a roll's `mods`):
- `security >= 30`, `day >= 5`, `ally.tokra`, `not tech.zat`
- teams:
  - `team SG-1 base`, `team {team} specialty combat`, `rank {team} >= veteran`
  - `team_available`, `any_compromised_idc`, `any_captured`
- worlds: `known {world}`, `status {world} probed`, `unlocked contact {world}`
- hidden world traits, which pick which scenario plays and so may only appear in `when`: `world {world} env toxic`, `world {world} inhabitants jaffa`, `world {world} feature ruins`

A world can be `{world}` or a designation like `P3X-866`. A team can be `{team}` or a name like `SG-1`.

One of your own scenarios with a mistake is skipped, with a log line that names the file and the problem. A mistake in a built-in scenario stops a campaign from starting. The built-in scenarios are good examples to copy.

## Config

The config file is `~/.config/stargate-sgc/config.toml`. Every key is optional. Bad values fall back to the defaults, and the problem is shown in the log.

```toml
volume = 0.3              # 0..1
sound = true
sound_pack = "synth"      # "synth" (our own) or "freesound"
fps = 24                  # 5..60, drops automatically if the terminal can't keep up
speed = 1.0               # animation speed multiplier
open_scale = 1.0          # how long the gate stays open (multiplier)
canon_ratio = 0.6         # share of canon vs random addresses
exit_duration = 6.0       # seconds of the animated shutdown
graphics = "auto"         # auto | kitty | sixel | iterm | blocks
# font_path = "/path/to/stargate_sg1_adress_glyphs.ttf"
transition_seconds = 10   # seconds to walk between the briefing room and the gate room (2-30)
# game_pace = 60          # real seconds per game hour (5-600); overrides and locks every campaign's pace
notify = true             # desktop notifications for game alarms (needs notify-send)
legend = "bar"            # the game's controls legend: "bar" | "full" | "off" (? cycles and saves it)

[event_weights]           # relative frequency of each event
science = 1.0
traffic = 1.2
malp = 1.0
failed_dial = 0.5
code_red = 0.7
friendly = 0.9
kawoosh_hazard = 0.4
```

## Sound

There are two packs:

- **`synth`** is original sound generated by `tools/make_sounds.py`, using only the stdlib. It's free to ship. Regenerate it with `python tools/make_sounds.py`.
- **`freesound`** is CC0/CC BY freesound previews, converted by `tools/convert_freesound.py`. See `assets/sounds/freesound/CREDITS.md`.

To override any sound, drop your own file in `~/.config/stargate-sgc/sounds/`. Use one of these names, with a `.wav` extension (`.ogg`, `.mp3` and `.flac` also work if ffmpeg is installed):

- `ring_spin`, `chevron_lock`, `kawoosh`, `wormhole_hum`, `shutdown`
- `iris_close`, `iris_open`, `iris_impact`
- `klaxon`, `dial_fail`, `idc_accept`

Playback goes through `pw-cat` (PipeWire), `paplay` (PulseAudio, including WSLg) or `aplay`, in that order. If none is found, it runs silently.

- **Format:** audio is streamed as 16-bit mono at 44.1 kHz. `pw-cat` and `paplay` are given that format explicitly, which works with every PipeWire version, including the 1.0.x in Ubuntu 24.04 (its `pw-cat` ignores WAV headers). `aplay` gets a WAV header instead.
- **Volume:** the stream shows up as **sgc** ("Stargate SGC") in your system's sound settings, and has its own volume there. The `+` / `-` keys change sgc's own mix on top of that.

**No sound?**
1. Check that a player is installed: `which pw-cat paplay aplay`.
2. Look for **sgc** in your sound settings (or `wpctl status` under *Streams*). Make sure it isn't muted or turned down, and that it's going to the output you're listening on.
3. Try `sgc --event kawoosh_hazard`: the kawoosh is one of the loudest cues.

## Terminal support

| Terminal | Graphics | Status |
|---|---|---|
| Ghostty 1.3.1 | kitty protocol | primary target; being tested live |
| Kitty, WezTerm, Konsole | kitty protocol | designed for, not yet verified |
| foot, xterm (vt340), mlterm, tmux ≥ 3.4 | sixel | designed for, not yet verified |
| GNOME Terminal and other truecolor terminals | half-blocks | designed for, not yet verified |
| Windows Terminal ≥ 1.22 (WSL) | sixel, or half-blocks | designed for, not yet verified |
| iTerm2 (macOS) | iTerm2 inline images | designed for, not yet verified |

## Development

```sh
.venv/bin/pytest -q
```

## Credits

- **Glyph character map** from [SG Dial Sim](https://github.com/kc7zax/sg-dial-sim) (MIT).
- **Address data** cross-checked between the [Stargate Wiki](https://stargate.fandom.com/wiki/Glyph) (CC BY-SA) and [rdanderson.com](https://rdanderson.com/stargate/glyphs/index.htm). The data came in via [StargateProject](https://github.com/jonnerd154/StargateProject-software).
- **Sound synthesis approach** inspired by [stargate-command](https://github.com/IAmMcNuggets/stargate-command) (MIT).
- **Freesound effects** are credited in `assets/sounds/freesound/CREDITS.md`.

Stargate SG-1 is © MGM. This is a non-commercial fan project and uses no show audio or footage.

## License

Code is MIT (see `LICENSE`). The freesound effects keep their own CC0 / CC BY licences, listed in `assets/sounds/freesound/CREDITS.md`. The `synth` sound pack is original and covered by the MIT licence.
