# Options block — selectable picker for human-in-the-loop choice

When you're driving a live browser session and you need the user to disambiguate
between concrete options (which SSD? which patty? which size?), emit a fenced
` ```options ` block carrying JSON. The renderer turns it into a clickable
card picker. After emitting the fence, **stop calling tools** — your turn ends
and the agent process idles until the user's selection arrives as the next user
message. The browser session stays warm; you resume right where you left off.

## When to use it

- Shopping / commerce: product search results where the user's task needs human
  taste ("buy me an SSD", "burger ingredients", "running shoes size 11").
- Disambiguation: search results, search-suggest dropdowns, mailing addresses,
  flight options, calendar slots — anything where you have a *short* list of
  concrete picks visible on the live page.
- NOT for: open-ended questions ("what should I do?"), confirmations ("ready to
  proceed?"), or anything text-only. Use plain markdown for those.

## The schema

```
```options
{
  "prompt": "Which SSD do you want?",
  "multiSelect": false,
  "min": 1,
  "max": 1,
  "fieldSchema": ["Price", "Speed", "Warranty"],
  "options": [
    {
      "id": "B0CRD1ZQXG",
      "image": "https://m.media-amazon.com/images/I/71...jpg",
      "title": "Samsung 990 Pro 2TB NVMe",
      "description": "Reliable default — the drive most pro builds ship with. 5-year warranty backed by Samsung.",
      "fields": {
        "Price": "$169.99",
        "Speed": "7,450 MB/s",
        "Warranty": "5 yr"
      },
      "url": "https://amazon.com/dp/B0CRD1ZQXG"
    }
  ]
}
```
```

| field         | required | notes |
|---------------|----------|-------|
| `prompt`      | no       | Short question shown above the cards. Skip if obvious from conversation. |
| `multiSelect` | no       | Default `false`. `true` allows the user to pick multiple. |
| `min` / `max` | no       | Bounds on count when `multiSelect: true`. Default min=1, max=options.length. |
| `fieldSchema` | no, but recommended | Ordered list of field labels EVERY card should fill. Guarantees vertical alignment across cards — missing values in any card render as `—`. Omit only when cards genuinely have heterogeneous fields. |
| `allowOther`  | no       | Default `true`. When `true`, the renderer appends a dashed "Other — describe…" card to the grid with a text input the user can type into. Their custom answer comes back as `Other: <text>` in the bulleted reply. Set `false` only when the listed options are truly exhaustive. |
| `options[].id`          | **yes** | Stable id — usually the retailer's SKU/ASIN/listing-id. You receive this back on selection so you can re-locate the tile in the live page. |
| `options[].image`       | **yes** | Absolute URL — the renderer loads it directly via `<img src>`. |
| `options[].title`       | **yes** | Front-and-center; the line the user reads first. Keep concise. |
| `options[].description` | no | Long-form copy (up to ~5 lines). Pitch / pros / context. |
| `options[].fields`      | no | `Record<string, string>` of label→value rows shown at the card foot. For visual alignment use the same keys across every card (matching `fieldSchema`). |
| `options[].url`         | no | Source URL (informational; not navigated automatically). |

Options missing any of `id`, `image`, or `title` are silently dropped by the
renderer. If zero survive validation the whole block is rejected — be careful
that every option has all three.

**Backward-compat sugar:** if you emit legacy `price`, `merchant`, or
`subtitle` on an option, the parser folds `price` → `fields.Price`,
`merchant` → `fields.Merchant`, and `subtitle` → `description`. Prefer the
new shape directly; the sugar is just there so older agent prompts keep
working.

## Pick the right fields per vertical

The skill is intentionally vertical-agnostic — *you* pick the 2–4 most
useful fields for what the user is choosing between. Some examples:

| vertical          | typical `fieldSchema`                                  |
|-------------------|---------------------------------------------------------|
| Physical product  | `["Price", "Brand"]` or `["Price", "Rating"]`           |
| Stay / rental     | `["Price", "Rating", "Bedrooms", "Distance"]`           |
| Flight            | `["Price", "Duration", "Stops", "Airline"]`             |
| Restaurant        | `["Cuisine", "Rating", "Distance", "Price"]`            |
| Event / ticket    | `["Date", "Venue", "Price"]`                            |
| Stock / fund      | `["Price", "1Y return", "Expense"]`                     |

Keep the labels short (1–2 words). Values are plain strings — include
units, currency symbols, etc. inline (`"$142/night"`, `"4.8★ (412)"`,
`"7,450 MB/s"`).

## Example — Airbnb stays

```
```options
{
  "prompt": "Which place do you want to book?",
  "fieldSchema": ["Price", "Rating", "Bedrooms", "Distance"],
  "options": [
    {
      "id": "abnb-12345",
      "image": "https://a0.muscache.com/im/.../listing.jpg",
      "title": "Charming Brooklyn Loft",
      "description": "Light-filled corner unit two blocks from the L train. Superhost with 412 reviews; flexible cancellation.",
      "fields": {
        "Price": "$142/night",
        "Rating": "4.8★ (412)",
        "Bedrooms": "2",
        "Distance": "0.4 mi from venue"
      },
      "url": "https://airbnb.com/rooms/12345"
    }
  ]
}
```
```

## Getting the data from the page

How you read each tile is up to you — look at the actual DOM with your
browser tool and pick selectors that fit the site. Don't reach for a
generic template; pages differ enough that one snippet rarely works
across sites.

Principles (apply per-site, in priority order):

- **Hero image.** Usually the first `<img>` inside each tile *is* the
  hero — on Amazon, eBay, Instacart, retail listings in general. But
  on sites with decorative chrome inside the tile (e.g. Airbnb's
  category icon, "Featured hotel" badge images, host avatars), the
  first `<img>` is wrong. Pick the largest visible `<img>` in the tile;
  skip anything rendered under ~200px wide. Prefer `<picture>` elements
  when present — those are explicitly the responsive hero.
- **Image quality.** Parse `srcset` and take the largest `w` entry, not
  `currentSrc` (which is the small thumbnail the narrow browser view
  loaded). If the URL has a known size parameter (Amazon's
  `_AC_UF800,800` segment, retailer CDNs with `?width=`), rewriting up
  is often free quality.
- **Lazy loading.** If a hero hasn't loaded yet, the real URL may live
  in `data-src` / `data-srcset` while `src` is a placeholder. If the
  page only loads images on scroll, scroll the tile into view before
  reading. Worst case: `Page.captureScreenshot` with a `clip` box
  around the tile's bounding rect and inline as a data URL.
- **Title / price / fields.** Look at what the user actually sees on
  the listing card — usually the largest text element is the title.
  Don't grab labels, badges ("Best seller"), or breadcrumbs by mistake.
- **Skip tiles missing image OR title.** A card with no image is dead
  weight in the picker.
- **Cap at 4–8 options.** More than that and the user scrolls a wall.

## The turn-ending rule

**Emit the fence, then stop.** No tool calls after the closing ``` ` ``` `.
Your turn is over. The agent process idles on the same browser session; when
the user clicks a card, you receive a new user message shaped like:

> Selected from options: Samsung 990 Pro 2TB NVMe (id: B0CRD1ZQXG)

Or for multi-select:

> Selected from options:
> - Beyond Burger Patties (id: patty-1)
> - 85/15 Ground Beef (id: patty-2)

Read the id(s), re-locate the tile in the live browser (the URL is also in
the block you emitted — you have it in your own message history), and
proceed.

## Multi-section pickers — one fence, several categories

When a single ask covers multiple categories the user has to choose
across (a burger needs a patty AND a bun AND maybe a tomato; a flight
booking needs an outbound AND a return), use a **`sections` array**
inside one ` ```options ` fence instead of emitting separate fences.
The renderer stacks each section as its own labeled sub-grid inside
one picker shell, with a single Confirm button at the foot that
bundles every section's picks into one resume call. One round-trip
back to you, not five.

