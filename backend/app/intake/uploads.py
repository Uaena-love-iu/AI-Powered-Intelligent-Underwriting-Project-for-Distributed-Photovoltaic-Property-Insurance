"""Bounded reads for uploaded files."""

from fastapi import UploadFile

from .files import MAX_FILE_SIZE_BYTES

UPLOAD_READ_CHUNK_BYTES = 64 * 1024


async def read_upload_limited(
    upload: UploadFile,
    *,
    limit_bytes: int = MAX_FILE_SIZE_BYTES,
) -> bytes:
    """Read no more than limit + 1 bytes, enough to detect an oversized upload."""

    chunks: list[bytes] = []
    total_bytes = 0

    while total_bytes <= limit_bytes:
        chunk = await upload.read(
            min(
                UPLOAD_READ_CHUNK_BYTES,
                limit_bytes - total_bytes + 1,
            )
        )
        if not chunk:
            break
        chunks.append(chunk)
        total_bytes += len(chunk)

    return b"".join(chunks)
