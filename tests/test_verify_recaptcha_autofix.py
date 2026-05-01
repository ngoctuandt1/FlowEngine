import pytest

from scripts import verify_recaptcha_autofix as verify_module


@pytest.fixture
def job_template() -> dict:
    return {
        "id": "job-rec-autofix-pytest",
        "type": "text-to-video",
        "profile": verify_module.OLD_PROFILE,
        "job_level": 1,
    }


@pytest.fixture
def scenario_spec(request) -> verify_module.ScenarioSpec:
    return verify_module.scenario_specs()[request.param]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_spec", ["A", "B", "C", "D", "E"], indirect=True)
async def test_simulated_recaptcha_autofix_scenarios(
    scenario_spec: verify_module.ScenarioSpec,
    job_template: dict,
):
    outcome = await verify_module.simulate_scenario(
        scenario_spec,
        dry_run=True,
        job=job_template,
    )

    verify_module.assert_outcome(outcome)


def test_verify_recaptcha_autofix_dry_run_executes_all_scenarios(capsys):
    exit_code = verify_module.main(["--dry-run"])

    assert exit_code == 0
    captured = capsys.readouterr()
    for scenario_key in ("A", "B", "C", "D", "E"):
        assert f"[PASS] {scenario_key}:" in captured.out
    assert "PASS [dry-run]" in captured.out
