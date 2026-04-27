def _payload(**overrides):
    payload = {
        "subject": "A red sports car",
        "action": None,
        "environment": None,
        "mood": None,
        "camera": None,
        "lighting": None,
        "lens": None,
        "style": None,
        "motion": None,
        "audio": None,
        "negative": None,
    }
    payload.update(overrides)
    return payload


async def test_assemble_prompt_minimal_subject_only(api_client):
    response = await api_client.post("/api/prompt-builder/assemble", json=_payload())

    assert response.status_code == 200
    assert response.json() == {
        "prompt": "A red sports car",
        "length": len("A red sports car"),
    }


async def test_assemble_prompt_full_spec(api_client):
    response = await api_client.post(
        "/api/prompt-builder/assemble",
        json=_payload(
            action="drifting through a tight corner",
            environment="on a rain-soaked mountain road",
            mood="tense and kinetic",
            camera="low tracking shot",
            lighting="neon reflections on wet asphalt",
            lens="35mm anamorphic lens",
            style="cinematic realism",
            motion="smooth lateral motion",
            audio="thunder, tire squeal, turbo whine",
            negative="cartoon look, blurry details",
        ),
    )

    expected = (
        "A red sports car, drifting through a tight corner, on a rain-soaked mountain road, "
        "tense and kinetic, low tracking shot, 35mm anamorphic lens, "
        "neon reflections on wet asphalt, smooth lateral motion, "
        "thunder, tire squeal, turbo whine, cinematic realism "
        "Avoid: cartoon look, blurry details."
    )

    assert response.status_code == 200
    assert response.json() == {"prompt": expected, "length": len(expected)}


async def test_assemble_prompt_requires_subject(api_client):
    response = await api_client.post("/api/prompt-builder/assemble", json={"action": "running"})

    assert response.status_code == 422


async def test_assemble_prompt_rejects_whitespace_only_subject(api_client):
    response = await api_client.post(
        "/api/prompt-builder/assemble",
        json=_payload(subject="   "),
    )

    assert response.status_code == 422


async def test_assemble_prompt_rejects_field_over_200_chars(api_client):
    response = await api_client.post(
        "/api/prompt-builder/assemble",
        json=_payload(camera="x" * 201),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Field 'camera' exceeds 200 characters"


async def test_assemble_prompt_rejects_total_prompt_over_1500_chars(api_client):
    long_value = "x" * 200
    response = await api_client.post(
        "/api/prompt-builder/assemble",
        json=_payload(
            action=long_value,
            environment=long_value,
            mood=long_value,
            camera=long_value,
            lighting=long_value,
            lens=long_value,
            style=long_value,
            motion=long_value,
            audio=long_value,
            negative=long_value,
        ),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Prompt exceeds 1500 characters"


async def test_assemble_prompt_uses_deterministic_order(api_client):
    response = await api_client.post(
        "/api/prompt-builder/assemble",
        json=_payload(
            style="stylized documentary",
            audio="city ambience",
            mood="quietly observational",
            lens="50mm prime lens",
            action="walking slowly",
            lighting="soft morning light",
            motion="gentle handheld sway",
            environment="through a busy outdoor market",
            camera="over-the-shoulder framing",
        ),
    )

    expected = (
        "A red sports car, walking slowly, through a busy outdoor market, "
        "quietly observational, over-the-shoulder framing, 50mm prime lens, "
        "soft morning light, gentle handheld sway, city ambience, stylized documentary"
    )

    assert response.status_code == 200
    assert response.json()["prompt"] == expected


async def test_assemble_prompt_uses_lens_when_camera_missing(api_client):
    response = await api_client.post(
        "/api/prompt-builder/assemble",
        json=_payload(lens="85mm portrait lens", style="editorial"),
    )

    expected = "A red sports car, 85mm portrait lens, editorial"

    assert response.status_code == 200
    assert response.json() == {"prompt": expected, "length": len(expected)}
