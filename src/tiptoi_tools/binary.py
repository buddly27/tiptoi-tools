import struct


def u8(data: bytes, offset: int) -> int:
    """Read an unsigned 8-bit integer."""
    return data[offset]


def u16le(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 16-bit integer."""
    return struct.unpack_from("<H", data, offset)[0]


def u32le(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 32-bit integer."""
    return struct.unpack_from("<I", data, offset)[0]


def lo_u8(n: int) -> int:
    """Extract the low byte of a 16-bit value."""
    return n & 0xFF


def hi_u8(n: int) -> int:
    """Extract the high byte of a 16-bit value."""
    return (n >> 8) & 0xFF


def ascii_clean(b: bytes) -> str:
    """Decode bytes as ASCII, replacing invalid characters."""
    return b.decode("ascii", errors="replace")


class BinaryReader:
    """Sequential reader for binary data with automatic offset tracking."""

    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def u8(self) -> int:
        """Read an unsigned 8-bit integer."""
        v = self.data[self.offset]
        self.offset += 1
        return v

    def u16(self) -> int:
        """Read a little-endian unsigned 16-bit integer."""
        v = u16le(self.data, self.offset)
        self.offset += 2
        return v

    def u32(self) -> int:
        """Read a little-endian unsigned 32-bit integer."""
        v = u32le(self.data, self.offset)
        self.offset += 4
        return v

    def bytes(self, n: int) -> bytes:
        """Read n raw bytes."""
        v = self.data[self.offset : self.offset + n]
        self.offset += n
        return v

    def u16_array(self, count: int) -> list[int]:
        """Read an array of u16 values."""
        values = [u16le(self.data, self.offset + i * 2) for i in range(count)]
        self.offset += count * 2
        return values

    def u32_array(self, count: int) -> list[int]:
        """Read an array of u32 values."""
        values = [u32le(self.data, self.offset + i * 4) for i in range(count)]
        self.offset += count * 4
        return values

    def u16_list(self) -> list[int]:
        """Read a length-prefixed list of u16 values."""
        n = self.u16()
        return self.u16_array(n)

    def at(self, offset: int) -> "BinaryReader":
        """Create a new reader at the given absolute offset."""
        return BinaryReader(self.data, offset)

    def skip(self, n: int) -> None:
        """Skip n bytes."""
        self.offset += n
