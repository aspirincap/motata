# Example Workflows

Use these examples to keep outputs concrete and consistent.

## Example 0: Product URL To Strategy Intake

User task:

```text
先研究这个商品页，给我一个可执行的首轮投放判断。
```

Expected flow:

1. run `motata product intake <url>`
2. summarize `product_intake`
3. build `campaign_brief`
4. decide whether execution discovery is needed

Expected intermediate shape:

```json
{
  "product_intake": {
    "offer_name": "Anker Laptop Power Bank (25K, 165W, Built-In and Retractable Cables)",
    "destination_type": "website",
    "price_anchor": 119.99,
    "headline_candidates": [
      "Anker Laptop Power Bank (25K, 165W, Built-In and Retractable Cables)",
      "Charge Four Devices Simultaneously"
    ],
    "value_prop_candidates": [
      "Triple 100W USB-C Ports for Multi-Device",
      "25,000mAh for Long-Haul Power"
    ],
    "cta_candidates": [
      "Add to Cart",
      "Buy Now",
      "Learn More"
    ]
  }
}
```

## Example 1: Strategy To TikTok Discovery

User task:

```text
帮我给一个瑜伽服品牌做 TikTok 首发投放方案，并看看我现有广告账户能不能直接起量。
```

Minimum inputs:

- product or store URL
- target market
- approximate budget range
- API key or advertiser context if execution is requested

Expected output shape:

```json
{
  "campaign_brief": {
    "objective": "first-purchase growth",
    "recommended_platform": "tiktok",
    "target_audience": ["women interested in activewear", "yoga and pilates shoppers"],
    "key_message_hypotheses": [
      "premium feel without luxury price",
      "studio-to-street versatility"
    ],
    "test_matrix": [
      "broad audience x product-first angle",
      "interest cluster x comfort angle"
    ],
    "required_assets": ["advertiser", "identity", "landing page", "promoted object"]
  },
  "execution_plan": {
    "platform": "tiktok",
    "read_steps": [
      "list advertiser accounts",
      "discover assets",
      "list identities"
    ],
    "validation_steps": [
      "validate promoted object"
    ],
    "write_steps": [],
    "token_source": "auth_center"
  }
}
```

## Example 2: Strategy To Meta Create-Paused

User task:

```text
根据这个独立站帮我规划 Meta 首轮测试，并直接准备创建一个暂停状态的 campaign。
```

Expected flow:

1. strategy research
2. `campaign_brief`
3. `execution_request` with `intent=create`
4. delivery through `motata ad ops`

Expected execution constraint:

```json
{
  "constraints": {
    "allow_writes": true,
    "default_status": "PAUSED",
    "exclude_copy": true,
    "exclude_aigc": true
  }
}
```

## Example 3: Review Existing TikTok Delivery

User task:

```text
复盘我全部 TikTok 账户 2025 年 12 月的 campaign 和高消耗 adset，告诉我下一步怎么调。
```

Expected output shape:

```json
{
  "review_report": {
    "scope": "all reachable TikTok advertisers",
    "time_range": "2025-12-01 to 2025-12-31",
    "key_findings": [
      "which accounts spent the most",
      "which campaign structures dominated",
      "whether high-spend adsets existed"
    ],
    "likely_causes": [
      "budget concentration",
      "campaign objective mix",
      "targeting or asset availability"
    ]
  },
  "next_actions": [
    "pull deeper insight for top-spend entities",
    "pause or keep based on stability",
    "revise next launch hypothesis"
  ]
}
```

## Example 4: Cross-Platform Planning Without Writes

User task:

```text
帮我判断这个 App 更适合先做 Meta 还是 TikTok，并检查我两边的账户和基础资产。
```

Expected route:

1. strategy research for platform fit
2. Meta asset discovery
3. TikTok asset and identity discovery
4. no writes

Expected output:

- `campaign_brief`
- `asset_inventory` for each platform
- platform recommendation
- missing prerequisites
