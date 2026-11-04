---
name: Закупки
description: A buyer's daily order list, set as a tear-off wall calendar leaf.
colors:
  ground: "#eceeec"
  leaf: "#fafaf8"
  leaf-2: "#f3f4f2"
  ink: "#161716"
  ink-2: "#4f5351"
  ink-3: "#727775"
  rule: "#cdd1ce"
  rule-strong: "#9aa09c"
  red: "#c42a1f"
  red-ink: "#a3221a"
  red-wash: "#f8e3e0"
  amber: "#a86a00"
  amber-wash: "#fbefd5"
  orange: "#b4501f"
  orange-wash: "#f9e6dc"
  green: "#1d7a35"
  green-wash: "#e2f1e5"
  blue: "#2a78d6"
  series-2: "#eb6834"
  series-3: "#1baf7a"
  focus: "#2a78d6"
typography:
  display:
    fontFamily: "Oswald, Arial Narrow, system-ui, sans-serif"
    fontSize: "132px"
    fontWeight: 600
    lineHeight: 0.95
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Golos Text, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "clamp(24px, 2.4vw, 32px)"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Golos Text, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  figure:
    fontFamily: "Oswald, Arial Narrow, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "0.01em"
    fontFeature: "tnum"
  body:
    fontFamily: "Golos Text, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
  caption:
    fontFamily: "Golos Text, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "Golos Text, system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "0.1em"
rounded:
  none: "0px"
  control: "2px"
spacing:
  xs: "8px"
  sm: "16px"
  md: "24px"
  lg: "32px"
  xl: "40px"
components:
  button:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
  button-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.leaf}"
  button-primary:
    backgroundColor: "{colors.red}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "9px 14px"
  button-primary-hover:
    backgroundColor: "{colors.red-ink}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
  chip-critical:
    backgroundColor: "{colors.red-wash}"
    textColor: "{colors.red-ink}"
    typography: "{typography.caption}"
    rounded: "{rounded.control}"
    padding: "1px 7px 1px 5px"
  chip-warning:
    backgroundColor: "{colors.amber-wash}"
    textColor: "{colors.amber}"
    rounded: "{rounded.control}"
    padding: "1px 7px 1px 5px"
  chip-serious:
    backgroundColor: "{colors.orange-wash}"
    textColor: "{colors.orange}"
    rounded: "{rounded.control}"
    padding: "1px 7px 1px 5px"
  chip-good:
    backgroundColor: "{colors.green-wash}"
    textColor: "{colors.green}"
    rounded: "{rounded.control}"
    padding: "1px 7px 1px 5px"
  chip-neutral:
    backgroundColor: "{colors.leaf-2}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.control}"
    padding: "1px 7px 1px 5px"
  tab:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink-2}"
    padding: "14px 16px 12px"
  tab-active:
    textColor: "{colors.ink}"
  day-leaf:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.red}"
    typography: "{typography.display}"
    rounded: "{rounded.none}"
    padding: "26px 22px 22px"
  month-leaf:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "24px 12px 12px"
    height: "132px"
  date-leaf:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "3px 6px 2px"
  input:
    backgroundColor: "{colors.leaf}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 10px"
    height: "38px"
---

# Design System: Закупки

## Overview

**Creative North Star: "The Tear-Off Calendar"**

The page is printed like a Soviet wall calendar: the leaf is today, the task is printed on it, and everything else is dates ahead. Cool newsprint ground, black ink, and one calendar red reserved for "today" and "will run out". Condensed bold numerals carry dates and quantities; a plain humanist sans carries words. Structure comes from hairline rules, heavy 2px ink rules under section heads, the dotted perforation and the leaf stack, never from rounded cards.

Density is operational: supplier rows ranked by urgency in fixed columns, each with one download. Light and dark themes are complete twins; dark keeps the same ink-on-paper logic with lifted reds.

**Key Characteristics:**
- Paper leaves on a cool grey ground; square corners everywhere except 2px on controls.
- Red means "today / runs out". Nothing decorative is red.
- Numbers in condensed Oswald with tabular figures; words in Golos Text.
- Hairline and 2px ink rules instead of boxes.
- One soft leaf-lift shadow; no other depth.

