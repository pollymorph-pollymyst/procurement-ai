# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static single-file HTML (vanilla JS + inline SVG charts, data embedded as JSON), built by Python (`python -m procurement run`) into `docs/index.html` and served by GitHub Pages. No framework, no external runtime dependencies. Confirmed in the brief interview (brief.md).

## Users

Primary: the purchasing manager (закупщик) or owner of a small importer of power tools, garden equipment and plumbing from China. Not technical, may never have used AI tools, and does not want to know how the calculation works. Opens the page in the morning (desktop at the office, sometimes a phone) and needs to decide in about a minute what to order today, from which supplier, and how much.

Secondary: a recruiter/evaluator of this test task, who opens the link from Telegram and judges whether the picture of purchases is complete and decision-ready.

## Product Purpose

Turns a raw ERP export (sales, stock, open purchase orders, receipts, suppliers) into a purchase plan: what to buy and how much, how long current stock plus goods in transit will last, when to place the next order, and which items risk a shortage or an excess. Lead time is 4–6 months by sea, so decisions are made against a seasonal demand forecast, not current sales. Success: the buyer downloads one Excel file per supplier and sends it, without having to interpret anything.

## Positioning

A buyer's to-do list, not an analytics tool: the first screen answers "what do I do today", grouped by supplier, with one download button per order. Every recommendation carries a plain-language reason ("Сейчас на складе 0 шт. … закончится до прихода …").

## Operating Context

- Data arrives as a zip export dropped into the «Входящие» folder; the buyer double-clicks «Обновить анализ.bat». It also reruns daily (GitHub Actions / Windows scheduler) and shows what changed since the previous run.
- Orders leave as Excel files sent to suppliers.
- Dataset date 2026-08-13; 791 items, 6 categories, 39 suppliers; chronically out of stock (191 selling items at zero now); supplier fill rate is low (~39%).

## Capabilities and Constraints

- Russian-only UI. Russian category names. Dates in Russian format ("13 августа 2026").
- Never use the words ИИ, LLM, промпт, модель in the UI. The text block is called «Комментарий аналитика».
- Minimum clicks: first screen = one-line conclusion, four key numbers, orders by supplier each with «Скачать заказ (Excel)», plus «Скачать всё».
- Secondary views: order calendar, risks (не успеем довезти / избыток / мёртвый остаток / низкая наценка / нужен поставщик / редкий спрос), all items with search and an item card with a chart, what changed, forecast quality and seasonality, suppliers, methodology (collapsed).
- Must work offline as a local file and on GitHub Pages; light and dark; phone width.

## Evidence on Hand

Real (anonymised) ERP dataset in `data/raw/`; computed result in `outputs/latest/result.json`; Excel orders in `docs/orders/`. IDs are anonymised codes (SKU-xxxxxx, SUP-xxxxxx, BRD-xxxxxx); there are no product names, prices or photos, and none must be invented.

## Product Principles

1. Action first: what to do today beats how we computed it.
2. Every number has a plain reason next to it.
3. Nothing ordered twice: goods in transit are always visible and counted.
4. Exceptions are separated, not hidden: low markup, no supplier and rare demand never land in an order file silently.
5. Honest about uncertainty: doubtful orders and forecast quality are shown, not smoothed away.

## Accessibility & Inclusion

Status always carried by icon + label, not colour alone. Readable for an older, non-technical user: body text no smaller than 15px, high contrast.
