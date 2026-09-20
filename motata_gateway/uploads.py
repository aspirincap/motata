"""Authenticated, size/hash checked spooling and bounded upstream multipart streams.

No path from a request is ever opened. Temporary files are service-owned and always
removed. Binary data is never converted to base64 or accumulated into one JSON body.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import secrets
import struct
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from .errors import GatewayError
from .protocol import PlatformRequest
from .responses import strict_json

CHUNK = 65536


class Reader:
    def __init__(self, receive):
        self.receive, self.buffer, self.done = receive, bytearray(), False

    async def chunk(self, maximum=CHUNK):
        while not self.buffer and not self.done:
            event = await self.receive()
            if event['type'] == 'http.disconnect':
                raise GatewayError('CLIENT_DISCONNECTED', 400, 'Upload was interrupted.', 'not_sent')
            if event['type'] != 'http.request':
                raise GatewayError('INVALID_REQUEST', 400, 'Invalid upload event.')
            data = event.get('body', b'')
            # ASGI servers control event chunk size. Do not admit an arbitrarily
            # large in-process event even when total upload quota is larger.
            if len(data) > 8 * 1024 * 1024:
                raise GatewayError('REQUEST_TOO_LARGE', 413, 'Upload frame exceeds limit.')
            self.buffer.extend(data)
            self.done = not event.get('more_body', False)
        result = bytes(self.buffer[:maximum])
        del self.buffer[:maximum]
        return result

    async def exact(self, count):
        result = bytearray()
        while len(result) < count:
            part = await self.chunk(min(CHUNK, count - len(result)))
            if not part:
                raise GatewayError('UPLOAD_TRUNCATED', 400, 'Upload ended before its declared size.', 'not_sent')
            result.extend(part)
        return bytes(result)


class UploadManager:
    def __init__(self, root: Path, *, capacity=4, max_file=4_000_000_000,
                 quota=8_000_000_000, deadline=300):
        self.root = root
        self.capacity, self.max_file, self.quota, self.deadline = capacity, max_file, quota, deadline
        self.active = self.reserved = 0

    @asynccontextmanager
    async def receive(self, receive, authorize):
        if self.active >= self.capacity:
            raise GatewayError('GATEWAY_BUSY', 503, 'Upload capacity reached.')
        self.active += 1
        reserved = 0
        try:
            async with asyncio.timeout(self.deadline):
                reader = Reader(receive)
                size = struct.unpack('!I', await reader.exact(4))[0]
                if not 1 <= size <= 1024 * 1024:
                    raise GatewayError('REQUEST_TOO_LARGE', 413, 'Upload metadata exceeds limit.')
                request = PlatformRequest.model_validate(strict_json(await reader.exact(size)))
                if not request.uploads:
                    raise GatewayError('INVALID_REQUEST', 400, 'Upload metadata is required.')
                # No credential resolution or file IO until coarse authorization.
                authorize(request)
                amount = sum(x.size for x in request.uploads)
                if any(x.size > self.max_file for x in request.uploads) or self.reserved + amount > self.quota:
                    raise GatewayError('UPLOAD_QUOTA', 413, 'Upload storage quota exceeded.', 'not_sent')
                self.reserved += amount
                reserved = amount
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                from .state import check_private
                check_private(self.root, directory=True)
                with tempfile.TemporaryDirectory(prefix='upload-', dir=self.root) as tmp:
                    staged = {}
                    for spec in request.uploads:
                        path = Path(tmp) / secrets.token_hex(16)
                        # Filename from request is never a path on disk.
                        with path.open('xb') as stream:
                            os.chmod(path, 0o600)
                            remaining, digest = spec.size, hashlib.sha256()
                            while remaining:
                                chunk = await reader.chunk(min(CHUNK, remaining))
                                if not chunk:
                                    raise GatewayError('UPLOAD_TRUNCATED', 400, 'Upload size mismatch.', 'not_sent')
                                await asyncio.to_thread(stream.write, chunk)
                                digest.update(chunk)
                                remaining -= len(chunk)
                        if digest.hexdigest() != spec.sha256:
                            raise GatewayError('UPLOAD_HASH_MISMATCH', 400, 'Upload content hash mismatch.', 'not_sent')
                        staged[spec.field] = (path, spec)
                    if await reader.chunk(1):
                        raise GatewayError('UPLOAD_TRAILING_BYTES', 400, 'Unexpected bytes after upload.', 'not_sent')
                    yield request, staged
        finally:
            self.reserved -= reserved
            self.active -= 1


class MultipartStream(httpx.AsyncByteStream):
    def __init__(self, fields: dict, files: dict):
        self.fields, self.files = fields, files
        self.boundary = 'motata-' + secrets.token_hex(24)

    async def __aiter__(self):
        boundary = self.boundary.encode()
        for key, value in self.fields.items():
            # Header field names are validated separately from flexible business JSON.
            import re
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', key):
                raise GatewayError('INVALID_REQUEST', 400, 'Invalid multipart field.')
            encoded = json.dumps(value, separators=(',', ':')) if isinstance(value, (dict, list, bool)) else str(value)
            yield b'--' + boundary + b'\r\nContent-Disposition: form-data; name="' + key.encode() + b'"\r\n\r\n'
            yield encoded.encode()
            yield b'\r\n'
        for field, (path, spec) in self.files.items():
            yield (b'--' + boundary + b'\r\nContent-Disposition: form-data; name="' + field.encode()
                   + b'"; filename="' + spec.filename.encode() + b'"\r\nContent-Type: '
                   + spec.content_type.encode() + b'\r\n\r\n')
            with path.open('rb') as source:
                while chunk := await asyncio.to_thread(source.read, CHUNK):
                    yield chunk
            yield b'\r\n'
        yield b'--' + boundary + b'--\r\n'
