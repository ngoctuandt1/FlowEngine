import pytest

from flow.trash import TrashActionError, build_trash_url, delete_all, restore_all


class FakeLocator:
    def __init__(self, page, label: str):
        self.page = page
        self.label = label
        self.first = self

    async def wait_for(self, **kwargs):
        self.page.events.append(("wait_for", self.label, kwargs))

    async def click(self, **kwargs):
        self.page.events.append(("click", self.label, kwargs))

    async def inner_text(self, **kwargs):
        self.page.events.append(("inner_text", self.label, kwargs))
        return self.page.body_text


class FakePage:
    def __init__(self):
        self.events = []
        self.body_text = "Trash\n0 Items in trash"

    async def goto(self, url, **kwargs):
        self.events.append(("goto", url, kwargs))

    def get_by_role(self, role, name=None):
        label = getattr(name, "pattern", name) or role
        return FakeLocator(self, f"role:{role}:{label}")

    def get_by_text(self, text, exact=False):
        return FakeLocator(self, f"text:{text}:{exact}")

    def locator(self, selector):
        return FakeLocator(self, f"locator:{selector}")


def test_build_trash_url_uses_flow_trash_route():
    url = build_trash_url("d254e570-f789-4afd-a0df-457682534809")

    assert url == (
        "https://labs.google/fx/tools/flow/project/"
        "d254e570-f789-4afd-a0df-457682534809/trash"
    )


async def test_restore_all_requires_explicit_confirmation():
    page = FakePage()

    with pytest.raises(TrashActionError, match="confirm=True"):
        await restore_all(page, "d254e570-f789-4afd-a0df-457682534809")

    assert page.events == []


async def test_delete_all_requires_explicit_confirmation():
    page = FakePage()

    with pytest.raises(TrashActionError, match="confirm=True"):
        await delete_all(page, "d254e570-f789-4afd-a0df-457682534809")

    assert page.events == []


async def test_restore_all_opens_trash_verifies_header_then_clicks():
    page = FakePage()

    await restore_all(page, "d254e570-f789-4afd-a0df-457682534809", confirm=True)

    assert page.events[0][0] == "goto"
    assert page.events[0][1].endswith("/fx/tools/flow/project/d254e570-f789-4afd-a0df-457682534809/trash")
    assert page.events[1][0] == "wait_for"
    assert page.events[2][0] == "click"
    assert "Restore" in page.events[2][1]
    assert "All" in page.events[2][1]


async def test_delete_all_opens_trash_verifies_header_then_clicks():
    page = FakePage()

    await delete_all(page, "d254e570-f789-4afd-a0df-457682534809", confirm=True)

    assert page.events[0][0] == "goto"
    assert page.events[1][0] == "wait_for"
    assert page.events[2][0] == "click"
    assert "Delete" in page.events[2][1]
    assert "All" in page.events[2][1]
