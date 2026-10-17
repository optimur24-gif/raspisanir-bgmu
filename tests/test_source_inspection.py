import json
from urllib.error import HTTPError, URLError

from tools.inspect_bgmu_source import PublicResponse, TARGET, allowed_url, inspect, make_fetcher


def test_collects_html_and_recursive_javascript_without_requesting_other_hosts(tmp_path):
    contents = {
        TARGET: '<html><script src="/assets/main.js"></script><link rel="modulepreload" href="/assets/shared.js"></html>',
        TARGET + 'assets/main.js': 'import "./chunk.js"; const api = "/api/faculties"; import "https://external.example/lib.js";',
        TARGET + 'assets/shared.js': 'export const name = "БГМУ";',
        TARGET + 'assets/chunk.js': 'fetch("/api/schedule");',
    }
    requested = []

    def fetch(url, timeout):
        requested.append(url)
        assert url in contents
        return PublicResponse(url, contents[url].encode(), 'text/html' if url == TARGET else 'text/javascript')

    report = inspect(tmp_path, fetch)
    assert report['status'] == 'homepage_fetched'
    assert len(report['files']) == 4
    assert len(requested) == 4
    assert '/api/faculties' in report['api_candidates']
    assert '/api/schedule' in report['api_candidates']
    assert (tmp_path / 'index.html').read_text() == contents[TARGET]
    assert any(item['reason'] == 'external_asset' for item in report['skipped'])


def test_failed_connection_still_produces_report_without_credentials(tmp_path):
    def fetch(url, timeout):
        raise URLError('http://private-user:private-password@proxy.example:3128')

    report = inspect(tmp_path, fetch, proxy_configured=True)
    text = (tmp_path / 'report.json').read_text()
    assert report['status'] == 'homepage_unavailable'
    assert report['proxy_configured']
    assert report['errors'][0]['type'] == 'connection_error'
    assert 'private-password' not in text
    assert 'private-user' not in text


def test_http_access_denial_is_preserved_as_failure(tmp_path):
    def fetch(url, timeout):
        raise HTTPError(url, 403, 'private-password', {}, None)

    report = inspect(tmp_path, fetch)
    assert report['errors'][0]['status'] == 403
    assert report['files'] == []
    assert 'private-password' not in json.dumps(report)


def test_failed_script_does_not_replace_it_with_a_spa_html_fallback(tmp_path):
    def fetch(url, timeout):
        return PublicResponse(url, b'<html><script src="/missing.js"></script></html>', 'text/html')

    report = inspect(tmp_path, fetch)
    assert len(report['files']) == 1
    assert len(report['errors']) == 1


def test_only_https_schedule_origins_are_accepted():
    assert allowed_url(TARGET)
    assert allowed_url('https://www.bgmu-time.ru/assets/main.js')
    assert not allowed_url('https://bgmu-time.ru.attacker.example/')
    assert not allowed_url('http://bgmu-time.ru/')
    assert not allowed_url('https://user:password@bgmu-time.ru/')
    assert not allowed_url('https://bgmu-time.ru:8443/')


def test_invalid_proxy_error_does_not_include_credentials():
    try:
        make_fetcher('http://private-user:private-password@proxy.example:invalid-port')
    except ValueError as error:
        assert 'private-password' not in str(error)
        assert 'private-user' not in str(error)
    else:
        raise AssertionError('Invalid proxy configuration was accepted')
