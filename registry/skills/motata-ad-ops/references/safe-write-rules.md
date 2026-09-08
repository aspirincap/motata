# Safe Write Rules

Use these rules before any mutation.

## Defaults

1. Reads before writes
2. Discovery before references
3. Validation before create
4. Paused or equivalent non-live state by default
5. Smallest possible scope

## When A Write Is Safe Enough To Attempt

Usually safe:

- same-value or no-op style status checks on confirmed objects
- low-risk updates on explicitly named targets
- create flows where all referenced assets were discovered and validated in the same session

Higher risk:

- copy or large fan-out recreation
- migration across accounts
- activating live spend immediately
- payload-file writes that were not first inspected

## Before Running

Confirm all of:

1. target platform
2. target account or advertiser
3. object IDs
4. token source
5. expected outcome

If any of these are missing and the write would be risky, stop and clarify.
