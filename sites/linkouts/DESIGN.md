---
name: linkouts
description: Human-readable pages for individual knowledge-graph edges; a cool-paper scientific instrument with one deep-teal accent, flat depth, and prose-first evidence.
colors:
  deep-teal: "oklch(48% 0.1 175)"
  sea-glass-teal: "oklch(78% 0.13 175)"
  registry-blue: "oklch(45% 0.02 250)"
  cool-paper: "oklch(98.5% 0.004 180)"
  paper-recessed: "oklch(95.5% 0.006 180)"
  paper-edge: "oklch(89% 0.01 180)"
  ink: "oklch(22% 0.02 180)"
  slate-bench: "oklch(16% 0.012 180)"
  slate-recessed: "oklch(21% 0.014 180)"
  slate-edge: "oklch(29% 0.016 180)"
  slate-ink: "oklch(92% 0.012 180)"
  verdict-green: "oklch(45% 0.12 155)"
  verdict-red: "oklch(50% 0.17 25)"
  caution-amber: "oklch(50% 0.12 75)"
typography:
  display:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "clamp(1.44rem, 1.296rem + 0.64vw, 1.728rem)"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "-0.01em"
  title:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "1.44rem"
    fontWeight: 700
    letterSpacing: "-0.015em"
  body:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "1rem"
    lineHeight: 1.7
  name:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "1.2rem"
    fontWeight: 650
  small:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "calc(1rem / 1.2)"
    lineHeight: 1.5
  node-name:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "1.2rem"
    fontWeight: 700
    lineHeight: 1.3
  note:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "1rem"
    lineHeight: 1.7
  caption:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "1rem"
    lineHeight: 1.7
  label:
    fontFamily: '"Atkinson Hyperlegible Next", system-ui, "Segoe UI", sans-serif'
    fontSize: "calc(1rem / 1.2)"
    fontWeight: 700
    letterSpacing: "0.08em"
  mono:
    fontFamily: 'ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", monospace'
    fontSize: "calc(1rem / 1.2)"
  identifier:
    fontFamily: 'ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", monospace'
    fontSize: "calc(1rem / 1.2)"
  micro:
    fontFamily: 'ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", monospace'
    fontSize: "calc(1rem / 1.2)"
    lineHeight: 1.4
rounded:
  field: "0.375rem"
  box: "0.625rem"
  pill: "999px"
spacing:
  rail: "64rem"
  measure: "60rem"
  state: "34rem"
  section: "3rem"
  card-pad: "0.85rem 1rem"
components:
  button-primary:
    backgroundColor: "{colors.deep-teal}"
    textColor: "{colors.cool-paper}"
    rounded: "{rounded.field}"
    height: "2.25rem"
    padding: "0 0.75rem"
  button-primary-dark:
    backgroundColor: "{colors.sea-glass-teal}"
    textColor: "{colors.slate-bench}"
    rounded: "{rounded.field}"
    height: "2.25rem"
    padding: "0 0.75rem"
  button-ghost:
    textColor: "{colors.deep-teal}"
    rounded: "{rounded.field}"
    height: "2.25rem"
    padding: "0 0.75rem"
  version-pill:
    backgroundColor: "color-mix(in oklab, {colors.deep-teal} 14%, {colors.cool-paper})"
    textColor: "{colors.deep-teal}"
    rounded: "{rounded.pill}"
    padding: "0.1rem 0.55rem"
  latest-badge:
    backgroundColor: "color-mix(in oklab, {colors.verdict-green} 18%, {colors.cool-paper})"
    textColor: "{colors.verdict-green}"
    rounded: "{rounded.pill}"
    padding: "0.05rem 0.42rem"
  count-badge:
    backgroundColor: "{colors.paper-recessed}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "0 0.45rem"
  card-kg-row:
    backgroundColor: "{colors.cool-paper}"
    rounded: "{rounded.box}"
    padding: "{spacing.card-pad}"
  card-edge-node:
    backgroundColor: "{colors.cool-paper}"
    rounded: "{rounded.box}"
    padding: "0.9rem 1rem"
  timeline-step:
    backgroundColor: "{colors.cool-paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "0.25rem 0.8rem"
  timeline-step-active:
    backgroundColor: "{colors.deep-teal}"
    textColor: "{colors.cool-paper}"
    rounded: "{rounded.pill}"
    padding: "0.25rem 0.8rem"
  code-inline:
    backgroundColor: "{colors.paper-recessed}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "0.1rem 0.45rem"
