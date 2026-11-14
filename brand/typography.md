# Typography

Three roles, three families, all OFL — free to embed in the Wails binary and on a thermal printer.

| Role | Family | Why |
|---|---|---|
| UI and data (Latin) | **Inter** | Best legibility at 12–14px on a 1366×768 Windows panel; true tabular figures; variable weight |
| UI (Telugu) | **Noto Sans Telugu** | Complete conjunct coverage, variable, neutral enough to sit beside Inter |
| Display and marketing | **Anek Telugu + Anek Latin** | Modern, variable (weight 100–800, width 75–125), one design across both scripts |

The wordmark is not a font — it is the vector from `brand/traasu/lockup.svg`. Do not typeset "traasu"; the mark is the T.

## Why Inter for the counter

A billing screen is a table of numbers that must line up. Inter ships `tnum`, so `₹1,23,456.00` occupies the same width as `₹9,99,999.00` and the amount column stops jittering as the cart changes. It also has a large x-height and open apertures, which is what survives a cheap 1366×768 panel viewed at arm's length under shop lighting.

Manrope and Plus Jakarta Sans look more distinctive in a screenshot and lose on exactly this: at 12px, in a column, next to a rupee symbol, Inter is quieter and faster to read. Personality belongs in the mark and the marketing page, not in the number the cashier checks.

## Why Noto Sans Telugu for the UI

- Full shaping for the conjuncts that matter — `త్రాసు` must render as a single cluster, not as `త` + `ర` + `ా`.
- Variable weight, so the same family covers body and emphasis.
- Neutral tone, so it does not fight Inter on the same screen.
- Noto also publishes a **UI cut** (`Noto Sans Telugu UI`, 958 glyphs, 11 OpenType features) with tighter spacing for small sizes. If the Wails bundle can carry it, prefer it over the Google Fonts cut for 12–14px text.

## Why Anek for display only

Anek is the modern, opinionated option: Ek Type designed Latin and Telugu together, so a Telugu headline and an English subhead share a skeleton instead of looking like two fonts glued together. Use it on the website, the box, and the WhatsApp header. Do not use it in the cart — its width axis tempts you into tight tracking that breaks at 12px.

## Type scale (POS screen, 1366×768)

| Element | Size / line-height | Weight | Features |
|---|---|---|---|
| Cart item name | 14 / 20 | 500 | — |
| Cart qty, rate, amount | 14 / 20 | 400 | `tnum` |
| Column headers | 11 / 16 | 600 | uppercase, `+0.04em` |
| Bill total | 22 / 28 | 600 | `tnum` |
| Keypad digits | 18 / 24 | 500 | `tnum` |
| Status / sync line | 12 / 16 | 400 | — |
| Telugu item name | 15 / 26 | 400 | — |

Telugu needs the taller line-height: matras sit above and below the baseline, and 20px will clip them.

**Verified, not assumed.** I rendered `త్రాసు` at 48px and at 14px. At 48px the `ra` vattu (the subscript hook)
is clean. At 14px it collapses into a smudge — the conjunct is still shaped correctly, but the subscript is
two pixels of grey. That is why Telugu text starts at 15px on screen and why the receipt renders Telugu at a
larger effective size than Latin, not the same.

## Tokens

```css
:root {
  --font-ui: "Inter", "Noto Sans Telugu", system-ui, sans-serif;
  --font-display: "Anek Latin", "Anek Telugu", sans-serif;
  --font-num: "Inter", system-ui, sans-serif;
}

.num, td.amount, .total {
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1, "cv05" 1; /* cv05: single-storey l, clearer in codes */
}
```

Load once, locally:

```html
<link rel="preload" as="font" type="font/woff2" href="/fonts/inter-var.woff2" crossorigin>
<link rel="preload" as="font" type="font/woff2" href="/fonts/noto-sans-telugu-var.woff2" crossorigin>
```

Self-host. Do not call `fonts.googleapis.com` from the app or the admin web — it breaks offline, leaks the operator's IP on every launch, and adds a network dependency to a screen that must open in 200ms.

## Printing

Latin receipts can stay as ESC/POS native text — fast, tiny, and every printer has the glyphs.

Telugu cannot. ESC/POS has no shaping, so `తెలుగు` must be rasterised: render the line to a 1-bit bitmap with Noto Sans Telugu, send it with `GS v 0`. Practical consequences:

- Embed the font in the Go binary (`go:embed`) and render with `golang.org/x/image/font/opentype`.
- Cache a glyph atlas per size; re-rendering every receipt wastes 30–80ms per line.
- Mixed receipts are normal: Latin lines as text, Telugu lines as raster. Do not rasterise the whole receipt because one line is Telugu.
- Test on the actual printer. A 58mm head at 203dpi turns a 14px matra into two grey pixels.

## What to check before committing

1. Render `త్రాసు` in Noto Sans Telugu at 14px and at 58mm/203dpi. If the `tra` conjunct breaks, nothing else in this document matters.
2. Confirm Inter's `tnum` on Windows WebView2 — the feature is standard, but the fallback path matters if the font fails to load.
3. Decide whether the admin web gets Anek for headings or stays entirely Inter. My preference: entirely Inter, because the admin is a data tool.
