"""Small ASGI surface. JWT is checked before bodies, accounts or credentials.

Production runs with direct TLS. Proxy headers are disabled in the launcher;
there is no authentication bypass header and no HTTP administration endpoint.
"""
from __future__ import annotations
import asyncio
import json
import re
import uuid
from .errors import GatewayError
from .protocol import PlatformRequest
from .responses import strict_json


class GatewayApp:
    def __init__(self, service, *, allow_local_http=False):
        self.service = service
        self.allow_local_http = allow_local_http

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                message = await receive()
                if message['type'] == 'lifespan.startup':
                    await send({'type': 'lifespan.startup.complete'})
                elif message['type'] == 'lifespan.shutdown':
                    await self.service.close()
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
            return
        if scope['type'] != 'http':
            return
        request_id = str(uuid.uuid4())
        started = False
        original_send = send
        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start': started = True
            await original_send(message)
        send = tracked_send
        try:
            if scope.get('scheme') != 'https':
                client = (scope.get('client') or ('', 0))[0]
                if not self.allow_local_http or client not in ('127.0.0.1', '::1'):
                    raise GatewayError('HTTPS_REQUIRED', 400, 'HTTPS is required.')
            authorization = [v for k, v in scope.get('headers', []) if k.lower() == b'authorization']
            if len(authorization) != 1:
                raise GatewayError('UNAUTHENTICATED', 401, 'One gateway Authorization header is required.')
            # Bound slow online-JWKS authentication too; not only post-auth requests.
            async with self.service.scheduler.admission():
                principal = await self.service.authenticate_async(authorization[0].decode('ascii'))
            if scope['method'] == 'GET' and scope['path'] == '/v1/health':
                if 'gateway:use' not in principal.scopes:
                    raise GatewayError('INSUFFICIENT_SCOPE', 403, 'Gateway scope is required.')
                await self.respond(send, 200, {'protocol': 'motata-gateway/v1', 'ok': True, 'status': 'experimental'})
                return
            async with self.service.scheduler.admission():
                if scope['method'] != 'POST':
                    raise GatewayError('NOT_FOUND', 404, 'Unknown gateway route.')
                if scope.get('query_string'):
                    raise GatewayError('INVALID_REQUEST', 400, 'URL query parameters are not supported.')
                if scope['path'].startswith('/v1/downloads/'):
                    key=scope['path'].removeprefix('/v1/downloads/')
                    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',key):
                        raise GatewayError('INVALID_REQUEST',400,'Invalid media reference.')
                    await self.service.send_download(key,principal,send)
                    return
                if scope['path'] == '/v1/platform/upload':
                    async with self.service.uploads.receive(receive,
                        lambda req: self.service.preauthorize_upload(req, principal)) as (request, files):
                        request_id = request.request_id
                        response = await self.service.dispatch(request, principal, files=files)
                        await self.respond(send, 200, response)
                    return
                if scope['path'].startswith('/v1/pages/'):
                    reference = scope['path'][10:]
                    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', reference):
                        raise GatewayError('INVALID_REQUEST', 400, 'Invalid pagination reference.')
                    request = self.service.pages.resolve(reference, principal)
                    request = request.model_copy(update={'request_id': request_id})
                elif scope['path'] in ('/v1/platform/request', '/v1/objects/resolve', '/v1/accounts', '/v1/meta/page-credentials'):
                    body = bytearray()
                    async with asyncio.timeout(self.service.limits.body_seconds):
                        while True:
                            event = await receive()
                            if event['type'] == 'http.disconnect':
                                raise GatewayError('CLIENT_DISCONNECTED', 400, 'Client disconnected before request submission.')
                            if event['type'] != 'http.request':
                                raise GatewayError('INVALID_REQUEST', 400, 'Invalid HTTP request.')
                            body.extend(event.get('body', b''))
                            if len(body) > self.service.limits.request_bytes:
                                raise GatewayError('REQUEST_TOO_LARGE', 413, 'Gateway request exceeded its byte limit.')
                            if not event.get('more_body', False):
                                break
                    parsed = strict_json(body)
                    if scope['path'] == '/v1/accounts':
                        await self.respond(send, 200, await self.service.list_accounts(parsed, principal))
                        return
                    request = PlatformRequest.model_validate(parsed)
                    request_id = request.request_id
                    if scope['path']=='/v1/meta/page-credentials':
                        result=await self.service.list_page_credentials(request,principal)
                        await self.respond(send,200,result)
                        return
                    if scope['path'] == '/v1/objects/resolve':
                        result = await self.service.resolve_object(request, principal)
                        await self.respond(send, 200, result)
                        return
                else:
                    raise GatewayError('NOT_FOUND', 404, 'Unknown gateway route.')
                response = await self.service.dispatch(request, principal)
                await self.respond(send, 200, response)
        except GatewayError as error:
            if started: raise
            await self.respond(send, error.status, error.envelope(request_id))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            if started: raise
            # Pydantic errors echo inputs; never send those exception strings.
            await self.respond(send, 400, GatewayError('INVALID_REQUEST', 400, 'Invalid gateway request.').envelope(request_id))
        except TimeoutError:
            if started: raise
            await self.respond(send, 408, GatewayError('BODY_TIMEOUT', 408, 'Request body deadline exceeded.').envelope(request_id))
        except Exception:
            if started: raise
            # No exception traceback/request object is emitted to a caller or debug log.
            await self.respond(send, 500, GatewayError('INTERNAL_ERROR', 500, 'Gateway operation could not complete.', 'unknown').envelope(request_id))

    @staticmethod
    async def respond(send, status, payload):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        headers = [(b'content-type', b'application/json'), (b'cache-control', b'no-store'),
                   (b'x-content-type-options', b'nosniff'), (b'content-length', str(len(data)).encode())]
        if status == 401:
            headers.append((b'www-authenticate', b'Bearer realm="motata-gateway"'))
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': data})
