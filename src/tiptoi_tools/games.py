from dataclasses import dataclass
from typing import Any

from tiptoi_tools.binary import OID, BinaryReader, BinaryWriter
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

# Subgame structure constants
SUBGAME_HEADER_SIZE = 20

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
class SubGame:
    """A subgame within a game, containing OID lists and playlists."""

    header: bytes
    oid1s: tuple[OID, ...]
    oid2s: tuple[OID, ...]
    oid3s: tuple[OID, ...]
    playlists: tuple[PlaylistTable, ...]

    def serialize(self) -> dict[str, Any]:
        """Serialize this subgame to a dictionary for YAML output."""

        def oid_str(xs: tuple[OID, ...]) -> str:
            return " ".join(str(x) for x in xs) if xs else ""

        playlists = [pl.serialize(collapse=True) for pl in self.playlists]
        return dict(
            sorted(
                {
                    "oids1": oid_str(self.oid1s),
                    "oids2": oid_str(self.oid2s),
                    "oids3": oid_str(self.oid3s),
                    "playlist": playlists,
                    "unknown": " ".join(f"{b:02X}" for b in self.header),
                }.items()
            )
        )

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "SubGame":
        """Deserialize a subgame from YAML data."""

        def parse_oids(s: str) -> tuple[OID, ...]:
            if not s or not s.strip():
                return ()
            return tuple(OID(int(x)) for x in s.split())

        def parse_header(s: str) -> bytes:
            if not s or not s.strip():
                return b"\x00" * SUBGAME_HEADER_SIZE
            return bytes(int(x, 16) for x in s.split())

        playlists_raw = data.get("playlist", [])
        playlists = [PlaylistTable.deserialize(pl) for pl in playlists_raw]
        # Pad to 9 playlists if needed
        while len(playlists) < 9:
            playlists.append(PlaylistTable(playlists=()))

        return cls(
            header=parse_header(data.get("unknown", "")),
            oid1s=parse_oids(data.get("oids1", "")),
            oid2s=parse_oids(data.get("oids2", "")),
            oid3s=parse_oids(data.get("oids3", "")),
            playlists=tuple(playlists),
        )

    def encode(self, w: BinaryWriter) -> None:
        """Encode this subgame to a BinaryWriter."""
        # Write subgame header
        if len(self.header) == SUBGAME_HEADER_SIZE:
            w.bytes(self.header)
        else:
            padded = self.header.ljust(SUBGAME_HEADER_SIZE, b"\x00")
            w.bytes(padded[:SUBGAME_HEADER_SIZE])

        # Write OID lists
        w.u16_list([int(oid) for oid in self.oid1s])
        w.u16_list([int(oid) for oid in self.oid2s])
        w.u16_list([int(oid) for oid in self.oid3s])

        # Write playlist pointers (we'll need to patch these)
        playlist_ptr_base = w.offset
        for _ in range(9):
            w.u32(0)

        # Write playlists and patch pointers
        for i, pl in enumerate(self.playlists[:9]):
            w.u32_at(playlist_ptr_base + i * 4, w.offset)
            pl.encode(w)


