from app import VIEW_TIMEOUT, background_allowed


def test_background_updates_switch():
    assert background_allowed({}, last_view=0, now=10_000)  # default: on
    assert background_allowed({"background_updates": True}, 0, 10_000)
    off = {"background_updates": False}
    assert not background_allowed(off, last_view=0, now=10_000)  # nobody looking
    assert background_allowed(off, last_view=10_000 - 60, now=10_000)  # page open
    assert not background_allowed(off, last_view=10_000 - VIEW_TIMEOUT - 1, now=10_000)
