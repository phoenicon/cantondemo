#!/usr/bin/env python3
"""
canton_lab.py — "Touching the Ledger" Canton low-level lab runner.

Walks the full hackathon flow against the DevNet validator:
  1. get JWT (Keycloak client_credentials)
  2. create a Party
  3. set up a TransferPreapproval
  4. read the ACS / ledger-end
  5. (team airdrops you Canton Coin)
  6. read your balance (sum of Holding contracts)
  7. Token-Standard transfer to another Party

Solid, runnable parts: JWT, ledger-end, ACS query + Holding balance, generic
submit-and-wait command runner, transfer-factory exercise structure.

Three things you must fill from the team / node before steps 2b, 3, 7 work end to end:
  * EXTERNAL-PARTY topology request/response field names  -> from /docs/openapi or the team
  * TransferPreapprovalProposal create-args               -> from the splice-wallet DAR
  * REGISTRY_BASE_URL + INSTRUMENT_ADMIN / INSTRUMENT_ID  -> from the team (CC registry)
They're marked  # TODO(lab):  and default to env vars so you never hard-code secrets.

Run:
  pip install httpx cryptography
  export CN_CLIENT_SECRET='...'            # given to you at the lab
  python canton_lab.py token               # smoke-test auth
  python canton_lab.py party --hint farmfort
  python canton_lab.py preapproval --party '<partyId>'
  python canton_lab.py balance --party '<partyId>'
  python canton_lab.py transfer --sender '<partyId>' --receiver '<otherPartyId>' --amount 10
  python canton_lab.py all --hint farmfort  # runs 1->6, prints partyId, waits for you
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

# --------------------------------------------------------------------------- #
# CONFIG  — defaults are the DevNet coordinates from the lab sheet.            #
# Override any of these with environment variables; never commit secrets.     #
# --------------------------------------------------------------------------- #
IDP_BASE        = os.getenv("CN_IDP_BASE",       "https://auth.dev.digik.cantor8.tech")
IDP_REALM       = os.getenv("CN_IDP_REALM",      "master")
CLIENT_ID       = os.getenv("CN_CLIENT_ID",      "hackathon")
CLIENT_SECRET   = os.getenv("CN_CLIENT_SECRET",  "")            # <-- set this in your shell
SCOPE           = os.getenv("CN_SCOPE",          "")            # some setups need "openid"

ADMIN_BASE      = os.getenv("CN_ADMIN_BASE",     "https://api.validator.dev.digik.cantor8.tech/api/validator")
LEDGER_BASE     = os.getenv("CN_LEDGER_BASE",    "https://api.validator.dev.digik.cantor8.tech/api/ledger")

# Token-Standard interface ids (stable across the standard).
HOLDING_IFACE   = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"
TRANSFER_FACTORY_IFACE = "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferFactory"

# TODO(lab): get these from the team. For Canton Coin the admin is the DSO party
# and the instrument id is "Amulet"; the registry base is the SV/Scan URL.
REGISTRY_BASE_URL = os.getenv("CN_REGISTRY_BASE", "")   # e.g. https://scan.sv.dev.digik.cantor8.tech/api/...
INSTRUMENT_ADMIN  = os.getenv("CN_INSTRUMENT_ADMIN", "")  # DSO party id
INSTRUMENT_ID     = os.getenv("CN_INSTRUMENT_ID", "Amulet")

# The userId the JWT authenticates as (used as actAs/userId in commands).
# For a custodial/internal party you usually actAs the party you allocated.
USER_ID = os.getenv("CN_USER_ID", "")  # often blank -> derived; set if the team tells you


# --------------------------------------------------------------------------- #
# Client                                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class Token:
    access_token: str
    expires_at: float


class Canton:
    def __init__(self) -> None:
        if not CLIENT_SECRET:
            sys.exit("Set CN_CLIENT_SECRET in your environment (the secret from the lab sheet).")
        self.http = httpx.Client(timeout=30.0)
        self._token: Optional[Token] = None

    # ---- 1. auth ---------------------------------------------------------- #
    def token(self) -> str:
        """Cache + refresh a client_credentials JWT."""
        if self._token and time.time() < self._token.expires_at - 30:
            return self._token.access_token
        data = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        if SCOPE:
            data["scope"] = SCOPE
        url = f"{IDP_BASE}/realms/{IDP_REALM}/protocol/openid-connect/token"
        r = self.http.post(url, data=data)
        r.raise_for_status()
        body = r.json()
        self._token = Token(
            access_token=body["access_token"],
            expires_at=time.time() + int(body.get("expires_in", 600)),
        )
        return self._token.access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}

    def _post(self, base: str, path: str, payload: dict[str, Any]) -> Any:
        r = self.http.post(f"{base}{path}", headers=self._headers(), json=payload)
        if r.status_code >= 400:
            print(f"\n!! {path} -> {r.status_code}\n{r.text}\n", file=sys.stderr)
        r.raise_for_status()
        return r.json()

    def _get(self, base: str, path: str) -> Any:
        r = self.http.get(f"{base}{path}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    # ---- 4. ledger state -------------------------------------------------- #
    def ledger_end(self) -> int:
        """Latest offset, needed as activeAtOffset for ACS queries."""
        body = self._get(LEDGER_BASE, "/v2/state/ledger-end")
        # response shape: {"offset": <int>}
        return body["offset"]

    # ---- 2a. quick custodial party (the easy path to keep moving) --------- #
    def allocate_internal_party(self, hint: str) -> str:
        """POST /v2/parties — a custodial/internal party the validator signs for.

        The lab sheet points you at the EXTERNAL-party topology route instead
        (create_external_party below). Use this only if you've confirmed an
        internal/custodial party is acceptable, or to unblock yourself.
        """
        body = self._post(LEDGER_BASE, "/v2/parties",
                          {"partyIdHint": hint, "identityProviderId": ""})
        party = body["partyDetails"]["party"]
        print(f"internal party: {party}")
        return party

    # ---- 2b. documented path: external (non-custodial) party -------------- #
    def create_external_party(self, hint: str) -> str:
        """generate -> sign -> submit against the validator Admin API.

        ⚠ The exact request/response field names for the Splice external-party
        topology endpoints aren't guaranteed here. Confirm them from the node's
        /docs/openapi or the team, then adjust the marked lines. Everything is
        printed verbosely so you can see the real shapes and adapt fast.
        """
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            sys.exit("pip install cryptography  (needed for external-party signing)")

        priv = Ed25519PrivateKey.generate()
        pub_raw = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        import base64
        pub_b64 = base64.b64encode(pub_raw).decode()

        # TODO(lab): confirm field names ("party_hint" / "public_key" / "public_key_format").
        gen = self._post(ADMIN_BASE, "/v0/admin/external-party/topology/generate",
                         {"party_hint": hint, "public_key": pub_b64})
        print("generate response:\n", json.dumps(gen, indent=2))

        # TODO(lab): the generate response contains transaction(s) + a hash to sign.
        # Confirm the field that holds the bytes-to-sign and the submit body shape.
        to_sign_b64 = gen.get("combined_hash") or gen.get("topology_transaction_hash")
        if not to_sign_b64:
            sys.exit("Couldn't find the hash-to-sign in the generate response — "
                     "inspect the printed JSON above and set to_sign_b64.")
        signature = priv.sign(base64.b64decode(to_sign_b64))
        sig_b64 = base64.b64encode(signature).decode()

        sub = self._post(ADMIN_BASE, "/v0/admin/external-party/topology/submit",
                        {"party_hint": hint,
                         "public_key": pub_b64,
                         "signed_topology_txs": [{"signature": sig_b64}],  # TODO(lab): shape
                         "topology_txs": gen.get("topology_txs", [])})
        print("submit response:\n", json.dumps(sub, indent=2))
        party = sub.get("party_id") or sub.get("partyId")
        print(f"external party: {party}")
        # keep the private key — you'll need it to prepare/execute transfers later
        with open(f"{hint}.ed25519.key", "wb") as f:
            f.write(priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
        print(f"private key saved -> {hint}.ed25519.key  (keep it; non-custodial = you hold it)")
        return party

    # ---- generic command submission --------------------------------------- #
    def submit_and_wait(self, commands: list[dict], act_as: list[str],
                        command_id: Optional[str] = None,
                        disclosed: Optional[list[dict]] = None) -> Any:
        payload: dict[str, Any] = {
            "commands": commands,
            "commandId": command_id or f"lab-{uuid.uuid4()}",
            "actAs": act_as,
        }
        if USER_ID:
            payload["userId"] = USER_ID
        if disclosed:
            payload["disclosedContracts"] = disclosed
        return self._post(LEDGER_BASE, "/v2/commands/submit-and-wait", payload)

    def create_contract(self, template_id: str, args: dict, act_as: list[str]) -> Any:
        return self.submit_and_wait(
            [{"CreateCommand": {"templateId": template_id, "createArguments": args}}],
            act_as)

    def exercise(self, template_id: str, contract_id: str, choice: str,
                 argument: dict, act_as: list[str],
                 disclosed: Optional[list[dict]] = None) -> Any:
        return self.submit_and_wait(
            [{"ExerciseCommand": {"templateId": template_id, "contractId": contract_id,
                                  "choice": choice, "choiceArgument": argument}}],
            act_as, disclosed=disclosed)

    # ---- ACS query by interface ------------------------------------------- #
    def active_contracts(self, party: str, interface_id: str,
                         offset: Optional[int] = None) -> list[dict]:
        offset = offset if offset is not None else self.ledger_end()
        payload = {
            "eventFormat": {
                "filtersByParty": {
                    party: {
                        "cumulative": [{
                            "identifierFilter": {"InterfaceFilter": {"value": {
                                "interfaceId": interface_id,
                                "includeInterfaceView": True,
                                "includeCreatedEventBlob": True,
                            }}}
                        }]
                    }
                },
                "verbose": False,
            },
            "activeAtOffset": offset,
        }
        rows = self._post(LEDGER_BASE, "/v2/state/active-contracts", payload)
        out = []
        for row in rows:
            entry = row.get("contractEntry", {})
            ac = entry.get("JsActiveContract")
            if ac:
                out.append(ac["createdEvent"])
        return out

    # ---- 6. balance from Holding contracts -------------------------------- #
    def holdings(self, party: str) -> list[dict]:
        events = self.active_contracts(party, HOLDING_IFACE)
        holdings = []
        for ev in events:
            for view in ev.get("interfaceViews", []):
                if view.get("interfaceId") == HOLDING_IFACE and view.get("viewValue"):
                    v = view["viewValue"]
                    holdings.append({
                        "cid": ev.get("contractId"),
                        "amount": v.get("amount"),
                        "instrumentId": v.get("instrumentId"),
                    })
        return holdings

    def balance(self, party: str, poll: bool = True) -> float:
        # PQS lag: holdings can take a few minutes (up to ~15) to show after an airdrop.
        for attempt in range(12 if poll else 1):
            hs = self.holdings(party)
            if hs:
                total = sum(float(h["amount"]) for h in hs if h["amount"] is not None)
                print(f"{len(hs)} Holding UTXO(s), total = {total}")
                for h in hs:
                    print(f"  {h['amount']:>14}  {h['instrumentId']}  ({h['cid'][:18]}…)")
                return total
            if poll:
                print("no holdings yet (PQS lag) — retrying in 30s…")
                time.sleep(30)
        print("balance: 0 (no Holding contracts found)")
        return 0.0

    # ---- 3. TransferPreapproval ------------------------------------------- #
    def setup_preapproval(self, party: str) -> Any:
        template = "splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapprovalProposal"
        # TODO(lab): fill the real create-args from the splice-wallet DAR.
        args = {
            "receiver": party,
            # "provider": <validator party>,
            # "expiresAt": <timestamp>,
            # ...confirm fields from the template.
        }
        print(f"creating {template} for {party} …")
        res = self.create_contract(template, args, act_as=[party])
        print(json.dumps(res, indent=2))
        return res

    # ---- 7. Token-Standard transfer (factory choice) ---------------------- #
    def fetch_transfer_factory(self, sender: str, receiver: str, amount: str) -> dict:
        """Ask the registry for a TransferFactory.

        Returns {factoryId, disclosedContracts, choiceContextData}.
        """
        if not REGISTRY_BASE_URL:
            sys.exit("Set CN_REGISTRY_BASE (the CC registry URL from the team).")
        # TODO(lab): confirm the transfer-factory request body the registry expects.
        payload = {
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "instrumentId": {"admin": INSTRUMENT_ADMIN, "id": INSTRUMENT_ID},
        }
        r = self.http.post(
            f"{REGISTRY_BASE_URL}/registry/transfer-instruction/v1/transfer-factory",
            headers=self._headers(), json=payload)
        r.raise_for_status()
        return r.json()

    def transfer(self, sender: str, receiver: str, amount: str) -> Any:
        f = self.fetch_transfer_factory(sender, receiver, amount)
        factory_id   = f["factoryId"]
        disclosed    = f.get("disclosedContracts", [])
        context      = f.get("choiceContextData", {})
        argument = {
            "expectedAdmin": INSTRUMENT_ADMIN,
            "transfer": {
                "sender": sender,
                "receiver": receiver,
                "amount": amount,
                "instrumentId": {"admin": INSTRUMENT_ADMIN, "id": INSTRUMENT_ID},
                # "requestedAt" / "executeBefore" / "inputHoldingCids": confirm from the std
            },
            "extraArgs": {"context": context, "meta": {}},
        }
        print(f"transfer {amount} from {sender[:18]}… to {receiver[:18]}…")
        res = self.exercise(TRANSFER_FACTORY_IFACE, factory_id, "TransferFactory_Transfer",
                            argument, act_as=[sender], disclosed=disclosed)
        print(json.dumps(res, indent=2))
        return res


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description="Canton lab runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("token")
    pp = sub.add_parser("party");   pp.add_argument("--hint", default="lab")
    pp.add_argument("--external", action="store_true", help="use external-party topology route")
    pe = sub.add_parser("preapproval"); pe.add_argument("--party", required=True)
    pb = sub.add_parser("balance"); pb.add_argument("--party", required=True)
    pa = sub.add_parser("acs");     pa.add_argument("--party", required=True)
    pa.add_argument("--interface", default=HOLDING_IFACE)
    pt = sub.add_parser("transfer")
    pt.add_argument("--sender", required=True); pt.add_argument("--receiver", required=True)
    pt.add_argument("--amount", required=True)
    pall = sub.add_parser("all");   pall.add_argument("--hint", default="lab")
    pall.add_argument("--external", action="store_true")

    a = p.parse_args()
    cn = Canton()

    if a.cmd == "token":
        print(cn.token()[:40] + "…  (ok)")
    elif a.cmd == "party":
        cn.create_external_party(a.hint) if a.external else cn.allocate_internal_party(a.hint)
    elif a.cmd == "preapproval":
        cn.setup_preapproval(a.party)
    elif a.cmd == "balance":
        cn.balance(a.party)
    elif a.cmd == "acs":
        print(json.dumps(cn.active_contracts(a.party, a.interface), indent=2))
    elif a.cmd == "transfer":
        cn.transfer(a.sender, a.receiver, a.amount)
    elif a.cmd == "all":
        print("== 1. auth =="); print(cn.token()[:40] + "…")
        print("== 2. party ==")
        party = cn.create_external_party(a.hint) if a.external else cn.allocate_internal_party(a.hint)
        print("== 3. preapproval =="); cn.setup_preapproval(party)
        print("== 4. ledger-end =="); print("offset:", cn.ledger_end())
        print(f"\n>>> send this partyId to the team for a CC airdrop:\n{party}\n")
        input("Press Enter once they've airdropped you Canton Coin…")
        print("== 6. balance =="); cn.balance(party)
        print("\nNow run:  python canton_lab.py transfer --sender '<this party>' "
              "--receiver '<other party>' --amount 10")


if __name__ == "__main__":
    main()