@dataclass(frozen=True)
class Game:
    """A game definition with its type and type-specific fields."""

    game_type: int
    fields: dict[str, object]

    @classmethod
    def decode(cls, data: bytes, offset: int) -> "Game":
        """Decode a single game structure from binary data."""
        r = GameReader(data, offset)
        game_type = r.u16()

        if game_type == GAME_TYPE_SPECIAL:
            return cls(game_type=GAME_TYPE_SPECIAL, fields={})

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

        return cls(game_type=game_type, fields=f)

    def serialize(self) -> dict[str, Any]:
        """Serialize this Game to a dictionary suitable for YAML output."""
        if self.game_type == GAME_TYPE_SPECIAL:
            return {"tag": "Game253Yaml"}

        f = self.fields
        out: dict[str, Any] = {}

        if self.game_type not in _NAMED_GAME_TYPES:
            out["gametype"] = int(self.game_type)

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
            out["subgames"] = [sg.serialize() for sg in subgames]

        # Tag at the end
        out["tag"] = _tag_for_game_type(self.game_type)

        return dict(sorted(out.items()))

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "Game":
        """Deserialize a game from YAML data."""
        tag = data.get("tag", "")

        if tag == "Game253Yaml":
            return cls(game_type=GAME_TYPE_SPECIAL, fields={})

        # Determine game type from tag or explicit field
        game_type = data.get("gametype", GAME_TYPE_COMMON)
        if tag == "Game6Yaml":
            game_type = GAME_TYPE_BONUS
        elif tag == "Game7Yaml":
            game_type = GAME_TYPE_GROUPED
        elif tag == "Game8Yaml":
            game_type = GAME_TYPE_SELECT
        elif tag == "Game9Yaml":
            game_type = GAME_TYPE_EXTRA_9
        elif tag == "Game10Yaml":
            game_type = GAME_TYPE_EXTRA_10
        elif tag == "Game16Yaml":
            game_type = GAME_TYPE_EXTRA_16

        f: dict[str, object] = {}

        # Scalar fields
        for yaml_key, field_key in _SCALAR_FIELDS:
            if yaml_key in data:
                f[field_key] = data[yaml_key]

        # OID list fields
        for yaml_key, field_key in _OID_LIST_FIELDS:
            if yaml_key in data:
                s = data[yaml_key]
                if s and isinstance(s, str):
                    f[field_key] = [OID(int(x)) for x in s.split()]
                else:
                    f[field_key] = []

        # Playlist fields
        for yaml_key, field_key in _PLAYLIST_FIELDS:
            if yaml_key in data:
                f[field_key] = PlaylistTable.deserialize(data[yaml_key])

        # Playlist list fields
        for yaml_key, field_key in _PLAYLIST_LIST_FIELDS:
            if yaml_key in data:
                raw = data[yaml_key]
                if isinstance(raw, list):
                    f[field_key] = [PlaylistTable.deserialize(pl) for pl in raw]
                else:
                    f[field_key] = []

        # Subgames
        if "subgames" in data:
            f["gSubgames"] = [SubGame.deserialize(sg) for sg in data["subgames"]]
        else:
            f["gSubgames"] = []

        # Compute subgame count from the list
        subgames = f.get("gSubgames", [])
        bonus_count = f.get("gBonusSubgameCount", 0)
        if game_type == GAME_TYPE_BONUS:
            f["gSubgameCount"] = len(subgames) - bonus_count
        else:
            f["gSubgameCount"] = len(subgames)

        return cls(game_type=game_type, fields=f)

    def encode(self, w: BinaryWriter) -> None:
        """Encode this game to a BinaryWriter."""
        if self.game_type == GAME_TYPE_SPECIAL:
            w.u16(GAME_TYPE_SPECIAL)
            return

        f = self.fields
        is_bonus = self.game_type == GAME_TYPE_BONUS

        # Game type
        w.u16(self.game_type)

        # Common scalars
        w.u16(int(f.get("gSubgameCount", 0)))
        w.u16(int(f.get("gRounds", 0)))
        if not is_bonus:
            w.u16(int(f.get("gUnknownC", 0)))

        # Bonus-only scalars
        if is_bonus:
            w.u16(int(f.get("gBonusSubgameCount", 0)))
            w.u16(int(f.get("gBonusRounds", 0)))
            w.u16(int(f.get("gBonusTarget", 0)))
            w.u16(int(f.get("gUnknownI", 0)))

        # More common scalars
        w.u16(int(f.get("gEarlyRounds", 0)))
        if is_bonus:
            w.u16(int(f.get("gUnknownQ", 0)))
        w.u16(int(f.get("gRepeatLastMedia", 0)))
        w.u16(int(f.get("gUnknownX", 0)))
        w.u16(int(f.get("gUnknownW", 0)))
        w.u16(int(f.get("gUnknownV", 0)))

        # Core playlist pointers (we'll patch these)
        playlist_ptrs: list[tuple[str, int]] = []

        def write_playlist_ptr(key: str) -> None:
            playlist_ptrs.append((key, w.offset))
            w.u32(0)

        write_playlist_ptr("gStartPlayList")
        write_playlist_ptr("gRoundEndPlayList")
        write_playlist_ptr("gFinishPlayList")
        write_playlist_ptr("gRoundStartPlayList")
        write_playlist_ptr("gLaterRoundStartPlayList")
        if is_bonus:
            write_playlist_ptr("gRoundStartPlayList2")
            write_playlist_ptr("gLaterRoundStartPlayList2")

        # Subgame pointers
        subgames = f.get("gSubgames", [])
        subgame_ptr_base = w.offset
        for _ in subgames:
            w.u32(0)

        # Target scores
        if is_bonus:
            scores = f.get("gTargetScores", [0, 0])
            for i in range(2):
                w.u16(scores[i] if i < len(scores) else 0)
            bonus_scores = f.get("gBonusTargetScores", [0] * 8)
            for i in range(8):
                w.u16(bonus_scores[i] if i < len(bonus_scores) else 0)
        else:
            scores = f.get("gTargetScores", [0] * 10)
            for i in range(10):
                w.u16(scores[i] if i < len(scores) else 0)

        # Finish playlist pointers
        finish_playlist_ptrs: list[int] = []
        if is_bonus:
            for _ in range(2):
                finish_playlist_ptrs.append(w.offset)
                w.u32(0)
            bonus_finish_ptrs: list[int] = []
            for _ in range(8):
                bonus_finish_ptrs.append(w.offset)
                w.u32(0)
        else:
            for _ in range(10):
                finish_playlist_ptrs.append(w.offset)
                w.u32(0)

        # Bonus subgame IDs pointer
        bonus_ids_ptr = 0
        if is_bonus:
            bonus_ids_ptr = w.offset
            w.u32(0)

        # Game type specific pointers
        grouped_ptr = 0
        select_oids_ptr = 0
        select_ids_ptr = 0
        select_err1_ptr = 0
        select_err2_ptr = 0
        extra_oids_ptr = 0
        extra_playlists_ptrs: list[int] = []

        if self.game_type == GAME_TYPE_GROUPED:
            grouped_ptr = w.offset
            w.u32(0)
        elif self.game_type == GAME_TYPE_SELECT:
            select_oids_ptr = w.offset
            w.u32(0)
            select_ids_ptr = w.offset
            w.u32(0)
            select_err1_ptr = w.offset
            w.u32(0)
            select_err2_ptr = w.offset
            w.u32(0)
        elif self.game_type == GAME_TYPE_EXTRA_16:
            extra_oids_ptr = w.offset
            w.u32(0)

        extra_count = _EXTRA_PLAYLIST_COUNTS.get(self.game_type, 0)
        for _ in range(extra_count):
            extra_playlists_ptrs.append(w.offset)
            w.u32(0)

        # Now write actual data and patch pointers

        # Core playlists
        for key, ptr_offset in playlist_ptrs:
            pl = f.get(key)
            if pl is not None:
                w.u32_at(ptr_offset, w.offset)
                pl.encode(w)
            else:
                w.u32_at(ptr_offset, w.offset)
                PlaylistTable(playlists=()).encode(w)

        # Subgames
        for i, sg in enumerate(subgames):
            w.u32_at(subgame_ptr_base + i * 4, w.offset)
            sg.encode(w)

        # Finish playlists
        if is_bonus:
            finish_pls = f.get("gFinishPlayLists", [])
            for i, ptr in enumerate(finish_playlist_ptrs):
                w.u32_at(ptr, w.offset)
                if i < len(finish_pls):
                    finish_pls[i].encode(w)
                else:
                    PlaylistTable(playlists=()).encode(w)

            bonus_finish_pls = f.get("gBonusFinishPlayLists", [])
            for i, ptr in enumerate(bonus_finish_ptrs):
                w.u32_at(ptr, w.offset)
                if i < len(bonus_finish_pls):
                    bonus_finish_pls[i].encode(w)
                else:
                    PlaylistTable(playlists=()).encode(w)

            # Bonus subgame IDs
            w.u32_at(bonus_ids_ptr, w.offset)
            ids = f.get("gBonusSubgameIds", [])
            w.u16(len(ids))
            for game_id in ids:
                w.u16(game_id + 1)  # Convert back to 1-indexed
        else:
            finish_pls = f.get("gFinishPlayLists", [])
            for i, ptr in enumerate(finish_playlist_ptrs):
                w.u32_at(ptr, w.offset)
                if i < len(finish_pls):
                    finish_pls[i].encode(w)
                else:
                    PlaylistTable(playlists=()).encode(w)

        # Game type specific data
        if self.game_type == GAME_TYPE_GROUPED:
            w.u32_at(grouped_ptr, w.offset)
            groups = f.get("gSubgameGroups", [])
            w.u16(len(groups))
            group_ptrs_base = w.offset
            for _ in groups:
                w.u32(0)
            for i, group in enumerate(groups):
                w.u32_at(group_ptrs_base + i * 4, w.offset)
                w.u16(len(group))
                for game_id in group:
                    w.u16(game_id + 1)

        elif self.game_type == GAME_TYPE_SELECT:
            # OIDs
            w.u32_at(select_oids_ptr, w.offset)
            oids = f.get("gGameSelectOIDs", [])
            w.u16_list([int(oid) for oid in oids])

            # Game IDs
            w.u32_at(select_ids_ptr, w.offset)
            ids = f.get("gGameSelect", [])
            w.u16(len(ids))
            for game_id in ids:
                w.u16(game_id + 1)

            # Error playlists
            w.u32_at(select_err1_ptr, w.offset)
            pl1 = f.get("gGameSelectErrors1")
            if pl1:
                pl1.encode(w)
            else:
                PlaylistTable(playlists=()).encode(w)

            w.u32_at(select_err2_ptr, w.offset)
            pl2 = f.get("gGameSelectErrors2")
            if pl2:
                pl2.encode(w)
            else:
                PlaylistTable(playlists=()).encode(w)

        elif self.game_type == GAME_TYPE_EXTRA_16:
            w.u32_at(extra_oids_ptr, w.offset)
            oids = f.get("gExtraOIDs", [])
            w.u16_list([int(oid) for oid in oids])

        # Extra playlists
        extra_pls = f.get("gExtraPlayLists", [])
        for i, ptr in enumerate(extra_playlists_ptrs):
            w.u32_at(ptr, w.offset)
            if i < len(extra_pls):
                extra_pls[i].encode(w)
            else:
                PlaylistTable(playlists=()).encode(w)


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
        header = self.data[ptr : ptr + SUBGAME_HEADER_SIZE]
        r.skip(SUBGAME_HEADER_SIZE)
        return SubGame(
            header=header,
            oid1s=tuple(OID(x) for x in r.u16_list()),
            oid2s=tuple(OID(x) for x in r.u16_list()),
            oid3s=tuple(OID(x) for x in r.u16_list()),
            playlists=tuple(r.playlist() for _ in range(9)),
        )


