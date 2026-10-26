import httpx
from app.providers.common import post_with_connect_retry


def test_retries_once_after_connection_timeout() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise httpx.ConnectTimeout(
                "TLS handshake timed out",
                request=request,
            )

        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as client:
        response = post_with_connect_retry(
            client,
            "https://api.example.com/v1/chat/completions",
            json={},
        )

    assert attempts == 2
    assert response.json() == {"ok": True}
