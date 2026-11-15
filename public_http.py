"""Bounded public JSON reads with safe diagnostics and transient-error retries."""
import json
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class PublicSourceError(RuntimeError):
    def __init__(self, code, message, http_status=None):
        super().__init__(message)
        self.code, self.http_status = code, http_status


def error_info(exc):
    if isinstance(exc, PublicSourceError):
        return {'error': str(exc), 'error_code': exc.code, **({'http_status': exc.http_status} if exc.http_status else {})}
    if isinstance(exc, HTTPError):
        return {'error': 'Public endpoint returned HTTP ' + str(exc.code), 'error_code': 'http_error', 'http_status': exc.code}
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return {'error': 'Public endpoint timed out', 'error_code': 'timeout'}
    return {'error': 'Source could not be read (' + type(exc).__name__ + ')', 'error_code': 'source_error'}


def fetch_json(url, payload=None, max_bytes=10_000_000, timeout=25, attempts=3):
    class SameOrigin(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            original, target = urlsplit(url), urlsplit(newurl)
            if target.scheme != 'https' or target.hostname != original.hostname or target.port != original.port:
                raise PublicSourceError('unexpected_redirect', 'Endpoint redirected away from its configured HTTPS origin')
            if req.data is not None and code in (301, 302, 303):
                raise PublicSourceError('unexpected_redirect', 'Public search redirected to a different method; review endpoint configuration')
            if code in (307, 308):
                return Request(newurl, data=req.data, headers=dict(req.headers), method=req.get_method())
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {'Accept': 'application/json', 'Accept-Language': 'en-US', 'User-Agent': 'PersonalJobDigest/1.0'}
    if data is not None: headers['Content-Type'] = 'application/json'
    for attempt in range(attempts):
        wait = min(2 ** attempt, 4)
        try:
            with build_opener(SameOrigin()).open(Request(url, data=data, headers=headers), timeout=timeout) as response:
                raw = response.read(max_bytes+1)
                if len(raw) > max_bytes:
                    raise PublicSourceError('response_too_large', 'Public response exceeded the configured size limit')
                try: result = json.loads(raw.decode('utf-8-sig'))
                except (ValueError, UnicodeError):
                    raise PublicSourceError('invalid_json', 'Public endpoint returned HTML or invalid JSON; no login/check bypass was attempted') from None
                if not isinstance(result, (dict, list)):
                    raise PublicSourceError('invalid_schema', 'Public endpoint returned an unexpected JSON type')
                return result
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts-1:
                raise PublicSourceError('http_error', 'Public endpoint returned HTTP ' + str(exc.code), exc.code) from None
            retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
            if retry_after.isdigit(): wait = min(int(retry_after), 10)
        except (TimeoutError, socket.timeout, ConnectionError) as exc:
            if attempt == attempts-1:
                raise PublicSourceError('timeout', 'Public endpoint timed out after bounded retries') from None
        except URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise PublicSourceError('tls_error', 'TLS verification failed; certificate checks remain enabled') from None
            if attempt == attempts-1:
                raise PublicSourceError('network_error', 'Public endpoint was unreachable after bounded retries') from None
        time.sleep(wait)
    raise PublicSourceError('network_error', 'Public endpoint could not be read')
