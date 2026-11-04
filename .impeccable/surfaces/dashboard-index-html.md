---
version: 1
slug: "dashboard-index-html"
primary_target: "dashboard/index.html"
related_targets: ["docs/index.html"]
---

# Dashboard: «Закупки» (docs/index.html, template dashboard/index.html)

Scope: the single dashboard page. Mode: Operate. Build path: code-led (Operate surface).

Audience/job: non-technical buyer decides in a minute what to order today and from whom, downloads Excel per supplier. Secondary: evaluator from Telegram.
Constraints: Russian UI, no AI words, «Комментарий аналитика», minimum clicks, offline file + GitHub Pages, light/dark, phone width.

Grounded candidates (by resonance): 1 warehouse rack labels and floor marking; 2 sea-freight sailing schedule; 3 container stencils and CSC plates; 4 dacha seasonal almanac; 5 bill of lading paperwork; 6 Soviet/Russian tear-off wall calendar (отрывной календарь); 7 hardware-store shelf price tags. Assigned: 6.
Challenger verdicts: split-flap concourse board — competitive (strong audience fit for "orders departing by date", loses clarity as a black board); kept: fixed columns, rows ranked by time. Terminal yellow wayfinding — declined; kept: one message per band, only the next decision first. Metro tiles — declined; kept: type-scale courage for the few numbers that matter. Saville sleeve, streaming wall — declined.
Run was unattended per user instruction to proceed without further questions; assigned direction built.

## Direction contract

THESIS: The question is *when*; the page is set like a tear-off calendar leaf: today's date and today's task printed as the leaf, everything else is dates ahead. Refuses the grey KPI-card grid with a sidebar.
OWN-WORLD: cool newsprint ground (not cream), black ink, «красный день» calendar red only for what must happen today/what will run out; condensed bold numerals for dates and quantities, plain humanist sans for words. Perforation line and leaf stack as structure. Hairline rules, no rounded cards, no shadows beyond the leaf lift.
STORY: Buyer sees "Сегодня 13 августа: заказать у 22 поставщиков", sees who first and why, presses one Excel button per supplier, optionally reads risks and dates ahead.
FIRST VIEWPORT: Left: the day leaf (big day number, month, weekday, one-line conclusion, «Скачать всё»). Right/below: four numbers set as calendar marginalia. Then supplier rows ranked by urgency, each with qty, arrival date as a leaf date, and «Скачать заказ (Excel)». Tabs below for calendar, risks, all items, changes, forecast, suppliers, method.
FORM: tear-off wall calendar, candidate 6 of 7. seed:0b0569cccf36b13c. Signature move: the order calendar as months of leaves where each order date is a torn-off-able leaf; raises: fixed columns/time-ranked rows (split-flap), one decision first (wayfinding), numeral scale courage (metro).
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
