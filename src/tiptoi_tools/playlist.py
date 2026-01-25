from dataclasses import dataclass

from tiptoi_tools.binary import BinaryReader


@dataclass(frozen=True)
class Playlist:
    """A playlist containing a sequence of media indices."""

    indices: tuple[int, ...]

    def __iter__(self):
        return iter(self.indices)

    def __len__(self) -> int:
        return len(self.indices)

    def serialize(self) -> str:
        """Serialize as comma-separated media IDs."""
        return ",".join(str(x) for x in self.indices)


@dataclass(frozen=True)
class PlaylistTable:
    """A table of playlists, decoded from a GME file."""

    playlists: tuple[Playlist, ...]

    def __iter__(self):
        return iter(self.playlists)

    def __len__(self) -> int:
        return len(self.playlists)

    def __getitem__(self, index: int) -> Playlist:
        return self.playlists[index]

    def serialize(self, collapse: bool = False) -> str | list[str]:
        """
        Serialize the playlist table.

        If collapse=True and there's exactly one playlist, returns it as a string.
        Otherwise returns a list of serialized playlists.
        """
        if not self.playlists:
            return []
        if collapse and len(self.playlists) == 1:
            return self.playlists[0].serialize()
        return [pl.serialize() for pl in self.playlists]


def decode_table(data: bytes, offset: int) -> PlaylistTable:
    """Decode a playlist table: a count followed by pointers to individual playlists."""
    r = BinaryReader(data, offset)
    count = r.u16()

    playlists: list[Playlist] = []
    for ptr in r.u32_array(count):
        indices = tuple(BinaryReader(data, ptr).u16_list())
        playlists.append(Playlist(indices=indices))

    return PlaylistTable(playlists=tuple(playlists))
