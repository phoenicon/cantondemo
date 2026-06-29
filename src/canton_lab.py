#!/usr/bin/env python3
"""
canton_lab.py — Canton DevNet low-level lab runner (rebuilt).

Drives the whole "Touching the Ledger" flow over the validator's Admin API and
the JSON Ledger API. Every request shape here has been checked against the live
DevNet node (Canton 3.5.5) and the Splice validator OpenAPI, not guessed.

Flow:
  1. token            auth smoke-test (Keycloak client_credentials, RS256 JWT)
  2. validator-info   fetch the validator operator party  <-- this is the preapproval `provider`
  3. party            allocate your party (custodial; --external for the topology route)
  4. acs              read the active contract set for any interface
  5. preapproval      create the TransferPreapprovalProposal (your half of auto-accept)
  6. pending          check for incoming Canton Coin waiting as a TransferInstruction
  7. balance          sum your Holding UTXOs
  8. transfer         Token Standard transfer to another party

Setup:
  pip install httpx cryptography
  export CN_CLIENT_SECRET='<secret from the lab sheet>'

Examples:
  python3 canton_lab.py token
  python3 canton_lab.py validator-info
  python3 canton_lab.py party --hint farmfort
  python3 canton_lab.py balance --party '<partyId>'
  python3 canton_lab.py preapproval --party '<partyId>'           # auto-fetches provider
  python3 canton_lab.py preapproval --party '<partyId>' --provider '<operator party>'
  python3 canton_lab.py pending --party '<partyId>'
  python3 canton_lab.py transfer --sender '<you>' --receiver '<them>' --amount 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any, Optional

import httpx

# --------------------------------------------------------------------------- #
# Config — DevNet coordinates from the lab sheet; override via env vars.       #
# --------------------------------------------------------------------------- #
IDP_BASE      = os.getenv("CN_IDP_BASE",     "https://auth.dev.digik.cantor8.tech")
IDP_REALM     = os.getenv("CN_IDP_REALM",    "master")
CLIENT_ID     = os.getenv("CN_CLIENT_ID",    "hackathon")
CLIENT_SECRET = os.getenv("CN_CLIENT_SECRET", "")
SCOPE         = os.getenv("CN_SCOPE",        "")          # set "openid" if auth 400s

VALIDATOR = os.getenv("CN_VALIDATOR_BASE", "https://api.validator.dev.digik.cantor8.tech/api/validator")
LEDGER    = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")

# Token-Standard / wallet identifiers (stable).
HOLDING_IFACE  = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"
TXINSTR_IFACE  = "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferInstruction"
FACTORY_IFACE  = "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferFactory"
PREAPPROVAL    = "#splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapprovalProposal"

# Needed only for transfers — get from the team (CC registry + DSO/instrument admin).
REGISTRY_BASE  = os.getenv("CN_REGISTRY_BASE", "")
INSTRUMENT_ADMIN = os.getenv("CN_INSTRUMENT_ADMIN", "")
INSTRUMENT_ID    = os.getenv("CN_INSTRUMENT_ID", "Amulet")


class Canton:
    def __init__(self) -> None:
        self.http = httpx.Client(timeout=60.0)
        self._tok: Optional[tuple[str, float]] = None

    # ---- auth ------------------------------------------------------------- #
    def token(self) -> str:
        if self._tok and time.time() < self._tok[1] - 30:
            return self._tok[0]
        if not CLIENT_SECRET:
            sys.exit("Set CN_CLIENT_SECRET in your shell (the secret from the lab sheet).")
        data = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
        if SCOPE:
            data["scope"] = SCOPE
        r = self.http.post(f"{IDP_BASE}/realms/{IDP_REALM}/protocol/openid-connect/token", data=data)
        r.raise_for_status()
        b = r.json()
        self._tok = (b["access_token"], time.time() + int(b.get("expires_in", 600)))
        return self._tok[0]

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}

    def _post(self, base: str, path: str, payload: dict, auth: bool = True) -> Any:
        headers = self._h() if auth else {"Content-Type": "application/json"}
        r = self.http.post(f"{base}{path}", headers=headers, json=payload)
        self._raise(r, path)
        return r.json()

    def _get(self, base: str, path: str, auth: bool = True) -> Any:
        headers = self._h() if auth else {}
        r = self.http.get(f"{base}{path}", headers=headers)
        self._raise(r, path)
        return r.json()

    @staticmethod
    def _raise(r: httpx.Response, path: str) -> None:
        if r.status_code < 400:
            return
        print(f"\n!! {path} -> {r.status_code}\n{r.text}\n", file=sys.stderr)
        if r.status_code == 403:
            print("403 here usually means your token can't actAs that party. Ask the team to\n"
                  "grant your hackathon user actAs rights on your party (UserManagementService),\n"
                  "or confirm which user/party your token is allowed to act as.", file=sys.stderr)
        r.raise_for_status()

    # ---- validator operator (the preapproval `provider`) ------------------ #
    def validator_info(self) -> dict:
        """GET /v0/validator-user — public, no auth. Returns the operator party."""
        return self._get(VALIDATOR, "/v0/validator-user", auth=False)

    def provider_party(self) -> Optional[str]:
        info = self.validator_info()
        for k in ("party_id", "party", "validator_party", "partyId"):
            if isinstance(info, dict) and info.get(k):
                return info[k]
        return None

    # ---- party allocation ------------------------------------------------- #
    def allocate_internal(self, hint: str) -> str:
        b = self._post(LEDGER, "/v2/parties", {"partyIdHint": hint, "identityProviderId": ""})
        party = b["partyDetails"]["party"]
        print(f"internal party: {party}")
        return party

    def allocate_external(self, hint: str) -> str:
        """Documented topology route: generate -> sign (ed25519) -> submit.

        Field shapes confirmed from the Splice validator OpenAPI:
          generate <- {party_hint, public_key(hex)}  -> {party_id, topology_txs:[{topology_tx,hash}]}
          submit   <- {public_key(hex), signed_topology_txs:[{topology_tx, signed_hash(hex)}]}
        """
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        priv = Ed25519PrivateKey.generate()
        pub_hex = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()

        gen = self._post(VALIDATOR, "/v0/admin/external-party/topology/generate",
                         {"party_hint": hint, "public_key": pub_hex})
        signed = []
        for tx in gen["topology_txs"]:
            sig_hex = priv.sign(bytes.fromhex(tx["hash"])).hex()       # ed25519 sig = r||s
            signed.append({"topology_tx": tx["topology_tx"], "signed_hash": sig_hex})

        sub = self._post(VALIDATOR, "/v0/admin/external-party/topology/submit",
                        {"public_key": pub_hex, "signed_topology_txs": signed})
        party = sub["party_id"]

        keyfile = f"{hint}.ed25519.key"
        with open(keyfile, "wb") as f:
            f.write(priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        print(f"external party: {party}\nprivate key saved -> {keyfile} (keep it — non-custodial)")
        return party

    # ---- commands --------------------------------------------------------- #
    def submit(self, commands: list[dict], act_as: list[str],
               disclosed: Optional[list] = None) -> Any:
        body: dict[str, Any] = {
            "commands": commands,
            "commandId": f"lab-{uuid.uuid4()}",
            "actAs": act_as,
        }
        if disclosed:
            body["disclosedContracts"] = disclosed
        return self._post(LEDGER, "/v2/commands/submit-and-wait", body)

    def create(self, template_id: str, args: dict, act_as: list[str]) -> Any:
        return self.submit([{"CreateCommand": {"templateId": template_id, "createArguments": args}}], act_as)

    def exercise(self, template_id: str, cid: str, choice: str, arg: dict,
                 act_as: list[str], disclosed: Optional[list] = None) -> Any:
        return self.submit([{"ExerciseCommand": {"templateId": template_id, "contractId": cid,
                                                  "choice": choice, "choiceArgument": arg}}],
                           act_as, disclosed)

    # ---- ledger state ----------------------------------------------------- #
    def ledger_end(self) -> int:
        return self._get(LEDGER, "/v2/state/ledger-end")["offset"]

    def acs(self, party: str, interface_id: str, offset: Optional[int] = None) -> list[dict]:
        offset = offset if offset is not None else self.ledger_end()
        body = {
            "eventFormat": {
                "filtersByParty": {party: {"cumulative": [{
                    "identifierFilter": {"InterfaceFilter": {"value": {
                        "interfaceId": interface_id,
                        "includeInterfaceView": True,
                        "includeCreatedEventBlob": True,
                    }}}
                }]}},
                "verbose": False,
            },
            "activeAtOffset": offset,
        }
        rows = self._post(LEDGER, "/v2/state/active-contracts", body)
        out = []
        for row in rows:
            ac = row.get("contractEntry", {}).get("JsActiveContract")
            if ac:
                out.append(ac["createdEvent"])
        return out

    def _views(self, events: list[dict], interface_id: str) -> list[dict]:
        out = []
        for ev in events:
            for v in ev.get("interfaceViews", []):
                if v.get("interfaceId") == interface_id and v.get("viewValue"):
                    out.append({"cid": ev.get("contractId"), **v["viewValue"]})
        return out

    # ---- balance ---------------------------------------------------------- #
    def balance(self, party: str, poll: bool = True) -> float:
        for _ in range(12 if poll else 1):
            hs = self._views(self.acs(party, HOLDING_IFACE), HOLDING_IFACE)
            if hs:
                total = sum(float(h["amount"]) for h in hs if h.get("amount") is not None)
                print(f"{len(hs)} Holding UTXO(s), total = {total}")
                for h in hs:
                    print(f"  {h.get('amount'):>14}  {h.get('instrumentId')}  ({str(h['cid'])[:18]}…)")
                return total
            if poll:
                print("no holdings yet (PQS lag) — retrying in 30s…")
                time.sleep(30)
        print("balance: 0 (no Holding contracts found)")
        return 0.0

    # ---- pending incoming transfers --------------------------------------- #
    def pending(self, party: str) -> list[dict]:
        instrs = self._views(self.acs(party, TXINSTR_IFACE), TXINSTR_IFACE)
        if not instrs:
            print("no pending TransferInstructions for this party.")
            print("(If the team sent coin and nothing's here, it likely landed directly — check balance.)")
            return []
        print(f"{len(instrs)} pending incoming transfer(s):")
        for t in instrs:
            print(json.dumps(t, indent=2))
        print("\nThese are sitting waiting. Accepting one needs the registry context — "
              "tell me and we wire the accept choice.")
        return instrs

    # ---- preapproval (your half) ------------------------------------------ #
    def preapproval(self, party: str, provider: Optional[str]) -> Any:
        if not provider:
            provider = self.provider_party()
            if provider:
                print(f"resolved provider (validator operator) = {provider}")
            else:
                sys.exit("Couldn't auto-resolve the provider party from /v0/validator-user.\n"
                         "Pass it explicitly: --provider '<validator operator party>'")
        args = {"receiver": party, "provider": provider}   # confirmed: no expiresAt field
        print(f"creating TransferPreapprovalProposal\n  receiver = {party}\n  provider = {provider}")
        res = self.create(PREAPPROVAL, args, act_as=[party])
        print(json.dumps(res, indent=2))
        print("\nProposal created. The validator operator now accepts it on their side\n"
              "(that's what turns it into a live TransferPreapproval and auto-accepts CC).")
        return res

    # ---- token standard transfer ------------------------------------------ #
    def transfer(self, sender: str, receiver: str, amount: str) -> Any:
        if not REGISTRY_BASE:
            sys.exit("Set CN_REGISTRY_BASE (CC registry URL from the team) for transfers.")
        factory = self.http.post(
            f"{REGISTRY_BASE}/registry/transfer-instruction/v1/transfer-factory",
            headers=self._h(),
            json={"sender": sender, "receiver": receiver, "amount": amount,
                  "instrumentId": {"admin": INSTRUMENT_ADMIN, "id": INSTRUMENT_ID}})
        factory.raise_for_status()
        f = factory.json()
        arg = {
            "expectedAdmin": INSTRUMENT_ADMIN,
            "transfer": {"sender": sender, "receiver": receiver, "amount": amount,
                         "instrumentId": {"admin": INSTRUMENT_ADMIN, "id": INSTRUMENT_ID}},
            "extraArgs": {"context": f.get("choiceContextData", {}), "meta": {}},
        }
        res = self.exercise(FACTORY_IFACE, f["factoryId"], "TransferFactory_Transfer",
                           arg, act_as=[sender], disclosed=f.get("disclosedContracts", []))
        print(json.dumps(res, indent=2))
        return res


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Canton DevNet lab runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("token")
    sub.add_parser("validator-info")
    pp = sub.add_parser("party");        pp.add_argument("--hint", default="lab")
    pp.add_argument("--external", action="store_true")
    pa = sub.add_parser("acs");          pa.add_argument("--party", required=True)
    pa.add_argument("--interface", default=HOLDING_IFACE)
    pb = sub.add_parser("balance");      pb.add_argument("--party", required=True)
    pn = sub.add_parser("pending");      pn.add_argument("--party", required=True)
    pr = sub.add_parser("preapproval");  pr.add_argument("--party", required=True)
    pr.add_argument("--provider", default=None)
    pt = sub.add_parser("transfer")
    pt.add_argument("--sender", required=True); pt.add_argument("--receiver", required=True)
    pt.add_argument("--amount", required=True)

    a = p.parse_args()
    cn = Canton()

    if a.cmd == "token":
        print(cn.token()[:40] + "…  (ok)")
    elif a.cmd == "validator-info":
        info = cn.validator_info()
        print(json.dumps(info, indent=2))
        prov = cn.provider_party()
        if prov:
            print(f"\n→ use this as the preapproval provider:\n{prov}")
    elif a.cmd == "party":
        cn.allocate_external(a.hint) if a.external else cn.allocate_internal(a.hint)
    elif a.cmd == "acs":
        print(json.dumps(cn.acs(a.party, a.interface), indent=2))
    elif a.cmd == "balance":
        cn.balance(a.party)
    elif a.cmd == "pending":
        cn.pending(a.party)
    elif a.cmd == "preapproval":
        cn.preapproval(a.party, a.provider)
    elif a.cmd == "transfer":
        cn.transfer(a.sender, a.receiver, a.amount)


if __name__ == "__main__":
    main()
