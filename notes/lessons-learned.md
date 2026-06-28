# Lessons learned — Canton low-level lab

Field notes from running the "Touching the Ledger" lab purely over REST, with no
`daml`/`dpm` SDK installed. Captured while fresh.

## What worked first try

- **Auth.** Keycloak `client_credentials` against the DevNet IdP returns an RS256
  JWT with no surprises. Cache it and refresh ~30s before `expires_in`.
- **Party allocation.** `POST /v2/parties` with `{partyIdHint, identityProviderId:""}`
  gives back `partyDetails.party` immediately — a custodial party the validator signs for.
- **ACS read path.** `/v2/state/ledger-end` to get the offset, then
  `/v2/state/active-contracts` with an `InterfaceFilter` on the Holding interface.
  An empty `[]` before any coin arrives is the *correct* result, not an error.

## Things that cost time

- **"Internal party" vs the external-party endpoints.** The sheet describes an
  internal/custodial party but routes to `/v0/admin/external-party/topology/...`.
  These are different trust models (validator-signed vs you-hold-the-key). Pick the
  custodial `/v2/parties` path to keep moving unless the task specifically needs
  non-custodial.
- **Preapproval create-args aren't discoverable over REST.** The JSON API's
  `/docs/openapi` carries HTTP shapes, not Daml template fields — those live in the
  DAR. Without the SDK you have to get the `TransferPreapprovalProposal` args from
  the team or the DAR.
- **PQS lag is real.** After an airdrop, Holding contracts can take minutes (up to
  ~15) to appear while the Participant Query Store syncs. An empty balance during
  that window is expected — poll, don't panic.
- **Transfer needs off-sheet inputs.** The Token Standard transfer is a *factory*
  flow: fetch `TransferFactory` from the registry → exercise `TransferFactory_Transfer`
  with the returned `disclosedContracts` + `choiceContextData`. You need the registry
  base URL and the DSO / instrument-admin party, and the registry↔admin mapping is
  maintained client-side today.

## Confirmed environment

- Node: **Canton 3.5.5** (from the participant's `/docs/openapi`).
- Command shapes used by the runner (`JsCommands` → `SubmitAndWaitResponse`, the
  `/v2/...` paths) match the live spec.

## Mental model that stuck

```
Party              = identity (the real actor on Canton)
Validator          = access / control layer
Daml contracts     = state
ACS                = current truth
TransferPreapproval = auto-accept wallet UX
Holding contracts  = balance, in UTXO pieces
Token Standard      = reusable transfer rail
```

## For FarmFort

This is the primitive layer for regulated RWA: SPV issuer Party → investor Party →
tokenised share/loan as a Token Standard instrument → compliance gate wrapping
`TransferFactory_Transfer` in custom Daml. The plumbing is reusable; the
differentiation is the contract logic around the transfer.
