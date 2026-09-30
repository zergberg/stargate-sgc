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

You command the SGC. Incoming activations stop for your orders, SG teams go out on missions from the briefing room, and your goal is to bring down the Goa'uld System Lords.

- **Modes:**
  - **Campaign:** defeat 3 System Lords, then get General Hammond's debrief and rating.
  - **Endless:** play until the base falls. A new System Lord rises whenever one is defeated.
- **Difficulty:** Recruit, Officer or Commander. Harder difficulties mean costlier mistakes and less information: on Commander, an incoming signal may only say "IDC received".
- **Orders:** press `1`–`4` before the countdown runs out. If you don't, the SGC follows standing procedure, usually the cautious option.
- **Base meters:** Security, Personnel and Intel. Quiet cycles let Security and Personnel recover a little.
- **Losing:** if the base is overrun, the game is over. That only happens through your own calls:
  - trusting an IDC you shouldn't have
  - losing a firefight in the gate room
  - not dealing with a bomb sent through the gate
- **Stand-downs:** an injured team is out for 2 cycles, and a lost team re-forms after 3. A captured team not rescued within 5 cycles is presumed lost. Press `r` to review and revoke IDC codes between cycles; revoking one stands that team down for the next cycle.
- **Allies and technology:** the Tok'ra, Tollan and Free Jaffa, zat'nik'tels, naquadah generators and long-range sensors give a lasting advantage. The Asgard and the Nox can each be called in once before you need to earn them again.
- **Pacing:** walking between the briefing room and the control room takes `transition_seconds` (default 10) and can't be skipped. Things take time. Pause (`p`) is only available on Recruit.
- **Saving:** progress saves automatically to `~/.local/share/stargate-sgc/campaign.json`, and `q` saves and quits. A saved game resumes at the start of the next cycle; a mission still in progress when you quit is called off. Starting a new game over a save asks you to confirm first. Finished runs go into the hall of records on the menu.

### Writing scenarios

Gate-room scenarios and missions are TOML files in `src/sgc/data/scenarios/`. Add your own in `~/.config/stargate-sgc/scenarios/`; a file with the same `id` replaces the built-in one. A scenario is a small graph of nodes. Each node has text (`full`, plus optional `partial`/`minimal` for harder difficulties), a `default` choice for when the countdown runs out, and 1–4 choices. Each choice's outcome can `goto` another node, play a `visual`, apply `effects`, `end`, or `roll` odds with modifiers.

A node's text can only use a `{placeholder}` the scenario actually binds: `{team}` needs `team = "compromised" | "captured" | "base" | "any"` (or being a mission), `{captive}` needs `captive = true`, `{goauld}` needs `goauld = "aggressor" | "any" | "weakest"`, and `{destination}` is always available. Format specs like `{team:>5}` aren't allowed. A node's `default` choice must always be able to reach an end, so a stalled countdown can never dead-end.

Effects:
- meters: `security -10`, `personnel +5`, `intel +15`
- `breach 20`: a security breach. If Security is already 0, the base falls.
- teams: `team {team} captured`, `idc {team} revoke`
- Goa'uld: `goauld {goauld} strength -1`, `goauld {goauld} aggression +10`
- allies and technology: `gain ally.tokra`; `use` only works on the single-use allies, `ally.asgard` and `ally.nox`
- `game_over <text>`
- any effect can end with `unless <flag>` (except `game_over`)

Conditions (used in `when`, `requires` and roll `mods`):
- meters and cycles: `intel >= 30`, `cycles >= 5`
- flags: `ally.tokra`, `not tech.zat`
- teams: `any_compromised_idc`, `any_captured`, `team SG-1 base`
- Goa'uld: `goauld {goauld} strength <= 1`

A scenario with a mistake is skipped with a log line that names the file and the problem. The built-in scenarios are good examples to copy.

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
transition_seconds = 10   # walking between the briefing room and the control room (2-30)
decision_countdown = 12   # seconds to give an order (5-30)

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
