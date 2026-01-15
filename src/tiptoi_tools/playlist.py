from tiptoi_tools.binary import BinaryReader


def decode_table(data: bytes, offset: int) -> list[list[int]]:
    """Decode a playlist table: a count followed by pointers to individual playlists."""
    r = BinaryReader(data, offset)
    count = r.u16()

    playlists: list[list[int]] = []
    for ptr in r.u32_array(count):
        playlists.append(BinaryReader(data, ptr).u16_list())

    return playlists


def serialize_table(
    playlists: list[list[int]], collapse: bool = False
) -> str | list[str]:
    """
    Serialize a playlist table.

    If collapse=True and there's exactly one playlist, returns it as a string.
    Otherwise returns a list of serialized playlists.
    """
    if not playlists:
        return []
    if collapse and len(playlists) == 1:
        return serialize(playlists[0])

    return [serialize(pl) for pl in playlists]


def serialize(xs: list[int]) -> str:
    """Serialize a single playlist as comma-separated media IDs."""
    return ",".join(str(x) for x in xs)
