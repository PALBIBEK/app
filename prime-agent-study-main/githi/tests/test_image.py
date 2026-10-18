"""Image detection for the read tool (port of `harness/tools/image.ts`).

githi's read tool decodes every file as text. Handed a PNG it produces a
screenful of replacement characters -- the model cannot see the image *and*
cannot tell that it failed to.

Detection is by **content**, never by extension. An extension is a claim made
by whoever named the file; the magic bytes are the file. Getting this backwards
fails in both directions: a `.png` that is really text would be base64'd and
billed as an image, and a screenshot saved without an extension would be
silently mangled into text.

The rejections are as important as the detections. A format the API cannot
render must come back as `None` so the caller can say so plainly, rather than
being sent as an attachment the model receives as nothing.
"""

from __future__ import annotations

import base64

from githi.tools.image import detect_supported_image_mime_type, encode_base64

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _chunk(kind: bytes, payload: bytes = b"") -> bytes:
    """length, type, payload, CRC. The trailing CRC is what makes the chunk
    walk land on the next chunk boundary rather than four bytes short of it."""
    return len(payload).to_bytes(4, "big") + kind + payload + b"\x00" * 4


def _png(chunks: bytes = b"") -> bytes:
    """A minimal valid PNG: signature, then a 13-byte IHDR chunk."""
    return PNG_SIGNATURE + _chunk(b"IHDR", b"\x00" * 13) + chunks


def _bmp(dib_size: int = 40, bits: int = 24, planes: int = 1) -> bytes:
    header = bytearray(b"BM" + (200).to_bytes(4, "little") + b"\x00" * 4
                       + (14 + dib_size).to_bytes(4, "little")
                       + dib_size.to_bytes(4, "little"))
    header += b"\x00" * (30 - len(header))
    header[26:28] = planes.to_bytes(2, "little")
    header[28:30] = bits.to_bytes(2, "little")
    return bytes(header) + b"\x00" * 20


class TestDetection:
    def test_png(self):
        assert detect_supported_image_mime_type(_png()) == "image/png"

    def test_jpeg(self):
        assert detect_supported_image_mime_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"

    def test_gif(self):
        assert detect_supported_image_mime_type(b"GIF89a....") == "image/gif"

    def test_webp(self):
        """WEBP is a RIFF container -- the marker is at offset 8, not 0."""
        assert detect_supported_image_mime_type(
            b"RIFF" + (100).to_bytes(4, "little") + b"WEBPVP8 ") == "image/webp"

    def test_bmp(self):
        assert detect_supported_image_mime_type(_bmp()) == "image/bmp"


class TestRejection:
    def test_plain_text_is_not_an_image(self):
        assert detect_supported_image_mime_type(b"hello world") is None

    def test_empty_input(self):
        assert detect_supported_image_mime_type(b"") is None

    def test_truncated_signature(self):
        assert detect_supported_image_mime_type(PNG_SIGNATURE[:4]) is None

    def test_animated_png_is_rejected(self):
        """An `acTL` chunk before `IDAT` means APNG. A still-image attachment
        cannot represent it, so refusing beats sending frame one and letting
        the model believe it saw the whole thing."""
        assert detect_supported_image_mime_type(
            _png(_chunk(b"acTL") + _chunk(b"IDAT"))) is None

    def test_png_with_idat_first_is_a_still_image(self):
        """`IDAT` before any `acTL` settles it: pixel data has started, so no
        animation control chunk can still be coming."""
        assert detect_supported_image_mime_type(
            _png(_chunk(b"IDAT"))) == "image/png"

    def test_arithmetic_coded_jpeg_is_rejected(self):
        """Marker 0xF7 is a lossless/arithmetic JPEG that most decoders on the
        far end cannot open."""
        assert detect_supported_image_mime_type(b"\xff\xd8\xff\xf7rest") is None

    def test_png_signature_without_ihdr_is_rejected(self):
        """The signature alone is cheap to fake; a real PNG opens with a
        13-byte IHDR."""
        assert detect_supported_image_mime_type(PNG_SIGNATURE + b"\x00" * 12) is None

    def test_riff_that_is_not_webp(self):
        assert detect_supported_image_mime_type(
            b"RIFF" + (100).to_bytes(4, "little") + b"WAVEfmt ") is None

    def test_bmp_with_impossible_bit_depth(self):
        """`BM` is only two bytes and collides with ordinary text; the DIB
        header has to corroborate it."""
        assert detect_supported_image_mime_type(_bmp(bits=7)) is None

    def test_bmp_with_multiple_color_planes(self):
        assert detect_supported_image_mime_type(_bmp(planes=2)) is None

    def test_bmp_with_unknown_dib_header_size(self):
        assert detect_supported_image_mime_type(_bmp(dib_size=999)) is None

    def test_short_bmp(self):
        assert detect_supported_image_mime_type(b"BM" + b"\x00" * 5) is None


class TestBase64:
    def test_matches_the_standard_encoding(self):
        payload = bytes(range(256))
        assert encode_base64(payload) == base64.b64encode(payload).decode()

    def test_pads_correctly(self):
        for length in (0, 1, 2, 3, 4, 5):
            payload = b"x" * length
            assert encode_base64(payload) == base64.b64encode(payload).decode()
