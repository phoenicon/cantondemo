#!/usr/bin/env python3
"""get_token.py — the smallest possible Canton auth: a client_credentials JWT.

    pip install httpx
    export CN_CLIENT_SECRET='...'
    python3 get_token.py
"""
import os
import httpx

IDP_BASE  = os.getenv("CN_IDP_BASE", "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM", "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID", "hackathon")
SECRET    = os.environ["CN_CLIENT_SECRET"]          # set this in your shell
SCOPE     = os.getenv("CN_SCOPE", "")               # some setups need "openid"


def get_token() -> str:
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": SECRET}
    if SCOPE:
        data["scope"] = SCOPE
    url = f"{IDP_BASE}/realms/{REALM}/protocol/openid-connect/token"
    r = httpx.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


if __name__ == "__main__":
    tok = get_token()
    print(tok[:40] + "…  (ok)")
