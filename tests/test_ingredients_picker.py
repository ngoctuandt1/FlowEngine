import pytest

from flow.operations import ingredients


class _FakeTile:
    def __init__(self, page, index):
        self._page = page
        self._index = index

    async def click(self, **kwargs):
        self._page.clicked.append({"index": self._index, "kwargs": kwargs})


class _FakeTileLocator:
    def __init__(self, page, selector):
        self._page = page
        self.selector = selector

    def nth(self, index):
        self._page.nth_indices.append(index)
        return _FakeTile(self._page, index)


class _FakePickerPage:
    def __init__(self, candidates):
        self.candidates = candidates
        self.clicked = []
        self.nth_indices = []
        self.locator_selectors = []
        self.evaluate_args = []

    async def evaluate(self, _script, arg):
        self.evaluate_args.append(arg)
        if isinstance(arg, dict) and "selector" in arg:
            return self.candidates
        return {"pickerOpen": False, "pickerText": "", "imgCount": 0, "composerImgs": 0}

    def locator(self, selector):
        self.locator_selectors.append(selector)
        return _FakeTileLocator(self, selector)


@pytest.mark.asyncio
async def test_commit_uploaded_tile_clicks_first_filename_match_fallback():
    page = _FakePickerPage(
        [
            {"index": 0, "filenameMatch": False, "selected": False},
            {"index": 1, "filenameMatch": True, "selected": False},
            {"index": 2, "filenameMatch": False, "selected": False},
        ]
    )

    await ingredients._commit_uploaded_tile_in_picker(
        page, "uploads/ref.png", timeout_sec=0.1
    )

    assert page.nth_indices == [1]
    assert page.clicked == [{"index": 1, "kwargs": {"timeout": 3000, "force": True}}]


@pytest.mark.asyncio
async def test_commit_uploaded_tile_prefers_selected_duplicate_basename():
    page = _FakePickerPage(
        [
            {"index": 0, "filenameMatch": True, "selected": False, "text": "ref.png"},
            {"index": 1, "filenameMatch": True, "selected": True, "text": "ref.png"},
        ]
    )

    await ingredients._commit_uploaded_tile_in_picker(
        page, "uploads/ref.png", timeout_sec=0.1
    )

    assert page.nth_indices == [1]
    assert page.clicked == [{"index": 1, "kwargs": {"timeout": 3000, "force": True}}]


def test_picker_tile_selection_priority_order():
    selected_only, reason = ingredients._select_picker_tile_candidate(
        [
            {"index": 0, "filenameMatch": True, "selected": False},
            {"index": 1, "filenameMatch": False, "selected": True},
        ]
    )
    assert selected_only == {"index": 1, "filenameMatch": False, "selected": True}
    assert reason == "selected"

    newest_created, reason = ingredients._select_picker_tile_candidate(
        [
            {"index": 0, "filenameMatch": True, "selected": False},
            {"index": 1, "filenameMatch": False, "selected": False, "createdSortKey": 10},
            {"index": 2, "filenameMatch": False, "selected": False, "createdSortKey": 20},
        ]
    )
    assert newest_created == {
        "index": 2,
        "filenameMatch": False,
        "selected": False,
        "createdSortKey": 20,
    }
    assert reason == "newest_created"

    stable_sort, reason = ingredients._select_picker_tile_candidate(
        [
            {"index": 0, "filenameMatch": False, "stableSortOrderKey": 2},
            {"index": 1, "filenameMatch": False, "stableSortOrderKey": 1},
        ]
    )
    assert stable_sort == {"index": 0, "filenameMatch": False, "stableSortOrderKey": 2}
    assert reason == "newest_stable_sort"
