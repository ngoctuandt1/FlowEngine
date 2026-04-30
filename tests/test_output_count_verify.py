import pytest

from flow.operations.generate import _chip_text_matches_output_count


@pytest.mark.parametrize(
    ("chip_text", "expected"),
    [
        ("Video\ncrop_16_9\n1x", True),
        ("Video\ncrop_16_9\nx1", True),
        ("Video\ncrop_16_9\nx2", False),
        ("Video\ncrop_16_9\nx3", False),
        ("", False),
    ],
)
def test_chip_text_matches_output_count_for_single_output(chip_text, expected):
    assert _chip_text_matches_output_count(chip_text, 1) is expected
