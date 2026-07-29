from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable

import requests

ErrorFactory = Callable[[str], Exception]

_DEBUG_ENABLED = False
_ASYNC_PROGRESS_INTERVAL_SECONDS = 10.0


def configure_meta_debug(enabled: bool) -> None:
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = bool(enabled)


def _debug_log(message: str) -> None:
    if _DEBUG_ENABLED:
        print(f"[motata meta debug] {message}", file=sys.stderr)


def _progress_log(message: str) -> None:
    print(f"[motata meta] {message}", file=sys.stderr)


class MetaClient:
    def __init__(
        self,
        access_token: str,
        *,
        version: str,
        base_url: str | None = None,
        error_factory: ErrorFactory = RuntimeError,
    ):
        self.access_token = access_token
        self.base = base_url or f"https://graph.facebook.com/{version}"
        self.error_factory = error_factory

    def _url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self.base}/{path}"

    def _sanitize_payload(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        sanitized = dict(payload)
        sanitized.pop("access_token", None)
        return sanitized

    def _handle(self, response: requests.Response) -> dict[str, Any]:
        payload = response.json()
        if "error" in payload:
            error = payload["error"]
            raise self.error_factory(
                json.dumps(
                    {
                        "source": "meta",
                        "code": error.get("code"),
                        "subcode": error.get("error_subcode"),
                        "title": error.get("error_user_title"),
                        "message": error.get("error_user_msg") or error.get("message"),
                        "fbtrace_id": error.get("fbtrace_id"),
                    },
                    ensure_ascii=False,
                )
            )
        response.raise_for_status()
        return payload

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        final_params = {"access_token": self.access_token, **(params or {})}
        _debug_log(f"GET {self._url(path)} params={self._sanitize_payload(final_params)}")
        response = requests.get(self._url(path), params=final_params, timeout=120)
        return self._handle(response)

    def post(
        self,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        final_data = {"access_token": self.access_token, **(data or {})}
        _debug_log(
            f"POST {self._url(path)} data={self._sanitize_payload(final_data)} files={sorted((files or {}).keys())}"
        )
        response = requests.post(self._url(path), data=final_data, files=files, timeout=300)
        return self._handle(response)

    def delete(self, path: str) -> dict[str, Any]:
        params = {"access_token": self.access_token}
        _debug_log(f"DELETE {self._url(path)}")
        response = requests.delete(self._url(path), params=params, timeout=120)
        return self._handle(response)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url = self._url(path)
        next_params = {"access_token": self.access_token, **(params or {})}
        page = 0
        while url:
            if max_pages is not None and page >= max_pages:
                break
            _debug_log(f"GET {url} params={self._sanitize_payload(next_params)}")
            response = requests.get(url, params=next_params, timeout=120)
            payload = self._handle(response)
            items.extend(payload.get("data") or [])
            url = ((payload.get("paging") or {}).get("next")) or None
            next_params = None
            page += 1
        return items

    def _should_auto_async_insights(self, params: dict[str, Any] | None) -> bool:
        params = params or {}
        fields = str(params.get("fields") or "")
        limit = int(params.get("limit") or 0)
        has_heavy_fields = any(
            field in fields
            for field in ("actions", "action_values", "purchase_roas", "website_purchase_roas", "conversions")
        )
        return bool(params.get("breakdowns")) or (has_heavy_fields and limit >= 1000) or limit >= 5000

    def _create_insights_report(self, path: str, params: dict[str, Any]) -> str:
        payload = self.post(path, data=params)
        report_run_id = payload.get("report_run_id")
        if not report_run_id:
            raise self.error_factory(
                json.dumps(
                    {"source": "meta", "message": "Async insights report did not return report_run_id"},
                    ensure_ascii=False,
                )
            )
        return str(report_run_id)

    def _wait_insights_report(
        self,
        report_run_id: str,
        *,
        timeout_seconds: int = 300,
        poll_seconds: float = 2.0,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last_payload: dict[str, Any] = {}
        next_progress_at = 0.0
        while time.time() < deadline:
            payload = self.get(
                report_run_id,
                params={"fields": "async_status,async_percent_completion"},
            )
            last_payload = payload
            status_label = str(payload.get("async_status") or "")
            percent = payload.get("async_percent_completion")
            now = time.time()
            if now >= next_progress_at:
                _progress_log(
                    "async insights polling "
                    f"report_run_id={report_run_id} "
                    f"status={status_label or 'unknown'} "
                    f"percent={percent if percent is not None else 'unknown'}"
                )
                next_progress_at = now + _ASYNC_PROGRESS_INTERVAL_SECONDS
            status = status_label.lower()
            if status in {"job completed", "completed", "success"}:
                _progress_log(
                    "async insights completed "
                    f"report_run_id={report_run_id} "
                    f"percent={percent if percent is not None else 'unknown'}"
                )
                return payload
            if status in {"job failed", "failed", "skipped"}:
                _progress_log(
                    "async insights failed "
                    f"report_run_id={report_run_id} "
                    f"status={status_label or 'unknown'}"
                )
                raise self.error_factory(
                    json.dumps(
                        {
                            "source": "meta",
                            "message": "Async insights report failed",
                            "report_run_id": report_run_id,
                            "async_status": payload.get("async_status"),
                        },
                        ensure_ascii=False,
                    )
                )
            time.sleep(poll_seconds)
        raise self.error_factory(
            json.dumps(
                {
                    "source": "meta",
                    "message": "Async insights report timed out",
                    "report_run_id": report_run_id,
                    "last_status": last_payload.get("async_status"),
                    "async_percent_completion": last_payload.get("async_percent_completion"),
                },
                ensure_ascii=False,
            )
        )

    def paginate_insights(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        prefer_async: bool = False,
        auto_async: bool = True,
    ) -> list[dict[str, Any]]:
        final_params = dict(params or {})
        use_async = prefer_async or (auto_async and self._should_auto_async_insights(final_params))
        if not use_async:
            return self.paginate(path, params=final_params)
        try:
            report_run_id = self._create_insights_report(path, final_params)
            self._wait_insights_report(report_run_id)
            limit = final_params.get("limit")
            result_params = {"limit": limit} if limit else None
            return self.paginate(f"{report_run_id}/insights", params=result_params)
        except Exception:
            if prefer_async:
                raise
            return self.paginate(path, params=final_params)
