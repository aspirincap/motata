# Motata Data Sources

## Command Discovery

When exact flags are unclear, run:

```bash
python3 -m motata_cli <group> --help
python3 -m motata_cli <group> <subcommand> --help
```

Use installed `motata` if available; use `python3 -m motata_cli` inside the repo when working from the local source tree.

## Token Handling

Prefer this order:

1. User-provided `--access-token`.
2. Environment token already configured.
3. `motata-token` skill.
4. Inventory discovery through `motata-token --mode inventory`.

Never print full access tokens in the response. Mask them if mentioning validation.

Do not add temporary/internal token-provider endpoints as first-class CLI or skill configuration. If a local test uses such an endpoint, fetch the token outside the Motata command and pass only the token through the existing `--access-token` path.

## Meta Data Surfaces

Common surfaces:

```bash
python3 -m motata_cli meta user-type analyze --help
python3 -m motata_cli meta landing-pages analyze --help
python3 -m motata_cli meta apps analyze --help
python3 -m motata_cli meta insights --help
python3 -m motata_cli meta metrics probe --help
python3 -m motata_cli meta audience breakdown --help
```

Useful analysis entry points:

- `meta user-type analyze`: vertical classification from top-spend campaigns and landing/app evidence.
- `meta landing-pages analyze`: landing page spend and W2A URL evidence.
- `meta apps analyze`: active app discovery.
- `meta metrics probe`: grouped metric support and active data discovery.
- `meta audience breakdown`: country, age/gender, placement, and device quality diagnostics.
- `meta insights`: ad account/campaign/adset/ad-level reporting.

## TikTok Data Surfaces

Common surfaces:

```bash
python3 -m motata_cli tiktok user-type analyze --help
python3 -m motata_cli tiktok landing-pages analyze --help
python3 -m motata_cli tiktok apps analyze --help
python3 -m motata_cli tiktok insights --help
python3 -m motata_cli tiktok metrics probe --help
python3 -m motata_cli tiktok audience breakdown --help
```

Useful analysis entry points:

- `tiktok user-type analyze`: vertical classification from campaigns, landing pages, and app evidence.
- `tiktok landing-pages analyze`: landing URL and spend analysis.
- `tiktok apps analyze`: active promoted app discovery.
- `tiktok metrics probe`: grouped `/report/integrated/get` metric support and active data discovery.
- `tiktok audience breakdown`: country, age/gender, placement, and device quality diagnostics with dimension fallbacks.
- `tiktok insights`: normal reporting.

## Cross-Platform Metric Presets

```bash
python3 -m motata_cli metrics presets recommend \
  --platform all \
  --user-type "工具" \
  --w2a \
  --json
```

Use `--probe-file` when a probe JSON exists so recommendations are marked active vs empty vs unavailable.

## Safe Execution

- Use read-only commands by default.
- For writes, route through `motata-ad-ops` and its safe-write rules.
- Do not mutate live campaigns based only on fast sampling.
- Preserve the exact command and date range in the final evidence.
