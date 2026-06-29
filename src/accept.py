#!/usr/bin/env python3
"""accept.py — accept the incoming Splice wallet TransferOffer, then show balance.

The team sent Canton Coin as a Splice.Wallet.TransferOffer (not a Token Standard
TransferInstruction), so this exercises TransferOffer_Accept on it. We already
hold actAs on the custodial party, so it's a plain submit-and-wait.

  export CN_CLIENT_SECRET='...'
  python src/accept.py --party 'farmfort::12204e94...'
"""
from __future__ import annotations
import argparse, base64, json, os, sys, time, uuid
import httpx

IDP       = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID",      "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET",  "")
SCOPE     = os.getenv("CN_SCOPE",          "")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")

OFFER   = "#splice-wallet:Splice.Wallet.TransferOffer:TransferOffer"
HOLDING = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"

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


def jwt_sub(tok: str) -> str:
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub", "")


def acs(party, H, end, template):
    body = {"eventFormat": {"filtersByParty": {party: {"cumulative": [
        {"identifierFilter": {"TemplateFilter": {"value": {
            "templateId": template, "includeCreatedEventBlob": True}}}}]}},
        "verbose": False}, "activeAtOffset": end}
    r = http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=body)
    if r.status_code >= 400:
        return []
    out = []
    for row in r.json():
        ac = row.get("contractEntry", {}).get("JsActiveContract")
        if ac:
            out.append(ac["createdEvent"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--party", required=True)
    ap.add_argument("--cid", default=None, help="specific TransferOffer contractId (optional)")
    a = ap.parse_args()

    tok = token()
    uid = jwt_sub(tok)
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]

    offers = acs(a.party, H, end, OFFER)
    offers = [o for o in offers if (o.get("createArgument") or o.get("createArguments") or {})
              .get("receiver") == a.party]
    if not offers:
        print("No TransferOffer waiting for this party. Either it was already accepted "
              "(check balance) or it went elsewhere.")
        sys.exit(0)

    if a.cid:
        offers = [o for o in offers if str(o.get("contractId", "")).startswith(a.cid)]
        if not offers:
            sys.exit(f"No TransferOffer matching cid prefix {a.cid!r}.")

    print(f"{len(offers)} TransferOffer(s) to accept.")
    for o in offers:
        cid = o["contractId"]
        args = o.get("createArgument") or o.get("createArguments") or {}
        print(f"\nAccepting offer {str(cid)[:24]}…  from {str(args.get('sender',''))[:24]}…")
        body = {
            "commands": [{"ExerciseCommand": {
                "templateId": OFFER,
                "contractId": cid,
                "choice": "TransferOffer_Accept",
                "choiceArgument": {},
            }}],
            "commandId": f"accept-{uuid.uuid4()}",
            "actAs": [a.party],
            "userId": uid,
        }
        r = http.post(f"{LEDGER}/v2/commands/submit-and-wait", headers=H, json=body)
        if r.status_code >= 400:
            print(f"  !! accept failed {r.status_code}:\n{r.text[:1200]}")
            print("\n  If this mentions a missing contract (AmuletRules / OpenMiningRound), "
                  "paste it to Claude — we add those as disclosed contracts and retry.")
            sys.exit(1)
        print(f"  accepted ✅  {json.dumps(r.json())[:200]}")

    # balance (poll for PQS lag)
    print("\nChecking balance (may lag a few minutes)…")
    for _ in range(6):
        end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]
        hold = acs(a.party, H, end, None) if False else None
        body = {"eventFormat": {"filtersByParty": {a.party: {"cumulative": [
            {"identifierFilter": {"InterfaceFilter": {"value": {
                "interfaceId": HOLDING, "includeInterfaceView": True,
                "includeCreatedEventBlob": True}}}}]}}, "verbose": False},
            "activeAtOffset": end}
        rr = http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=body)
        total, n = 0.0, 0
        if rr.status_code < 400:
            for row in rr.json():
                ac = row.get("contractEntry", {}).get("JsActiveContract")
                if not ac:
                    continue
                for v in ac["createdEvent"].get("interfaceViews", []):
                    vv = v.get("viewValue") or {}
                    if vv.get("amount") is not None:
                        total += float(vv["amount"]); n += 1
        if n:
            print(f"\n🎉 BALANCE: {total}  across {n} Holding(s). The Canton Coin is yours.")
            return
        print("  not visible yet (PQS lag) — waiting 30s…")
        time.sleep(30)
    print("\nAccepted, but holdings not surfaced yet — re-run:  python src/verify.py --party '"
          + a.party + "'  in a few minutes.")


if __name__ == "__main__":
    main()
