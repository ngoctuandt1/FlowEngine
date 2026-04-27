from pathlib import Path

import pytest
from fastapi.testclient import TestClient


class FakeYoutubeDL:
    last_options = None

    def __init__(self, options):
        self.options = options
        FakeYoutubeDL.last_options = options

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=True):
        output_path = Path(self.options["outtmpl"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-video")
        return {
            "title": "Example video",
            "duration": 42,
            "webpage_url": url,
        }


def _client_with_data_dir(monkeypatch, tmp_path):
    import server.routes.media_fetch
    import server.app

    download_dir = (tmp_path / "downloads").resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOW_DOWNLOAD_DIR", str(download_dir))
    return TestClient(server.app.app), download_dir, server.routes.media_fetch


def test_fetch_url_happy_path(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(media_fetch.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(media_fetch.socket.AF_INET, None, None, "", ("93.184.216.34", 0))],
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://www.youtube.com/watch?v=demo", "max_height": 720},
    )

    assert response.status_code == 200
    body = response.json()
    output_path = download_dir / body["output_path"]
    assert output_path.is_file()
    assert output_path.parent == download_dir / "fetched"
    assert body["title"] == "Example video"
    assert body["duration_seconds"] == 42
    assert body["source_url"] == "https://www.youtube.com/watch?v=demo"
    assert FakeYoutubeDL.last_options["format"] == "bestvideo[height<=?720]+bestaudio/best[height<=?720]"


@pytest.mark.parametrize("url", ["ftp://example.com/video.mp4", "file:///tmp/video.mp4"])
def test_fetch_url_rejects_non_http_urls(temp_db_path, monkeypatch, tmp_path, url):
    client, _, _ = _client_with_data_dir(monkeypatch, tmp_path)

    response = client.post("/api/media/fetch-url", json={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url must use http or https"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/video.mp4",
        "http://127.0.0.1/video.mp4",
        "http://10.0.0.25/video.mp4",
        "http://172.16.5.4/video.mp4",
        "http://192.168.1.10/video.mp4",
        "http://169.254.10.20/video.mp4",
        "http://[::1]/video.mp4",
        "http://[fe80::1]/video.mp4",
        "http://224.0.0.1/video.mp4",
        "http://service.internal/video.mp4",
    ],
)
def test_fetch_url_rejects_denied_hosts(temp_db_path, monkeypatch, tmp_path, url):
    client, _, _ = _client_with_data_dir(monkeypatch, tmp_path)

    response = client.post("/api/media/fetch-url", json={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url host is not allowed"


def test_fetch_url_rejects_invalid_max_height(temp_db_path, monkeypatch, tmp_path):
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(media_fetch.socket.AF_INET, None, None, "", ("93.184.216.34", 0))],
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4", "max_height": 999},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, max_height must be one of 360, 480, 720, 1080"


def test_fetch_url_maps_download_error_to_502(temp_db_path, monkeypatch, tmp_path):
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(media_fetch.socket.AF_INET, None, None, "", ("93.184.216.34", 0))],
    )

    class FailingYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=True):
            raise media_fetch.yt_dlp.utils.DownloadError("boom C:\\secret\\path")

    monkeypatch.setattr(media_fetch.yt_dlp, "YoutubeDL", FailingYoutubeDL)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Failed to fetch media from source URL"
    assert "secret" not in response.text


def test_fetch_url_output_path_stays_under_download_dir(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(media_fetch.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(media_fetch.socket.AF_INET, None, None, "", ("93.184.216.34", 0))],
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://cdn.example.com/video.mp4"},
    )

    assert response.status_code == 200
    output_path = download_dir / response.json()["output_path"]
    assert output_path.is_relative_to(download_dir.resolve())
    assert output_path.name.startswith("fetch_")
    assert output_path.suffix == ".mp4"


def test_fetch_url_rejects_dns_rebinding_target(temp_db_path, monkeypatch, tmp_path):
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(media_fetch.socket.AF_INET, None, None, "", ("10.0.0.8", 0))],
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url host is not allowed"
