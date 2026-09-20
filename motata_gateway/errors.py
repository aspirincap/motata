"""Only explicit safe messages cross the gateway boundary."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class GatewayError(Exception):
    code: str
    status: int = 400
    message: str = 'The gateway rejected this request.'
    write_outcome: str = 'not_sent'

    def __str__(self) -> str:
        return self.message

    def envelope(self, request_id: str) -> dict:
        return {'protocol': 'motata-gateway/v1', 'request_id': request_id, 'ok': False,
                'error': {'code': self.code, 'message': self.message,
                          'write_outcome': self.write_outcome}}
