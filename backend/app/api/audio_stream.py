"""Byte-range partial content HTTP 206 audio streaming per Spec 05 section 5."""

import os
import aiofiles
from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse


async def serve_byte_range_audio(file_path: str, request: Request) -> StreamingResponse:
    """Serve MP3 audio files with HTTP 206 byte-range support for browser seekability."""
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audio file not found: {os.path.basename(file_path)}",
        )

    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")

    if not range_header:
        # Full file delivery
        async def full_stream():
            async with aiofiles.open(file_path, mode="rb") as f:
                while chunk := await f.read(65536):
                    yield chunk

        return StreamingResponse(
            full_stream(),
            status_code=200,
            media_type="audio/mpeg",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    # Range parsing: bytes=START-END, bytes=START-, or bytes=-SUFFIX
    header_val = range_header.strip()
    if not header_val.lower().startswith("bytes="):
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    byte_range = header_val[6:].strip()
    # RFC 7233 / Spec 07: Reject multipart ranges or invalid syntax
    if "," in byte_range or "-" not in byte_range:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    parts = byte_range.split("-", 1)
    raw_start = parts[0].strip()
    raw_end = parts[1].strip()

    try:
        if not raw_start:
            # Suffix range: bytes=-SUFFIX
            suffix_len = int(raw_end)
            if suffix_len <= 0 or file_size == 0:
                raise HTTPException(
                    status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        elif not raw_end:
            # Prefix range: bytes=START-
            start = int(raw_start)
            end = file_size - 1
        else:
            # Explicit range: bytes=START-END
            start = int(raw_start)
            end = int(raw_end)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Clamping & satisfiable validation:
    # If start is beyond EOF or negative, or start > end:
    if file_size == 0 or start < 0 or start >= file_size or start > end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # If end is beyond EOF, RFC 7233 allows clamping to file_size - 1
    if end >= file_size:
        end = file_size - 1


    chunk_length = (end - start) + 1

    async def range_stream():
        async with aiofiles.open(file_path, mode="rb") as f:
            await f.seek(start)
            bytes_left = chunk_length
            while bytes_left > 0:
                read_size = min(65536, bytes_left)
                chunk = await f.read(read_size)
                if not chunk:
                    break
                bytes_left -= len(chunk)
                yield chunk

    return StreamingResponse(
        range_stream(),
        status_code=206,
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(chunk_length),
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )

