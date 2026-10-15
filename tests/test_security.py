from app import PORT, request_problem

OWN = {"Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}", "Content-Type": "application/json"}


def test_own_page_and_local_tools_allowed():
    assert request_problem("GET", {"Host": f"127.0.0.1:{PORT}"}) is None
    assert request_problem("GET", {"Host": f"localhost:{PORT}"}) is None
    assert request_problem("POST", OWN) is None
    assert request_problem("DELETE", {"Host": f"127.0.0.1:{PORT}", "Origin": f"http://127.0.0.1:{PORT}"}) is None
    # Command-line tools (curl, the installer, tests) send no Origin.
    assert request_problem("POST", {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json"}) is None


def test_dns_rebinding_blocked():
    assert request_problem("GET", {"Host": f"evil.example:{PORT}"})
    assert request_problem("GET", {})


def test_other_websites_cannot_make_changes():
    assert request_problem("POST", {**OWN, "Origin": "https://evil.example"})
    assert request_problem("DELETE", {"Host": f"127.0.0.1:{PORT}", "Origin": "https://evil.example"})
    assert request_problem("POST", {**OWN, "Origin": "null"})  # sandboxed iframes, file:// pages


def test_non_json_posts_refused():
    # A form or plain-text POST is what another site can send without permission.
    assert request_problem("POST", {**OWN, "Content-Type": "text/plain"})
    assert request_problem("POST", {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/x-www-form-urlencoded"})

