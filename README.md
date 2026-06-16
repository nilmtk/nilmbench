# NILMBench2026 — project website

Source for the **[NILMBench2026](https://sustainability-lab.github.io/nilmbench/)** project page —
*A Benchmark for Energy Disaggregation* (BuildSys '26, **Best Paper Candidate**).

> One aggregate power signal in. Appliance-level estimates out. We benchmark **16 NILM models**
> across **3 datasets** and **2 resolutions** — on accuracy, efficiency, and generalization —
> and find that **generalization is the wall**.

**Authors:** Aayush Kuloor\*, Anurag Singh\*, Harsh Dhru\*, Nipun Batra† · IIT Gandhinagar
(\* equal contribution, † corresponding author)

## Links

- 📄 **Paper:** https://sustainability-lab.github.io/papers/2026/nilmbench2026_buildsys.pdf
- 💻 **Code (modernized NILMTK):** https://github.com/nilmtk/nilmtk-contrib
- 🌐 **Website:** https://sustainability-lab.github.io/nilmbench/

## About this repo

A single self-contained `index.html` (no build step) deployed to GitHub Pages via GitHub Actions.

```
index.html              # the entire site (HTML + CSS + JS inline)
static/images/          # paper figures (real UK-DALE / REDD / REFIT predictions)
.github/workflows/      # GitHub Pages deploy workflow
```

- **Theme:** dark/light toggle, an "energy / power-spectrum" palette.
- **Hero:** animated SVG of the disaggregation task (aggregate mains → fridge / washing machine / microwave / kettle).
- **Results explorer:** the paper's tables, heat-mapped (green = good, red = bad), with best/second highlighting.
- **Extend & Compete:** how to add a model / metric, and the leaderboard vision — *NILM's ImageNet*.

## Develop locally

Just open `index.html` in a browser, or serve the folder:

```bash
python3 -m http.server 8000   # then visit http://localhost:8000
```

## Cite

```bibtex
@inproceedings{kuloor2026nilmbench,
  title     = {NILMBench2026: A Benchmark for Energy Disaggregation},
  author    = {Kuloor, Aayush and Singh, Anurag and Dhru, Harsh and Batra, Nipun},
  booktitle = {Proceedings of the 13th ACM International Conference on Systems for
               Energy-Efficient Buildings, Cities, and Transportation (BuildSys '26)},
  year      = {2026},
  doi       = {10.1145/3744256.3812587},
  publisher = {ACM},
  address   = {Banff, AB, Canada}
}
```