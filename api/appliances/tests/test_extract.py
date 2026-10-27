import pytest
import requests
import responses

from api.appliances.eprel_sync import (
    EprelAuthError,
    RateLimiter,
    fetch_page,
    iter_group_pages,
)

API = {
    "base_url": "https://eprel.test/api",
    "page_size": 100,
    "max_in_flight": 4,
    "max_per_second": 4,
    "retries": 4,
    "timeout_s": 5,
}
URL = "https://eprel.test/api/products/ovens"


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def hits(start, count):
    return [{"eprelRegistrationNumber": str(n)} for n in range(start, start + count)]


@responses.activate
def test_key_is_sent_in_the_header():
    responses.get(URL, json={"size": 0, "hits": []})

    fetch_page(requests.Session(), API, "k", "ovens", 1)

    request = responses.calls[0].request
    assert request.headers["X-API-KEY"] == "k"
    assert "_page=1" in request.url and "_limit=100" in request.url


@responses.activate
def test_403_raises_auth_error_without_retrying():
    responses.get(URL, json={"message": "Forbidden"}, status=403)
    waits = []

    with pytest.raises(EprelAuthError, match="EPREL_API_KEY"):
        fetch_page(requests.Session(), API, "bad", "ovens", 1, sleep=waits.append)

    assert len(responses.calls) == 1 and waits == []


@responses.activate
def test_429_then_200_retries_with_waits():
    responses.get(URL, status=429)
    responses.get(URL, json={"size": 1, "hits": hits(1, 1)})
    waits = []

    page = fetch_page(requests.Session(), API, "k", "ovens", 1, sleep=waits.append)

    assert page["size"] == 1 and waits == [2]


@responses.activate
def test_gives_up_after_4_retries():
    for _ in range(5):
        responses.get(URL, status=503)
    waits = []

    with pytest.raises(requests.HTTPError):
        fetch_page(requests.Session(), API, "k", "ovens", 1, sleep=waits.append)

    assert len(responses.calls) == 5 and waits == [2, 4, 8, 16]


@responses.activate
def test_connection_error_is_retried():
    responses.get(URL, body=requests.ConnectionError("reset"))
    responses.get(URL, json={"size": 0, "hits": []})
    waits = []

    fetch_page(requests.Session(), API, "k", "ovens", 1, sleep=waits.append)

    assert waits == [2]


@responses.activate
def test_iter_group_pages_reads_every_page():
    for page, (start, count) in enumerate([(0, 100), (100, 100), (200, 50)], start=1):
        responses.get(
            URL,
            json={"size": 250, "hits": hits(start, count)},
            match=[responses.matchers.query_param_matcher({"_page": str(page), "_limit": "100"})],
        )
    clock = FakeClock()
    limiter = RateLimiter(4, clock=clock.now, sleep=clock.sleep)

    pages = list(iter_group_pages(requests.Session(), API, "k", "ovens", limiter))

    numbers = sorted(int(h["eprelRegistrationNumber"]) for page in pages for h in page)
    assert numbers == list(range(250))
    assert len(responses.calls) == 3


@responses.activate
def test_iter_group_pages_stops_on_empty_first_page():
    responses.get(URL, json={"size": 0, "hits": []})
    clock = FakeClock()

    pages = list(iter_group_pages(requests.Session(), API, "k", "ovens",
                                  RateLimiter(4, clock=clock.now, sleep=clock.sleep)))

    assert pages == [] and len(responses.calls) == 1


def test_rate_limiter_spaces_calls():
    clock = FakeClock()
    limiter = RateLimiter(4, clock=clock.now, sleep=clock.sleep)

    for _ in range(5):
        limiter.wait()

    assert clock.now() >= 1.0


@responses.activate
def test_every_attempt_goes_through_before_request():
    responses.get(URL, status=429)
    responses.get(URL, json={"size": 0, "hits": []})
    starts = []

    fetch_page(requests.Session(), API, "k", "ovens", 1, sleep=lambda s: None,
               before_request=lambda: starts.append(1))

    assert len(starts) == 2


@responses.activate
def test_cut_off_body_is_retried():
    responses.get(URL, body=requests.exceptions.ChunkedEncodingError("cut"))
    responses.get(URL, json={"size": 0, "hits": []})
    waits = []

    fetch_page(requests.Session(), API, "k", "ovens", 1, sleep=waits.append)

    assert waits == [2]


@responses.activate
def test_invalid_json_is_retried():
    responses.get(URL, body="<html>busy</html>", status=200)
    responses.get(URL, json={"size": 0, "hits": []})
    waits = []

    page = fetch_page(requests.Session(), API, "k", "ovens", 1, sleep=waits.append)

    assert waits == [2] and page["size"] == 0