## Colors

Grey newsprint and black ink with a single calendar red; status hues appear only inside chips and charts.

### Primary
- **Calendar Red** (red): the day number, the red header band of the today leaf and current month leaf, the date-leaf top bar, deficit counts, the active tab underline, the primary "download all" button. Dark: `#ef5a4b`.
- **Deep Calendar Red** (red-ink): primary button hover, critical chip text. **Red Wash** (red-wash): critical chip ground and "out of stock" chart bands.

### Neutral
- **Cool Newsprint** (ground): page background, sticky tabs, dialog sheet. Cool, never cream.
- **Leaf Paper** (leaf) and **Leaf Underside** (leaf-2): printed surfaces (leaves, tables, commentary) and their second tone (table heads, stacked leaves under the day leaf, neutral chips).
- **Ink** (ink), **Ink Grey** (ink-2), **Faint Ink** (ink-3): text in three strengths; ink also draws 2px section rules and the black header band of non-current month leaves.
- **Hairline** (rule) and **Strong Hairline** (rule-strong): row dividers, control borders, perforation dots.

### Status and charts
- **Amber / Orange / Green** with their washes: chip pairs for warning, serious and good. Always paired with an icon and a label.
- **Chart Blue** (blue) for sales bars, **series-2** orange for the forecast line, **series-3** green for "stock with plan". Charts use at most these plus red.
- **Focus Blue** (focus): focus outline only.

### Named Rules
**The Red Day Rule.** Red marks only what is today or what will run out before arrival. If an element is red, the buyer must act on it.

**The Twin Theme Rule.** Every token has a dark twin declared both under `prefers-color-scheme: dark` and `[data-theme="dark"]`; never hardcode a light-only value.

## Typography

**Display / Numeral Font:** Oswald 500–600 (with Arial Narrow, system-ui)
**Body Font:** Golos Text 400–700 (with system-ui, Segoe UI, Roboto)

**Character:** Calendar-print numerals against a quiet, legible Russian humanist sans. Body runs with tabular figures so columns of quantities align.

**Known deviation:** both families load from Google Fonts (the page is served from GitHub Pages and opened online), not self-hosted; the system fallbacks above keep the offline file legible.

### Hierarchy
- **Display** (Oswald 600, 132px, 0.95; 84px on phones): the day number on the today leaf only, in red.
- **Headline** (Golos 600, clamp 24–32px, 1.2, max 28ch, balanced): the one-line conclusion. Numbers and units joined with NBSP.
- **Title** (Golos 700, 22px): section heads above a 2px ink rule; panel subheads 18px, dialog subheads 15px.
- **Figure** (Oswald 500, 22px row cells, 28px marginalia, 34px/600 month leaves, 26px/600 date leaves, 20px dialog facts): every quantity and date number.
- **Body** (Golos 400, 15px, 1.5): minimum body size; tables 14px; reasons capped at 62–72ch.
- **Caption** (Golos 13px, ink-2): cell keys, sub-lines, hints, footer.
- **Label** (Golos 600, 12px, 0.1–0.12em, uppercase): the printed weekday and month name on leaves, and commentary column heads. Nowhere else.

### Named Rules
**The Numerals Are Print Rule.** A number the buyer reads as a quantity or date is set in Oswald; prose numbers stay in Golos.

## Layout

Single column at max 1240px with 24px side padding (16px under 760px). The first screen is a two-column grid: the day leaf (220–280px) and the conclusion with buttons and three inline margin notes separated by hairlines under a 2px ink rule. Then «Заказать сегодня» supplier rows, then «Комментарий аналитика», then sticky tabs over panels. Sections are spaced 40px; tabs start 48px below.

Supplier rows are a fixed-column grid (name, positions, pieces, deficit, arrival date leaf, download, expand). Under 760px they reflow to name / four figures in a row / full-width download; deficit and arrival stay at all widths. Month leaves run 6 across, 4 under 1060px, 2 under 760px. Tabs wrap instead of scrolling on phones.

## Elevation & Depth

Flat paper with one lift. Only leaves (day leaf and its stack, month leaves, date leaves) and the tooltip carry the leaf shadow; everything else separates by tone (ground vs leaf) and rules.

