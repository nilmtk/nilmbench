"use strict";

async function loadHomeSummary() {
  const modelCount = document.getElementById("home-model-count");
  const entryCount = document.getElementById("home-entry-count");
  const runCount = document.getElementById("home-run-count");
  const leader = document.getElementById("home-current-leader");

  try {
    const response = await fetch("leaderboard.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const payload = await response.json();
    const entries = Array.isArray(payload.entries) ? payload.entries : [];
    const models = new Set(entries.map((entry) => entry.model));
    const firstPlaces = entries
      .filter((entry) => entry.rank === 1)
      .sort((left, right) => left.task.localeCompare(right.task));

    modelCount.textContent = models.size.toString();
    entryCount.textContent = entries.length.toString();
    runCount.textContent = Number(payload.source_result_count || 0).toString();

    if (firstPlaces.length === 1) {
      const top = firstPlaces[0];
      leader.textContent = `Current smoke leader: ${top.model} · ${Number(top.mae_mean).toFixed(2)} W MAE`;
    } else if (firstPlaces.length > 1) {
      leader.textContent = `${firstPlaces.length} evaluation cohorts currently published`;
    } else {
      leader.textContent = "No ranked cohort has been published yet.";
    }
  } catch (error) {
    modelCount.textContent = "—";
    entryCount.textContent = "—";
    runCount.textContent = "—";
    leader.textContent = "Leaderboard artifact is temporarily unavailable.";
  }
}

loadHomeSummary();
