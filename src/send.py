#!/usr/bin/env python3
"""send.py — Token Standard transfer (lab Step 8), done with the real factory shape.

The factory request needs the FULL TransferFactory_Transfer arguments wrapped in
`choiceArguments` — timestamps, the holdings to spend, and a two-phase context:
  1. POST transfer-factory with the intended args (empty context) -> get
     factoryId + choiceContext{choiceContextData, disclosedContracts}
  2. exercise TransferFactory_Transfer on factoryId with the SAME args but
     extraArgs.context filled from the returned choiceContextData, passing the
     disclosedContracts.

If the receiver has no live preapproval (our case), this creates a
TransferInstruction the receiver then accepts. 20 CC stays locked from the sender
until accepted/withdrawn — nothing is lost on DevNet.

  export CN_CLIENT_SECRET=...  CN_REGISTRY_BASE=...  CN_INSTRUMENT_ADMIN=...  CN_INSTRUMENT_ID=Amulet
  python src/send.py --sender 'farmfort::…' --receiver 'farmfort2::…' --amount 20
"""
from __future__ import annotations
import argparse, base64, json, os, sys, uuid
from datetime import datetime, timedelta, timezone
import httpx

IDP       = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID",      "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET",  "")
SCOPE     = os.getenv("CN_SCOPE",          "")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")
REGISTRY  = os.getenv("CN_REGISTRY_BASE",  "")
ADMIN     = os.getenv("CN_INSTRUMENT_ADMIN", "")
INSTR     = os.getenv("CN_INSTRUMENT_ID",  "Amulet")

HOLDING = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"
FACTORY = "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferFactory"

http = httpx.Client(timeout=60.0)


def die(r, where):
    if r.status_code >= 400:
        print(f"\n!! {where} -> {r.status_code}\n{r.text[:1500]}\n", file=sys.stderr)
        sys.exit(1)


def token() -> str:
    if not SECRET:
        sys.exit("Set CN_CLIENT_SECRET.")
    if not REGISTRY or not ADMIN:
        sys.exit("Set CN_REGISTRY_BASE and CN_INSTRUMENT_ADMIN (see notes/devnet-coordinates.md).")
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": SECRET}
    if SCOPE:
        data["scope"] = SCOPE
    r = http.post(f"{IDP}/realms/{REALM}/protocol/openid-connect/token", data=data)
    r.raise_for_status()
    return r.json()["access_token"]


def jwt_sub(tok):
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub", "")


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender", required=True)
    ap.add_argument("--receiver", required=True)
    ap.add_argument("--amount", required=True)
    a = ap.parse_args()

    tok = token()
    uid = jwt_sub(tok)
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]

    # 1. the sender's holdings to spend
    body = {"eventFormat": {"filtersByParty": {a.sender: {"cumulative": [
        {"identifierFilter": {"InterfaceFilter": {"value": {
            "interfaceId": HOLDING, "includeInterfaceView": True,
            "includeCreatedEventBlob": True}}}}]}}, "verbose": False}, "activeAtOffset": end}
    r = http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=body); die(r, "holdings")
    cids = []
    for row in r.json():
        ac = row.get("contractEntry", {}).get("JsActiveContract")
        if ac:
            cids.append(ac["createdEvent"]["contractId"])
    if not cids:
        sys.exit("Sender has no Holding contracts to spend.")
    print(f"spending from {len(cids)} holding(s): {[c[:16]+'…' for c in cids]}")

    now = datetime.now(timezone.utc)
    transfer = {
        "sender": a.sender,
        "receiver": a.receiver,
        "amount": a.amount,
        "instrumentId": {"admin": ADMIN, "id": INSTR},
        "requestedAt": ts(now),
        "executeBefore": ts(now + timedelta(hours=1)),
        "inputHoldingCids": cids,
        "meta": {"values": {}},
    }
    choice_args = {
        "expectedAdmin": ADMIN,
        "transfer": transfer,
        "extraArgs": {"context": {"values": {}}, "meta": {"values": {}}},
    }

    # 2. ask the registry for the factory + the context this choice needs
    r = http.post(f"{REGISTRY}/registry/transfer-instruction/v1/transfer-factory",
                  headers=H, json={"choiceArguments": choice_args, "excludeDebugFields": False})
    die(r, "transfer-factory")
    fr = r.json()
    print(f"\nfactory ok. transferKind = {fr.get('transferKind')}")
    factory_id = fr["factoryId"]
    ctx = fr.get("choiceContext", fr)
    context_data = ctx.get("choiceContextData", ctx.get("choiceContext", {}))
    disclosed = ctx.get("disclosedContracts", [])
    print(f"factoryId = {str(factory_id)[:20]}…   disclosed contracts: {len(disclosed)}")

    # 3. exercise the choice with the real context filled in
    choice_args["extraArgs"]["context"] = context_data
    cmd = {
        "commands": [{"ExerciseCommand": {
            "templateId": FACTORY,
            "contractId": factory_id,
            "choice": "TransferFactory_Transfer",
            "choiceArgument": choice_args,
        }}],
        "commandId": f"send-{uuid.uuid4()}",
        "actAs": [a.sender],
        "userId": uid,
        "disclosedContracts": disclosed,
    }
    r = http.post(f"{LEDGER}/v2/commands/submit-and-wait", headers=H, json=cmd)
    die(r, "TransferFactory_Transfer")
    print("\n✅ transfer submitted:")
    print(json.dumps(r.json(), indent=2)[:600])
    print(f"\nIf transferKind was 'offer'/'instruction', {a.receiver[:18]}… now has a "
          "TransferInstruction to accept. Run inbox.py on the receiver to see it.")


if __name__ == "__main__":
    main()
