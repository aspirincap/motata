"""Shared HTTP primitives without implicit .netrc credentials or redirects."""
from __future__ import annotations

import json

import requests


class ExplicitAuth(requests.auth.AuthBase):
    """Keep proxy/CA environment support, but suppress Requests' .netrc lookup."""

    def __call__(self, request):
        return request


def bounded_json(response, *, limit: int = 32 * 1024 * 1024):
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=65536):
        size += len(chunk)
        if size > limit:
            raise ValueError("HTTP response exceeds configured JSON size limit")
        chunks.append(chunk)
    return json.loads(b"".join(chunks))
