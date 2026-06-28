#!/usr/bin/env python3
"""create_party.py — allocate a custodial party and print its on-ledger id.

    python3 create_party.py farmfort

The lab sheet also points at the external-party topology route
(/v0/admin/external-party/topology/{generate,submit}); this file shows only the
quick custodial path via /v2/parties. See src/canton_lab.py for the external flow.
"""
import sys
import httpx
from get_token import get_token

LEDGER_BASE = "https://api.validator.dev.digik.cantor8.tech/api/ledger"


def create_party(hint: str) -> str:
    headers = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}
    r = httpx.post(f"{LEDGER_BASE}/v2/parties",
                   headers=headers,
                   json={"partyIdHint": hint, "identityProviderId": ""},
                   timeout=30)
    r.raise_for_status()
    return r.json()["partyDetails"]["party"]


if __name__ == "__main__":
    hint = sys.argv[1] if len(sys.argv) > 1 else "lab"
    print(create_party(hint))
