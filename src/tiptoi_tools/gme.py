import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from tiptoi_tools.binary import (
    OID,
    BinaryReader,
    BinaryWriter,
    ascii_clean,
    u8,
    u16le,
    u32le,
)
from tiptoi_tools.games import Game
from tiptoi_tools.games import decode as decode_games
from tiptoi_tools.games import serialize as serialize_game
from tiptoi_tools.media import MediaEntry
from tiptoi_tools.media import DEFAULT_XOR_KEY
from tiptoi_tools.media import decode as decode_media_entries
from tiptoi_tools.media import encode as encode_media_entries
from tiptoi_tools.playlist import Playlist, PlaylistTable
from tiptoi_tools.scripts import ActionKind, ScriptLine, ScriptTable
from tiptoi_tools.scripts import decode as decode_scripts
from tiptoi_tools.scripts import encode as encode_scripts
from tiptoi_tools.scripts import serialize as serialize_scripts
from tiptoi_tools.scripts import deserialize as deserialize_scripts

# GME file header layout
HEADER_END = 0x60
HEADER_SIZE = 0x200  # Size of extended header used when building
GME_MAGIC = 0x0000238B  # Magic number for GME files

# Core header offsets (0x00-0x1F) - main pointers and metadata
OFFSET_SCRIPT_TABLE = 0x0000
OFFSET_MEDIA_TABLE = 0x0004
OFFSET_MAGIC = 0x0008
OFFSET_ADDITIONAL_SCRIPT_TABLE = 0x000C
OFFSET_GAME_TABLE = 0x0010
OFFSET_PRODUCT_ID = 0x0014
OFFSET_REGISTER_INIT = 0x0018
OFFSET_RAW_XOR = 0x001C

# Variable-length comment section (0x20-0x5F)
OFFSET_COMMENT = 0x0020

# Extended header offsets (0x60+) - additional tables and flags
OFFSET_ADDITIONAL_MEDIA_TABLE = 0x0060
OFFSET_WELCOME_SOUNDS = 0x0071

# Binary and game data pointers (0x8C-0xCF)
OFFSET_MEDIA_FLAGS = 0x008C
OFFSET_GAME_BINARIES_1 = 0x0090
OFFSET_SPECIAL_OIDS = 0x0094
OFFSET_GAME_BINARIES_2 = 0x0098
OFFSET_UNKNOWN_009C = 0x009C
OFFSET_SINGLE_BINARY_1 = 0x00A0
OFFSET_HEADER_FLAG = 0x00A4
OFFSET_SINGLE_BINARY_2 = 0x00A8
OFFSET_SINGLE_BINARY_3 = 0x00C8
OFFSET_GAME_BINARIES_3 = 0x00CC

# Maximum valid OID value. OIDs above this threshold are likely pointers,
# not OID pairs. Tiptoi OIDs are typically in the 0-999 range.
_MAX_VALID_OID = 10_000


@dataclass(frozen=True)
class GmeHeader:
    """Core GME file header containing offsets and metadata."""

    script_table_offset: int
    media_table_offset: int
    additional_script_table_offset: int
    game_table_offset: int
    product_id_code: int
    register_init_offset: int
    raw_xor: int
    comment: str
    date_string: str
    language_string: str


class Similarity(Enum):
    """Result of comparing two data regions for duplication."""

    ABSENT = "Absent"
    EQUAL = "Equal"
    SIMILAR = "Similar"


@dataclass(frozen=True)
class ParsedGme:
    """Complete parsed GME file with all decoded structures."""

    header: GmeHeader
    registers: list[int]
    media_entries: list[MediaEntry]
    duplicated_table: Similarity
    welcome_sounds: PlaylistTable
    binary_tables_entries: tuple[int, int, int]
    single_binary_tables_entries: tuple[int, int, int]
    special_oids: tuple[OID, OID] | None
    script_table: ScriptTable
    games: list[Game]
    checksum_found: int
    checksum_calculated: int


def parse_file(path: Path) -> ParsedGme:
    """Parse a GME file from disk."""
    return decode(path.read_bytes())


def decode(data: bytes) -> ParsedGme:
    """Decode a GME file from raw bytes into a structured representation."""
    header = _decode_header(data)
    registers = _decode_registers(data, header.register_init_offset)

    additional_media_offset = _decode_additional_media_table_offset(data)
    media_entries = decode_media_entries(
        data,
        header.media_table_offset,
        stop_at=additional_media_offset,
    )
    duplication = _detect_media_table_duplication(
        data,
        header.media_table_offset,
        offset_end=min(e.offset for e in media_entries),
    )

    welcome_offset = (
        u32le(data, OFFSET_WELCOME_SOUNDS)
        if len(data) >= OFFSET_WELCOME_SOUNDS + 4
        else 0
    )
    welcome_sounds = (
        PlaylistTable.decode(data, welcome_offset)
        if welcome_offset
        else PlaylistTable(playlists=())
    )

    binary_tables_entries = (
        _decode_binaries_table_count(data, OFFSET_GAME_BINARIES_1),
        _decode_binaries_table_count(data, OFFSET_GAME_BINARIES_2),
        _decode_binaries_table_count(data, OFFSET_GAME_BINARIES_3),
    )

    single_binary_tables_entries = (
        _decode_binaries_table_count(data, OFFSET_SINGLE_BINARY_1),
        _decode_binaries_table_count(data, OFFSET_SINGLE_BINARY_2),
        _decode_binaries_table_count(data, OFFSET_SINGLE_BINARY_3),
    )

    special_oids = _decode_special_oids(data, OFFSET_SPECIAL_OIDS)

    script_table = decode_scripts(data, header.script_table_offset)
    games = decode_games(data, header.game_table_offset)

    checksum_found = u32le(data, len(data) - 4)
    checksum_calculated = sum(data[:-4]) & 0xFFFFFFFF

    return ParsedGme(
        header=header,
        registers=registers,
        media_entries=media_entries,
        duplicated_table=duplication,
        welcome_sounds=welcome_sounds,
        binary_tables_entries=binary_tables_entries,
        single_binary_tables_entries=single_binary_tables_entries,
        special_oids=special_oids,
        script_table=script_table,
        games=games,
        checksum_found=checksum_found,
        checksum_calculated=checksum_calculated,
    )


def encode(parsed: ParsedGme, audio_files: list[bytes]) -> bytes:
    """
    Encode a ParsedGme structure to GME binary format.

    Args:
        parsed: ParsedGme containing the GME structure
        audio_files: List of audio file contents (decrypted)

    Returns:
        Complete GME file as bytes
    """
    w = BinaryWriter()
    header = parsed.header

    # Reserve header space
    w.pad_to(HEADER_SIZE)

    # Write script table
    script_table_offset = w.offset
    encode_scripts(w, parsed.script_table)

    # Write media table
    media_table_offset = w.offset
    encode_media_entries(w, audio_files, header.raw_xor or DEFAULT_XOR_KEY)

    # Write register init
    register_init_offset = w.offset
    w.u16_list(parsed.registers)

    # Write welcome playlist
    welcome_offset = w.offset
    parsed.welcome_sounds.encode(w)

    # Write special OIDs
    special_oids_offset = 0
    if parsed.special_oids:
        special_oids_offset = w.offset
        w.u16(parsed.special_oids[0])
        w.u16(parsed.special_oids[1])

    # Write checksum placeholder
    checksum_offset = w.offset
    w.u32(0)

    # Patch header
    _encode_header(
        w,
        header,
        script_table_offset,
        media_table_offset,
        register_init_offset,
        welcome_offset,
        special_oids_offset,
    )

    # Calculate and write checksum
    raw = w.to_bytes()
    checksum = sum(raw[:-4]) & 0xFFFFFFFF
    w.u32_at(checksum_offset, checksum)

    return w.to_bytes()


def serialize(
    parsed: ParsedGme,
    media_path: str = "audio",
) -> dict[str, Any]:
    """Convert a parsed GME to a dictionary suitable for YAML serialization."""
    header = parsed.header

    # Welcome: single playlist -> string, multiple -> list
    welcome = parsed.welcome_sounds.serialize(collapse=True)

    out: dict[str, Any] = {
        "product-id": int(header.product_id_code),
        "comment": header.comment or "",
        "welcome": welcome,
        "media-path": media_path,
        "init": _serialize_registers(parsed.registers),
        "scripts": serialize_scripts(parsed.script_table.scripts),
        "games": [serialize_game(g) for g in parsed.games],
    }

    if header.language_string:
        out["gme-lang"] = header.language_string

    if parsed.special_oids is not None:
        replay, stop = parsed.special_oids
        out["replay"] = int(replay)
        out["stop"] = int(stop)

    return out


def export_yaml(
    parsed: ParsedGme,
    out_path: Path,
    media_path: str = "audio",
) -> None:
    """Export a parsed GME to a tttool-compatible YAML file."""
    obj = serialize(parsed, media_path=media_path)
    text = yaml.safe_dump(
        obj,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=80,
    )
    out_path.write_text(text, encoding="utf-8")


