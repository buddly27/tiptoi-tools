from pathlib import Path

import click

import tiptoi_tools.gme
import tiptoi_tools.media


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(package_name="tiptoi-tools")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Tools for working with Tiptoi GME files."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command("info")
@click.argument(
    "gme_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
def info_cmd(gme_file: Path) -> None:
    """Print general information about a GME file."""
    parsed = tiptoi_tools.gme.parse_file(gme_file)
    hdr = parsed.header

    click.echo(f"File: {gme_file}")
    click.echo(f"Size: {gme_file.stat().st_size} bytes")
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

    click.echo(f"Initial sounds: {_print_welcome(parsed.welcome_sounds)}")
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
    click.echo(f"Scripts: {sum(1 for v in parsed.scripts.values() if v)} present")
    click.echo(f"Games: {len(parsed.games)} total")
    click.echo("")
    found = parsed.checksum_found
    calc = parsed.checksum_calculated
    click.echo(f"Checksum found 0x{found:08X}, calculated 0x{calc:08X}")


@cli.command("media")
@click.argument(
    "gme_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
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
def media_cmd(gme_file: Path, out_dir: Path, limit: int | None) -> None:
    """Extract and decrypt media samples from a GME file."""
    parsed = tiptoi_tools.gme.parse_file(gme_file)
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

    data = gme_file.read_bytes()
    count = 0
    for entry in entries:
        if limit is not None and count >= limit:
            break

        enc = data[entry.offset : entry.offset + entry.length]
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
    "gme_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "out_file", required=False, type=click.Path(dir_okay=False, path_type=Path)
)
@click.option(
    "--media-path",
    default=None,
    help="Value for the 'media-path' field in YAML. Default: media/{stem}_%s",
)
def export_cmd(gme_file: Path, out_file: Path | None, media_path: str | None) -> None:
    """
    Dump the file in human-readable YAML format.

    If OUT_FILE is omitted, writes <GME>.yaml next to the input file.
    """
    parsed = tiptoi_tools.gme.parse_file(gme_file)

    if out_file is None:
        out_file = gme_file.with_suffix(".yaml")

    if media_path is None:
        media_path = f"media/{gme_file.stem}_%s"

    tiptoi_tools.gme.export_yaml(parsed, out_file, media_path=media_path)
    click.echo(f"Wrote {out_file}")


def _print_welcome(welcome_sounds: list[list[int]]) -> str:
    if not welcome_sounds:
        return "[]"
    if len(welcome_sounds) == 1:
        return ",".join(str(x) for x in welcome_sounds[0])
    inner = "; ".join(",".join(str(x) for x in pl) for pl in welcome_sounds)
    return "[" + inner + "]"


def _print_audio_xors(entries: list[tiptoi_tools.gme.MediaEntry]) -> str:
    xors = sorted({e.magic_xor for e in entries})
    if not xors:
        return "[]"
    return "[" + ",".join(f"{x:#04X}" for x in xors) + "]"
