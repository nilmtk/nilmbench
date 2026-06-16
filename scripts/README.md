# Slide figure generation

Scripts that regenerate every figure in `slides/figs/` used by
`slides/nilmbench2026.md`. Edit a script, rerun it, rebuild the deck.

## Setup
```bash
pip install pandas matplotlib remotezip          # for figures.py
npm i playwright-core && npx playwright install chromium   # for render.mjs
```

## Vector figures — diagrams, icons, model tree, task diagrams
```bash
python scripts/diagrams.py     # -> scripts/_diagrams.html
node   scripts/render.mjs       # -> slides/figs/{seq2point,transformer,fhmm,model_tree,task_t1..3,ic-*}.png
```
`render.mjs` screenshots every `<div data-fig>` at 3x. If it can't find a
browser, set `CHROMIUM_PATH=/path/to/Chromium`.

## Data figures — decomposition, signatures, Hart, efficiency
```bash
python scripts/figures.py      # streams real UK-DALE house 1 from CEDA
```
Only the chosen ~1-day window is downloaded (HTTP range via `remotezip`, not the
full 3.3 GB). The appliance icons (`ic-*.png`) are overlaid if you ran the
vector step first. To use a different house/day or a local `ukdale.h5`, edit the
constants at the top of `figures.py`.

> Data: UK-DALE (Kelly & Knottenbelt, 2015, CC-BY). The paper's result figures
> (`generalization_failure.png`, `microwave_miss.png`, `github_issues.png`) come from the paper's own
> runs / repo and are not regenerated here.

## Rebuild the deck
```bash
cd slides
npx @marp-team/marp-cli nilmbench2026.md --pdf --html --allow-local-files
```
Fonts are local in `slides/fonts/` (no network needed). If the PDF build hangs
on Chromium, build `--html` only and print to PDF from the HTML with a headless
browser.
