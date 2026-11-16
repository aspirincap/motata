"""Bounded upload framing. Clients send bytes, never gateway filesystem paths."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
import uuid
from pydantic import BaseModel, ConfigDict, Field

from .errors import GatewayError
from .protocol import PlatformRequest
from .responses import strict_json


class UploadHeader(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request: PlatformRequest
    field: str = Field(pattern=r'^[a-z_]{1,32}$')
    filename: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,128}$')
    content_type: str = Field(pattern=r'^[a-z]+/[a-z0-9.+-]+$', max_length=100)
    size: int = Field(gt=0, le=512 * 1024 * 1024)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


async def receive_upload(receive, service, principal):
    """Validate identity/account/policy before accepting the potentially large body."""
    prefix = bytearray()
    metadata_size = None
    header = None
    stream = None
    size = 0
    digest = hashlib.sha256()
    try:
        async with asyncio.timeout(service.limits.upload_seconds):
            while True:
                event = await receive()
                if event['type'] != 'http.request':
                    raise GatewayError('CLIENT_DISCONNECTED', 400, 'Upload interrupted before platform submission.')
                chunk = event.get('body', b'')
                if header is None:
                    # Never buffer an unbounded ASGI chunk while reading metadata.
                    needed = 4 - len(prefix) if metadata_size is None else metadata_size + 4 - len(prefix)
                    prefix.extend(chunk[:needed]); chunk = chunk[needed:]
                    if metadata_size is None and len(prefix) == 4:
                        metadata_size = int.from_bytes(prefix, 'big')
                        if not 1 <= metadata_size <= 65536:
                            raise GatewayError('INVALID_REQUEST', 400, 'Invalid upload metadata size.')
                        needed = metadata_size + 4 - len(prefix)
                        prefix.extend(chunk[:needed]); chunk = chunk[needed:]
                    if metadata_size is not None and len(prefix) == metadata_size + 4:
                        header = UploadHeader.model_validate(strict_json(prefix[4:]))
                        service.preflight(header.request, principal)
                        expected = ('bytes' if header.request.path.endswith('/adimages') else 'source') if header.request.platform == 'meta' else ('image_file' if '/image/' in header.request.path else 'video_file')
                        allowed_path = (re.fullmatch(r'act_[0-9]+/(adimages|advideos)', header.request.path)
                                        if header.request.platform == 'meta' else header.request.path in ('file/image/ad/upload/', 'file/video/ad/upload/'))
                        if header.request.method != 'POST' or not allowed_path or header.field != expected:
                            raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Unreviewed upload field or endpoint.')
                        if header.size > service.limits.upload_bytes:
                            raise GatewayError('REQUEST_TOO_LARGE', 413, 'Upload exceeded configured size limit.')
                        stream = tempfile.TemporaryFile(mode='w+b')
                if chunk:
                    if header is None:
                        raise GatewayError('INVALID_REQUEST', 400, 'Invalid upload framing.')
                    size += len(chunk)
                    if size > header.size:
                        raise GatewayError('REQUEST_TOO_LARGE', 413, 'Upload length exceeds its declared size.')
                    digest.update(chunk)
                    await asyncio.to_thread(stream.write, chunk)
                if not event.get('more_body', False):
                    break
            if header is None or size != header.size or digest.hexdigest() != header.sha256:
                raise GatewayError('UPLOAD_INTEGRITY_FAILED', 400, 'Upload is incomplete or failed its digest check.')
            stream.seek(0)
            return header, stream
    except BaseException:
        if stream is not None:
            stream.close()
        raise


def multipart(header, stream):
    boundary = 'motata-' + uuid.uuid4().hex
    parts = bytearray()
    for key, value in (header.request.body or {}).items():
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', key):
            raise GatewayError('INVALID_REQUEST', 400, 'Invalid multipart field.')
        if value is None:
            continue
        value = json.dumps(value, separators=(',', ':')) if not isinstance(value, str) else value
        parts.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{header.field}"; filename="{header.filename}"\r\nContent-Type: {header.content_type}\r\n\r\n'.encode())
    tail = f'\r\n--{boundary}--\r\n'.encode()
    async def chunks():
        yield bytes(parts)
        while chunk := await asyncio.to_thread(stream.read, 65536):
            yield chunk
        yield tail
    return chunks(), {'Content-Type': 'multipart/form-data; boundary=' + boundary,
                      'Content-Length': str(len(parts) + header.size + len(tail))}