def import_yaml(yaml_path: Path) -> tuple[ParsedGme, list[bytes]]:
    """Import a tttool-compatible YAML file into ParsedGme and audio files."""
    with open(yaml_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    base_dir = yaml_path.parent

    # Basic fields
    product_id = raw.get("product-id", 0)
    comment = raw.get("comment", "")
    lang = raw.get("gme-lang", "")
    media_path = raw.get("media-path", "")

    # Initial registers
    init_registers: list[int] = []
    if init_str := raw.get("init"):
        init_registers = _deserialize_registers(init_str)

    # Welcome sounds
    welcome = Playlist()
    if welcome_raw := raw.get("welcome"):
        welcome = Playlist.serialize(welcome_raw)

    # Special OIDs
    special_oids: tuple[OID, OID] | None = None
    replay = raw.get("replay")
    stop = raw.get("stop")
    if replay is not None and stop is not None:
        special_oids = (OID(int(replay)), OID(int(stop)))

    # Scripts - parse to intermediate form, then convert to ScriptLine dataclasses
    scripts_raw = raw.get("scripts", {})
    script_lines = deserialize_scripts(scripts_raw)

    first_oid = OID(min(script_lines.keys())) if script_lines else OID(0)
    last_oid = OID(max(script_lines.keys())) if script_lines else OID(0)

    # Compute active_oids and game_starters
    active_oids: list[OID] = []
    game_starters: list[tuple[OID, int]] = []
    for oid, lines in script_lines.items():
        if lines:
            active_oids.append(oid)
            for line in lines:
                for action in line.actions:
                    if action.kind == ActionKind.START_GAME:
                        game_starters.append((oid, int(action.payload)))

    script_table = ScriptTable(
        first_oid=first_oid,
        last_oid=last_oid,
        scripts=script_lines,
        active_oids=active_oids,
        game_starters=game_starters,
    )

    # Games (TODO: full game parsing)
    games_raw = raw.get("games", [])

    # Collect all audio indices from scripts, welcome, and games
    audio_indices = _collect_audio_indices(script_lines, welcome, games_raw)

    # Load audio files
    audio_files = _load_audio_files(base_dir, media_path, audio_indices)

    # Build header
    header = GmeHeader(
        script_table_offset=0,  # Will be set during encoding
        media_table_offset=0,
        additional_script_table_offset=0,
        game_table_offset=0,
        product_id_code=product_id,
        register_init_offset=0,
        raw_xor=DEFAULT_XOR_KEY,
        comment=comment,
        date_string="",  # Will be set during encoding
        language_string=lang,
    )

    # Build welcome PlaylistTable
    if welcome:
        welcome_table = PlaylistTable(playlists=(Playlist(indices=tuple(welcome)),))
    else:
        welcome_table = PlaylistTable(playlists=())

    # Build ParsedGme
    parsed = ParsedGme(
        header=header,
        registers=init_registers,
        media_entries=[],  # Not needed for encoding
        duplicated_table=Similarity.ABSENT,
        welcome_sounds=welcome_table,
        binary_tables_entries=(0, 0, 0),
        single_binary_tables_entries=(0, 0, 0),
        special_oids=special_oids,
        script_table=script_table,
        games=[],  # TODO: parse games
        checksum_found=0,
        checksum_calculated=0,
    )

    return parsed, audio_files


def _serialize_registers(registers: list[int]) -> str:
    """
    Serialize init as a single command-line-like string, e.g.:
      "$0:=1 $152:=99"
    Only non-zero registers are emitted.
    """
    parts = [f"${i}:={v}" for i, v in enumerate(registers) if v != 0]
    return " ".join(parts)


def _deserialize_registers(init_str: str) -> list[int]:
    """Parse init string like '$0:=1 $152:=99' into register values."""
    registers: dict[int, int] = {}
    if not init_str:
        return []

    for part in init_str.split():
        part = part.strip()
        if not part:
            continue
        if ":=" in part and part.startswith("$"):
            reg_str, val_str = part[1:].split(":=")
            registers[int(reg_str)] = int(val_str)

    if not registers:
        return []

    max_reg = max(registers.keys())
    result = [0] * (max_reg + 1)
    for reg, val in registers.items():
        result[reg] = val
    return result


def _decode_header(data: bytes) -> GmeHeader:
    """Decode the GME file header from raw bytes."""
    if len(data) < HEADER_END + 1:
        raise ValueError("File too small to be a valid GME")

    r = BinaryReader(data, OFFSET_COMMENT)
    comment_length = r.u8()
    comment = ascii_clean(r.bytes(comment_length))
    date_string = ascii_clean(r.bytes(8))

    # Language string is null-terminated within the header
    start = r.offset
    end = data.find(0, start, HEADER_END)
    if end == -1:
        end = HEADER_END  # No null found; use header boundary
    language_string = ascii_clean(data[start:end])

    return GmeHeader(
        script_table_offset=u32le(data, OFFSET_SCRIPT_TABLE),
        media_table_offset=u32le(data, OFFSET_MEDIA_TABLE),
        additional_script_table_offset=u32le(data, OFFSET_ADDITIONAL_SCRIPT_TABLE),
        game_table_offset=u32le(data, OFFSET_GAME_TABLE),
        product_id_code=u32le(data, OFFSET_PRODUCT_ID),
        register_init_offset=u32le(data, OFFSET_REGISTER_INIT),
        raw_xor=u8(data, OFFSET_RAW_XOR),
        comment=comment,
        date_string=date_string,
        language_string=language_string,
    )


def _decode_registers(data: bytes, offset: int) -> list[int]:
    """Decode the initial register values from the register table."""
    if offset <= 0 or offset + 2 > len(data):
        return []

    r = BinaryReader(data, offset)
    count = r.u16()

    registers: list[int] = []
    for _ in range(count):
        if r.offset + 2 > len(data):
            break
        registers.append(r.u16())

    return registers


def _decode_additional_media_table_offset(data: bytes) -> int | None:
    """Get the additional media table offset if present, else None."""
    if len(data) < OFFSET_ADDITIONAL_MEDIA_TABLE + 4:
        return None
    offset = u32le(data, OFFSET_ADDITIONAL_MEDIA_TABLE)
    return offset or None


def _detect_media_table_duplication(
    data: bytes,
    offset_start: int,
    offset_end: int,
) -> Similarity:
    """Check if the media table region contains duplicated data."""
    if offset_start <= 0 or offset_end <= offset_start:
        return Similarity.ABSENT

    region = data[offset_start:offset_end]
    n = len(region)
    if n < 16 or (n % 2) != 0:
        return Similarity.ABSENT

    half = n // 2
    a, b = region[:half], region[half:]

    if a == b:
        return Similarity.EQUAL

    same = sum(1 for x, y in zip(a, b, strict=True) if x == y)
    if (same / half) >= 0.8:
        return Similarity.SIMILAR

    return Similarity.ABSENT


def _decode_binaries_table_count(data: bytes, offset: int) -> int:
    """Read a pointer at offset and return the u16 count at the target."""
    p = _decode_ptr32_maybe(data, offset)
    if p is None:
        return 0
    if p + 2 > len(data):
        raise ValueError(f"Binaries table pointer at 0x{offset:04X} is truncated")
    return u16le(data, p)


def _decode_ptr32_maybe(data: bytes, offset: int) -> int | None:
    """Read a u32 pointer, returning None if null/invalid."""
    if offset < 0 or offset + 4 > len(data):
        return None
    p = u32le(data, offset)
    if p in (0x00000000, 0xFFFFFFFF):
        return None
    if p >= len(data):
        return None
    return p


def _decode_special_oids(data: bytes, offset: int) -> tuple[OID, OID] | None:
    """
    Decode replay/stop special OIDs from the header.

    Some files store these directly as u16,u16 at OFFSET_SPECIAL_OIDS.
    Others store a pointer there. We try pointer interpretation first,
    then fall back to treating it as direct values (matching tttool behavior).
    """
    if offset + 4 > len(data):
        return None

    raw_u32 = u32le(data, offset)

    # Null pointer (0x00000000) means no special OIDs defined
    if raw_u32 == 0x00000000:
        return None

    # Try as a pointer first
    p = _decode_ptr32_maybe(data, offset)
    if p is not None and p + 4 <= len(data):
        a, b = u16le(data, p), u16le(data, p + 2)
        # (0xFFFF, 0xFFFF) means unused, but (0, 0) is valid (no special OID)
        if (a, b) != (0xFFFF, 0xFFFF):
            return OID(a), OID(b)

    # Fall back to direct interpretation as two u16 values
    a, b = u16le(data, offset), u16le(data, offset + 2)
    is_unused = (a, b) == (0xFFFF, 0xFFFF)
    is_valid_oid_pair = a < _MAX_VALID_OID and b < _MAX_VALID_OID
    if not is_unused and is_valid_oid_pair:
        return OID(a), OID(b)

    return None


def _encode_header(
    w: BinaryWriter,
    header: GmeHeader,
    script_table_offset: int,
    media_table_offset: int,
    register_init_offset: int,
    welcome_offset: int,
    special_oids_offset: int,
) -> None:
    """Encode the GME header by patching values at fixed offsets."""
    # Core pointers
    w.u32_at(OFFSET_SCRIPT_TABLE, script_table_offset)
    w.u32_at(OFFSET_MEDIA_TABLE, media_table_offset)
    w.u32_at(OFFSET_MAGIC, GME_MAGIC)
    w.u32_at(OFFSET_ADDITIONAL_SCRIPT_TABLE, script_table_offset)
    w.u32_at(OFFSET_GAME_TABLE, 0)
    w.u32_at(OFFSET_PRODUCT_ID, header.product_id_code)
    w.u32_at(OFFSET_REGISTER_INIT, register_init_offset)
    w.u32_at(OFFSET_RAW_XOR, header.raw_xor or DEFAULT_XOR_KEY)

    # Comment section: [length][comment][date][language][null]
    comment_bytes = header.comment.encode("ascii", errors="replace")[:49]
    date_str = datetime.now().strftime("%Y%m%d").encode("ascii")
    lang = header.language_string or ""
    lang_bytes = lang.encode("ascii", errors="replace")

    comment_section = (
        bytes([len(comment_bytes)]) + comment_bytes + date_str + lang_bytes + b"\x00"
    )
    w.bytes_at(OFFSET_COMMENT, comment_section)

    # Welcome and special OIDs
    w.u32_at(OFFSET_WELCOME_SOUNDS, welcome_offset)
    if special_oids_offset:
        w.u32_at(OFFSET_SPECIAL_OIDS, special_oids_offset)


def _collect_audio_indices(
    scripts: dict[int, list[ScriptLine]],
    welcome: Playlist,
    games: list[dict],
) -> set[int]:
    """Collect all audio indices from scripts, welcome, and games."""
    indices: set[int] = set()

    for lines in scripts.values():
        for line in lines:
            indices.update(line.audio_links)

    indices.update(welcome)

    for game in games:
        _collect_indices_from_game(game, indices)

    return indices


def _collect_indices_from_game(game: dict, indices: set[int]) -> None:
    """Recursively collect audio indices from a game structure."""
    for key, value in game.items():
        if key.endswith("playlist") or key.endswith("playlists"):
            _collect_indices_from_playlist(value, indices)
        elif key == "subgames" and isinstance(value, list):
            for subgame in value:
                if isinstance(subgame, dict):
                    _collect_indices_from_game(subgame, indices)


def _collect_indices_from_playlist(value, indices: set[int]) -> None:
    """Collect indices from a playlist value (can be nested)."""
    if isinstance(value, int):
        indices.add(value)
    elif isinstance(value, str):
        for part in value.split(","):
            part = part.strip()
            if part.isdigit():
                indices.add(int(part))
    elif isinstance(value, list):
        for item in value:
            _collect_indices_from_playlist(item, indices)


def _load_audio_files(
    base_dir: Path,
    media_path: str,
    audio_indices: set[int],
) -> list[bytes]:
    """Load audio files for all referenced indices."""
    if not audio_indices:
        return []

    max_index = max(audio_indices)
    missing_files: list[int] = []

    audio_files: list[bytes] = []
    for i in range(max_index + 1):
        audio_data = None

        if media_path and "%s" in media_path:
            for ext in [".ogg", ".wav", ".mp3", ".flac"]:
                path = base_dir / (media_path.replace("%s", str(i)) + ext)
                if path.exists():
                    audio_data = path.read_bytes()
                    break
                path = base_dir / (media_path.replace("%s", f"{i:04d}") + ext)
                if path.exists():
                    audio_data = path.read_bytes()
                    break

        if audio_data is None:
            if i in audio_indices:
                missing_files.append(i)
            audio_data = b""

        audio_files.append(audio_data)

    if missing_files:
        if media_path and "%s" in media_path:
            pattern = base_dir / (media_path.replace("%s", "<N>") + ".ogg")
        else:
            pattern = base_dir / "media" / "<N>.ogg"

        if len(missing_files) <= 10:
            indices_str = ", ".join(str(i) for i in missing_files)
        else:
            indices_str = (
                ", ".join(str(i) for i in missing_files[:10])
                + f"... ({len(missing_files)} total)"
            )

        raise RuntimeError(
            f"Missing {len(missing_files)} audio file(s): {indices_str}\n"
            f"Expected path pattern: {pattern}\n"
            f"Run 'tiptoi-tools <file.gme> export' to extract audio files."
        )