---

# Design System: linkouts

## Overview

**Creative North Star: "Cool Paper"**

The page is a sheet of cool paper on the bench: quiet, slightly bluish-white, with dark ink and exactly one accent pen in deep teal. Everything on it is evidence, laid out the way a careful lab record reads — the claim as a sentence, the facts beneath it, the versions along the margin. Nothing on the page competes with the content for attention; the design's job is to make a scientist trust it at a glance and quote it without reformatting.

This is a scientific instrument, not a brand exercise. The interface recedes: flat surfaces, hairline borders, no shadows, no gradients, no decorative imagery. Colour is evidence, not decoration — it appears only where the markup also says what it means in words (diff markers, state kickers, the research-use banner). Legibility wins every tradeoff, including thematic ones: amber text was dropped from dark mode because unreadable beats on-theme, and the typeface was chosen for the legibility of look-alike characters in scientific identifiers.

Light cool paper is the default; slate bench is its equal after dark, chosen by toggle or `prefers-color-scheme`, applied before first paint. Both themes carry the same single teal accent at matched contrast.

**Key Characteristics:**
- Flat, bordered, tonal: depth comes from three neutral steps and 1px hairlines, never shadows.
- One accent: deep teal (sea-glass after dark) marks links, actions, and current state — nothing else.
- Words first: colour never carries meaning alone; every coloured signal has a text or marker twin.
- Prose-first evidence: facts render as citable sentences with inline links, not label-value grids.
- One left rail: header brand, headline, and evidence share a single left edge at every width.

## Colors

The palette is a cool, near-achromatic paper family with a single deep-teal accent and a reserved muted blue. Light values are canonical; every color has a dark counterpart at matched lightness/contrast.

### Primary
- **Deep Teal** (`oklch(48% 0.1 175)`, dark: Sea-Glass Teal `oklch(78% 0.13 175)`): the only action and link color. Primary buttons, the active version step, link text, hover borders, and focus outlines. Its rarity is the point — on most screens it appears only on controls the reader can act on.

### Secondary
- **Registry Blue** (`oklch(45% 0.02 250)`, dark `oklch(70% 0.02 250)`): a desaturated, near-neutral blue reserved for quiet secondary identification (KG source lines). It never competes with the teal.

### Neutral
- **Cool Paper** (`oklch(98.5% 0.004 180)`): the page background. Bluish-white, deliberately not pure white.
- **Paper Recessed** (`oklch(95.5% 0.006 180)`): recessed surfaces — the research banner, inline code chips, count badges, boxed notes.
- **Paper Edge** (`oklch(89% 0.01 180)`): every hairline border and divider, light theme.
- **Ink** (`oklch(22% 0.02 180)`): body text.
- **Slate Bench** (`oklch(16% 0.012 180)`): dark page background; **Slate Recessed** (`oklch(21% 0.014 180)`) and **Slate Edge** (`oklch(29% 0.016 180)`) mirror the recessed/border roles; **Slate Ink** (`oklch(92% 0.012 180)`) is dark body text.

### Semantic (state only)
- **Verdict Green** (`oklch(45% 0.12 155)`, dark `oklch(76% 0.14 155)`): added lines in diffs, the "latest" badge, success copy states.
- **Verdict Red** (`oklch(50% 0.17 25)`, dark `oklch(72% 0.16 25)`): removed lines in diffs, error kickers, struck-through old values.
- **Caution Amber** (`oklch(50% 0.12 75)`, dark `oklch(78% 0.13 75)`): warning kickers and the known-error box's left border bar only — never as text in dark mode (it washes out; the warning icon and bold kicker carry it instead).

