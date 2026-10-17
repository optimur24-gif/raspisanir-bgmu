"""Collect public HTML/JavaScript for inspection on an authorized runner.

This is a development diagnostic, not the bot's schedule data source. Proxy
credentials are read from the process environment and never included in output.
"""

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


TARGET = "https://bgmu-time.ru/"
ALLOWED_HOSTS = {"bgmu-time.ru", "www.bgmu-time.ru"}
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILES = 40
MAX_SECONDS = 120


def allowed_url(url):
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in ALLOWED_HOSTS
            and parsed.port in {None, 443}
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


def without_fragment(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


class RestrictedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError("redirect_outside_schedule_website")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class PublicResponse:
    url: str
    content: bytes
    content_type: str


def make_fetcher(proxy_url):
    handlers = [RestrictedRedirects()]
    if proxy_url:
        try:
            parsed = urlsplit(proxy_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("diagnostic_requires_http_or_https_proxy_url") from None
        handlers.append(ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = build_opener(*handlers)

    def fetch(url, timeout):
        if not allowed_url(url):
            raise ValueError("outside_schedule_website")
        request = Request(url, headers={
            "User-Agent": "BGMU-Schedule-Source-Inspection/0.1",
            "Accept-Encoding": "identity",
        })
        with opener.open(request, timeout=timeout) as response:
            if not allowed_url(response.geturl()):
                raise ValueError("redirect_outside_schedule_website")
            content = response.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("response_too_large")
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                # Limit decompression too, so a small compressed response
                # cannot cause unbounded memory use.
                import io
                with gzip.GzipFile(fileobj=io.BytesIO(content)) as stream:
                    content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("response_too_large")
            return PublicResponse(
                response.geturl(), content,
                response.headers.get("Content-Type", ""),
            )

    return fetch


class PageScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])
        if tag == "link" and values.get("href"):
            rel = values.get("rel", "").split()
            if "modulepreload" in rel or (
                "preload" in rel and values.get("as") == "script"
            ):
                self.scripts.append(values["href"])


JS_REFERENCES = re.compile(r'''["'`]([^"'`\s<>\\]{1,500}\.m?js(?:\?[^"'`\s<>\\]*)?)["'`]''')
URL_STRINGS = re.compile(r'''["'`]((?:https?://|/)[^"'`\s<>\\]{1,500})["'`]''')
API_WORDS = ("api", "schedule", "timetable", "facult", "course", "group", "raspis")


def source_references(text, base_url, *, html=False):
    references = []
    if html:
        parser = PageScripts()
        parser.feed(text)
        references.extend(parser.scripts)
    references.extend(JS_REFERENCES.findall(text))
    return [without_fragment(urljoin(base_url, item)) for item in references]


def safe_error(error):
    # Exception strings can contain proxy URLs or authentication challenges.
    if isinstance(error, HTTPError):
        return {"type": "http_error", "status": error.code}
    if isinstance(error, URLError):
        return {"type": "connection_error", "reason_type": type(error.reason).__name__}
    if isinstance(error, ValueError):
        # All ValueErrors generated here are fixed diagnostic codes. Never
        # copy arbitrary messages from URL parsers or external libraries.
        return {"type": "invalid_response_or_configuration"}
    return {"type": type(error).__name__}


def inspect(output, fetcher, *, proxy_configured=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "target": TARGET,
        "proxy_configured": bool(proxy_configured),
        "status": "homepage_unavailable",
        "files": [], "errors": [], "skipped": [], "api_candidates": [],
    }
    queue = deque([TARGET])
    visited = set()
    candidates = set()
    total = 0
    while queue and len(visited) < MAX_FILES:
        if time.monotonic() - started >= MAX_SECONDS:
            report["skipped"].append({"reason": "time_limit"})
            break
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if not allowed_url(url):
            # Record external origins only, without query strings.
            parsed = urlsplit(url)
            report["skipped"].append({"host": parsed.hostname, "reason": "external_asset"})
            continue
        try:
            timeout = max(1, min(15, MAX_SECONDS - (time.monotonic() - started)))
            response = fetcher(url, timeout)
            if len(response.content) > MAX_FILE_BYTES or total + len(response.content) > MAX_TOTAL_BYTES:
                report["skipped"].append({"url": url, "reason": "size_limit"})
                continue
            if not allowed_url(response.url):
                raise ValueError("response_outside_schedule_website")
            text = response.content.decode("utf-8", errors="replace")
            is_home = url == TARGET
            if is_home:
                if "<html" not in text.lower() and "<!doctype" not in text.lower():
                    raise ValueError("homepage_is_not_html")
                filename = "index.html"
            else:
                if "html" in response.content_type.lower() or text.lstrip().lower().startswith("<!doctype html"):
                    raise ValueError("javascript_url_returned_html")
                basename = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(urlsplit(url).path).name)[:100] or "script.js"
                filename = "assets/" + hashlib.sha256(url.encode()).hexdigest()[:12] + "_" + basename
            path = output / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            total += len(response.content)
            report["files"].append({
                "url": response.url, "path": filename, "bytes": len(response.content),
                "sha256": hashlib.sha256(response.content).hexdigest(),
            })
            if is_home:
                report["status"] = "homepage_fetched"
            for reference in source_references(text, response.url, html=is_home):
                if reference not in visited:
                    queue.append(reference)
            for value in URL_STRINGS.findall(text):
                if any(word in value.lower() for word in API_WORDS) and len(candidates) < 256:
                    candidates.add(value)
        except (OSError, ValueError, HTTPError, URLError) as error:
            report["errors"].append({"url": url, **safe_error(error)})
    report["api_candidates"] = sorted(candidates)
    report["downloaded_bytes"] = total
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def probe_api(fetcher):
    """Read the API calls observed in the website's app.js, keeping small samples."""
    result = {"status": "unavailable", "catalogs": {}, "schedules": {}, "errors": []}

    def api(**params):
        response = fetcher(TARGET + "api.php?" + urlencode(params), 30)
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ValueError("api_unsuccessful")
        return payload

    try:
        period = api(action="period")
        result["period"] = period
        year, semester = period["period"]["year"], period["period"]["semester"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        result["errors"].append({"action": "period", **safe_error(error)})
        return result
    for entity_type in ("group", "teacher", "room"):
        try:
            catalog = api(action="catalog", type=entity_type, year=year, semester=semester)
            items = catalog["items"]
            if not isinstance(items, list):
                raise ValueError("invalid_catalog")
            result["catalogs"][entity_type] = {
                "count": len(items), "samples": items[:3], "meta": catalog.get("meta"),
                "faculties": sorted({item.get("faculty", "") for item in items if item.get("faculty")}),
                "courses": sorted({str(item["course"]) for item in items if item.get("course") is not None}),
            }
            if not items:
                continue
            chosen = next((item for item in items if item.get("name") == "Л-101"), items[0])
            schedule = api(action="schedule", type=entity_type, id=chosen["id"], year=year, semester=semester)
            events = schedule["events"]
            samples = []
            kinds = set()
            for event in events:
                kind = event.get("kind", "")
                if kind not in kinds and len(samples) < 8:
                    kinds.add(kind)
                    samples.append(event)
            result["schedules"][entity_type] = {
                "entity": schedule.get("entity"), "event_count": len(events),
                "samples": samples, "meta": schedule.get("meta"),
                "sourceUrl": schedule.get("sourceUrl"), "undatedRecords": schedule.get("undatedRecords"),
                "dates": sorted({event.get("date", "") for event in events})[:5],
                "subgroups": sorted({str(detail["subgroup"]) for event in events for detail in event.get("details", []) if detail.get("subgroup")}),
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            result["errors"].append({"entity_type": entity_type, **safe_error(error)})
    if result["schedules"]:
        result["status"] = "api_responses_received"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="source-inspection")
    args = parser.parse_args()
    proxy_url = os.environ.get("BGMU_PROXY_URL", "").strip()
    try:
        fetcher = make_fetcher(proxy_url)
    except ValueError:
        def fetcher(url, timeout):
            raise ValueError("invalid_proxy_configuration")
    report = inspect(args.output, fetcher, proxy_configured=bool(proxy_url))
    if report["status"] == "homepage_fetched":
        report["api_probe"] = probe_api(fetcher)
        (Path(args.output) / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Inspection: {report['status']}; source files: {len(report['files'])}; errors: {len(report['errors'])}.")
    return 0 if report["status"] == "homepage_fetched" else 1


if __name__ == "__main__":
    raise SystemExit(main())
