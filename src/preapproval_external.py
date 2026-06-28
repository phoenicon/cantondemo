import argparse, base64, json, os, sys, uuid
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

IDP_BASE  = os.getenv("CN_IDP_BASE", "https://auth.dev.digik.cantor8.tech")
REALM     = os.getenv("CN_IDP_REALM", "master")
CLIENT_ID = os.getenv("CN_CLIENT_ID", "hackathon")
SECRET    = os.getenv("CN_CLIENT_SECRET", "")
SCOPE     = os.getenv("CN_SCOPE", "")
VALIDATOR = "https://api.validator.dev.digik.cantor8.tech/api/validator"
LEDGER    = "https://api.validator.dev.digik.cantor8.tech/api/ledger"
PREAPPROVAL = "#splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapprovalProposal"
http = httpx.Client(timeout=60.0)

def die(r, where):
    if r.status_code >= 400:
        print(f"\n!! {where} -> {r.status_code}\n{r.text}\n", file=sys.stderr); r.raise_for_status()

def token():
    data = {"grant_type":"client_credentials","client_id":CLIENT_ID,"client_secret":SECRET}
    if SCOPE: data["scope"]=SCOPE
    r = http.post(f"{IDP_BASE}/realms/{REALM}/protocol/openid-connect/token", data=data)
    r.raise_for_status(); return r.json()["access_token"]

def jwt_sub(tok):
    p = tok.split(".")[1]; p += "="*(-len(p)%4)
    return json.loads(base64.urlsafe_b64decode(p)).get("sub","")

ap = argparse.ArgumentParser()
ap.add_argument("--party", required=True)
ap.add_argument("--keyfile", required=True)
ap.add_argument("--provider", default=None)
a = ap.parse_args()

tok = token()
H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
user_id = jwt_sub(tok)
fingerprint = a.party.split("::",1)[1]
priv = serialization.load_pem_private_key(open(a.keyfile,"rb").read(), password=None)

provider = a.provider
if not provider:
    info = http.get(f"{VALIDATOR}/v0/validator-user").json()
    provider = info.get("party_id") or info.get("party")
print("provider =", provider)

sync = http.get(f"{LEDGER}/v2/state/connected-synchronizers", headers=H); die(sync,"sync")
synchronizer_id = sync.json()["connectedSynchronizers"][0]["synchronizerId"]
print("synchronizer =", synchronizer_id)

prepare_body = {
    "userId": user_id,
    "commandId": f"preapproval-{uuid.uuid4()}",
    "actAs": [a.party],
    "readAs": [],
    "synchronizerId": synchronizer_id,
    "disclosedContracts": [],
    "packageIdSelectionPreference": [],
    "commands": [{"CreateCommand": {"templateId": PREAPPROVAL,
        "createArguments": {"receiver": a.party, "provider": provider}}}],
}
pr = http.post(f"{LEDGER}/v2/interactive-submission/prepare", headers=H, json=prepare_body)
die(pr,"prepare")
prepared = pr.json()
tx = prepared["preparedTransaction"]
h = prepared["preparedTransactionHash"]
scheme = prepared.get("hashingSchemeVersion","HASHING_SCHEME_VERSION_V2")
print("prepared OK - signing...")

sig = priv.sign(base64.b64decode(h))
execute_body = {
    "preparedTransaction": tx,
    "hashingSchemeVersion": scheme,
    "userId": user_id,
    "submissionId": str(uuid.uuid4()),
    "deduplicationPeriod": {"Empty": {}},
    "partySignatures": {"signatures": [{"party": a.party, "signatures": [{
        "format": "SIGNATURE_FORMAT_CONCAT",
        "signature": base64.b64encode(sig).decode(),
        "signedBy": fingerprint,
        "signingAlgorithmSpec": "SIGNING_ALGORITHM_SPEC_ED25519"}]}]},
}
ex = http.post(f"{LEDGER}/v2/interactive-submission/execute", headers=H, json=execute_body)
die(ex,"execute")
print(json.dumps(ex.json(), indent=2))
print("\nSubmitted - preapproval proposal should be on-ledger now.")
