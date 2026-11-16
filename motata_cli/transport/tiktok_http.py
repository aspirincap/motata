"""Direct TikTok transport with one bounded retry owner for read-only requests."""
from __future__ import annotations

import atexit
import logging
import mimetypes
import time
import weakref
from contextlib import ExitStack
from pathlib import Path

import requests
from requests_toolbelt import MultipartEncoder

from motata_cli.common.errors import CliError
from motata_cli.common.http import ExplicitAuth, bounded_json
from motata_cli.common.security import network_error
from motata_cli.tiktok import codec
from motata_cli.tiktok.errors import TikTokError, check_response
from motata_cli.tiktok.retry import ReadRetryPolicy
from motata_cli.tiktok.throttle import default_read_throttle

_clients = weakref.WeakSet()
_logger = logging.getLogger(__name__)


def _close_clients():
    for client in list(_clients):
        client.close()


atexit.register(_close_clients)


class TikTokHTTP:
    BASE_URL = "https://business-api.tiktok.com/open_api/v1.3/"

    def __init__(self, credential, *, session=None, response_limit=32 * 1024 * 1024, retry_policy=None, read_throttle=None):
        self.credential = credential
        self.session = session if session is not None else requests.Session()
        self.owns_session = session is None
        self.response_limit = response_limit
        self.retry_policy = retry_policy or ReadRetryPolicy()
        self.read_throttle = read_throttle
        self.closed = False
        _clients.add(self)

    def close(self):
        if not self.closed and self.owns_session:
            self.session.close()
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        if hasattr(self, "closed"):
            self.close()

    def request(self, endpoint, *, params=None, body=None, files=None, timeout=None, _lease=None):
        policy = self.retry_policy
        waited = 0.0
        readonly = not endpoint.write and not files
        last_error = None
        throttle = None

        def admission():
            nonlocal waited, throttle
            if throttle is None:
                if self.read_throttle is None:
                    self.read_throttle = default_read_throttle()
                throttle = self.read_throttle
            while True:
                delay = throttle.acquire_delay(endpoint)
                if delay <= 0:
                    return
                if delay > policy.max_wait - waited:
                    error = last_error or TikTokError('TikTok shared read cooldown exceeds the remaining wait budget.',
                                                     category='rate_limit', retry_after=delay)
                    error.retry_managed = True
                    error.attempts = attempt - 1
                    error.retry_stop_reason = 'wait_budget'
                    raise error
                _logger.warning('TikTok shared read wait %.1fs: %s.', delay, endpoint.path)
                time.sleep(delay)
                waited += delay

        for attempt in range(1, policy.max_attempts + 1):
            try:
                return self._request_once(endpoint, params=params, body=body, files=files, timeout=timeout,
                                          _lease=_lease, _before_send=admission if readonly else None)
            except Exception as exc:
                # Also mark writes and provider failures: an outer generic read
                # helper must never replay them or restart an exhausted budget.
                already_managed = getattr(exc, 'retry_managed', False) is True
                exc.retry_managed = True
                if not already_managed:
                    exc.attempts = attempt
                elif getattr(exc, 'retry_stop_reason', None) == 'limiter_error':
                    exc.attempts = attempt - 1
                if (already_managed or endpoint.write or files or not isinstance(exc, TikTokError)
                        or exc.category not in ('network', 'rate_limit')):
                    raise
                last_error = exc
                delay = policy.delay(attempt, exc.retry_after)
                if exc.category == 'rate_limit':
                    try:
                        throttle.defer(endpoint, delay)
                    except CliError:
                        exc.retry_stop_reason = 'limiter_error'
                        raise exc from None
                if attempt >= policy.max_attempts:
                    exc.retry_stop_reason = 'attempt_limit'
                    raise
                if delay > policy.max_wait - waited:
                    exc.retry_stop_reason = 'wait_budget'
                    _logger.warning('TikTok read retry stopped: %s; required wait %.1fs exceeds remaining budget %.1fs.',
                                    endpoint.path, delay, max(0.0, policy.max_wait - waited))
                    raise
                _logger.warning('TikTok read retry %d/%d in %.1fs: %s (%s).',
                                attempt + 1, policy.max_attempts, delay, endpoint.path, exc.category)
                # _request_once has closed the response and recorded usage before
                # sleeping. Each new attempt resolves its own current token lease.
                time.sleep(delay)
                waited += delay

    def _request_once(self, endpoint, *, params=None, body=None, files=None, timeout=None, _lease=None, _before_send=None):
        if self.closed:
            raise CliError("TikTok HTTP client is closed.")
        credential = _lease or (self.credential.for_request(params or body or {}) if hasattr(self.credential, "for_request") else self.credential)
        lease = credential if hasattr(credential, "get_token") else None
        token = lease.get_token() if lease else self.credential
        if not isinstance(token, str) or not token.strip():
            raise CliError("Missing TikTok access token. Configure Auth Center or provide a platform token.")
        if endpoint.method == "GET" and (body is not None or files):
            raise CliError("GET endpoints cannot carry a request body.")
        if lease:
            lease.check_scope(params or body or {})
            if endpoint.write and not lease.verified:
                from motata_cli.tiktok.endpoints import ENDPOINTS
                account = lease.account_id
                if not account:
                    raise CliError("Auth Center writes require an explicit verified advertiser account.")
                proof = self.request(ENDPOINTS["advertiser_info"], params={"advertiser_ids": [account]}, _lease=lease)
                rows = proof.get("data", {}).get("list", [])
                if not any(str(row.get("advertiser_id")) == account for row in rows if isinstance(row, dict)):
                    raise CliError("The Auth Center token cannot access the requested TikTok advertiser.")
                lease.verified = True
                refreshed_token = lease.get_token()
                if refreshed_token != token:
                    lease.verified = False
                    raise CliError("TikTok credential changed during account verification; run validation again before writing.")
        headers = {"Access-Token": token, "Accept": "application/json"}
        options = {"params": codec.query(params), "headers": headers, "auth": ExplicitAuth(),
                   "timeout": (10, timeout or (300 if files else 120 if "report" in endpoint.path else 60)),
                   "allow_redirects": False, "stream": True}
        event = None
        sent = False
        outcome = "unknown"
        response = None
        with ExitStack() as stack:
            if files:
                fields = codec.form(body)
                for field, file_path in files.items():
                    path = Path(file_path).expanduser()
                    if not path.is_file():
                        raise CliError(f"Upload file not found: {path}")
                    stream = stack.enter_context(path.open("rb"))
                    fields[field] = (path.name, stream, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                encoder = MultipartEncoder(fields=fields)
                headers["Content-Type"] = encoder.content_type
                options["data"] = encoder
            elif body is not None:
                options["json"] = body
            if _before_send:
                _before_send()
            if lease:
                # Shared cooldowns can outlive a lease. Refresh before billing
                # and sending, never use the pre-wait token for a read attempt.
                if _before_send:
                    headers['Access-Token'] = token = lease.get_token()
                event = lease.before_use(endpoint.path)
            try:
                sent = True
                response = self.session.request(endpoint.method, self.BASE_URL + endpoint.path, **options)
                stack.callback(response.close)
                if not 200 <= response.status_code < 300 and response.status_code < 400:
                    raise TikTokError("TikTok redirect refused", http_status=response.status_code,
                                      category="protocol", write_outcome="unknown" if endpoint.write else "failed")
                data = bounded_json(response, limit=self.response_limit)
                secrets = tuple(source[key] for source in (params or {}, body or {})
                                for key in ("secret", "app_secret", "client_secret", "access_token", "refresh_token")
                                if isinstance(source.get(key), str))
                result = check_response(data, token=token, status=response.status_code,
                                        write=endpoint.write, retry_after=response.headers.get("Retry-After"), secrets=secrets)
                outcome = "succeeded"
                return result
            except TikTokError as exc:
                outcome = exc.write_outcome
                if lease and exc.category == "auth":
                    lease.invalidate()
                raise
            except (requests.RequestException, ValueError) as exc:
                if isinstance(exc, requests.ConnectTimeout):
                    sent = False
                    outcome = "not_submitted"
                status = response.status_code if response is not None else None
                category = "network" if isinstance(exc, requests.RequestException) or (status and status >= 500) else "protocol"
                if status in (401, 403):
                    category = "auth"
                    if lease:
                        lease.invalidate()
                elif status == 429:
                    category = "rate_limit"
                raise TikTokError(network_error("TikTok API", exc, write=endpoint.write and sent),
                                  http_status=status, category=category,
                                  retry_after=response.headers.get("Retry-After") if response is not None else None,
                                  write_outcome="unknown" if endpoint.write and sent else "not_submitted") from None
            finally:
                if lease and event:
                    # Reporting cannot mask an already completed platform operation.
                    lease.after_use(event, sent=sent, outcome=outcome)