### Named Rules
**The One Accent Rule.** Deep teal is the only hue that marks action or currency. Semantic green/red/amber appear only on state (diff lines, badges, kickers) and always paired with words or `+`/`-` markers. No other color enters the system.

**The Words-First Rule.** Colour is never the only signal. Every diff line carries a `+`/`-` marker and a visually-hidden added/removed/changed word; every state kicker is text. If the colorblind test fails, the component is wrong.

**The Tint, Not Paint Rule.** Soft states (version pills, latest badges, notice boxes) use `color-mix` tints of the accent over the page background — never solid fills of the semantic color.

## Typography

**Display/Body Font:** Atkinson Hyperlegible Next (self-hosted variable, SIL OFL 1.1; falls back to system-ui)
**Mono Font:** ui-monospace stack ("SF Mono", "Cascadia Mono", "JetBrains Mono")

**Character:** A typeface chosen for evidence work: Atkinson Hyperlegible Next was designed for character differentiation, which matters when the payload is identifiers like `infores:drugapprovals-kp` where look-alikes (`1/l/I`, `0/O`) change meaning. The voice is plain, confident, and slightly narrow-tracked at headlines — a lab record, not a brochure.

### Hierarchy
- **Display** (600, fluid 1.44-1.728rem, line-height 1.4, -0.01em tracking): the edge claim, the largest text on the page. Interpolates between scale steps from 22.5rem to 67.5rem viewport widths.
- **Title** (700, 1.44rem, line-height 1.4, -0.015em tracking): KG bar and state-page titles.
- **Lead** (1.2rem, line-height 1.4): the source's "asserts" line, below the claim and above body. KG and node names use the same step with their own weight.
- **Body** (400, 1rem, line-height 1.7): Methods, Provenance, About, Tips, Feedback, Known issues, and Changes. The header wordmark also uses body size, with weight rather than another size for emphasis.
- **Small** (1rem / 1.2, about 0.8333rem): section kickers, metadata, controls, IDs, versions, diff paths and values, count badges, and footer. Mono roles keep their mono face; semantic colors and weights retain their meanings. UI line-height is 1.5, and prose remains 1.7.
- **Micro** (1rem / 1.44, about 0.6944rem): the uppercase `latest` badge only. It sits one step below the version pill it trails so the pair reads as label plus annotation, never as two competing pills.
- **Label** (700, small step, 0.08em tracking, uppercase): section and node-role labels, diff group headings, state kickers. The existing trailing section hairline is unchanged.

### Scale
A minor-third scale with a 1.2 ratio and a 1rem base. At the browser's default 16px, the six role sizes are:

| Token | rem | px | Role |
| --- | --- | --- | --- |
| `--type-micro` | `calc(1rem / 1.44)` | 11.11 | Uppercase badge micro-labels (`latest`), never content |
| `--type-small` | `calc(1rem / 1.2)` | 13.33 | Metadata, labels, controls, mono |
| `--type-body` | `1rem` | 16 | Reading text |
| `--type-lead` | `1.2rem` | 19.2 | Source lead and names |
| `--type-title` | `1.44rem` | 23.04 | Page titles, mobile claim floor |
| `--type-claim` | `1.728rem` | 27.65 | Desktop claim ceiling |

`--type-micro` is the second step down and is reserved: no identifier, count, version, or prose of any kind may use it, because those have to stay readable. It exists solely so a short, bold, uppercase badge can sit visibly below the label it annotates. Everywhere else, use weight, tracking, and ink level for secondary hierarchy instead of more nearly identical font sizes. Keep rem units so user text-size preferences work; the header wraps and long IDs break rather than overflowing when text is enlarged.

