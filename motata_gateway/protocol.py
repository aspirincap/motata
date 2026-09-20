"""Bounded protocol; origin, headers, authentication placement are server-owned."""
from __future__ import annotations
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$')
PATH = re.compile(r'^[A-Za-z0-9_/-]{1,512}$')
SECRET_KEYS = re.compile(r'(?i)^(access[-_]?token|refresh[-_]?token|authorization|proxy[-_]?authorization|(?:x[-_]?)?api[-_]?key|appsecret_proof|(?:app|client)[-_]?secret|secret|password|cookie|set-cookie)$')
SECRET_SELECTOR = re.compile(r'(?i)(?:^|[^a-z0-9])(?:access_token|refresh_token|client_secret|app_secret|appsecret_proof)(?:$|[^a-z0-9])')


def validate_tree(value, depth=0):
    if depth > 24:
        raise ValueError('Payload nesting exceeds limit')
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or SECRET_KEYS.fullmatch(key):
                raise ValueError('Credential fields are forbidden')
            validate_tree(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 10000:
            raise ValueError('Array limit exceeded')
        for child in value:
            validate_tree(child, depth + 1)
    elif isinstance(value, str):
        if len(value) > 262144:
            raise ValueError('String limit exceeded')
        # Inspect JSON serialized into API fields; do not trust a second encoding layer.
        if value.lstrip().startswith(('{', '[')):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                return
            validate_tree(decoded, depth + 1)
    elif type(value) is float:
        import math
        if not math.isfinite(value):
            raise ValueError('Non-finite numeric value')
    elif value is not None and type(value) not in (int, bool):
        raise ValueError('Unsupported JSON value')


class UploadSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    field: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(pattern=r'^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$')
    size: int = Field(ge=0, le=4_000_000_000)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('filename')
    @classmethod
    def safe_filename(cls, value):
        if any(ord(c) < 32 or c in '\\/:"' for c in value) or value in ('.', '..'):
            raise ValueError('Unsafe filename')
        return value


class PlatformRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    protocol: Literal['motata-gateway/v1'] = 'motata-gateway/v1'
    request_id: str = Field(min_length=1, max_length=128)
    platform: Literal['meta', 'tiktok']
    account_id: str = Field(pattern=r'^[0-9]{1,32}$')
    credential_ref: str | None = Field(default=None, max_length=128)
    method: Literal['GET', 'POST', 'DELETE']
    path: str
    query: dict = Field(default_factory=dict)
    body: dict | None = None
    page_credential_ref: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{16,64}$')
    body_encoding: Literal['json', 'form'] = 'json'
    idempotency_key: str | None = Field(default=None, max_length=128)
    uploads: list[UploadSpec] = Field(default_factory=list, max_length=8)

    @field_validator('request_id', 'credential_ref', 'idempotency_key')
    @classmethod
    def identifier(cls, value):
        if value is not None and not ID.fullmatch(value):
            raise ValueError('Invalid identifier')
        return value

    @field_validator('path')
    @classmethod
    def relative_path(cls, value):
        # Percent, backslash, query, fragment, absolute/network-path references are rejected.
        if not PATH.fullmatch(value) or value.startswith('/') or '//' in value:
            raise ValueError('Only a canonical relative platform path is allowed')
        return value

    @model_validator(mode='after')
    def payload(self):
        if self.uploads:
            if self.method != 'POST' or len({x.field for x in self.uploads}) != len(self.uploads):
                raise ValueError('Invalid upload metadata')
            if set(x.field for x in self.uploads).intersection(self.body or {}):
                raise ValueError('Upload field collides with business field')
        validate_tree(self.query)
        validate_tree(self.body)
        if self.method in ('GET', 'DELETE') and self.body:
            raise ValueError('Body forbidden for this method')
        for name in ('fields', 'field', 'include', 'expand'):
            selected = self.query.get(name)
            if selected is not None and SECRET_SELECTOR.search(str(selected)):
                raise ValueError('Credential selectors forbidden')
        return self
