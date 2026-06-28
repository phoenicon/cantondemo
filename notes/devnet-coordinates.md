# DevNet coordinates discovered during the lab

- External party (non-custodial, key held locally):
  farmfort2::1220f67dbd62e241ed9d1936ceb879ec06a122cf64999a58a7f5e1838e1a1e0faaf2
- Validator operator (preapproval provider), from /v0/validator-user:
  cantor8-digik-1::12204e94c0e449c0efcd270dd1e68259c36471cebef132e5c7dfc2750fe8c9eed77f
- Synchronizer: global-domain::1220be58c29e65de40bf273be1dc2b266d43a9a002ea5b18955aeef7aac881bb471a
- DSO / instrument admin: DSO::1220be58c29e65de40bf273be1dc2b266d43a9a002ea5b18955aeef7aac881bb471a
- Instrument: Amulet (Canton Coin, CC, 10 decimals)
- Registry base: https://api.validator.dev.digik.cantor8.tech/api/validator/v0/scan-proxy

Transfer config:
  export CN_REGISTRY_BASE='https://api.validator.dev.digik.cantor8.tech/api/validator/v0/scan-proxy'
  export CN_INSTRUMENT_ADMIN='DSO::1220be58c29e65de40bf273be1dc2b266d43a9a002ea5b18955aeef7aac881bb471a'
  export CN_INSTRUMENT_ID='Amulet'
