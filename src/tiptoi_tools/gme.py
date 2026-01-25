from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from tiptoi_tools.binary import OID, BinaryReader, ascii_clean, u8, u16le, u32le
from tiptoi_tools.games import Game
from tiptoi_tools.games import decode as decode_games
from tiptoi_tools.games import serialize as serialize_game
from tiptoi_tools.media import MediaEntry
from tiptoi_tools.media import decode as decode_media_entries
from tiptoi_tools.playlist import PlaylistTable
from tiptoi_tools.playlist import decode_table as decode_playlist_table
from tiptoi_tools.scripts import ScriptTable
from tiptoi_tools.scripts import decode as decode_scripts
from tiptoi_tools.scripts import serialize as serialize_scripts

# GME file header layout
HEADER_END = 0x60

# Core header offsets (0x00-0x1F) - main pointers and metadata
OFFSET_SCRIPT_TABLE = 0x0000
OFFSET_MEDIA_TABLE = 0x0004
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
        decode_playlist_table(data, welcome_offset)
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
        "init": _serialize_init(parsed.registers),
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


def _serialize_init(registers: list[int]) -> str:
    """
    Serialize init as a single command-line-like string, e.g.:
      "$0:=1 $152:=99"
    Only non-zero registers are emitted.
    """
    parts = [f"${i}:={v}" for i, v in enumerate(registers) if v != 0]
    return " ".join(parts)


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
