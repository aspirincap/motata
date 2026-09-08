# Intake Contract

Collect only the minimum fields needed for the current phase.

## Strategy Research

Preferred inputs:

- product URL, app URL, store URL, or plain-language product description
- business objective
- target market or geography
- target platform if already known
- budget range if the user has one
- known competitors or reference brands

Useful but optional:

- current campaign links or IDs
- landing page
- current audience assumptions

If a URL is present, run `motata product intake <url>` before broad strategy synthesis.

Minimum viable output from intake:

- offer name
- destination type
- price anchor when available
- headline candidates
- value prop candidates
- CTA candidates

## Delivery Execution

Required inputs depend on the platform:

### Meta

- ad account ID
- access token, or API key path that can retrieve a usable token
- object IDs needed for the requested command

### TikTok

- advertiser ID
- access token, or API key path that can retrieve a usable token
- object IDs needed for the requested command

Useful but optional:

- page, pixel, identity, media, or promoted object IDs
- a known source campaign if the task is update, copy planning, or migration

## Post-Campaign Review

Preferred inputs:

- account or advertiser ID
- campaign, ad set, ad group, or ad IDs when the scope is narrow
- date range or month
- success metric if the user has one

If the user does not provide a date range, infer a sensible default and state it clearly in the result.

## Fallback Rule

If the user asks for a full workflow with sparse inputs:

1. Start with strategy research if no account context is known.
2. Start with account discovery if the user already has platform context but no object IDs.
3. Start with review if the user references existing live campaigns and wants diagnosis.

If a product or app URL exists, prepend product intake before step 1.
