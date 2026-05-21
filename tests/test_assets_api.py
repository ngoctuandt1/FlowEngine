from pathlib import Path
import json


async def test_voice_asset_crud_and_read_only_presets(api_client):
    create_response = await api_client.post(
        "/api/assets",
        json={
            "type": "voice",
            "name": "Narrator",
            "description": "Warm read",
            "sample_url": "https://example.test/narrator.wav",
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["type"] == "voice"
    assert created["name"] == "Narrator"
    assert created["source"] == "user"

    list_response = await api_client.get("/api/assets?type=voice")
    assert list_response.status_code == 200
    assert [asset["id"] for asset in list_response.json()] == [created["id"]]

    update_response = await api_client.put(
        f"/api/assets/{created['id']}",
        json={"name": "Narrator 2", "description": "Brighter"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Narrator 2"

    import_response = await api_client.post(
        "/api/assets/voices/import",
        json={
            "result": {
                "data": {
                    "json": {
                        "projectContents": {
                            "externalReferenceMedia": [
                                {
                                    "mediaId": "achernar",
                                    "mediaType": "AUDIO",
                                    "workflowDisplayName": "Achernar",
                                    "media": {
                                        "name": "achernar",
                                        "audio": {
                                            "generatedAudio": {
                                                "name": "Achernar",
                                                "description": "Female, soft, high pitch",
                                                "audioSamplePath": "https://gstatic.com/aitestkitchen/voices/samples/Achernar.wav",
                                            }
                                        },
                                    },
                                },
                                {"mediaId": "not-a-voice", "mediaType": "IMAGE"},
                            ]
                        }
                    }
                }
            }
        },
    )
    assert import_response.status_code == 200
    imported = import_response.json()
    assert imported == [
        {
            "id": "achernar",
            "type": "voice",
            "name": "Achernar",
            "description": "Female, soft, high pitch",
            "sample_url": "https://gstatic.com/aitestkitchen/voices/samples/Achernar.wav",
            "source": "flow_preset",
            "created_at": imported[0]["created_at"],
        }
    ]

    readonly_response = await api_client.put(
        "/api/assets/achernar",
        json={"name": "Mutated"},
    )
    assert readonly_response.status_code == 409


async def test_asset_create_rejects_forged_flow_preset_source(api_client):
    response = await api_client.post(
        "/api/assets",
        json={
            "type": "voice",
            "name": "Forged Preset",
            "source": "flow_preset",
            "sample_url": "https://example.test/voice.wav",
        },
    )

    assert response.status_code == 422
    assert "flow_preset assets can only be imported" in response.text


async def test_asset_create_rejects_unsafe_sample_url(api_client):
    response = await api_client.post(
        "/api/assets",
        json={
            "type": "voice",
            "name": "Unsafe Sample",
            "sample_url": "javascript:alert(1)",
        },
    )

    assert response.status_code == 422
    assert "sample_url must be https:// or /uploads/..." in response.text


async def test_delete_referenced_voice_asset_is_blocked(api_client):
    asset_response = await api_client.post(
        "/api/assets",
        json={"id": "narrator", "type": "voice", "name": "Narrator"},
    )
    assert asset_response.status_code == 201
    job_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "A narrated launch film",
            "voice_asset_id": "narrator",
        },
    )
    assert job_response.status_code == 201

    delete_response = await api_client.delete("/api/assets/narrator")

    assert delete_response.status_code == 409
    assert "referenced by jobs" in delete_response.text


async def test_voice_import_accepts_discovery_capture_preview(api_client):
    capture = json.loads(Path("docs/discovery_extension_captures.jsonl").read_text().splitlines()[72])

    assert capture["surface"] == "voices"
    assert "externalReferenceMedia" in capture["response_preview"]
    response = await api_client.post(
        "/api/assets/voices/import",
        json={
            "result": {
                "data": {
                    "json": {
                        "projectContents": {
                            "externalReferenceMedia": [
                                {
                                    "mediaId": "achernar",
                                    "mediaType": "AUDIO",
                                    "workflowDisplayName": "Achernar",
                                    "media": {
                                        "audio": {
                                            "generatedAudio": {
                                                "name": "Achernar",
                                                "description": "Female, soft, high pitch",
                                                "audioSamplePath": "https://gstatic.com/aitestkitchen/voices/samples/Achernar.wav",
                                            }
                                        }
                                    },
                                },
                                {
                                    "mediaId": "achird",
                                    "mediaType": "AUDIO",
                                    "workflowDisplayName": "Achird",
                                    "media": {
                                        "audio": {
                                            "generatedAudio": {
                                                "name": "Achird",
                                                "description": "Male, friendly, mid pitch",
                                                "audioSamplePath": "https://gstatic.com/aitestkitchen/voices/samples/Achird.wav",
                                            }
                                        }
                                    },
                                },
                            ]
                        }
                    }
                }
            }
        },
    )

    assert response.status_code == 200
    voices = response.json()
    assert len(voices) == 2
    achernar = next(asset for asset in voices if asset["id"] == "achernar")
    assert achernar["name"] == "Achernar"
    assert achernar["description"] == "Female, soft, high pitch"
    assert achernar["sample_url"].endswith("/Achernar.wav")
    assert achernar["source"] == "flow_preset"