@dataclass(frozen=True)
class GameTable:
    """Table of games from a GME file."""

    games: tuple[Game, ...]

    def __iter__(self):
        return iter(self.games)

    def __len__(self) -> int:
        return len(self.games)

    def __getitem__(self, index: int) -> Game:
        return self.games[index]

    @classmethod
    def decode(cls, data: bytes, offset: int) -> "GameTable":
        """Decode all games from the game table at the given offset."""
        if offset <= 0 or offset + 4 > len(data):
            return cls(games=())

        r = GameReader(data, offset)
        n_games = r.u32()

        games: list[Game] = []
        for _ in range(n_games):
            g_off = r.u32()
            if 0 < g_off < len(data):
                games.append(Game.decode(data, g_off))

        return cls(games=tuple(games))

    def serialize(self) -> list[dict[str, Any]]:
        """Serialize all games to a list of dictionaries for YAML output."""
        return [g.serialize() for g in self.games]

    @classmethod
    def deserialize(cls, data: list[dict[str, Any]]) -> "GameTable":
        """Deserialize a game table from YAML data."""
        if not data:
            return cls(games=())
        return cls(games=tuple(Game.deserialize(g) for g in data))

    def encode(self, w: BinaryWriter) -> None:
        """Encode the game table to a BinaryWriter."""
        if not self.games:
            return

        # Write game count
        w.u32(len(self.games))

        # Write game pointer placeholders
        game_ptr_base = w.offset
        for _ in self.games:
            w.u32(0)

        # Write each game and patch its pointer
        for i, game in enumerate(self.games):
            w.u32_at(game_ptr_base + i * 4, w.offset)
            game.encode(w)


def _tag_for_game_type(game_type: int) -> str:
    """Return the YAML tag name for a game type."""
    if game_type in _NAMED_GAME_TYPES:
        return f"Game{game_type}Yaml"
    return "CommonGameYaml"
