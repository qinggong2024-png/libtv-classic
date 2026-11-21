import hashlib
import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImageInputError(ValueError):
    """An explicit file-validation failure, distinct from processing bugs."""

    def __init__(
        self, code: str, message: str, user_message: str,
        details: dict[str, str | int] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = user_message
        self.details = details or {}


@dataclass(frozen=True)
class PreprocessedImage:
    asset_id: uuid.UUID
    sha256: str
    normalized_path: Path
    thumbnail_path: Path
    width: int
    height: int
    mime_type: str
    order: int
    version: int = 1


def preprocess_image(
    data: bytes,
    filename: str,
    order: int,
    artifact_root: Path,
    *,
    asset_id: uuid.UUID | None = None,
    max_bytes: int = 20 * 1024 * 1024,
) -> PreprocessedImage:
    if not 1 <= order <= 7:
        raise ValueError("storyboard order must be between 1 and 7")
    if not data:
        raise ImageInputError("IMAGE_EMPTY", "image is empty", "图片文件为空，请重新选择原图。")
    if len(data) > max_bytes:
        raise ImageInputError(
            "INPUT_TOO_LARGE", f"image exceeds the {max_bytes}-byte size limit",
            f"图片超过本站 {max_bytes / 1024 / 1024:g} MiB 的大小限制。",
            {"maxBytes": max_bytes},
        )
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ALLOWED_FORMATS:
                actual_format = source.format or "UNKNOWN"
                raise ImageInputError(
                    "IMAGE_TYPE_UNSUPPORTED",
                    "only JPEG, PNG and WebP storyboard images are supported",
                    f"图片实际格式为 {actual_format}，本站只支持 JPG、PNG、WebP；修改扩展名无效。",
                    {"actualFormat": actual_format},
                )
            normalized = ImageOps.exif_transpose(source)
            if not 128 <= normalized.width <= 8192 or not 128 <= normalized.height <= 8192:
                raise ImageInputError(
                    "IMAGE_DIMENSIONS_INVALID",
                    "image dimensions must be between 128 and 8192 pixels",
                    f"图片尺寸为 {normalized.width} × {normalized.height} 像素，"
                    "宽、高均须在 128～8192 像素之间。",
                    {"width": normalized.width, "height": normalized.height,
                     "minDimension": 128, "maxDimension": 8192},
                )
    except Image.DecompressionBombError as exc:
        raise ImageInputError(
            "IMAGE_DIMENSIONS_INVALID", "image exceeds safe pixel limits",
            "图片像素过大，宽、高均须在 128～8192 像素之间。",
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageInputError(
            "IMAGE_INVALID", f"invalid or incomplete image: {filename}",
            "后台无法完整解码这张图片，请重新导出为 JPG、PNG 或 WebP 后再上传。",
        ) from exc

    # Encoding failures are processing failures, not evidence of an invalid input.
    if normalized.mode not in {"RGB", "L"}:
        background = Image.new("RGB", normalized.size, "white")
        if "A" in normalized.getbands():
            background.paste(normalized, mask=normalized.getchannel("A"))
        else:
            background.paste(normalized.convert("RGB"))
        normalized = background
    else:
        normalized = normalized.convert("RGB")
    normalized_buffer = io.BytesIO()
    normalized.save(normalized_buffer, format="JPEG", quality=92, optimize=True)
    normalized_bytes = normalized_buffer.getvalue()
    width, height = normalized.size
    thumb = normalized.copy()
    thumb.thumbnail((360, 640), Image.Resampling.LANCZOS)
    thumb_buffer = io.BytesIO()
    thumb.save(thumb_buffer, format="JPEG", quality=82, optimize=True)

    resolved_id = asset_id or uuid.uuid4()
    digest = hashlib.sha256(normalized_bytes).hexdigest()
    normalized_path = artifact_root / "inputs" / str(resolved_id) / "normalized.jpg"
    thumbnail_path = artifact_root / "thumbs" / str(resolved_id) / "thumbnail.jpg"
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path.write_bytes(normalized_bytes)
    thumbnail_path.write_bytes(thumb_buffer.getvalue())
    return PreprocessedImage(
        asset_id=resolved_id,
        sha256=digest,
        normalized_path=normalized_path,
        thumbnail_path=thumbnail_path,
        width=width,
        height=height,
        mime_type="image/jpeg",
        order=order,
    )
