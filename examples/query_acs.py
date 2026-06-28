#!/usr/bin/env python3
"""query_acs.py — read a party's active contracts for the Holding interface.

    python3 query_acs.py 'farmfort::1220…'

Prints the raw active-contract entries. An empty list ([]) is the correct
result before any Canton Coin has been received.
"""
import json
import sys
import httpx
from get_token import get_token

LEDGER_BASE = "https://api.validator.dev.digik.cantor8.tech/api/ledger"
HOLDING = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"


def query_acs(party: str, interface_id: str = HOLDING) -> list:
    headers = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}

    # 1. latest offset
    end = httpx.get(f"{LEDGER_BASE}/v2/state/ledger-end", headers=headers, timeout=30)
    end.raise_for_status()
    offset = end.json()["offset"]

    # 2. active contracts at that offset, filtered to the interface
    body = {
        "eventFormat": {
            "filtersByParty": {
                party: {"cumulative": [{
                    "identifierFilter": {"InterfaceFilter": {"value": {
                        "interfaceId": interface_id,
                        "includeInterfaceView": True,
                        "includeCreatedEventBlob": True,
                    }}}
                }]}
            },
            "verbose": False,
        },
        "activeAtOffset": offset,
    }
    r = httpx.post(f"{LEDGER_BASE}/v2/state/active-contracts", headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python3 query_acs.py '<partyId>'")
    print(json.dumps(query_acs(sys.argv[1]), indent=2))
