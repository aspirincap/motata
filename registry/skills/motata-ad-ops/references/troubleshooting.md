# Troubleshooting

Use this file when a command fails or an API response is surprising.

## First Checks

1. verify account or advertiser ID
2. verify token source and scope
3. verify object ID really belongs to the current account
4. run the same command with `--help` if the parser shape is uncertain

## Meta

If the user needs raw API context:

- use `motata meta debug graph`

If create or update fails:

1. rerun asset discovery
2. rerun the relevant validation command
3. confirm page, pixel, or promoted object compatibility

## TikTok

If reads work but writes fail:

1. confirm the object is valid and not deleted
2. confirm the route is normal vs SmartPlus
3. confirm identities, assets, or promoted objects exist in the advertiser
4. rerun the relevant validation command

Known examples from repository validation:

- `tiktok ads status` can return `ad not valid` on bad samples
- parser availability does not guarantee the target object is writable

## Command Surface Checks

If you are unsure whether a command exists or the flags changed:

1. inspect the repository README command surface
2. inspect local audit notes if present
3. run `motata <group> <action> --help`

Prefer this over guessing undocumented flags.
