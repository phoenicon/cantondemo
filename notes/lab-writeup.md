# Touching the Ledger — Canton low-level lab, the full story

*A field write-up: what we set out to do, what broke, how we fixed it, and what it
proved. Written the night before the Monday launch.*

---

## 1. What we were trying to do

The Cantor8 **"Touching the Ledger"** workshop: interact with a permissioned Canton
DevNet validator **directly over its low-level Admin and Ledger REST APIs** — no
`daml`/`dpm` SDK, no wallet UI. The point isn't to use Canton; it's to understand
the mechanics that wallets and dApps normally hide from you.

The prescribed flow:

```
1. Get a JWT (Keycloak client_credentials)
2. Allocate a Party (your identity on Canton — like an address)
3. Set up a TransferPreapproval contract with the validator
4. Check your balance by reading the Active Contract Set (ACS)
5. Receive Canton Coin (CC) airdropped by the team
6. See the balance appear as Holding contracts (UTXO pieces)
7. (Optional) Token Standard transfer to another Party
```

**Why it matters to us:** this is the exact primitive layer Tokenise.Farm runs on.
SPV-issuer Party → investor Party → tokenised share/loan as a Token Standard
instrument → a compliance gate wrapped around the transfer. If we can drive these
APIs by hand, we can build the regulated-RWA plumbing on top of them. The lab was a
capability proof, not a tutorial box-tick.

---

## 2. How the pieces fit

| Concept | What it actually is |
|---|---|
| **Party** (`name::fingerprint`) | An identity on Canton. The fingerprint ties it to a signing key. |
| **Validator** | The node that hosts your Party and submits to the network on its behalf. |
| **Internal / custodial party** | Validator holds the key and signs for you (`/v2/parties`). |
| **External / non-custodial party** | *You* hold the key; you sign locally (topology route). |
| **TransferPreapproval** | A contract that says "auto-accept incoming CC for me" — the wallet UX you never see. |
| **ACS** (Active Contract Set) | The current truth of the ledger. You read balances out of it. |
| **Holding contracts** | Your balance, expressed as UTXO-style pieces, not one account number. |

---

## 3. The problems we hit (in order)

### Problem 0 — the script shipped with a hole
The lab runner (`canton_lab.py`) had the PreApproval step stubbed out:

```python
args = {"receiver": party}   # placeholder — missing fields, wrong template ref
```

The real Daml template fields aren't discoverable over REST (the OpenAPI carries
HTTP shapes, not Daml template fields — those live in the DAR). So the first wall
was simply *not knowing the payload*.

**Fix:** the correct create-arguments are just two fields, against the package-name
template ref:

```python
template = "#splice-wallet:Splice.Wallet.TransferPreapproval:TransferPreapprovalProposal"
args     = {"receiver": <yourParty>, "provider": <validator operator party>}
```

The `provider` auto-resolves from `GET /v0/validator-user`. No `expiresAt`, nothing else.

### Problem 1 — three diverged copies of the script
We had `canton_lab.py` in three places that had drifted apart; the *fixed* one was
sitting loose in `Downloads/` while the project copy still had the broken stub. So
"it works / it doesn't" depended on which directory you ran from.

**Fix:** consolidated onto the corrected version across all copies (old ones kept as
`*.bak-broken`).

### Problem 2 — the external party was never really born
The doc's prescribed route creates the Party via
`/v0/admin/external-party/topology/{generate,submit}` (the **non-custodial** path —
you hold the key). We did that and got back a party id + a saved key
(`farmfort2.ed25519.key`)… but every command against it failed:

```
prepare -> 404  UNKNOWN_INFORMEES
"The participant is not connected to any synchronizer where the given informees are known."
```

**Diagnosis:** a party id is *deterministically derived from the public key*, so the
endpoint hands you one even if onboarding didn't actually finish. Canton's rule:
**"if a topology transaction is not fully authorized, it is treated as a proposal."**
The party-to-participant hosting was never authorized, so the participant doesn't
host `farmfort2` — to the ledger it doesn't exist. The "success" print was a lie.

**Fix:** parked the non-custodial path (a clean daytime job) and switched to the
custodial party to keep moving — exactly what the lessons-learned notes already
advised.

### Problem 3 — the custodial party: 403, permission denied
The custodial party `farmfort::12204e94…` *is* known to the network, but:

```
/v2/commands/submit-and-wait -> 403
"A security-sensitive error has been received"   (PERMISSION_DENIED)
```

**Diagnosis:** allocating a party via `/v2/parties` does **not** automatically grant
your user the right to act as it. Our JWT user (`validator-backend@clients`) had
`CanActAs` on a pile of other parties — but not the freshly-created `farmfort`. So
the ledger refused the command, and deliberately gave a vague "security-sensitive"
error rather than leaking why.

