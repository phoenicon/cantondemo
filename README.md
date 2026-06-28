# Touching the Ledger

Low-level exploration of **Canton Network DevNet** through the Validator Admin API
and the JSON Ledger API — no wallet, no dApp, no SDK on the machine. Just the raw
ledger primitives a regulated RWA platform actually has to build on.

Built during the Canton hackathon, June 2026, as the groundwork for
[FarmFort](#farmfort-vision) — regulated farmland infrastructure on Canton.

> The full write-up, with the live trace and feedback for the lab team, is in
> **[`docs/canton-lab-report.html`](docs/canton-lab-report.html)**
> (published via GitHub Pages — see [below](#publishing-the-report)).

## What the lab covers

The "bare metal" Canton path — create a Party, install a preapproval contract,
inspect active contracts, receive Canton Coin, then make a Token Standard transfer.

```
Party  →  Validator  →  Ledger API  →  ACS  →  Holdings  →  Transfer
identity   access       commands       truth   balance      rail
```

## Progress

| Stage | Status | Notes |
|-------|--------|-------|
| JWT authentication | ✅ done | Keycloak `client_credentials`, RS256 token |
| Party allocation | ✅ done | custodial party via `/v2/parties` |
| ACS querying | ✅ done | `ledger-end` → `active-contracts` → Holding `InterfaceFilter` |
| TransferPreapproval | ⏳ ready | command path built; needs the template create-args |
| Receive Canton Coin | 🟡 awaiting | operator-distributed on DevNet; awaiting team airdrop |
| Token Standard transfer | ⏳ ready | factory flow wired; needs registry URL + DSO party |

Node verified live: **Canton 3.5.5** (confirmed via the participant's `/docs/openapi`).

## Party created

```
farmfort::12204e94c0e449c0efcd270dd1e68259c36471cebef132e5c7dfc2750fe8c9eed77f
```

## Quick start

```bash
pip install -r requirements.txt
export CN_CLIENT_SECRET='<the secret from the lab>'

python3 src/canton_lab.py token                    # smoke-test auth
python3 src/canton_lab.py party --hint farmfort     # allocate a party
python3 src/canton_lab.py acs --party '<partyId>'   # read the ACS (empty [] is correct)
python3 src/canton_lab.py balance --party '<partyId>'   # sums Holding UTXOs (auto-retries PQS lag)
```

Standalone, dependency-light versions of the first three calls live in
[`examples/`](examples/) if you'd rather read one flow at a time.

## Project structure

```
touching-the-ledger/
├── src/         core lab runner (canton_lab.py)
├── examples/    minimal single-purpose API scripts
├── docs/        the HTML lab report (served by GitHub Pages)
├── screenshots/ visual evidence (see screenshots/README.md for what to grab)
└── notes/       lessons learned + feedback for the lab team
```

## Publishing the report

GitHub Pages serves `/docs` directly:

1. Push this repo to GitHub.
2. **Settings → Pages → Deploy from branch → `main` → `/docs`.**
3. The report goes live at `https://<you>.github.io/touching-the-ledger/`.

## FarmFort vision

This explores the primitives a regulated **Real World Asset** platform needs on
Canton: on-ledger identity (Party), holdings as UTXOs, preapproval-based transfer
UX, and the Token Standard as a reusable settlement rail. The same shapes map
straight onto a farmland SPV issuing tokenised shares to investor parties under a
compliance gate — the next step is wrapping `TransferFactory_Transfer` in custom
Daml that enforces it.

## Licence

MIT — see [LICENSE](LICENSE).
