# Touching the Ledger

Low-level exploration of Canton Network DevNet through the Validator Admin API and the JSON Ledger API - no wallet, no dApp, no SDK. Raw ledger primitives, hand-rolled over JSON.

Built for the Cantor Bytes / Canton hackathon, June 2026.

## Progress

| Stage | Status | Notes |
|-------|--------|-------|
| JWT authentication | done | Keycloak client_credentials, RS256 token |
| Party allocation | done | external (non-custodial) via topology/{generate,submit}, self-signed ed25519 |
| ACS querying | done | ledger-end then active-contracts with InterfaceFilter |
| TransferPreapprovalProposal | DONE | created via interactive prepare -> sign -> execute; verified on-ledger |
| Receive Canton Coin | awaiting | validator accepts proposal + sends CC |
| Token Standard transfer | ready | factory wired; registry + DSO + Amulet instrument resolved |

Node: Canton 3.5.5.

## Key insight

A custodial party (/v2/parties) hits a 403 on submit-and-wait - no actAs rights. The fix, and the point of the lab, is an external party that authorizes by signing its own transactions. The preapproval goes through prepare -> sign (ed25519) -> execute instead. See src/preapproval_external.py.

## Party created

farmfort2::1220f67dbd62e241ed9d1936ceb879ec06a122cf64999a58a7f5e1838e1a1e0faaf2

## Structure

- src/canton_lab.py - runner
- src/preapproval_external.py - prepare/sign/execute flow
- examples/ - minimal API scripts
- docs/ - HTML lab report
- notes/ - lessons-learned + devnet-coordinates

MIT licence.
