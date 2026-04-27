from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def _client():
    import server.app

    return TestClient(server.app.app)


def _patch_tts_dir(monkeypatch, tmp_path):
    import server.routes.tts as tts

    tts_dir = (tmp_path / "downloads" / "tts").resolve()
    monkeypatch.setattr(tts, "TTS_DIR", tts_dir, raising=False)
    return tts, tts_dir


def test_post_tts_happy_path_writes_mp3(temp_db_path, monkeypatch, tmp_path):
    tts, tts_dir = _patch_tts_dir(monkeypatch, tmp_path)
    calls = {}

    class FakeCommunicate:
        def __init__(self, *, text, voice, rate, pitch):
            calls["init"] = {
                "text": text,
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
            }

        async def save(self, output_path):
            calls["save_path"] = output_path
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    response = _client().post("/api/tts", json={"text": "Xin chao the gioi"})

    assert response.status_code == 200
    body = response.json()
    assert body["voice"] == "vi-VN-HoaiMyNeural"
    assert body["duration_seconds_estimate"] == round(len("Xin chao the gioi") / tts.CHARS_PER_SECOND_ESTIMATE, 2)
    assert calls["init"] == {
        "text": "Xin chao the gioi",
        "voice": "vi-VN-HoaiMyNeural",
        "rate": "+0%",
        "pitch": "+0Hz",
    }
    assert body["output_path"].startswith("tts/tts_")
    output_path = Path(calls["save_path"])
    assert output_path.is_file()
    assert output_path.parent == tts_dir


def test_post_tts_rejects_empty_text_before_edge_tts(temp_db_path, monkeypatch, tmp_path):
    tts, _ = _patch_tts_dir(monkeypatch, tmp_path)
    called = False

    class FakeCommunicate:
        def __init__(self, **kwargs):
            nonlocal called
            called = True

        async def save(self, output_path):
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    response = _client().post("/api/tts", json={"text": ""})

    assert response.status_code == 422
    assert called is False


def test_post_tts_rejects_text_over_5000_before_edge_tts(temp_db_path, monkeypatch, tmp_path):
    tts, _ = _patch_tts_dir(monkeypatch, tmp_path)
    called = False

    class FakeCommunicate:
        def __init__(self, **kwargs):
            nonlocal called
            called = True

        async def save(self, output_path):
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    response = _client().post("/api/tts", json={"text": "a" * 5001})

    assert response.status_code == 422
    assert called is False


def test_post_tts_rejects_unknown_voice(temp_db_path, monkeypatch, tmp_path):
    tts, _ = _patch_tts_dir(monkeypatch, tmp_path)
    called = False

    class FakeCommunicate:
        def __init__(self, **kwargs):
            nonlocal called
            called = True

        async def save(self, output_path):
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    response = _client().post("/api/tts", json={"text": "hello", "voice": "fr-FR-DeniseNeural"})

    assert response.status_code == 422
    assert called is False
    assert "voice must match one of" in response.json()["detail"][0]["msg"]


def test_post_tts_allows_weird_rate_and_pitch_passthrough(temp_db_path, monkeypatch, tmp_path):
    tts, _ = _patch_tts_dir(monkeypatch, tmp_path)
    calls = {}

    class FakeCommunicate:
        def __init__(self, *, text, voice, rate, pitch):
            calls["init"] = {
                "text": text,
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
            }

        async def save(self, output_path):
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    response = _client().post(
        "/api/tts",
        json={
            "text": "edge tts passthrough",
            "voice": "en-US-JennyNeural",
            "rate": "fast++??",
            "pitch": "up??",
        },
    )

    assert response.status_code == 200
    assert calls["init"] == {
        "text": "edge tts passthrough",
        "voice": "en-US-JennyNeural",
        "rate": "fast++??",
        "pitch": "up??",
    }


def test_post_tts_output_path_stays_under_download_dir(temp_db_path, monkeypatch, tmp_path):
    tts, tts_dir = _patch_tts_dir(monkeypatch, tmp_path)

    class FakeCommunicate:
        def __init__(self, **kwargs):
            pass

        async def save(self, output_path):
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    response = _client().post("/api/tts", json={"text": "path safety"})

    assert response.status_code == 200
    output_path = tts_dir / Path(response.json()["output_path"]).name
    assert output_path.parent == tts_dir


def test_post_tts_accepts_allowed_voice_prefixes(temp_db_path, monkeypatch, tmp_path):
    tts, _ = _patch_tts_dir(monkeypatch, tmp_path)
    voices = []

    class FakeCommunicate:
        def __init__(self, *, text, voice, rate, pitch):
            voices.append(voice)

        async def save(self, output_path):
            Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(tts, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate), raising=False)

    for voice in ["vi-VN-HoaiMyNeural", "en-US-JennyNeural", "en-GB-SoniaNeural", "ja-JP-NanamiNeural", "ko-KR-SunHiNeural"]:
        response = _client().post("/api/tts", json={"text": "ok", "voice": voice})
        assert response.status_code == 200

    assert voices == ["vi-VN-HoaiMyNeural", "en-US-JennyNeural", "en-GB-SoniaNeural", "ja-JP-NanamiNeural", "ko-KR-SunHiNeural"]


def test_post_tts_returns_503_when_edge_tts_missing(temp_db_path, monkeypatch, tmp_path):
    tts, _ = _patch_tts_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(tts, "edge_tts", None, raising=False)

    response = _client().post("/api/tts", json={"text": "dependency check"})

    assert response.status_code == 503
    assert response.json()["detail"] == "edge-tts dependency is not installed"
