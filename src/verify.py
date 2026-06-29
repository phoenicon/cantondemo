#!/usr/bin/env python3
"""verify.py — read-only check that the PreApproval is on-ledger and show balance.

Queries by TEMPLATE (the right filter for TransferPreapproval*), so unlike the
quick check inside unblock.py this actually displays the contracts. Safe to run
as many times as you like — it never writes anything.

  export CN_CLIENT_SECRET='...'
  python src/verify.py --party 'farmfort::12204e94...'
"""
from __future__ import annotations
import argparse, json, os, sys
import httpx

IDP       = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID",      "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET",  "")
SCOPE     = os.getenv("CN_SCOPE",          "")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")

PROPOSAL = "#splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapprovalProposal"
ACCEPTED = "#splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapproval"
HOLDING  = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--party", required=True)
    a = ap.parse_args()
    H = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]

    def query(identifier_filter):
        body = {"eventFormat": {"filtersByParty": {a.party: {"cumulative": [
            {"identifierFilter": identifier_filter}]}}, "verbose": False},
            "activeAtOffset": end}
        r = http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=body)
        if r.status_code >= 400:
            # A template/interface the participant has never seen 404s here — that
            # just means "none of those exist yet", not a real error. Treat as empty.
            return []
        out = []
        for row in r.json():
            ac = row.get("contractEntry", {}).get("JsActiveContract")
            if ac:
                out.append(ac["createdEvent"])
        return out

    def by_template(tid):
        return query({"TemplateFilter": {"value": {"templateId": tid, "includeCreatedEventBlob": True}}})

    def by_interface(iid):
        return query({"InterfaceFilter": {"value": {"interfaceId": iid,
                     "includeInterfaceView": True, "includeCreatedEventBlob": True}}})

    props = by_template(PROPOSAL)
    print(f"\nTransferPreapprovalProposal (your half): {len(props)} found")
    for c in props:
        print(f"  contractId: {c.get('contractId','')[:24]}…")
        print(f"  args: {json.dumps(c.get('createArgument', c.get('createArguments', {})))[:300]}")

    accepted = by_template(ACCEPTED)
    print(f"\nTransferPreapproval (ACCEPTED by provider — this is the live one): {len(accepted)} found")
    for c in accepted:
        print(f"  contractId: {c.get('contractId','')[:24]}…")

    hold = by_interface(HOLDING)
    total = 0.0
    for ev in hold:
        for v in ev.get("interfaceViews", []):
            vv = v.get("viewValue") or {}
            if vv.get("amount") is not None:
                total += float(vv["amount"])
    print(f"\nHolding balance: {total}  ({len(hold)} UTXO(s))")

    print("\n----------------------------------------------------------")
    if props or accepted:
        print("PreApproval IS on-ledger. ✅  Safe to tell the tutor it's set up.")
    else:
        print("No PreApproval found — re-run unblock.py, then this again.")
    if accepted:
        print("Provider has ACCEPTED it — CC sent to you will auto-accept.")
    elif props:
        print("Still a proposal — the tutor (provider) accepts it on their side, "
              "then CC flows. That's expected.")
    print("----------------------------------------------------------")


if __name__ == "__main__":
    main()
