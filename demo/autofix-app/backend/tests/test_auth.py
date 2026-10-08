"""Session behavior for valid, invalid, and degraded-provider logins."""

from auth import authenticate


def test_invalid_credentials_rejected():
    result = authenticate("test@example.com", "wrong")
    assert result["ok"] is False


def test_valid_login_with_degraded_provider():
    # Token service is degraded (empty payload): valid users must still
    # get a limited session instead of a 500.
    result = authenticate("test@example.com", "Test1234!")
    assert result["ok"] is True
    assert result["token"] is None
    assert result["limited"] is True
