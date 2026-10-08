"""Receipt behavior for gateway responses with and without transaction ids."""

import pytest

from payments.service import charge


def test_charge_success():
    receipt = charge(1999, {"transaction_id": "txn_123"})
    assert receipt == {
        "status": "charged",
        "amount_cents": 1999,
        "transaction_id": "txn_123",
    }


def test_charge_missing_transaction_id():
    # Gateways omit transaction_id on declined/timeout responses.
    # The service must still return a receipt instead of raising.
    receipt = charge(1999, {"status": "declined", "error": "timeout"})
    assert receipt["status"] == "charged"
    assert receipt["amount_cents"] == 1999
    assert receipt["transaction_id"] is None
