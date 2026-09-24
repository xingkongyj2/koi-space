# Neobrutalist HTML — house style

How `\`\`\`html` blocks should LOOK. This skill defines the visual language and
basic structure patterns. If the system prompt tells you an answer is a good
HTML candidate, make it look like this.

## Good HTML candidates

Use an HTML block when the output has dense facts that are easy to organize into
labeled rows, columns, cards, or a receipt-style summary. Browser task
confirmations often qualify: selected item, retailer, cart quantity, price,
delivery/pickup window, delivery address, reservation details, order state, and
next-step choices. If you have 3+ concrete facts from the page, a compact HTML
summary is usually easier to scan than a prose paragraph.

Keep genuinely short answers in markdown. Don't emit HTML just to decorate one
sentence.

## Structure patterns

- **Receipt summary:** one headline item, then 4-8 label/value rows for price,
  quantity, delivery window, address, retailer, cart state, or confirmation
  number.
- **Comparison grid:** 2-6 cards with the same field labels across each card.
- **Status panel:** current state at top, evidence/facts in rows, next actions
  at the bottom.

## Mandatory visual rules

- **3px solid palette border** on every block-level element you want visible.
  In light mode use black; in dark mode use cream (`#f4ecd8`). Not 1px, not
  subtle gray. (For nested elements 2px is acceptable.)
- **Hard offset shadow** using the palette shadow color: black in light mode,
  cream (`#f4ecd8`) in dark mode. Example: `box-shadow: 4px 4px 0 #f4ecd8;`.
  Larger surfaces can use 6px or 8px. Never soft shadows.
- **No accent-color structural shadows.** Gold/yellow, red, green, blue, pink,
  and purple are for small highlights, badges, selected metrics, or short
  dividers; they should not become the large outer frame or the dominant
  shadow in dark mode.
- **Square corners**: `border-radius: 0`. (4px max is tolerable on small chips
  if every chip uses the same value.)
- **Flat color fills** — no gradients, no semi-transparent overlays for
  background.
- **System font stack only** — `@font-face` and external font URLs are
  blocked by the sandbox. Use `-apple-system, "Segoe UI", system-ui,
  sans-serif` for sans and `ui-monospace, SFMono-Regular, Menlo, monospace`
  for mono.
- **No JavaScript, no external resources** — sandbox blocks both. Inline
  styles or a single inline `<style>` block. No images by URL.

## Banned

- Soft blurred shadows (`0 4px 12px rgba(...)`).
- Pillow corners (`border-radius` over ~4px).
- Linear-gradient backgrounds.
- Inter font.
- Left-edge colored accent stripes.
- The sparkles ✨ glyph.

## Theme-aware palette

The desktop app runs in light or dark mode. The current theme is told to you
in the system prompt (`UI THEME: light` or `UI THEME: dark`). Pick the
palette that matches.

### LIGHT theme — card sits on a white/grey app background

| role           | hex      |
|----------------|----------|
| Card bg        | `#f4ecd8` (cream) · `#fff4d6` (soft yellow) · `#ffffff` |
| Foreground     | `#000`   |
| Muted text     | `#444`   |
| Border         | `#000`   |
| Shadow         | `#000`   |
| Accent — red   | `#ff2b2b` |
| Accent — blue  | `#1a73ff` |
| Accent — green | `#00c853` |
| Accent — gold  | `#ffd400` |
| Accent — pink  | `#ff7ec6` |
| Accent — purple| `#9c27ff` |

### DARK theme — card sits on a near-black app background

| role           | hex      |
|----------------|----------|
| Card bg        | `#1c1c20` · `#22221f` · `#2a2616` (warm dark) |
| Foreground     | `#f4ecd8` (cream) — high-contrast against dark fills |
| Muted text     | `#b9b9b3` |
| Border         | `#f4ecd8` (cream) — pop against the dark background |
| Shadow         | `#f4ecd8` (cream) — offset shadow stays visible on dark |
| Accent — red   | `#ff5252` |
| Accent — blue  | `#4ea3ff` |
| Accent — green | `#3ddc84` |
| Accent — gold  | `#ffd400` |
| Accent — pink  | `#ff7ec6` |
| Accent — purple| `#bb86fc` |

### Two-color rule (both themes)

Pick **one** bold accent + **one** secondary accent per artifact, but keep them
small. More than two accents fights the bold-borders aesthetic, and a large
accent-colored shadow usually overwhelms dark-mode artifacts.

## Typography

- Headlines: bold (`font-weight: 800`), tight (`letter-spacing: -0.02em`),
  large (22-32px). Sans-serif system stack.
- Body: 14-15px, `line-height: 1.5`.
- Mono / status chips: 11-12px, all-caps with `letter-spacing: 0.06em` if
  you want the chip look.

## How "neobrutalist" reads

Bold borders, hard shadows, flat fills, square corners, intentional rawness.
Layouts can be asymmetric or feel a little off-grid — that's the point.
What you build is up to you; just make it look like the rules above.
