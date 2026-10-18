"""Image detection for the read tool (port of `harness/tools/image.ts`).

Detection is by **content**, never by extension. An extension is a claim made
by whoever named the file; the magic bytes are the file. Trusting the name
fails in both directions -- a `.png` that is really text gets base64'd and
billed as an image, and a screenshot saved without an extension gets mangled
into replacement characters.

The rejections carry as much weight as the detections. Every format here is
one the model's API can actually render; anything else returns `None` so the
read tool can say "this is a binary file" instead of attaching something that
arrives as nothing. Sending an unrenderable attachment is the worst outcome,
because the model has no way to tell that it saw nothing.
"""

from __future__ import annotations

import base64

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
_BMP_BIT_DEPTHS = frozenset({1, 4, 8, 16, 24, 32})


def detect_supported_image_mime_type(buffer: bytes) -> str | None:
    """Return the MIME type, or `None` if this is not a renderable image."""
    if buffer.startswith(b"\xff\xd8\xff"):
        # 0xF7 is a lossless/arithmetic JPEG that most decoders cannot open.
        return None if len(buffer) > 3 and buffer[3] == 0xF7 else "image/jpeg"

    if buffer.startswith(PNG_SIGNATURE):
        return "image/png" if _is_png(buffer) and not _is_animated_png(buffer) else None

    if buffer.startswith(b"GIF"):
        return "image/gif"

    if buffer.startswith(b"RIFF") and buffer[8:12] == b"WEBP":
        return "image/webp"

    if buffer.startswith(b"BM") and _is_bmp(buffer):
        return "image/bmp"

    return None


def encode_base64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _is_png(buffer: bytes) -> bool:
    """A real PNG opens with a 13-byte IHDR chunk. The 8-byte signature alone
    is cheap for an unrelated file to collide with."""
    return (len(buffer) >= 16
            and int.from_bytes(buffer[8:12], "big") == 13
            and buffer[12:16] == b"IHDR")


def _is_animated_png(buffer: bytes) -> bool:
    """Walk the chunk list looking for `acTL` before `IDAT`.

    Order settles it: `acTL` must precede the first `IDAT`, so reaching pixel
    data without having seen one proves the file is a still image. Stopping at
    `IDAT` also means we never walk the (potentially huge) pixel payload.
    """
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(buffer):
        chunk_length = int.from_bytes(buffer[offset:offset + 4], "big")
        chunk_type = buffer[offset + 4:offset + 8]
        if chunk_type == b"acTL":
            return True
        if chunk_type == b"IDAT":
            return False
        # 8 bytes of header + payload + 4-byte CRC.
        next_offset = offset + 8 + chunk_length + 4
        # A length that does not advance, or runs past the end, means the file
        # is malformed or truncated -- stop rather than loop forever.
        if next_offset <= offset or next_offset > len(buffer):
            return False
        offset = next_offset
    return False


def _is_bmp(buffer: bytes) -> bool:
    """`BM` is two ASCII bytes and collides with ordinary text, so the DIB
    header has to corroborate it before we call this an image."""
    if len(buffer) < 26:
        return False

    declared_size = int.from_bytes(buffer[2:6], "little")
    pixel_offset = int.from_bytes(buffer[10:14], "little")
    dib_size = int.from_bytes(buffer[14:18], "little")

    # A declared size of 0 appears in the wild and is tolerated; a nonzero one
    # smaller than the headers it claims to contain is not.
    if declared_size != 0 and declared_size < 26:
        return False
    if pixel_offset < 14 + dib_size:
        return False
    if declared_size != 0 and pixel_offset >= declared_size:
        return False

    if dib_size == 12:                          # BITMAPCOREHEADER
        planes = int.from_bytes(buffer[22:24], "little")
        bits_per_pixel = int.from_bytes(buffer[24:26], "little")
    elif 40 <= dib_size <= 124:                 # BITMAPINFOHEADER and later
        if len(buffer) < 30:
            return False
        planes = int.from_bytes(buffer[26:28], "little")
        bits_per_pixel = int.from_bytes(buffer[28:30], "little")
    else:
        return False

    return planes == 1 and bits_per_pixel in _BMP_BIT_DEPTHS
