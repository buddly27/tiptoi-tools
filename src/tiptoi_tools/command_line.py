from pathlib import Path

import click

import tiptoi_tools.audio
import tiptoi_tools.gme
import tiptoi_tools.media


class GmeContext:
    """Context object holding the parsed GME file and raw data."""

    def __init__(self, gme_file: Path):
        self.gme_file = gme_file
        self._parsed = None
        self._data = None

    @property
    def parsed(self):
        """Lazily parse the GME file."""
        if self._parsed is None:
            self._parsed = tiptoi_tools.gme.parse_file(self.gme_file)
        return self._parsed

    @property
    def data(self) -> bytes:
        """Lazily read the raw file data."""
        if self._data is None:
            self._data = self.gme_file.read_bytes()
        return self._data


pass_gme = click.make_pass_decorator(GmeContext)


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.argument(
    "gme_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.version_option(package_name="tiptoi-tools")
@click.pass_context
def cli(ctx: click.Context, gme_file: Path) -> None:
    """Tools for working with Tiptoi GME files.

    Usage: tiptoi-tools <file.gme> <action> [options]

    Examples:
      tiptoi-tools file.gme info
      tiptoi-tools file.gme play 123
      tiptoi-tools file.gme play @1632
      tiptoi-tools file.gme games -v
    """
    ctx.obj = GmeContext(gme_file)
    if ctx.invoked_subcommand is None:
        # Default to info if no subcommand given
        ctx.invoke(info_cmd)


@cli.command("info")
@pass_gme
def info_cmd(gme: GmeContext) -> None:
    """Show general information about the GME file."""
    parsed = gme.parsed
    hdr = parsed.header

    click.echo(f"File: {gme.gme_file}")
    click.echo(f"Size: {gme.gme_file.stat().st_size} bytes")
    click.echo("")
    click.echo("Header:")
    click.echo(f"  Product id code:               {hdr.product_id_code}")
    click.echo(f"  Raw XOR value:                 0x{hdr.raw_xor:04X}")
    click.echo(f"  Comment:                       {hdr.comment}")
    click.echo(f"  Date string:                   {hdr.date_string}")
    click.echo(f"  Language:                      {hdr.language_string or '(none)'}")
    click.echo("")

    click.echo(f"Registers: {len(parsed.registers)}")
    click.echo("  init: " + (tiptoi_tools.gme.serialize(parsed).get("init") or ""))
    click.echo("")

    welcome = parsed.welcome_sounds.serialize(collapse=True)
    click.echo(f"Welcome sounds: @{welcome}" if welcome else "Welcome sounds: (none)")
    click.echo(f"Audio table entries: {len(parsed.media_entries)}")
    click.echo(f"Audio table copy: {parsed.duplicated_table.value}")
    click.echo(f"Audio XOR values: {_print_audio_xors(parsed.media_entries)}")

    b1, b2, b3 = parsed.binary_tables_entries
    click.echo(f"Binary tables entries: {b1}/{b2}/{b3}")

    s1, s2, s3 = parsed.single_binary_tables_entries
    click.echo(f"Single binary table entries: {s1}/{s2}/{s3}")

    if parsed.special_oids is None:
        click.echo("Special OIDs: <none>")
    else:
        replay, stop = parsed.special_oids
        click.echo(f"Special OIDs: replay={replay}, stop={stop}")

    click.echo("")
    click.echo(f"Scripts: {len(parsed.script_table.active_oids)} present")
    st = parsed.script_table
    click.echo(f"OID range: {st.first_oid}-{st.last_oid}")
    click.echo(f"Games: {len(parsed.games)} total")
    click.echo("")
    found = parsed.checksum_found
    calc = parsed.checksum_calculated
    click.echo(f"Checksum found 0x{found:08X}, calculated 0x{calc:08X}")


@cli.command("extract")
@click.option(
    "--dir",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("media"),
    show_default=True,
    help="Media output directory",
)
@click.option(
    "--limit", type=int, default=None, help="Only extract first N media files"
)
@pass_gme
def extract_cmd(gme: GmeContext, out_dir: Path, limit: int | None) -> None:
    """Extract and decrypt all media files from the GME."""
    parsed = gme.parsed
    hdr = parsed.header

    out_dir.mkdir(parents=True, exist_ok=True)

    entries = parsed.media_entries
    if not entries:
        raise click.ClickException(
            "No media entries found (media table missing or unparseable)."
        )

    click.echo(
        f"Found {len(entries)} media entries in table at 0x{hdr.media_table_offset:08X}"
    )

    count = 0
    for entry in entries:
        if limit is not None and count >= limit:
            break

        enc = gme.data[entry.offset : entry.offset + entry.length]
        dec = tiptoi_tools.media.decrypt_media(enc, entry.magic_xor)
        ext = tiptoi_tools.media.guess_extension(dec)

        out_path = out_dir / f"{entry.index:04d}{ext}"
        out_path.write_bytes(dec)

        click.echo(
            f"  [{entry.index:4d}] off=0x{entry.offset:08X} len={entry.length:8d}"
            f" -> {out_path.name}"
        )
        count += 1


@cli.command("export")
@click.argument(
    "out_file", required=False, type=click.Path(dir_okay=False, path_type=Path)
)
@click.option(
    "--media-path",
    default=None,
    help="Value for the 'media-path' field in YAML. Default: media/{stem}_%s",
)
@pass_gme
def export_cmd(gme: GmeContext, out_file: Path | None, media_path: str | None) -> None:
    """Export the GME file to tttool-compatible YAML format."""
    parsed = gme.parsed

    if out_file is None:
        out_file = gme.gme_file.with_suffix(".yaml")

    if media_path is None:
        media_path = f"media/{gme.gme_file.stem}_%s"

    tiptoi_tools.gme.export_yaml(parsed, out_file, media_path=media_path)
    click.echo(f"Wrote {out_file}")


@cli.command("play")
@click.argument("target", type=str)
@click.option(
    "--all",
    "play_all",
    is_flag=True,
    help="Play all audio from all script lines (not just the first)",
)
@click.option(
    "--line",
    "line_index",
    type=int,
    default=None,
    help="Play audio from a specific script line (0-indexed)",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show audio player output for debugging",
)
@click.option(
    "--save",
    "save_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Save audio files to this directory instead of playing",
)
@pass_gme
def play_cmd(
    gme: GmeContext,
    target: str,
    play_all: bool,
    line_index: int | None,
    verbose: bool,
    save_dir: Path | None,
) -> None:
    """Play audio by OID or media index.

    TARGET can be:
      123      - Play audio for OID 123
      @456     - Play media index 456 directly
      @1,2,3   - Play multiple media indices
    """
    parsed = gme.parsed

    # Parse target: @prefix means media index, otherwise OID
    if target.startswith("@"):
        try:
            media_indices = [int(x.strip()) for x in target[1:].split(",")]
        except ValueError:
            click.echo(f"Invalid media index format: {target}")
            raise SystemExit(1) from None
        source_desc = f"media {target[1:]}"
    else:
        try:
            oid = int(target)
        except ValueError:
            click.echo(f"Invalid target: {target} (use OID number or @media_index)")
            raise SystemExit(1) from None

        # Check if OID exists
        if oid not in parsed.script_table.scripts:
            available = parsed.script_table.active_oids
            if available:
                click.echo(
                    f"OID {oid} not found. Available: {available[0]}-{available[-1]}"
                )
            else:
                click.echo(f"OID {oid} not found. No scripts in this file.")
            raise SystemExit(1)

        script_lines = parsed.script_table.scripts[oid]
        if script_lines is None or len(script_lines) == 0:
            click.echo(f"OID {oid} has no script (null pointer)")
            raise SystemExit(1)

        # Determine which lines to play
        if line_index is not None:
            if line_index < 0 or line_index >= len(script_lines):
                n = len(script_lines)
                click.echo(f"Line {line_index} out of range. OID {oid} has {n} line(s)")
                raise SystemExit(1)
            lines_to_play = [script_lines[line_index]]
        elif play_all:
            lines_to_play = script_lines
        else:
            lines_to_play = [script_lines[0]]

        # Collect all unique media indices
        media_indices = []
        for line in lines_to_play:
            for idx in line.audio_links:
                if idx not in media_indices:
                    media_indices.append(idx)

        if not media_indices:
            click.echo(f"OID {oid} has no audio links")
            raise SystemExit(1)
        source_desc = f"OID {oid}"

    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        n = len(media_indices)
        click.echo(f"{source_desc}: saving {n} audio file(s) to {save_dir}")
    else:
        click.echo(f"{source_desc}: {len(media_indices)} audio file(s) to play")
        click.echo(f"Audio player: {tiptoi_tools.audio.get_player_info()}")

    for idx in media_indices:
        if idx < 0 or idx >= len(parsed.media_entries):
            click.echo(f"  [{idx}] Invalid media index (skipping)")
            continue

        entry = parsed.media_entries[idx]
        if entry.length == 0:
            click.echo(f"  [{idx}] Empty media entry (skipping)")
            continue

        enc = gme.data[entry.offset : entry.offset + entry.length]
        dec = tiptoi_tools.media.decrypt_media(enc, entry.magic_xor)
        ext = tiptoi_tools.media.guess_extension(dec)

        if verbose:
            hdr_bytes = dec[:16].hex() if len(dec) >= 16 else dec.hex()
            click.echo(f"    XOR key: 0x{entry.magic_xor:02X}, header: {hdr_bytes}")

        if save_dir:
            out_path = save_dir / f"{idx:04d}{ext}"
            out_path.write_bytes(dec)
            click.echo(f"  [{idx}] Saved to {out_path} ({entry.length} bytes)")
        else:
            click.echo(f"  [{idx}] Playing ({entry.length} bytes, {ext})...")
            try:
                tiptoi_tools.audio.play_audio(dec, verbose=verbose)
            except tiptoi_tools.audio.AudioPlaybackError as e:
                click.echo(f"    Error: {e}")
                raise SystemExit(1) from None

    click.echo("Done.")


@cli.command("games")
@click.option("-v", "--verbose", is_flag=True, help="Show detailed subgame info")
@pass_gme
def games_cmd(gme: GmeContext, verbose: bool) -> None:
    """List games and their structure."""
    parsed = gme.parsed

    if not parsed.games:
        click.echo("No games found.")
        return

    click.echo(f"Found {len(parsed.games)} game(s):\n")

    for i, game in enumerate(parsed.games, start=1):
        type_name = _game_type_name(game.game_type)
        click.echo(f"Game #{i}: {type_name} (type {game.game_type})")

        if game.game_type == 253:
            continue

        f = game.fields
        subgame_count = f.get("gSubgameCount", 0)
        rounds = f.get("gRounds", 0)
        click.echo(f"  Subgames: {subgame_count}")
        click.echo(f"  Rounds: {rounds}")

        if verbose and (subs := f.get("gSubgames")):
            for j, sg in enumerate(subs, start=1):
                n1, n2, n3 = len(sg.oid1s), len(sg.oid2s), len(sg.oid3s)
                click.echo(f"    Subgame #{j}:")
                click.echo(f"      OIDs: {n1}/{n2}/{n3} (oid1s/oid2s/oid3s)")

                if sg.oid1s:
                    oids = " ".join(str(o) for o in sg.oid1s)
                    click.echo(f"        oid1s: {oids}")
                if sg.oid2s:
                    oids = " ".join(str(o) for o in sg.oid2s)
                    click.echo(f"        oid2s: {oids}")
                if sg.oid3s:
                    oids = " ".join(str(o) for o in sg.oid3s)
                    click.echo(f"        oid3s: {oids}")

                total_entries = sum(len(pl) for pl in sg.playlists)
                n_pl = len(sg.playlists)
                click.echo(f"      Playlists: {n_pl} ({total_entries} entries)")

                for k, pl in enumerate(sg.playlists):
                    if pl:
                        media_ids = [m for entry in pl for m in entry]
                        media_str = ",".join(str(m) for m in media_ids[:10])
                        if len(media_ids) > 10:
                            media_str += f"... (+{len(media_ids) - 10} more)"
                        click.echo(f"        [{k}]: {len(pl)} -> @{media_str}")

        click.echo()


@cli.command("scripts")
@click.argument("oid", type=int, required=False)
@click.option(
    "--action",
    type=click.Choice(["play", "game", "jump", "cancel", "timer", "arithmetic"]),
    help="Filter scripts by action type",
)
@click.option("--media", type=int, help="Find scripts referencing this media index")
@click.option("--register", type=int, help="Find scripts using this register")
@pass_gme
def scripts_cmd(
    gme: GmeContext,
    oid: int | None,
    action: str | None,
    media: int | None,
    register: int | None,
) -> None:
    """Browse and search scripts.

    With no arguments, lists all scripts with a summary.
    With an OID argument, shows detailed info for that script.
    Use --action, --media, or --register to filter scripts.
    """
    parsed = gme.parsed

    # If a specific OID is requested, show detailed view
    if oid is not None:
        _show_script_detail(parsed, oid)
        return

    # Build list of scripts with their properties
    script_infos = []
    for script_oid, lines in parsed.script_table.scripts.items():
        if lines is None or len(lines) == 0:
            continue
        info = _analyze_script(script_oid, lines)
        script_infos.append(info)

    if not script_infos:
        click.echo("No scripts found.")
        return

    # Apply filters
    if action:
        action_map = {
            "play": {
                "PlayMedia",
                "PlayMediaRange",
                "PlayRandomInRange",
                "PlayVariantRandom",
                "PlayVariantAll",
            },
            "game": {"StartGame"},
            "jump": {"Jump"},
            "cancel": {"Cancel"},
            "timer": {"SetTimer"},
            "arithmetic": {"Arithmetic"},
        }
        target_actions = action_map.get(action, set())
        script_infos = [s for s in script_infos if s["actions"] & target_actions]

    if media is not None:
        script_infos = [s for s in script_infos if media in s["media_refs"]]

    if register is not None:
        script_infos = [s for s in script_infos if register in s["registers"]]

    if not script_infos:
        click.echo("No scripts match the filters.")
        return

    # Display results
    oid_range = f"{parsed.script_table.first_oid}-{parsed.script_table.last_oid}"
    click.echo(f"OID Range: {oid_range} ({len(script_infos)} scripts)\n")

    # Header
    click.echo(f"{'OID':<8} {'Lines':<6} {'Actions':<35} {'Audio'}")
    click.echo("-" * 75)

    for info in script_infos:
        actions_str = ", ".join(sorted(info["actions"])) if info["actions"] else "-"
        if len(actions_str) > 35:
            actions_str = actions_str[:32] + "..."

        audio_refs = info["media_refs"]
        if len(audio_refs) == 0:
            audio_str = "[]"
        elif len(audio_refs) <= 3:
            audio_str = "@" + ",".join(str(m) for m in sorted(audio_refs))
        else:
            sorted_refs = sorted(audio_refs)
            audio_str = f"@{sorted_refs[0]}..{sorted_refs[-1]} ({len(audio_refs)})"

        click.echo(
            f"{info['oid']:<8} {info['line_count']:<6} {actions_str:<35} {audio_str}"
        )


@cli.command("oids")
@click.argument("oid", type=int, required=False)
@click.option("--game", type=int, help="Show OIDs used by this game (0-indexed)")
@pass_gme
def oids_cmd(gme: GmeContext, oid: int | None, game: int | None) -> None:
    """Explore OIDs and their relationships.

    With no arguments, shows OID range summary.
    With an OID argument, shows what that OID is used for.
    Use --game to list OIDs used by a specific game.
    """
    parsed = gme.parsed

    # If --game is specified, show OIDs for that game
    if game is not None:
        _show_game_oids(parsed, game)
        return

    # If a specific OID is requested, look it up
    if oid is not None:
        _lookup_oid(parsed, oid)
        return

    # Default: show OID range summary
    _show_oid_summary(parsed)


# Helper functions


def _game_type_name(game_type: int) -> str:
    """Return a human-readable name for a game type."""
    return {
        1: "Common",
        6: "Bonus",
        7: "Grouped",
        8: "Select",
        9: "Extra9",
        10: "Extra10",
        16: "Extra16",
        253: "Special",
    }.get(game_type, "Unknown")


def _print_audio_xors(entries: list[tiptoi_tools.gme.MediaEntry]) -> str:
    xors = sorted({e.magic_xor for e in entries})
    if not xors:
        return "[]"
    return "[" + ",".join(f"{x:#04X}" for x in xors) + "]"


def _analyze_script(oid: int, lines: list) -> dict:
    """Analyze a script and extract summary info."""
    actions: set[str] = set()
    media_refs: set[int] = set()
    registers: set[int] = set()

    for line in lines:
        # Collect audio links
        media_refs.update(line.audio_links)

        # Analyze conditions for register usage
        for cond in line.conditions:
            if cond.left.is_register:
                registers.add(cond.left.raw)
            if cond.right.is_register:
                registers.add(cond.right.raw)

        # Analyze actions
        for act in line.actions:
            actions.add(act.kind.value)

            # Track register usage in actions
            if act.register != 0:
                registers.add(act.register)

            # Extract register references from payloads
            if act.payload is not None:
                if hasattr(act.payload, "is_register") and act.payload.is_register:
                    registers.add(act.payload.raw)
                elif isinstance(act.payload, tuple) and len(act.payload) == 3:
                    # Arithmetic: (op, reg, value)
                    _, reg, val = act.payload
                    registers.add(reg)
                    if hasattr(val, "is_register") and val.is_register:
                        registers.add(val.raw)

    return {
        "oid": oid,
        "line_count": len(lines),
        "actions": actions,
        "media_refs": media_refs,
        "registers": registers,
    }


def _show_script_detail(parsed, oid: int) -> None:
    """Show detailed info for a specific script."""
    st = parsed.script_table
    if oid not in st.scripts:
        click.echo(f"OID {oid} not found (range: {st.first_oid}-{st.last_oid})")
        raise SystemExit(1)

    lines = parsed.script_table.scripts[oid]
    if lines is None or len(lines) == 0:
        click.echo(f"OID {oid}: no script (null pointer)")
        return

    click.echo(f"OID {oid}: {len(lines)} line(s)\n")

    for i, line in enumerate(lines):
        click.echo(f"  Line {i}:")

        # Conditions
        if line.conditions:
            conds_str = " AND ".join(str(c) for c in line.conditions)
            click.echo(f"    Conditions: {conds_str}")
        else:
            click.echo("    Conditions: (none)")

        # Actions
        if line.actions:
            click.echo("    Actions:")
            for act in line.actions:
                click.echo(f"      - {_format_action_detail(act)}")
        else:
            click.echo("    Actions: (none)")

        # Audio
        if line.audio_links:
            audio_str = ", ".join(str(m) for m in line.audio_links)
            click.echo(f"    Audio: @{audio_str}")
        else:
            click.echo("    Audio: (none)")

        click.echo()


def _format_action_detail(action) -> str:
    """Format an action for detailed display."""
    kind = action.kind.value
    payload = action.payload

    if action.kind.value == "Cancel":
        return "Cancel"
    if action.kind.value == "StartGame":
        return f"StartGame({payload})"
    if action.kind.value == "Jump":
        return f"Jump(line {payload})"
    if action.kind.value == "PlayMedia":
        return f"PlayMedia(index {payload})"
    if action.kind.value in ("PlayMediaRange", "PlayRandomInRange"):
        if isinstance(payload, tuple):
            return f"{kind}({payload[0]}-{payload[1]})"
        return f"{kind}({payload})"
    if action.kind.value == "SetTimer":
        return f"SetTimer(${{action.register}}, {payload})"
    if action.kind.value == "Arithmetic":
        if isinstance(payload, tuple) and len(payload) == 3:
            op, reg, val = payload
            return f"${reg} {op} {val}"
        return f"Arithmetic({payload})"
    if action.kind.value == "NegateRegister":
        return f"Negate(${action.register})"
    if action.kind.value in ("PlayVariantRandom", "PlayVariantAll"):
        return f"{kind}({payload})"

    return f"{kind}({payload})"


def _show_oid_summary(parsed) -> None:
    """Show a summary of OID ranges and their usage."""
    active_oids = parsed.script_table.active_oids
    game_oids = _collect_game_oids(parsed)
    special = parsed.special_oids

    st = parsed.script_table
    click.echo(f"OID Range: {st.first_oid}-{st.last_oid}")
    click.echo(f"  Scripts: {len(active_oids)} OIDs with scripts")
    if active_oids:
        click.echo(f"    First: {min(active_oids)}, Last: {max(active_oids)}")
    click.echo()

    if special:
        replay, stop = special
        click.echo("Special OIDs:")
        click.echo(f"  Replay: {replay}")
        click.echo(f"  Stop: {stop}")
        click.echo()

    if game_oids:
        click.echo(
            f"Game OIDs ({len(game_oids)} total across {len(parsed.games)} game(s)):"
        )
        for game_idx, oids_by_type in game_oids.items():
            game = parsed.games[game_idx]
            type_name = _game_type_name(game.game_type)
            total = sum(len(v) for v in oids_by_type.values())
            click.echo(f"  Game {game_idx} ({type_name}): {total} OIDs")
            for oid_type, oid_list in oids_by_type.items():
                if oid_list:
                    if len(oid_list) <= 5:
                        oid_str = ", ".join(str(o) for o in oid_list)
                    else:
                        oid_str = (
                            f"{min(oid_list)}-{max(oid_list)} ({len(oid_list)} OIDs)"
                        )
                    click.echo(f"    {oid_type}: {oid_str}")

    if parsed.script_table.game_starters:
        click.echo()
        click.echo("Scripts that start games:")
        for script_oid, game_id in parsed.script_table.game_starters:
            click.echo(f"  OID {script_oid} -> Game {game_id}")


def _lookup_oid(parsed, oid: int) -> None:
    """Look up what a specific OID is used for."""
    click.echo(f"OID {oid}:\n")

    found_something = False

    # Check if it's the welcome OID
    if oid == parsed.script_table.first_oid:
        click.echo("  [Welcome OID] - First OID in range")
        found_something = True

    # Check special OIDs
    if parsed.special_oids:
        replay, stop = parsed.special_oids
        if oid == replay:
            click.echo("  [Special] Replay OID")
            found_something = True
        if oid == stop:
            click.echo("  [Special] Stop OID")
            found_something = True

    # Check if it has a script
    if oid in parsed.script_table.scripts:
        lines = parsed.script_table.scripts[oid]
        if lines:
            click.echo(f"  [Script] {len(lines)} line(s)")
            info = _analyze_script(oid, lines)
            if info["actions"]:
                click.echo(f"    Actions: {', '.join(sorted(info['actions']))}")
            if info["media_refs"]:
                refs = sorted(info["media_refs"])
                if len(refs) <= 5:
                    click.echo(f"    Audio: @{','.join(str(r) for r in refs)}")
                else:
                    click.echo(f"    Audio: @{refs[0]}..{refs[-1]} ({len(refs)} files)")

            # Check if this script starts a game
            for line in lines:
                for act in line.actions:
                    if act.kind.value == "StartGame":
                        game_id = act.payload
                        if 0 <= game_id < len(parsed.games):
                            game = parsed.games[game_id]
                            type_name = _game_type_name(game.game_type)
                            click.echo(f"    Starts: Game {game_id} ({type_name})")
            found_something = True
        else:
            click.echo("  [Script] Null pointer (no script)")
            found_something = True

    # Check if it's used in any game
    game_refs = _find_oid_in_games(parsed, oid)
    if game_refs:
        for ref in game_refs:
            click.echo(f"  [Game {ref['game']}] {ref['context']}")
        found_something = True

    if not found_something:
        st = parsed.script_table
        if st.first_oid <= oid <= st.last_oid:
            click.echo("  Not found in scripts or games (within OID range)")
        else:
            click.echo(f"  Outside OID range ({st.first_oid}-{st.last_oid})")


def _show_game_oids(parsed, game_idx: int) -> None:
    """Show all OIDs used by a specific game."""
    if game_idx < 0 or game_idx >= len(parsed.games):
        click.echo(f"Game {game_idx} not found (0-{len(parsed.games) - 1} available)")
        raise SystemExit(1)

    game = parsed.games[game_idx]
    type_name = _game_type_name(game.game_type)
    click.echo(f"Game {game_idx}: {type_name} (type {game.game_type})\n")

    if game.game_type == 253:
        click.echo("  (Special game type with no OIDs)")
        return

    f = game.fields

    # Game-level OIDs
    if oids := f.get("gGameSelectOIDs"):
        click.echo(f"  GameSelectOIDs: {' '.join(str(o) for o in oids)}")

    if oids := f.get("gExtraOIDs"):
        click.echo(f"  ExtraOIDs: {' '.join(str(o) for o in oids)}")

    # Subgame OIDs
    if subgames := f.get("gSubgames"):
        click.echo(f"\n  Subgames ({len(subgames)}):")
        for i, sg in enumerate(subgames):
            has_oids = sg.oid1s or sg.oid2s or sg.oid3s
            if has_oids:
                click.echo(f"    Subgame {i}:")
                if sg.oid1s:
                    oids_str = _format_oid_list(sg.oid1s)
                    click.echo(f"      oid1s: {oids_str}")
                if sg.oid2s:
                    oids_str = _format_oid_list(sg.oid2s)
                    click.echo(f"      oid2s: {oids_str}")
                if sg.oid3s:
                    oids_str = _format_oid_list(sg.oid3s)
                    click.echo(f"      oid3s: {oids_str}")


def _format_oid_list(oids: list[int]) -> str:
    """Format a list of OIDs for display."""
    if not oids:
        return "(none)"
    if len(oids) <= 8:
        return " ".join(str(o) for o in oids)
    return f"{oids[0]}-{oids[-1]} ({len(oids)} OIDs)"


def _collect_game_oids(parsed) -> dict[int, dict[str, list[int]]]:
    """Collect all OIDs used by games."""
    result: dict[int, dict[str, list[int]]] = {}

    for i, game in enumerate(parsed.games):
        if game.game_type == 253:
            continue

        oids_by_type: dict[str, list[int]] = {}
        f = game.fields

        if oids := f.get("gGameSelectOIDs"):
            oids_by_type["GameSelectOIDs"] = list(oids)

        if oids := f.get("gExtraOIDs"):
            oids_by_type["ExtraOIDs"] = list(oids)

        # Collect subgame OIDs
        all_oid1s: list[int] = []
        all_oid2s: list[int] = []
        all_oid3s: list[int] = []

        if subgames := f.get("gSubgames"):
            for sg in subgames:
                all_oid1s.extend(sg.oid1s)
                all_oid2s.extend(sg.oid2s)
                all_oid3s.extend(sg.oid3s)

        if all_oid1s:
            oids_by_type["Subgame oid1s"] = all_oid1s
        if all_oid2s:
            oids_by_type["Subgame oid2s"] = all_oid2s
        if all_oid3s:
            oids_by_type["Subgame oid3s"] = all_oid3s

        if oids_by_type:
            result[i] = oids_by_type

    return result


def _find_oid_in_games(parsed, target_oid: int) -> list[dict]:
    """Find all references to an OID in game structures."""
    refs = []

    for i, game in enumerate(parsed.games):
        if game.game_type == 253:
            continue

        f = game.fields

        if (oids := f.get("gGameSelectOIDs")) and target_oid in oids:
            idx = list(oids).index(target_oid)
            refs.append({"game": i, "context": f"GameSelectOID[{idx}]"})

        if (oids := f.get("gExtraOIDs")) and target_oid in oids:
            idx = list(oids).index(target_oid)
            refs.append({"game": i, "context": f"ExtraOID[{idx}]"})

        if subgames := f.get("gSubgames"):
            for sg_idx, sg in enumerate(subgames):
                if target_oid in sg.oid1s:
                    idx = sg.oid1s.index(target_oid)
                    refs.append(
                        {"game": i, "context": f"Subgame[{sg_idx}].oid1s[{idx}]"}
                    )
                if target_oid in sg.oid2s:
                    idx = sg.oid2s.index(target_oid)
                    refs.append(
                        {"game": i, "context": f"Subgame[{sg_idx}].oid2s[{idx}]"}
                    )
                if target_oid in sg.oid3s:
                    idx = sg.oid3s.index(target_oid)
                    refs.append(
                        {"game": i, "context": f"Subgame[{sg_idx}].oid3s[{idx}]"}
                    )

    return refs
