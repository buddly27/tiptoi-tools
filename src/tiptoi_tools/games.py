from dataclasses import dataclass
from typing import Any

from tiptoi_tools.binary import OID, BinaryReader
from tiptoi_tools.playlist import PlaylistTable

# Game type constants
GAME_TYPE_COMMON = 1
GAME_TYPE_BONUS = 6
GAME_TYPE_GROUPED = 7
GAME_TYPE_SELECT = 8
GAME_TYPE_EXTRA_9 = 9
GAME_TYPE_EXTRA_10 = 10
GAME_TYPE_EXTRA_16 = 16
GAME_TYPE_SPECIAL = 253

_EXTRA_PLAYLIST_COUNTS: dict[int, int] = {
    GAME_TYPE_EXTRA_9: 75,
    GAME_TYPE_EXTRA_10: 1,
    GAME_TYPE_EXTRA_16: 3,
}

# Field mappings: (yaml_key, internal_key)
_SCALAR_FIELDS = [
    ("rounds", "gRounds"),
    ("earlyrounds", "gEarlyRounds"),
    ("repeatlastmedia", "gRepeatLastMedia"),
    ("unknownc", "gUnknownC"),
    ("unknownx", "gUnknownX"),
    ("unknownw", "gUnknownW"),
    ("unknownv", "gUnknownV"),
    ("unknownq", "gUnknownQ"),
    ("bonussubgamecount", "gBonusSubgameCount"),
    ("bonusrounds", "gBonusRounds"),
    ("bonustarget", "gBonusTarget"),
    ("unknowni", "gUnknownI"),
    ("targetscores", "gTargetScores"),
    ("bonustargetscores", "gBonusTargetScores"),
    ("bonussubgameids", "gBonusSubgameIds"),
    ("subgamegroups", "gSubgameGroups"),
    ("gameselect", "gGameSelect"),
]

_OID_LIST_FIELDS = [
    ("gameselectoids", "gGameSelectOIDs"),
    ("extraoids", "gExtraOIDs"),
]

_PLAYLIST_FIELDS = [
    ("startplaylist", "gStartPlayList"),
    ("roundendplaylist", "gRoundEndPlayList"),
    ("finishplaylist", "gFinishPlayList"),
    ("roundstartplaylist", "gRoundStartPlayList"),
    ("laterroundstartplaylist", "gLaterRoundStartPlayList"),
    ("roundstartplaylist2", "gRoundStartPlayList2"),
    ("laterroundstartplaylist2", "gLaterRoundStartPlayList2"),
    ("gameselecterrors1", "gGameSelectErrors1"),
    ("gameselecterrors2", "gGameSelectErrors2"),
]

_PLAYLIST_LIST_FIELDS = [
    ("finishplaylists", "gFinishPlayLists"),
    ("bonusfinishplaylists", "gBonusFinishPlayLists"),
    ("extraplaylists", "gExtraPlayLists"),
]

_NAMED_GAME_TYPES = {
    GAME_TYPE_BONUS,
    GAME_TYPE_GROUPED,
    GAME_TYPE_SELECT,
    GAME_TYPE_EXTRA_9,
    GAME_TYPE_EXTRA_10,
    GAME_TYPE_EXTRA_16,
}


@dataclass(frozen=True)
class Game:
    """A game definition with its type and type-specific fields."""

    game_type: int
    fields: dict[str, object]


@dataclass(frozen=True)
class SubGame:
    """A subgame within a game, containing OID lists and playlists."""

    header: bytes
    oid1s: list[OID]
    oid2s: list[OID]
    oid3s: list[OID]
    playlists: list[PlaylistTable]


class GameReader(BinaryReader):
    """BinaryReader extended with game-specific parsing methods."""

    def playlist(self) -> PlaylistTable:
        """Read a pointer and decode the playlist table at that location."""
        return PlaylistTable.decode(self.data, self.u32())

    def playlists(self, count: int) -> list[PlaylistTable]:
        """Read multiple playlist table pointers."""
        return [self.playlist() for _ in range(count)]

    def oids(self) -> list[OID]:
        """Read an OID list via pointer indirection."""
        return [OID(x) for x in GameReader(self.data, self.u32()).u16_list()]

    def game_ids(self) -> list[int]:
        """Read a game ID list (converts from 1-indexed to 0-indexed)."""
        r = GameReader(self.data, self.u32())
        return [r.u16() - 1 for _ in range(r.u16())]

    def game_id_groups(self) -> list[list[int]]:
        """Read a list of pointers to game ID lists."""
        r = GameReader(self.data, self.u32())
        n = r.u16()
        return [GameReader(self.data, r.u32()).game_ids_direct() for _ in range(n)]

    def game_ids_direct(self) -> list[int]:
        """Read a game ID list directly (no pointer indirection)."""
        return [self.u16() - 1 for _ in range(self.u16())]

    def subgame(self) -> SubGame:
        """Read a subgame structure via pointer."""
        ptr = self.u32()
        r = GameReader(self.data, ptr)
        header = self.data[ptr : ptr + 20]
        r.skip(20)
        return SubGame(
            header=header,
            oid1s=[OID(x) for x in r.u16_list()],
            oid2s=[OID(x) for x in r.u16_list()],
            oid3s=[OID(x) for x in r.u16_list()],
            playlists=[r.playlist() for _ in range(9)],
        )


def decode(data: bytes, offset: int) -> list[Game]:
    """Decode all games from the game table at the given offset."""
    if offset <= 0 or offset + 4 > len(data):
        return []

    r = GameReader(data, offset)
    n_games = r.u32()

    games: list[Game] = []
    for _ in range(n_games):
        g_off = r.u32()
        if 0 < g_off < len(data):
            games.append(_decode_game(data, g_off))
    return games


