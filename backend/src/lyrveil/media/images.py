"""Checks, cleans and re-encodes uploaded images with Pillow, crops stored ones and writes their thumbnails
(docs/security.md, Input validation; docs/character-system.md, Avatars and images)."""

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps

from lyrveil.domain.errors import PayloadTooLargeError, UnsupportedMediaError, ValidationFailedError

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_SIDE = 8192
CROP_SIDE = 512
THUMBNAIL_SIDE = 512
# Pillow warns when an image declares more pixels than this and refuses twice as many. The warning is turned into a
# refusal below, so no image over 8192 x 8192 pixels is ever decoded, whatever its file size.
Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE

_TOO_LARGE_BYTES = "L'immagine supera i 15 MB."
_TOO_LARGE_PIXELS = "L'immagine supera 8192 x 8192 pixel."
_UNSUPPORTED = "Il file non è un'immagine PNG, JPEG o WebP valida."
_CROP_OUTSIDE = "Il ritaglio deve stare dentro l'immagine."
_ENCODE_QUALITY = 92
_THUMBNAIL_QUALITY = 85


@dataclass(frozen=True)
class ImageFormat:
    """A format uploads may have: Pillow's name for it, its media type and the file extension it is stored with."""

    pillow_name: str
    media_type: str
    extension: str


PNG = ImageFormat("PNG", "image/png", "png")
JPEG = ImageFormat("JPEG", "image/jpeg", "jpg")
WEBP = ImageFormat("WEBP", "image/webp", "webp")
FORMATS = {item.media_type: item for item in (PNG, JPEG, WEBP)}


@dataclass(frozen=True)
class EncodedImage:
    """An image ready to store: the re-encoded file, which holds no metadata, its WebP thumbnail (longest side at most
    THUMBNAIL_SIDE) and what the asset row keeps. `sha256` is the hex hash of `data`."""

    data: bytes
    thumbnail: bytes
    format: ImageFormat
    width: int
    height: int
    sha256: str


def sniff(data: bytes) -> ImageFormat | None:
    """Returns the format the magic bytes at the start of `data` announce, or None when they announce none of the
    three. Pillow is then told to decode that format only, so a file cannot pass as another one."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    return None


def _open_clean(data: bytes, image_format: ImageFormat) -> Image.Image:
    """Returns the first frame of `data` decoded as `image_format`, turned upright by its EXIF orientation, in a new RGB
    or RGBA image whose metadata is empty: no EXIF, ICC profile, XMP or text chunks survive.

    Raises PayloadTooLargeError when the declared size is over MAX_SIDE on a side or over Image.MAX_IMAGE_PIXELS,
    before any pixel is decoded, and UnsupportedMediaError when Pillow cannot decode the whole frame.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data), formats=[image_format.pillow_name])
            width, height = image.size
            if width > MAX_SIDE or height > MAX_SIDE:
                raise PayloadTooLargeError(_TOO_LARGE_PIXELS)
            image.load()
            upright = ImageOps.exif_transpose(image)
            mode = "RGBA" if upright.has_transparency_data else "RGB"
            converted = upright.convert(mode)
        except PayloadTooLargeError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise PayloadTooLargeError(_TOO_LARGE_PIXELS) from None
        # A hostile file can make a decoder raise almost anything (OSError, SyntaxError, ValueError, struct.error...);
        # each one means the bytes are not an image this service accepts.
        except Exception:
            raise UnsupportedMediaError(_UNSUPPORTED) from None
    # A new image starts with empty `info` and no EXIF, so nothing of the original file but its pixels is kept.
    clean = Image.new(mode, converted.size)
    clean.paste(converted)
    return clean


def _encode(image: Image.Image, image_format: ImageFormat) -> EncodedImage:
    """Returns `image` encoded as `image_format` with its thumbnail. No metadata is passed to the encoders."""
    buffer = io.BytesIO()
    if image_format is PNG:
        image.save(buffer, "PNG")
    else:
        image.save(buffer, image_format.pillow_name, quality=_ENCODE_QUALITY)
    data = buffer.getvalue()
    thumbnail = image.copy()
    thumbnail.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE), Image.Resampling.LANCZOS)
    thumbnail_buffer = io.BytesIO()
    thumbnail.save(thumbnail_buffer, "WEBP", quality=_THUMBNAIL_QUALITY)
    return EncodedImage(
        data=data,
        thumbnail=thumbnail_buffer.getvalue(),
        format=image_format,
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def prepare_upload(data: bytes, declared_type: str | None) -> EncodedImage:
    """Returns the upload `data` re-encoded in its own format without metadata, with its thumbnail.

    `declared_type` is the media type the client sent, or None when it sent none; the magic bytes decide the format,
    and a declared type must agree with them. Raises PayloadTooLargeError over MAX_UPLOAD_BYTES or over the pixel
    limits, and UnsupportedMediaError for bytes that are not PNG, JPEG or WebP, a declared type that disagrees, or a
    file Pillow cannot decode. An animated file keeps its first frame only.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(_TOO_LARGE_BYTES)
    image_format = sniff(data)
    if image_format is None or (declared_type is not None and declared_type != image_format.media_type):
        raise UnsupportedMediaError(_UNSUPPORTED)
    return _encode(_open_clean(data, image_format), image_format)


def crop(data: bytes, image_format: ImageFormat, *, x: int, y: int, width: int, height: int) -> EncodedImage:
    """Returns the rectangle at (`x`, `y`) of `width` x `height` pixels of the stored image `data`, scaled to
    CROP_SIDE x CROP_SIDE in the same format. A rectangle that is not square is centred and trimmed to a square
    first, so the picture is never stretched.

    Requires non-negative `x` and `y` and positive sizes. Raises ValidationFailedError when the rectangle leaves the
    image, and the errors of prepare_upload when `data` does not decode.
    """
    image = _open_clean(data, image_format)
    if x + width > image.width or y + height > image.height:
        raise ValidationFailedError(_CROP_OUTSIDE)
    region = image.crop((x, y, x + width, y + height))
    square = ImageOps.fit(region, (CROP_SIDE, CROP_SIDE), method=Image.Resampling.LANCZOS)
    return _encode(square, image_format)
