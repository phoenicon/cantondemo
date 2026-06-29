#!/usr/bin/env python3
"""inbox.py — dump EVERYTHING a party holds on-ledger (read-only).

No filters, no assumptions: a wildcard ACS query that lists every active
contract for each party you pass, grouped by template. This is how we find
where the Canton Coin actually landed (a Holding? a TransferInstruction? a
Splice wallet TransferOffer? a different party?).

  export CN_CLIENT_SECRET='...'
  python src/inbox.py 'farmfort::12204e94...' 'farmfort2::1220f67dbd62...'
"""
from __future__ import annotations
import json, os, sys
import httpx

IDP       = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID",      "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET",  "")
SCOPE     = os.getenv("CN_SCOPE",          "")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")

http = httpx.Client(timeout=60.0)


def token() -> str:
    if not SECRET:
        sys.exit("Set CN_CLIENT_SECRET in your shell first.")
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": SECRET}
    if SCOPE:
        data["scope"] = SCOPE
    r = http.post(f"{IDP}/realms/{REALM}/protocol/openid-connect/token", data=data)
    r.raise_for_status()
    return r.json()["access_token"]


def dump(party: str, H: dict, end: int) -> None:
    print(f"\n================ {party[:42]}… ================")
    body = {"eventFormat": {"filtersByParty": {party: {"cumulative": [
        {"identifierFilter": {"WildcardFilter": {"value": {"includeCreatedEventBlob": True}}}}
    ]}}, "verbose": False}, "activeAtOffset": end}
    r = http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=body)
    if r.status_code >= 400:
        print(f"  query failed {r.status_code}: {r.text[:300]}")
        print("  (a hard failure here usually means this party isn't hosted/known at all.)")
        return
    rows = r.json()
    contracts = []
    for row in rows:
        ac = row.get("contractEntry", {}).get("JsActiveContract")
        if ac:
            contracts.append(ac["createdEvent"])
    if not contracts:
        print("  EMPTY — this party holds nothing on-ledger.")
        return
    print(f"  {len(contracts)} active contract(s):")
    for ev in contracts:
        tid = ev.get("templateId", "?")
        # templateId looks like  <pkgid>:Module.Sub:Entity  — show the readable tail
        tail = tid.split(":", 1)[1] if ":" in tid else tid
        args = ev.get("createArgument") or ev.get("createArguments") or {}
        blurb = json.dumps(args)
        if len(blurb) > 220:
            blurb = blurb[:220] + "…"
        print(f"\n  • {tail}")
        print(f"      cid:  {str(ev.get('contractId',''))[:30]}…")
        print(f"      args: {blurb}")


def main() -> None:
    parties = sys.argv[1:]
    if not parties:
        sys.exit("Pass one or more party ids:\n  python src/inbox.py 'farmfort::…' 'farmfort2::…'")
    H = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]
    for p in parties:
        dump(p, H, end)
    print("\n----------------------------------------------------------")
    print("Look for: a 'Holding' (= coins landed), a 'TransferInstruction' or")
    print("'TransferOffer' (= coins waiting for you to ACCEPT), or EMPTY (= nothing here).")
    print("Paste this whole output back to Claude.")
    print("----------------------------------------------------------")


if __name__ == "__main__":
    main()