### Shadow Vocabulary
- **Leaf lift** (`box-shadow: 0 1px 0 rgba(22,23,22,.06), 0 6px 18px -10px rgba(22,23,22,.22)`; dark `0 1px 0 rgba(0,0,0,.3), 0 8px 22px -12px rgba(0,0,0,.7)`): paper leaves only.

### Named Rules
**The Only Paper Lifts Rule.** If it is not a calendar leaf, it does not cast a shadow. Month leaves rise 3px on hover; nothing else moves.

## Shapes

Square paper, 0 radius on every surface. Controls (buttons, inputs, toggles, chips, tooltip) take a barely softened 2px. Leaves carry a solid top band (10px red on the day leaf, 6px ink or red on month leaves, 4px red on date leaves) and a 2px dotted perforation line just under it. The today leaf has two stacked underside leaves (leaf-2) peeking 5px and 10px below.

## Components

### Buttons
- **Shape:** 2px corners, 1px border, 9px 14px padding, 600 14px, 16px stroke icon at left.
- **Default:** leaf ground, ink border and text; hover inverts to ink ground, leaf text (0.15s).
- **Primary:** calendar red fill, white text; hover deep red. One per screen («Скачать все заказы (Excel)»).
- **Ghost:** transparent, strong-hairline border, weight 500; hover leaf ground.
- **Icon / toggle buttons:** 36px square, strong-hairline border, transparent.

### Chips
- **Style:** status chips, 13px 600, wash ground + matching ink, 14px stroke icon + label always.
- **State:** critical (red), warning (amber), serious (orange), good (green), neutral (leaf-2). Segmented list choosers: strong-hairline outline; pressed = ink fill, leaf text.

### Cards / Containers
- **Corner Style:** square.
- **Background:** leaf on ground; tables in a 1px hairline frame with leaf-2 sticky heads; category and commentary panels topped by a rule (1–3px ink).
- **Shadow Strategy:** none (see Elevation).
- **Internal Padding:** 16–22px.

### Inputs / Fields
- **Style:** leaf ground, 1px strong hairline, 2px corners, min-height 38px.
- **Focus:** global 2px focus-blue outline, 2px offset.

### Navigation
- **Tabs:** 600 14px ink-2 text with an Oswald count; active = ink text with a 3px red underline; sticky on the ground with a strong hairline beneath; wrap and unstick on phones.

### Day Leaf (signature)
Leaf paper with 10px red band, dotted perforation, uppercase weekday, red 132px day, Oswald month and year, a hairline-divided footnote. Sits on a two-sheet stack. On phones it turns horizontal: day at left, weekday and month at right.

### Month Leaf (signature)
Button-sized leaf: ink band (red for the current month), perforation, uppercase month, Oswald quantity, caption foot. Empty months are a dashed outline with no band. Selected = 2px ink outline.

### Date Leaf
A miniature leaf for arrival dates in supplier rows: 26px Oswald day under a 4px red bar with leaf lift, month and year as a caption beside it.

### Supplier Row
Hairline-separated row, no fill and no side stripe: bold supplier code with caption sub-line, labelled figure cells (deficit in red), date leaf, «Скачать заказ (Excel)», expand toggle opening its item table.

### Charts
Inline SVG, hairline gridlines, 12px ink-3 text, blue bars, 2px lines (forecast series-2, dashed where projected), red-wash bands for stockout months, dashed ink "сегодня" marker; ink tooltip with leaf text.

## Do's and Don'ts

### Do:
- **Do** keep red for today and for "runs out before arrival" only.
- **Do** set every quantity and date in Oswald with tabular figures.
- **Do** carry status as icon + label in a chip, never colour alone.
- **Do** separate with hairlines and 2px ink rules; keep surfaces square.
- **Do** keep body text at 15px or larger and define a dark twin for every new token.

### Don't:
- **Don't** build grey KPI-card grids or a sidebar; key numbers are inline margin notes.
- **Don't** round surfaces or add shadows to anything that is not a leaf.
- **Don't** add coloured side stripes to rows or multi-hue bars to month leaves.
- **Don't** use uppercase tracked labels as kickers above headings.
