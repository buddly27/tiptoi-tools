from dataclasses import dataclass
from typing import Any

from tiptoi_tools.binary import BinaryReader, BinaryWriter


@dataclass(frozen=True)
class Playlist:
    """A playlist containing a sequence of media indices."""

    indices: tuple[int, ...] = tuple()

    def __iter__(self):
        return iter(self.indices)

    def __len__(self) -> int:
        return len(self.indices)

    def serialize(self) -> str:
        """Serialize as comma-separated media IDs."""
        return ",".join(str(x) for x in self.indices)

    @classmethod
    def deserialize(cls, data: Any) -> "Playlist":
        indices: list[int] = []

        if isinstance(data, int):
            indices.append(data)

        elif isinstance(data, str):
            s = data.strip()
            if s:
                indices.extend(int(x.strip()) for x in s.split(",") if x.strip())

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, int):
                    indices.append(item)
                elif isinstance(item, str):
                    s = item.strip()
                    if s:
                        indices.extend(
                            int(x.strip()) for x in s.split(",") if x.strip()
                        )

        return cls(tuple(indices))


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

    @classmethod
    def decode(cls, data: bytes, offset: int) -> "PlaylistTable":
        """Decode a playlist table from binary data."""
        r = BinaryReader(data, offset)
        count = r.u16()

        playlists: list[Playlist] = []
        for ptr in r.u32_array(count):
            indices = tuple(BinaryReader(data, ptr).u16_list())
            playlists.append(Playlist(indices=indices))

        return cls(playlists=tuple(playlists))

    def encode(self, w: BinaryWriter) -> None:
        """Encode the playlist table to a BinaryWriter."""
        w.u16(len(self.playlists))

        # Write pointer placeholders
        pointer_base = w.offset
        for _ in self.playlists:
            w.u32(0)

        # Write each playlist and patch its pointer
        for i, playlist in enumerate(self.playlists):
            w.u32_at(pointer_base + i * 4, w.offset)
            w.u16_list(list(playlist.indices))

    @classmethod
    def deserialize(cls, data: Any) -> "PlaylistTable":
        """
        Deserialize a playlist table from YAML data.

        Handles both collapsed format (single string) and list format.
        """
        if data is None or data == []:
            return cls(playlists=())

        if isinstance(data, (str, int)):
            # Collapsed single playlist
            return cls(playlists=(Playlist.deserialize(data),))

        if isinstance(data, list):
            return cls(playlists=tuple(Playlist.deserialize(item) for item in data))

        return cls(playlists=())
