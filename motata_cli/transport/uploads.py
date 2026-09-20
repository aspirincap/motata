"""Bounded upload framing. Local files are opened by the CLI, not by the server."""
from __future__ import annotations
import hashlib
import io
import json
import re
import struct
from contextlib import AbstractContextManager
from pathlib import Path
from motata_cli.common.errors import CliError

CHUNK = 64 * 1024
MAX_FILE = 4_000_000_000


class UploadBody(AbstractContextManager):
    def __init__(self, files: dict):
        self.streams = []
        self.specs = []
        self.owned = []
        try:
            if not 1 <= len(files) <= 8:
                raise CliError('An upload must contain between one and eight files.')
            for field, item in sorted(files.items()):
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', field):
                    raise CliError('Invalid upload field.')
                if isinstance(item, tuple):
                    name, stream = item[:2]
                    mime = item[2] if len(item) >= 3 else 'application/octet-stream'
                else:
                    stream = item
                    name, mime = Path(getattr(stream, 'name', field)).name, 'application/octet-stream'
                name = Path(str(name)).name
                if not name or len(name) > 200 or any(c in name for c in '\r\n"\\') or any(ord(c) < 32 for c in name):
                    raise CliError('Invalid upload filename.')
                if not isinstance(mime, str) or not re.fullmatch(r'[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+', mime):
                    raise CliError('Invalid upload media type.')
                if isinstance(stream, bytes):
                    stream = io.BytesIO(stream)
                    self.owned.append(stream)
                if not hasattr(stream, 'read') or not stream.seekable():
                    raise CliError('Upload source must be a seekable binary stream.')
                offset = stream.tell()
                size, digest = 0, hashlib.sha256()
                while chunk := stream.read(CHUNK):
                    if not isinstance(chunk, bytes):
                        raise CliError('Upload source must contain bytes.')
                    size += len(chunk)
                    if size > MAX_FILE:
                        raise CliError('Upload file exceeds the 4,000,000,000-byte client limit.')
                    digest.update(chunk)
                stream.seek(offset)
                self.streams.append((stream, offset, size))
                self.specs.append({'field': field, 'filename': name, 'content_type': mime,
                                   'size': size, 'sha256': digest.hexdigest()})
        except Exception:
            self.__exit__(None, None, None)
            raise

    def encode(self, payload):
        header = json.dumps({**payload, 'uploads': self.specs}, allow_nan=False, separators=(',', ':')).encode()
        if len(header) > 1024 * 1024:
            raise CliError('Upload metadata exceeds limit.')
        yield struct.pack('!I', len(header))
        yield header
        for stream, offset, size in self.streams:
            stream.seek(offset)
            left = size
            while left:
                chunk = stream.read(min(left, CHUNK))
                if not chunk:
                    raise CliError('Upload file changed during transmission.')
                left -= len(chunk)
                yield chunk

    def __exit__(self, *args):
        for stream in self.owned:
            stream.close()
        return False
