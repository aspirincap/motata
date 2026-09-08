# Product Intake

Use this workflow when the user provides a product page, app page, or store URL.

## Why This Exists

The strategy layer should not start from a raw URL when a deterministic scraper can first convert that page into structured inputs.

This skill includes two equivalent entry points for that purpose:

- preferred: `motata product intake <url>`
- optional wrapper script: `python3 scripts/product_intake.py <url>` requires `motata-cli` installed in that Python interpreter. npm users should use the CLI, which selects its private runtime.

## When To Run It

Run product intake before broader research when the user provides:

- an ecommerce product URL
- an App Store URL
- a Google Play URL
- a landing page that is clearly the offer destination

## What It Produces

The script returns a strategy-ready object with:

- `url`
- `url_type`
- `scrape`
- `page_metadata`
- `page_signals`
- `signal_summary`
- `strategy_hints`
- `page_fetch_warning` when storefront HTML is noisy or mismatched

## How To Use The Output

Use the output this way:

1. `scrape.name` -> candidate product or app name
2. `scrape.price` -> offer and pricing context
3. `scrape.images` -> visual starting point
4. `page_metadata` -> page framing and promise
5. `page_signals.feature_bullets` -> value proposition candidates
6. `page_signals.cta_candidates` -> CTA direction
7. `signal_summary.primary_headline` -> likely hero message
8. `strategy_hints` -> objective and asset requirements

## Commands

```bash
motata product intake "https://example.com/product"
```

```bash
python3 scripts/product_intake.py "https://example.com/product"
```

## Notes

- It wraps the current `motata` product scraper and adds strategy-facing fields.
- It is now a first-class `motata` CLI capability, not just a skill-local helper.
- It is deterministic and should be preferred over ad hoc manual parsing for first-pass product intake.
- It filters obvious navigation noise so the output is easier to use in campaign strategy work.
- For App Store pages, it prefers lookup data and suppresses mismatched storefront HTML when Apple returns noisy landing pages.
