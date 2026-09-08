# Budget And Bid Optimization

## Purpose

Use this reference when the user asks where to move spend, what to scale, what to pause, or how to interpret bid strategy.

## Safety

- Never execute budget/status changes without explicit user approval.
- For large changes, present the exact command or payload first.
- Do not scale based on one-day data unless the user is managing an urgent incident.
- Do not use fast sampling for final budget decisions.

## Required Evidence

- Spend and result/value by campaign and ad group/ad set.
- Objective and optimization goal.
- Bid strategy, billing event, budget where available.
- Trend vs comparison period.
- Vertical-specific quality metric.

## Action Bands

| Band | Evidence | Action |
| --- | --- | --- |
| Scale | Stable spend, efficient cost, healthy vertical metric | Increase 10-20% or duplicate/test after approval |
| Maintain | Efficient but limited evidence or learning risk | Keep budget, monitor |
| Fix | Spend with mixed evidence | Refresh creative, adjust audience/placement/bid |
| Reduce | Inefficient and enough spend | Reduce 10-30% after approval |
| Pause | High spend, no result, no assist signal | Pause after approval |

## Bid Diagnosis

| Symptom | Interpretation |
| --- | --- |
| Low delivery, tight bid/cost cap | bid too restrictive or audience too narrow |
| Spend spikes, CPA worsens | broad delivery without quality guardrail |
| High CPM, low CTR | quality/creative problem before bid problem |
| Good CPA, weak downstream value | optimize for deeper event |

## Vertical Rules

- 电商: optimize for purchase value/ROAS when value is reliable.
- App/W2A/tools: do not optimize only for install if trial/subscribe/registration exists.
- Games: optimize toward retention/tutorial/purchase/ad monetization when populated.
- Finance: optimize toward apply/credit/disbursement, not raw lead if deeper events exist.
- Search套利: maintain margin discipline; cheap clicks can still be bad.

## Output

Return a budget move table:

| Entity | Current spend | KPI | Diagnosis | Recommendation | Approval needed |
| --- | ---: | --- | --- | --- | --- |
