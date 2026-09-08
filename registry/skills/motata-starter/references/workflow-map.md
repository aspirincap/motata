# Workflow Map

Use this file to decide which phase to enter and what to produce.

## Phase 0: Product Intake

Enter here first when the user gives:

- a product URL
- an App Store URL
- a Google Play URL
- a destination page that is clearly the offer source

Primary outputs:

- `product_intake`
- landing-page or store-page message signals
- objective and asset hints

Preferred command:

- `motata product intake <url>`

## Phase 1: Strategy Research

Enter here when the user asks:

- how to advertise a product, app, or landing page
- which channel to use
- what audience, angle, or budget split to test
- how to structure a first launch

Primary outputs:

- `campaign_brief`
- optional `asset_requirements`
- optional `testing_matrix`

## Phase 2: Delivery Execution

Enter here when the user asks:

- what accounts are available
- what campaigns, adsets, ads, creatives, identities, pixels, or assets exist
- to validate a promotable object, creative, or routing choice
- to create, update, migrate, or inspect delivery objects

Primary outputs:

- `asset_inventory`
- `execution_plan`
- command results

Use `motata ad ops` for this phase.

## Phase 3: Post-Campaign Review

Enter here when the user asks:

- what is performing well or poorly
- why a campaign is underperforming
- what to pause, scale, or revise
- for a review by campaign, ad set, ad group, or ad level

Primary outputs:

- `review_report`
- `next_actions`

## Default End-to-End Order

When the user asks for a full flow from scratch:

1. Product intake when a URL exists
2. Strategy research
3. Account and asset discovery
4. Delivery validation
5. Delivery execution
6. Review and optimization

## Non-Goals

Do not treat this skill as a creative production engine. It can define messaging, test cells, and asset requirements, but it should not claim to generate final creatives unless another dedicated creative skill is explicitly added later.
