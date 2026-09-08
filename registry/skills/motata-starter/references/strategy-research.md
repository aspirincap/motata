# Strategy Research

Use web research for this phase. The goal is not a generic marketing report; the goal is an executable first delivery plan.

## Product Intake First

If the user provides a product URL, app URL, or store URL, run `motata product intake <url>` before broader web research.

Use `python3 scripts/product_intake.py <url>` only when `motata-cli` is installed in that Python interpreter. For npm installations, use `motata product intake` so the private runtime is selected correctly.

Use it to extract:

- normalized product or app identity
- price and image candidates
- page metadata and message clues
- CTA candidates
- likely headline and hero framing
- strategy hints such as destination type, objective guess, and required asset types

Then use that output as the structured base for the rest of the strategy work.

## Research Questions

Answer only the questions needed for the user's objective:

1. What is the offer and why would someone click?
2. Which audience segments appear most likely to respond first?
3. Which platform should lead, and which should follow?
4. What should the first testing matrix look like?
5. What assets will delivery need before launch?

## Recommended Research Areas

- brand site or app store listing
- pricing and offer structure
- competitor positioning
- market language and hooks
- seasonal or geographic constraints
- platform fit between Meta and TikTok

## Required Outputs

Return a concise `campaign_brief` with:

- business goal
- target audience
- platform recommendation
- key message hypotheses
- landing or store destination
- budget split hypothesis
- first testing matrix
- required assets before launch

If product intake was used, include a short `product_intake` summary inside the reasoning that led to the brief, but keep the final output concise.

Recommended `product_intake` summary fields:

- `offer_name`
- `destination_type`
- `price_anchor`
- `headline_candidates`
- `value_prop_candidates`
- `cta_candidates`

## Delivery Handoff

If the user also wants execution, convert research into an `execution_plan` that includes:

- target platform
- target account or advertiser if known
- discovery tasks required before writes
- validations required before writes
- minimal launch sequence

Do not fabricate object IDs or pretend delivery prerequisites already exist.
