from dataclasses import dataclass

from tiptoi_tools.binary import BinaryReader

# Known audio file magic bytes and their extensions
FILE_SIGNATURES: dict[bytes, str] = {
    b"OggS": ".ogg",
    b"RIFF": ".wav",
    b"fLaC": ".flac",
    b"ID3": ".mp3",
}


@dataclass(frozen=True)
class MediaEntry:
    """A single media file entry from the GME media table."""

    index: int
    offset: int
    length: int
    magic_xor: int


def decode(
    data: bytes,
    table_offset: int,
    stop_at: int | None,
) -> list[MediaEntry]:
    """
    Decode the media table from GME binary data.

    The table contains offset/length pairs for each media file. Parsing stops
    when the table cursor reaches the first media blob or the stop_at offset.
    """
    if table_offset <= 0 or table_offset + 8 > len(data):
        raise ValueError(f"Invalid media table offset: 0x{table_offset:08X}")

    entries: list[MediaEntry] = []
    r = BinaryReader(data, table_offset)
    min_media_offset: int | None = None
    index = 0
    detected_magic_xor: int | None = None

    while r.offset + 8 <= len(data):
        if stop_at is not None and r.offset >= stop_at:
            break

        offset = r.u32()
        length = r.u32()

        # Skip completely null entries but continue parsing
        if offset == 0 and length == 0:
            entries.append(MediaEntry(index, 0, 0, 0))
            index += 1
            continue

        if offset + length > len(data):
            raise ValueError(f"Media entry {index} out of bounds")

        # For entries with sufficient data, try to detect magic_xor
        if length >= 4:
            try:
                magic_xor = _find_magic_xor(data[offset : offset + 4])
                if detected_magic_xor is None:
                    detected_magic_xor = magic_xor
            except ValueError:
                # Detection failed (unusual file format) - use previously detected value
                magic_xor = detected_magic_xor if detected_magic_xor is not None else 0
        else:
            # Small/empty entry - use previously detected magic_xor or 0
            magic_xor = detected_magic_xor if detected_magic_xor is not None else 0

        entries.append(MediaEntry(index, offset, length, magic_xor))
        index += 1

        if length > 0:
            if min_media_offset is None or offset < min_media_offset:
                min_media_offset = offset

        # Stop when the table cursor reaches the first media blob
        if min_media_offset is not None and r.offset >= min_media_offset:
            break

    if not entries:
        raise ValueError(
            f"Media table at 0x{table_offset:08X} contained no valid entries"
        )

    return entries


def _find_magic_xor(first4: bytes) -> int:
    """
    Find the XOR key by trying all 256 values until decryption yields
    a known file signature.
    """
    if len(first4) < 4:
        raise ValueError("Need 4 bytes to detect magic_xor")

    for x in range(256):
        x &= 0xFF
        # Bytes unchanged during encryption:
        # - 0x00: XOR with anything is identity for the key bits
        # - 0xFF: all bits set
        # - x: the key itself
        # - x^0xFF: the key's complement
        keep = (0x00, 0xFF, x, x ^ 0xFF)
        dec4 = bytes(b if b in keep else (b ^ x) for b in first4[:4])
        if any(dec4.startswith(m) for m in FILE_SIGNATURES):
            return x

    raise ValueError("Could not find magic_xor")


def decrypt_media(payload: bytes, magic_xor: int) -> bytes:
    """
    Tiptoi media decryption:
    - bytes 0x00, 0xFF, x, (x ^ 0xFF) are unchanged
    - everything else XORed with x
    """
    x = magic_xor & 0xFF
    keep = {0x00, 0xFF, x, x ^ 0xFF}
    return bytes(b if b in keep else (b ^ x) for b in payload)


def guess_extension(decrypted: bytes) -> str:
    """Guess file extension from decrypted media header."""
    for magic, ext in FILE_SIGNATURES.items():
        if decrypted.startswith(magic):
            return ext
    return ".bin"