def _decode_game(data: bytes, offset: int) -> Game:
    """Decode a single game structure from the binary data."""
    r = GameReader(data, offset)
    game_type = r.u16()

    if game_type == GAME_TYPE_SPECIAL:
        return Game(game_type=GAME_TYPE_SPECIAL, fields={})

    is_bonus = game_type == GAME_TYPE_BONUS
    f: dict[str, object] = {}

    # Common scalars
    f["gSubgameCount"] = r.u16()
    f["gRounds"] = r.u16()
    if not is_bonus:
        f["gUnknownC"] = r.u16()

    # Bonus-only scalars
    if is_bonus:
        f["gBonusSubgameCount"] = r.u16()
        f["gBonusRounds"] = r.u16()
        f["gBonusTarget"] = r.u16()
        f["gUnknownI"] = r.u16()

    # More common scalars
    f["gEarlyRounds"] = r.u16()
    if is_bonus:
        f["gUnknownQ"] = r.u16()
    f["gRepeatLastMedia"] = r.u16()
    f["gUnknownX"] = r.u16()
    f["gUnknownW"] = r.u16()
    f["gUnknownV"] = r.u16()

    # Core playlists
    f["gStartPlayList"] = r.playlist()
    f["gRoundEndPlayList"] = r.playlist()
    f["gFinishPlayList"] = r.playlist()
    f["gRoundStartPlayList"] = r.playlist()
    f["gLaterRoundStartPlayList"] = r.playlist()
    if is_bonus:
        f["gRoundStartPlayList2"] = r.playlist()
        f["gLaterRoundStartPlayList2"] = r.playlist()

    # Subgames
    subgame_count = int(f["gSubgameCount"])
    if is_bonus:
        subgame_count += int(f["gBonusSubgameCount"])
    f["gSubgames"] = [r.subgame() for _ in range(subgame_count)]

    # Target scores and finish playlists
    if is_bonus:
        f["gTargetScores"] = r.u16_array(2)
        f["gBonusTargetScores"] = r.u16_array(8)
        f["gFinishPlayLists"] = r.playlists(2)
        f["gBonusFinishPlayLists"] = r.playlists(8)
        f["gBonusSubgameIds"] = r.game_ids()
    else:
        f["gTargetScores"] = r.u16_array(10)
        f["gFinishPlayLists"] = r.playlists(10)

    # Game type specific fields
    if game_type == GAME_TYPE_GROUPED:
        f["gSubgameGroups"] = r.game_id_groups()
    elif game_type == GAME_TYPE_SELECT:
        f["gGameSelectOIDs"] = r.oids()
        f["gGameSelect"] = r.game_ids()
        f["gGameSelectErrors1"] = r.playlist()
        f["gGameSelectErrors2"] = r.playlist()
    elif game_type == GAME_TYPE_EXTRA_16:
        f["gExtraOIDs"] = r.oids()

    # Extra playlists (game types 9, 10, 16)
    extra_count = _EXTRA_PLAYLIST_COUNTS.get(game_type, 0)
    if extra_count:
        f["gExtraPlayLists"] = r.playlists(extra_count)

    return Game(game_type=game_type, fields=f)


def serialize(g: Game) -> dict[str, Any]:
    """Serialize a Game to a dictionary suitable for YAML output."""
    if g.game_type == GAME_TYPE_SPECIAL:
        return {"tag": "Game253Yaml"}

    f = g.fields
    out: dict[str, Any] = {}

    if g.game_type not in _NAMED_GAME_TYPES:
        out["gametype"] = int(g.game_type)

    # Scalar fields
    for yaml_key, field_key in _SCALAR_FIELDS:
        if (v := f.get(field_key)) is not None:
            out[yaml_key] = v

    # OID list fields (space-separated strings)
    for yaml_key, field_key in _OID_LIST_FIELDS:
        if (v := f.get(field_key)) is not None:
            out[yaml_key] = " ".join(str(x) for x in v)

    # Playlist fields (single playlist table)
    for yaml_key, field_key in _PLAYLIST_FIELDS:
        if (v := f.get(field_key)) is not None:
            out[yaml_key] = v.serialize(collapse=True)

    # Playlist list fields (list of playlist tables)
    for yaml_key, field_key in _PLAYLIST_LIST_FIELDS:
        if (v := f.get(field_key)) is not None and v:
            out[yaml_key] = [pl.serialize(collapse=True) for pl in v]

    # Subgames
    if (subgames := f.get("gSubgames")) is not None:
        out["subgames"] = [_serialize_subgame(sg) for sg in subgames]

    # Tag at the end
    out["tag"] = _tag_for_game_type(g.game_type)

    return dict(sorted(out.items()))


def _serialize_subgame(sg: SubGame) -> dict[str, Any]:
    """Serialize a subgame to a dictionary for YAML output."""

    def oid_str(xs: list[OID]) -> str:
        return " ".join(str(x) for x in xs) if xs else ""

    playlists = [pl.serialize(collapse=True) for pl in sg.playlists]
    return dict(
        sorted(
            {
                "oids1": oid_str(sg.oid1s),
                "oids2": oid_str(sg.oid2s),
                "oids3": oid_str(sg.oid3s),
                "playlist": playlists,
                "unknown": " ".join(f"{b:02X}" for b in sg.header),
            }.items()
        )
    )


def _tag_for_game_type(game_type: int) -> str:
    """Return the YAML tag name for a game type."""
    # Named game types (6-10, 16) use specific tags
    if game_type in _NAMED_GAME_TYPES:
        return f"Game{game_type}Yaml"
    # All other types use CommonGameYaml (tttool compatibility)
    return "CommonGameYaml"