```
```options
{
  "prompt": "Pick burger ingredients",
  "sections": [
    {
      "label": "Patty",
      "multiSelect": false,
      "fieldSchema": ["Price", "Protein"],
      "options": [
        { "id": "patty-1", "image": "https://...", "title": "Beyond Burger",
          "fields": { "Price": "$6.49", "Protein": "20g" } },
        { "id": "patty-2", "image": "https://...", "title": "85/15 Ground Beef (1 lb)",
          "fields": { "Price": "$8.99", "Protein": "22g" } }
      ]
    },
    {
      "label": "Bun",
      "multiSelect": false,
      "options": [
        { "id": "bun-1", "image": "https://...", "title": "Brioche Buns" },
        { "id": "bun-2", "image": "https://...", "title": "Sesame Buns" }
      ]
    },
    {
      "label": "Tomato (optional, up to 2)",
      "multiSelect": true,
      "min": 0, "max": 2,
      "options": [
        { "id": "tom-1", "image": "https://...", "title": "Roma Tomato" },
        { "id": "tom-2", "image": "https://...", "title": "Heirloom Tomato" }
      ]
    }
  ]
}
```
```

Each section is independent — its own `label`, `multiSelect`, `min/max`,
`fieldSchema`, and `options`. Confirm enables only when **every section**
meets its bounds. The user's reply comes back to you shaped like:

```
Selected from options:
- Patty: Beyond Burger (id: patty-1)
- Bun: Brioche Buns (id: bun-1)
- Tomato (optional, up to 2): Roma Tomato (id: tom-1)
- Tomato (optional, up to 2): Heirloom Tomato (id: tom-2)
```

Each line is prefixed with the section label so you can route picks
back to categories without guessing. Then proceed: add each picked item
to cart, navigate to checkout, etc.

### When to use sections vs. separate fences

- **Use `sections`** when the choices are coordinated parts of one task
  (burger ingredients, flight outbound+return, multi-room hotel booking,
  outfit combinations). The user is making one combined decision.
- **Use separate fences across turns** when each pick genuinely depends
  on the previous one — "first pick a restaurant; once I see it, I'll
  show you available reservation times." You need the user's answer
  before you can compute the next set of options.

## One block per category — single-section (legacy)

For multi-step tasks like "burger ingredients", emit **one block per
category** in sequence: pick a patty → user submits → "now the bun" → user
submits → etc. Don't try to nest categories inside one block. The renderer
isn't designed for that and the UX is worse — small focused decisions feel
faster than a giant grid.

## Worked example — "buy me an SSD"

1. Navigate to amazon.com, search for "2tb nvme ssd", wait_for_load.
2. Read product tiles from the page using selectors that fit the current site.
3. Emit:

   ```
   ```options
   { "prompt": "Which SSD?", "options": [ … 4 items … ] }
   ```

4. **Stop.** No more tool calls this turn.
5. Next user message arrives: `Selected from options: Samsung 990 Pro 2TB
   NVMe (id: B0CRD1ZQXG)`.
6. Navigate to that product's URL (you have it from step 2), add to cart,
   proceed to checkout. If checkout needs another decision (shipping speed,
   payment method), emit another `options` block.

## Banned

- Per-card "Buy" / "Select" CTAs inside option JSON — selection is the card,
  the picker has its own confirm button.
- Tags / chip arrays per option — just put the info in `title` or
  `subtitle`. Real product data rarely has clean structured tags.
- Images served as base64 unless you absolutely must (huge tokens). Use
  the CDN URL the page already loads.
- More than ~8 options per block.
