#!/usr/bin/env python3
"""accept_external.py — external (non-custodial) party accepts a TransferInstruction.

For farmfort2, which signs its own transactions. Flow:
  1. find the TransferInstruction sitting for the party (ACS)
  2. ask the registry for the accept choice-context + disclosed contracts
  3. interactive-submission/prepare the TransferInstruction_Accept exercise
  4. sign the prepared hash with the party's ed25519 key
  5. interactive-submission/execute
  6. read the party's Holding balance

  export CN_CLIENT_SECRET=...  CN_REGISTRY_BASE=...
  python src/accept_external.py --party 'farmfort2::1220f67dbd62…' \
      --keyfile /Users/bitcolin/Downloads/farmfort2.ed25519.key
"""
from __future__ import annotations
import argparse, base64, json, os, sys, time, uuid
import httpx
from cryptography.hazmat.primitives import serialization

IDP       = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID",      "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET",  "")
SCOPE     = os.getenv("CN_SCOPE",          "")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")
REGISTRY  = os.getenv("CN_REGISTRY_BASE",  "")

TXINSTR = "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferInstruction"
HOLDING = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"

http = httpx.Client(timeout=60.0)


def die(r, where):
    if r.status_code >= 400:
        print(f"\n!! {where} -> {r.status_code}\n{r.text[:1500]}\n", file=sys.stderr)
        sys.exit(1)


def token():
    if not SECRET:
        sys.exit("Set CN_CLIENT_SECRET.")
    if not REGISTRY:
        sys.exit("Set CN_REGISTRY_BASE.")
    data = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": SECRET}
    if SCOPE:
        data["scope"] = SCOPE
    r = http.post(f"{IDP}/realms/{REALM}/protocol/openid-connect/token", data=data)
    r.raise_for_status()
    return r.json()["access_token"]


def jwt_sub(tok):
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub", "")


def acs_interface(party, H, interface):
    end = http.get(f"{LEDGER}/v2/state/ledger-end", headers=H).json()["offset"]
    body = {"eventFormat": {"filtersByParty": {party: {"cumulative": [
        {"identifierFilter": {"InterfaceFilter": {"value": {
            "interfaceId": interface, "includeInterfaceView": True,
            "includeCreatedEventBlob": True}}}}]}}, "verbose": False}, "activeAtOffset": end}
    r = http.post(f"{LEDGER}/v2/state/active-contracts", headers=H, json=body)
    if r.status_code >= 400:
        return []
    out = []
    for row in r.json():
        ac = row.get("contractEntry", {}).get("JsActiveContract")
        if ac:
            out.append(ac["createdEvent"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--party", required=True)
    ap.add_argument("--keyfile", required=True)
    a = ap.parse_args()

    tok = token()
    uid = jwt_sub(tok)
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    fingerprint = a.party.split("::", 1)[1]
    priv = serialization.load_pem_private_key(open(a.keyfile, "rb").read(), password=None)

    # 1. the TransferInstruction waiting for this party
    instrs = acs_interface(a.party, H, TXINSTR)
    if not instrs:
        print("No TransferInstruction for this party. (Already accepted? check balance.)")
        sys.exit(0)
    cid = instrs[0]["contractId"]
    print(f"accepting TransferInstruction {str(cid)[:24]}…")

    # 2. registry accept choice-context + disclosed contracts
    r = http.post(f"{REGISTRY}/registry/transfer-instruction/v1/{cid}/choice-contexts/accept",
                  headers=H, json={"meta": {}, "excludeDebugFields": True})
    die(r, "choice-contexts/accept")
    cc = r.json()
    context_data = cc.get("choiceContextData", {})
    disclosed_raw = cc.get("disclosedContracts", [])
    disclosed = [{"templateId": d["templateId"], "contractId": d["contractId"],
                  "createdEventBlob": d["createdEventBlob"], "synchronizerId": d["synchronizerId"]}
                 for d in disclosed_raw]
    print(f"got accept context + {len(disclosed)} disclosed contract(s)")

    # synchronizer
    sync = http.get(f"{LEDGER}/v2/state/connected-synchronizers", headers=H); die(sync, "sync")
    synchronizer_id = sync.json()["connectedSynchronizers"][0]["synchronizerId"]

    # 3. prepare the exercise (external party signs it itself)
    prepare_body = {
        "userId": uid,
        "commandId": f"accept-{uuid.uuid4()}",
        "actAs": [a.party],
        "readAs": [],
        "synchronizerId": synchronizer_id,
        "disclosedContracts": disclosed,
        "packageIdSelectionPreference": [],
        "commands": [{"ExerciseCommand": {
            "templateId": TXINSTR,
            "contractId": cid,
            "choice": "TransferInstruction_Accept",
            "choiceArgument": {"extraArgs": {"context": context_data, "meta": {"values": {}}}},
        }}],
    }
    pr = http.post(f"{LEDGER}/v2/interactive-submission/prepare", headers=H, json=prepare_body)
    die(pr, "prepare")
    prepared = pr.json()
    tx = prepared["preparedTransaction"]
    h = prepared["preparedTransactionHash"]
    scheme = prepared.get("hashingSchemeVersion", "HASHING_SCHEME_VERSION_V2")
    print("prepared OK — signing…")

    # 4. sign + 5. execute
    sig = priv.sign(base64.b64decode(h))
    execute_body = {
        "preparedTransaction": tx,
        "hashingSchemeVersion": scheme,
        "userId": uid,
        "submissionId": str(uuid.uuid4()),
        "deduplicationPeriod": {"Empty": {}},
        "partySignatures": {"signatures": [{"party": a.party, "signatures": [{
            "format": "SIGNATURE_FORMAT_CONCAT",
            "signature": base64.b64encode(sig).decode(),
            "signedBy": fingerprint,
            "signingAlgorithmSpec": "SIGNING_ALGORITHM_SPEC_ED25519"}]}]},
    }
    ex = http.post(f"{LEDGER}/v2/interactive-submission/execute", headers=H, json=execute_body)
    die(ex, "execute")
    print("✅ accepted — executed on-ledger.")

    # 6. balance
    print("\nReading farmfort2 balance (PQS lag possible)…")
    for _ in range(6):
        hold = acs_interface(a.party, H, HOLDING)
        total = 0.0
        for ev in hold:
            for v in ev.get("interfaceViews", []):
                vv = v.get("viewValue") or {}
                if vv.get("amount") is not None:
                    total += float(vv["amount"])
        if hold:
            print(f"\n🎉 farmfort2 BALANCE: {total}  across {len(hold)} Holding(s).")
            return
        print("  not visible yet — waiting 30s…")
        time.sleep(30)
    print("\nAccepted; holdings not surfaced yet — re-run: python src/inbox.py '" + a.party + "'")


if __name__ == "__main__":
    main()
