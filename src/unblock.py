#!/usr/bin/env python3
"""unblock.py — fix the custodial-party 403 and create the PreApproval in one shot.

What it does, in order, printing everything as it goes:
  1. get a JWT and decode WHO we are (the user id = JWT `sub`)
  2. read our current ledger rights        -> diagnosis
  3. grant our user CanActAs on --party     -> the missing permission behind the 403
  4. resolve the validator operator (the preapproval `provider`)
  5. create the TransferPreapprovalProposal via submit-and-wait
  6. read the PreApproval contract + Holding balance back

Run:
  export CN_CLIENT_SECRET='...'
  python src/unblock.py --party 'farmfort::12204e94...'
"""
from __future__ import annotations
import argparse, base64, json, os, sys, uuid
import httpx

IDP       = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID",      "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET",  "")
SCOPE     = os.getenv("CN_SCOPE",          "")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")
VALIDATOR = os.getenv("CN_VALIDATOR_BASE", "https://api.validator.dev.digik.cantor8.tech/api/validator")

PREAPPROVAL = "#splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapprovalProposal"
HOLDING     = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"

http = httpx.Client(timeout=60.0)


def show(label, r):
    ok = r.status_code < 400
    mark = "ok" if ok else f"FAIL {r.status_code}"
    print(f"\n--- {label}: {mark} ---")
    try:
        print(json.dumps(r.json(), indent=2)[:2000])
    except Exception:
        print(r.text[:2000])
    return ok


def token() -> str:
    if not SECRET:
        sys.exit("Set CN_CLIENT_SECRET in your shell first.")
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": SECRET}
    if SCOPE:
        data["scope"] = SCOPE
    r = http.post(f"{IDP}/realms/{REALM}/protocol/openid-connect/token", data=data)
    r.raise_for_status()
    return r.json()["access_token"]


def jwt_sub(tok: str) -> str:
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    claims = json.loads(base64.urlsafe_b64decode(p))
    return claims.get("sub", "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--party", required=True)
    ap.add_argument("--provider", default=None)
    a = ap.parse_args()

    tok = token()
    uid = jwt_sub(tok)
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    print(f"token ok. our ledger user (JWT sub) = {uid!r}")

    # 2. what can we already do?
    show("current rights", http.get(f"{LEDGER}/v2/users/{uid}/rights", headers=H))

    # 3. grant ourselves CanActAs on the party (the fix for the 403)
    grant_body = {"userId": uid, "rights": [
        {"kind": {"CanActAs": {"value": {"party": a.party}}}}
    ]}
    granted = show("grant CanActAs on party",
                   http.post(f"{LEDGER}/v2/users/{uid}/rights", headers=H, json=grant_body))
    if not granted:
        print("\n>> Couldn't grant the right. If this is 403/PERMISSION_DENIED, our hackathon\n"
              ">> user isn't a participant admin — that's the one thing the team must do:\n"
              f">> grant CanActAs on {a.party} to user {uid!r}.\n"
              ">> Paste this whole output to Claude either way.")
        # keep going — maybe we already had the right and the grant was a no-op/duplicate.

    # 4. provider = validator operator
    provider = a.provider
    if not provider:
        info = http.get(f"{VALIDATOR}/v0/validator-user", headers=H).json()
        provider = info.get("party_id") or info.get("party") or info.get("partyId")
    print(f"\nprovider (validator operator) = {provider}")

    # 5. create the TransferPreapprovalProposal as the party
    body = {
        "commands": [{"CreateCommand": {
            "templateId": PREAPPROVAL,
            "createArguments": {"receiver": a.party, "provider": provider},
        }}],
        "commandId": f"unblock-{uuid.uuid4()}",
        "actAs": [a.party],
        "userId": uid,
    }
    ok = show("create TransferPreapprovalProposal",
              http.post(f"{LEDGER}/v2/commands/submit-and-wait", headers=H, json=body))
    if not ok:
        print("\n>> Preapproval create still failed — paste this output to Claude.")
        sys.exit(1)

    # 6. read it back + balance
    end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]

    def acs(interface):
        b = {"eventFormat": {"filtersByParty": {a.party: {"cumulative": [
            {"identifierFilter": {"InterfaceFilter": {"value": {
                "interfaceId": interface, "includeInterfaceView": True,
                "includeCreatedEventBlob": True}}}}]}}, "verbose": False},
             "activeAtOffset": end}
        return http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=b)

    show("ACS: PreApproval proposal", acs(PREAPPROVAL))
    show("ACS: Holding balance (expect empty until CC arrives)", acs(HOLDING))

    print("\n========================================================")
    print("DONE. PreApproval is on-ledger. Now send this PartyId to the team for CC:")
    print(f"  {a.party}")
    print("========================================================")


if __name__ == "__main__":
    main()
