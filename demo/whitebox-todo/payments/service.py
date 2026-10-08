"""Payment charging against a third-party gateway."""

from typing import Any


def charge(amount_cents: int, gateway_response: dict[str, Any]) -> dict[str, Any]:
    """Charge a customer and return the receipt.

    BUG (intentional, for the white-box demo): when the gateway omits
    ``transaction_id`` (declined/timeout responses), this raises KeyError
    instead of recording a receipt with a missing transaction id.
    """
    transaction_id = gateway_response["transaction_id"]
    return {
        "status": "charged",
        "amount_cents": amount_cents,
        "transaction_id": transaction_id,
    }
