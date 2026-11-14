# Traasu brand

**Bill sariga undali.** — *Every bill, weighed right.*

The mark is a balance scale whose beam and stem form a **T**. The name means scale in Telugu (త్రాసు), and
the promise is the same one the software makes: the weight is right, the price is right, the account is right.

## Palette

| Token | Hex | Use |
|---|---|---|
| `pine` | `#FDEFD6` | background, receipt paper, app canvas |
| `burgundy` | `#731328` | mark, wordmark, primary text on pine |
| `butter` | `#FEC40B` | the fulcrum only — one accent per surface, never as text |
| `ink` | `#002941` | alternative primary when burgundy feels too heavy (dark UI, admin web) |

Burgundy on pine is the default. Ink is the fallback for dense admin screens where burgundy at small sizes
reads as brown.

## Files

| File | Use |
|---|---|
| `traasu/mark.svg` | primary mark, 2:1, for ≥32px |
| `traasu/mark-mono.svg` | one colour, for thermal receipts and 1-bit print |
| `traasu/icon-16.svg` | 16px favicon / taskbar icon — the T alone, fulcrum on top |
| `traasu/lockup.svg` | mark + wordmark, transparent |
| `traasu/lockup-bg.svg` | lockup on pine |
| `quiver/traasu-02-lockup.svg` | the original QuiverAI export, unmodified |

## The 16px rule

The full mark does not survive below 32px: the pans merge into the beam and the fulcrum disappears. That is
why `icon-16.svg` exists as a separate drawing — a bold T with the fulcrum sitting *above* the beam, where
rasterisation cannot swallow it. Do not scale the mark down to 16px; use the icon.

## Wordmark

The Quiver lockup sets the mark as the **T** of `traasu`, so the mark and the first letter are the same shape.
Keep that: a separate mark plus a full wordmark would repeat the T twice.

## Colour correction

The Quiver export approximated the palette (`#FDEECD` background, `#800D2E` mark, `#FBC008` fulcrum). The files
in `traasu/` are corrected to `#FDEFD6`, `#731328`, `#FEC40B`. Do not re-export over them.

## Not yet done

- Telugu lockup (త్రాసు) — needs a Noto Sans Telugu outline pass and a receipt test.
- Vector versions of the wordmark at weights other than the one Quiver produced.
- A one-colour lockup for the receipt footer.