**Fix (the key unlock):** grant our own user the right, because that user turned out
to be a **participant admin** (it could allocate parties, so it could also manage
rights). Confirmed the exact JSON shape from the Canton docs:

```http
POST /v2/users/{userId}/rights
{
  "rights": [
    { "kind": { "CanActAs": { "value": { "party": "farmfort::12204e94…" } } } }
  ]
}
```

After that, `submit-and-wait` went straight through:

```
create TransferPreapprovalProposal: ok
{ "updateId": "1220b4424f87…", "completionOffset": 2192501 }   ← committed on-ledger
```

### Problem 4 — a self-inflicted read-back scare
Our confirmation query reported `404 NO_INTERFACE_FOR_PACKAGE_NAME_AND_QUALIFIED_NAME`
and looked like a failure. It wasn't: `TransferPreapprovalProposal` is a **template**,
and we were querying it with an **interface** filter. The contract was fine; the
*question* was malformed.

**Fix:** a read-only `verify.py` that queries by **template**, tolerates
"never-seen-that-type" 404s as empty, and prints a plain-English status. It confirmed:

```
TransferPreapprovalProposal (your half): 1 found   ✅
  args: {receiver: farmfort::…, provider: cantor8-digik-1::…}
TransferPreapproval (ACCEPTED by provider): 0       ← waiting on the tutor
Holding balance: 0.0                                 ← waiting on the CC
```

---

## 4. What we ended up with

Three small, dependency-light scripts (just `httpx` + `cryptography`), in `src/`:

- **`canton_lab.py`** — the full lab runner: `token`, `validator-info`, `party`
  (`--external`), `acs`, `balance`, `pending`, `preapproval`, `transfer`.
- **`unblock.py`** — one-shot custodial fix: decode who we are → grant our user
  `CanActAs` on the party → resolve the provider → create the PreApproval → read it
  back. This is the script that broke the logjam.
- **`verify.py`** — read-only, safe to spam. Shows the proposal, whether the provider
  has accepted, and the live balance. Doubles as the CC tracker.

Working end-to-end flow (custodial path):

```bash
export CN_CLIENT_SECRET='…'                              # never committed (.gitignore blocks *.key/.env)
python src/canton_lab.py token                            # auth smoke-test
python src/unblock.py    --party 'farmfort::12204e94…'    # grant + create PreApproval
python src/verify.py     --party 'farmfort::12204e94…'    # confirm, then track CC
```

**State at write-up:** PreApproval proposal committed and verified on-ledger. Handed
the partyId to the tutor. Remaining steps are the provider's: accept the proposal →
send CC → balance appears. Our half is done.

---

## 5. Lessons (the part worth keeping)

1. **A returned identifier is not proof of success.** The external party handed us a
   party id while its onboarding had silently failed. Always verify the *state*
   (can the participant find it?), not just the *response*.
2. **"Created" ≠ "authorized."** In Canton, an under-signed topology transaction sits
   as a proposal forever. Non-custodial onboarding needs the participant's
   co-signature to actually host the party.
3. **Allocating a party doesn't grant you rights to it.** Custodial party creation and
   `CanActAs` are two separate steps. The 403 was a missing grant, not a broken key.
4. **Vague errors are deliberate.** "A security-sensitive error" = permission denied,
   detail withheld on purpose. Don't read meaning into the blankness; go check rights.
5. **Match the query to the type.** Templates use `TemplateFilter`, interfaces use
   `InterfaceFilter`. Holding is an interface; TransferPreapprovalProposal is a
   template. Half our "failures" were just the wrong filter.
6. **Take the custodial path to keep moving.** The non-custodial route is the "proper"
   one, but when you're blocked and time-boxed, the custodial party reaches the same
   learning outcome. Park the hard path for daylight.
7. **Keep the runner thin and the secret in the shell.** Three tiny scripts, env-var
   config, secrets never on disk. Easy to read, easy to hand to a mentor, nothing to
   leak.

---

## 6. Why this is a Tokenise.Farm asset, not just a hackathon tick

We now have, by hand: authenticate → allocate a party → grant it ledger rights →
compose and commit a real Daml transaction → read balances out of the ACS. That is
the skeleton of regulated tokenised land:

```
SPV issuer Party → investor Party
   → tokenised share/loan as a Token Standard instrument
   → compliance gate wrapping TransferFactory_Transfer in custom Daml
```

The plumbing is reusable. The differentiation is the contract logic we wrap around the
transfer. Tonight we proved we can drive the plumbing — the night before launch,
under pressure, from a cold stop. That's the story.
