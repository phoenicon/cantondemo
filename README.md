# Touching the Ledger

A low-level walk through the **Canton Network DevNet** — driven entirely over the
Validator Admin API and the JSON Ledger API. **No wallet, no dApp, no SDK.** Raw
ledger primitives, hand-rolled over JSON, to understand exactly what those layers
normally hide.

Built for the Cantor8 / Canton hackathon ("Build on Canton", Track 2 — RWA), June 2026.
Node: **Canton 3.5.5**.

> **Result:** the full lab, end to end — **100 Canton Coin received and verified
> on-ledger**, then **20 of it transferred to a second, non-custodial party that signs
> its own transactions.** A complete round-trip between two parties we control.
>
> 📄 **[Read the lab report →](docs/canton-lab-report.html)**

---

## What got done

| # | Stage | Status | How |
|---|-------|--------|-----|
| 1 | Authenticate | ✅ | Keycloak `client_credentials`, RS256 JWT |
| 2 | Allocate a party | ✅ | custodial via `/v2/parties` **and** external (non-custodial) via `topology/{generate,submit}` with a self-held ed25519 key |
| 3 | Grant ledger rights | ✅ | self-grant `CanActAs` via `POST /v2/users/{sub}/rights` — the unlock |
| 4 | TransferPreapproval (proposal) | ✅ | `receiver` + `provider`; committed via `submit-and-wait` |
| 5 | Receive Canton Coin | ✅ | arrived as a `Splice.Wallet.TransferOffer`; found via a wildcard ACS dump |
| 6 | Accept the coin | ✅ | `TransferOffer_Accept` → **100 CC** as a Holding |
| 7 | Read balance | ✅ | `ledger-end` → `active-contracts`, summing Holding UTXOs |
| 8 | Token Standard transfer | ✅ | sent **20 CC** to the external party via `TransferFactory_Transfer`; receiver accepted by signing `TransferInstruction_Accept` with its own key |

---

## The two walls (and how each fell)

**1. Allocating a party doesn't grant you rights to it.**
A custodial party from `/v2/parties` returns `403` ("a security-sensitive error") on
`submit-and-wait` — the user has no `actAs` on it. The fix is a single self-grant of
`CanActAs` (the lab user happened to be a participant admin). This, not cryptography,
was the real blocker. See [`src/unblock.py`](src/unblock.py).

**2. The airdrop isn't where the inbox looks.**
The team sends CC as a `Splice.Wallet.TransferOffer` — **not** a Token Standard
`TransferInstruction`. They live in different mailboxes, so an interface-scoped inbox
check shows nothing. A wildcard ACS read finds it. See [`src/inbox.py`](src/inbox.py).

---

## Quickstart

```bash
pip install httpx cryptography
export CN_CLIENT_SECRET='<secret from the lab sheet>'   # never commit this

# 1. smoke-test auth
python src/canton_lab.py token

# 2. allocate a custodial party (note the partyId it prints)
python src/canton_lab.py party --hint farmfort

# 3. grant rights + create the preapproval, in one shot
python src/unblock.py --party '<partyId>'

# 4. see everything the party holds (finds an incoming TransferOffer)
python src/inbox.py '<partyId>'

# 5. accept the incoming Canton Coin
python src/accept.py --party '<partyId>'

# 6. confirm balance any time (read-only)
python src/verify.py --party '<partyId>'
```

Token Standard transfer to another party (optional Step 8):

```bash
export CN_REGISTRY_BASE='https://api.validator.dev.digik.cantor8.tech/api/validator/v0/scan-proxy'
export CN_INSTRUMENT_ADMIN='DSO::1220be58c29e65de40bf273be1dc2b266d43a9a002ea5b18955aeef7aac881bb471a'
export CN_INSTRUMENT_ID='Amulet'

# send from the custodial party
python src/send.py --sender '<custodialParty>' --receiver '<externalParty>' --amount 20

# the external (non-custodial) receiver accepts by signing with its own key
python src/accept_external.py --party '<externalParty>' --keyfile <externalParty>.ed25519.key
```

> Secrets and keys never touch the repo — `.gitignore` blocks `*.key` and `.env`,
> and `CN_CLIENT_SECRET` lives only in your shell.

---

## The scripts

| File | What it does |
|------|--------------|
| [`src/canton_lab.py`](src/canton_lab.py) | The runner — `token`, `validator-info`, `party` (`--external`), `acs`, `balance`, `pending`, `preapproval`, `transfer` |
| [`src/unblock.py`](src/unblock.py) | Self-grants `CanActAs`, then creates the TransferPreapproval — clears the 403 |
| [`src/inbox.py`](src/inbox.py) | Wildcard ACS dump — shows every contract a party holds |
| [`src/accept.py`](src/accept.py) | Accepts an incoming `Splice.Wallet.TransferOffer` |
| [`src/verify.py`](src/verify.py) | Read-only confirm of the preapproval + Holding balance |
| [`src/send.py`](src/send.py) | Token Standard `TransferFactory_Transfer` (two-phase factory context) |
| [`src/accept_external.py`](src/accept_external.py) | External party accepts a `TransferInstruction` by signing it itself |
| [`src/preapproval_external.py`](src/preapproval_external.py) | The interactive `prepare → sign → execute` flow for an external party |

---

## Mental model that stuck

```
Party               = identity (the real actor on Canton)
Validator           = access / control layer
Daml contracts      = state
ACS                 = current truth
TransferPreapproval = auto-accept wallet UX (a proposal until the provider accepts)
Holding contracts   = balance, in UTXO pieces
Token Standard      = reusable transfer rail
```

## Why this matters for Tokenise.Farm / FarmFort

This is the primitive layer for regulated RWA: SPV-issuer Party → investor Party →
tokenised share/loan as a Token Standard instrument → a compliance gate wrapping the
transfer. The plumbing here is reusable; the differentiation is the contract logic
around the transfer. This repo proves the plumbing can be driven by hand.

## More

- 📄 **[Lab report](docs/canton-lab-report.html)** — the visual trace + feedback for the lab team
- 📝 **[Full write-up](notes/lab-writeup.md)** — goal, problems, fixes, lessons
- 🗒️ **[Lessons learned](notes/lessons-learned.md)** · **[DevNet coordinates](notes/devnet-coordinates.md)**
- 🧪 [`examples/`](examples/) — minimal one-purpose API scripts

MIT licence.