The scale is a consistency tool, not a guarantee of visual quality. Review actual long labels at 320px and with 200% text sizing in both themes. [Modular Scale](https://www.modularscale.com/) explains the ratio method and explicitly permits breaking the scale for legibility.

### Named Rules
**The Sentence Rule.** Facts about an edge are written as prose sentences with links inline, never as label-value pairs the reader must decode. The claim and its evidence sit at the same size so they read as one argument.

**The Identifier Rule.** Identifiers (`infores:` curies, node IDs, versions) always render in mono, never in body face. A bare identifier in prose face is a defect.

## Layout

One shell, one rail. Header, main, and footer share a 64rem (`max-w-5xl`) centered container with 2rem side padding, so the brand, the page headline, and the content keep a single left rail at every viewport width — and a single right edge: the edge article spans main's whole content column instead of capping at its own measure, so the header controls and the research banner above never outrun the text and boxes below. The research banner stays a full-bleed strip, but its text rides the same rail. Content blocks are left-aligned to that rail, not centered; the state pages at 34rem are the only centered blocks, being terminal states.

Inside the article, sections stack in a single `minmax(0, 1fr)` grid column with a fixed 3rem step (2.25rem below 40rem), so the whole page reads on one vertical rhythm. The evidence table and relation line keep one label column every row aligns to. Density is calm: 0.75rem between KG rows, 1rem card padding, 0.35rem between version pills.

The relationship diagram is a three-column grid (subject card, predicate, object card) that collapses to one column below 48rem, rotating the arrow 90deg. Two breakpoints only: 40rem (section rhythm) and 48rem (diagram).

## Elevation & Depth

There are no shadows. The system is explicitly flat (`--depth: 0`): depth is conveyed entirely by tonal steps — Cool Paper surface, Paper Recessed wells, Paper Edge hairlines — plus `color-mix` tints for soft states. A card is a bordered region on the page, not a floating object.

Hover does not lift; it re-inks. A hovered chip, pill, or row keeps its position and border but recolors the border and text to the teal accent, with a short border-color/color transition. Focus is a 2px teal outline offset 2px, visible in both themes.

### Named Rules
**The Flat Bench Rule.** Shadows, glows, and drop filters are banned at every level, including "just this once" hero cards. Depth is a tonal decision (base-100/200/300), never a cast shadow.

## Shapes

Utility-square with rounded working edges. Interactive smalls — buttons, inputs, inline code chips, the "Show N more" inline button — use a 6px radius (`--radius-field`). Cards (KG rows, edge-node boxes) use 10px (`--radius-box`). Versions, counts, and status badges are full pills (999px). Every bordered element carries a 1px hairline in the theme's edge color; borders are never removed, only recolored (to teal on hover, to the semantic color on state). Corners are never fully round except pills, and never sharp except the diagram arrow.

## Components

### Buttons
- **Shape:** 6px radius, uniform 2.25rem height for every header control so the random-edge button and theme toggle align as a pair.
- **Primary:** solid Deep Teal fill with paper-colored text (btn-sm sizing); used for the header "Random edge" action and page actions. Dark: Sea-Glass fill on Slate Bench.
- **Ghost:** text-only teal (the theme toggle); border appears from the button chrome only.
- **Copy:** small-step recessed chip with hairline border; hover re-inks to teal; the copied state flips border and text to Verdict Green.
- **Inline "Show N more":** a chip-shaped button that lives inside a comma-separated sentence — inline, not block, so folding a list never opens a vertical gap.
- **Hover/Focus:** hover re-inks border/text to teal (never lifts); `:focus-visible` is the global 2px teal outline.

### Chips and Pills
- **Version pill:** full pill, teal 14% tint background, teal text, teal-35% tint border; mono label. The unit of navigation on the KG bar and the versions timeline.
- **Count badge:** small-step mono full pill on Paper Recessed with hairline border; sits as a flex sibling of its pill, never nested inside it.
- **Latest badge:** uppercase 700 micro-label at `--type-micro`, Verdict Green 18% tint; trails the version number inline in count lines and as a flex sibling in pill rows. It is always a step smaller than the pill or version it annotates.
- **Identifier chip (`.chip`):** mono, recessed, max-width 16rem, wraps anywhere; hover re-inks to teal (it is usually a link).

### Cards / Containers
- **KG row:** 10px radius, 1px edge border, paper background, 0.85rem x 1rem padding; name, canonical identifier, and the measured pill row inside.
- **Edge node:** 10px radius card holding one side of the relationship diagram: uppercase role label, 1.2rem 700 name, category, and an ID line with a copy chip.
- **Boxes/notes:** known-error and bar notices are recessed fills with hairline borders and the standard 10px box radius; the known-error box adds a 3px Caution Amber left bar whose ends follow the rounding.

### Versions Timeline
A wrapping row of step pills; the current release fills solid teal with paper text (its count badge inverts to a tint of the fill). Hover re-inks the border. The active state is the only solid-fill navigation element, and this component is the page's single owner of version and latest state. There is no kicker above the sentence on a decoded edge page: the claim's opening KG name (linked to the source's own documentation) is the page's only KG-name link up top, and the header's context-scoped random button owns "another edge from this graph and release".

### Diff
A one-line caption under the heading names the two releases in short labels ("1 field changed from 1.23.3 to 1.23.4"); there is no legend, because every line below already carries its `+`/`-` marker, a visually-hidden word, and its field name. Changes render as classic +/- hunks: every change is a full-width line with a light green (added) or light red (removed) backdrop, and the value in a mono chip on the same tint, one step stronger. Changed values render as two stacked lines (struck old above, new below), never joined by an arrow or any other icon; the diff contains no icons at all. Colour is the backup signal, not the primary one. When a list change is exactly one object out and one object in, the panel diffs the two objects field by field and addresses each moved field by its full dotted path in mono ("sources.infores:drugapprovals-kp.source_record_urls"), so a list holding several sources still says whose field changed — never the whole objects as raw JSON. Group headings ("Changed lists") appear only when a heading batches several entries; a single-entry group starts straight at the changed lines.

### Navigation
A single site header: brand wordmark left, random-edge primary button and theme toggle right, on the shared 64rem rail with a hairline bottom border. The random button is context-scoped (whole store, one KG, or one release) so it never surprises. Below it, the research-use banner spans the viewport as a recessed fill, but its text rides the shared rail — a legal boundary, always banner-treated and always in theme colors.

### Signature Component: The Relationship Diagram
Two edge-node cards joined by a centered predicate (arrow icon, human-readable predicate, raw curie beneath). The clearest one-glance statement of what the edge asserts; collapses to a vertical stack below 48rem with the arrow rotated 90deg.

## Do's and Don'ts

### Do:
- **Do** pair every colour signal with words or markers (`+`/`-`, uppercase kickers, hidden screen-reader text).
- **Do** write edge facts as citable prose sentences at body size, with identifiers in mono chips.
- **Do** keep header, headline, and content on the one 64rem left rail; center only terminal state pages (34rem).
- **Do** use teal tints (`color-mix` at 10-18%) for soft states and solid teal only for actions and the current version.
- **Do** keep both themes at matched contrast: when a light-mode color fails in dark, redesign the treatment (border bar, icon, kicker), never just darken the text.

### Don't:
- **Don't** add shadows, glows, gradients, or any cast depth — the bench is flat at rest and on hover.
- **Don't** introduce hues beyond the teal accent, Registry Blue, and the three-color state trio.
- **Don't** center article content or split the left rail; the page reads as one grid with one left edge.
- **Don't** render identifiers, versions, or counts in the body face; mono with a recessed chip is the only correct treatment.
- **Don't** use amber as text in dark mode, and don't strip the research-use banner to a footnote — it is a legal boundary and keeps banner treatment in both themes.
- **Don't** nest badges inside pills or buttons; siblings in a flex row keep every chip's border intact.
