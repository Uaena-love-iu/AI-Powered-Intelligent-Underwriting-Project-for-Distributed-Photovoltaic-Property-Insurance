import asyncio

from app.intake.uploads import read_upload_limited


class ChunkedUpload:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.position = 0
        self.requested_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        end = len(self.content) if size < 0 else self.position + size
        chunk = self.content[self.position:end]
        self.position += len(chunk)
        return chunk


def test_upload_reader_stops_after_limit_plus_one_byte() -> None:
    upload = ChunkedUpload(b"0123456789")

    content = asyncio.run(
        read_upload_limited(upload, limit_bytes=5)  # type: ignore[arg-type]
    )

    assert content == b"012345"
    assert upload.position == 6


def test_upload_reader_returns_small_upload_completely() -> None:
    upload = ChunkedUpload(b"small")

    content = asyncio.run(
        read_upload_limited(upload, limit_bytes=10)  # type: ignore[arg-type]
    )

    assert content == b"small"
    assert upload.position == len(upload.content)
