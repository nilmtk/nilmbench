"use strict";

const filters = {
  task: document.getElementById("filter-task"),
  appliance: document.getElementById("filter-appliance"),
  scope: document.getElementById("filter-scope"),
  status: document.getElementById("filter-status"),
  model: document.getElementById("filter-model"),
};

const root = document.getElementById("leaderboard-root");
const boardStatus = document.getElementById("board-status");
const cohortTemplate = document.getElementById("cohort-template");
let sourceEntries = [];
let sourceRunCount = 0;

function uniqueValues(entries, key) {
  return [...new Set(entries.map((entry) => entry[key]).filter(Boolean))].sort(
    (left, right) => String(left).localeCompare(String(right)),
  );
}

function addOptions(select, values) {
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  }
}

function metric(mean, spread, count, digits = 2) {
  if (mean == null) return "—";
  const value = Number(mean).toFixed(digits);
  if (count > 1 && spread != null) {
    return `${value} ± ${Number(spread).toFixed(digits)}`;
  }
  return value;
}

function compactCount(value) {
  if (value == null) return "—";
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return Math.round(value).toString();
}

function elapsed(entry) {
  if (entry.elapsed_seconds_mean == null) return "—";
  return `${metric(
    entry.elapsed_seconds_mean,
    entry.elapsed_seconds_std,
    entry.run_count,
    1,
  )} s`;
}

function memory(value) {
  return value == null ? "—" : `${(Number(value) / 1048576).toFixed(1)} MiB`;
}

function setup(entry) {
  return [
    entry.sequence_length ? `seq ${entry.sequence_length}` : null,
    entry.epochs ? `${entry.epochs} epochs` : null,
    entry.max_samples_per_window
      ? `limit ${entry.max_samples_per_window}`
      : "full window",
  ]
    .filter(Boolean)
    .join(" · ");
}

function cohortKey(entry) {
  if (entry.ranking_protocol_sha256) return entry.ranking_protocol_sha256;
  return JSON.stringify({
    task: entry.task,
    appliance: entry.appliance,
    sample_period: entry.sample_period,
    profile: entry.profile,
    scope: entry.scope,
    target_data_access: entry.target_data_access,
    max_samples_per_window: entry.max_samples_per_window,
  });
}

function makeCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function statusCell(entry) {
  const cell = document.createElement("td");
  const status = document.createElement("span");
  const knownStatuses = new Set([
    "full-verified",
    "smoke-verified",
    "smoke-partial",
    "candidate",
    "smoke-unverified",
  ]);
  status.className = "status-chip";
  if (knownStatuses.has(entry.status)) status.classList.add(entry.status);
  status.textContent = entry.status || "unknown";
  cell.append(status);
  return cell;
}

function rowFor(entry) {
  const row = document.createElement("tr");
  const rank = Number.isInteger(entry.rank) ? entry.rank.toString() : "—";
  const seeds = Array.isArray(entry.seeds) ? entry.seeds.join(", ") : "—";

  row.append(
    makeCell(rank, "rank-cell"),
    makeCell(entry.model || "Unknown model", "model-cell"),
    makeCell(metric(entry.mae_mean, entry.mae_std, entry.run_count)),
    makeCell(metric(entry.f1_mean, entry.f1_std, entry.run_count)),
    makeCell(compactCount(entry.trainable_parameters_mean)),
    makeCell(elapsed(entry)),
    makeCell(memory(entry.peak_accelerator_memory_bytes_mean)),
    makeCell(`${entry.run_count || 0} · seeds ${seeds}`),
    makeCell(setup(entry), "setup-cell"),
    statusCell(entry),
  );
  return row;
}

function cohortFor(entries, key) {
  const first = entries[0];
  const protocol = first.ranking_protocol || {};
  const fragment = cohortTemplate.content.cloneNode(true);
  const section = fragment.querySelector(".cohort");
  const title = fragment.querySelector(".cohort-title");
  const meta = fragment.querySelector(".cohort-meta");
  const digest = fragment.querySelector(".cohort-digest");
  const body = fragment.querySelector("tbody");

  title.textContent = `${first.task} · ${first.appliance}`;
  meta.textContent = [
    `${first.sample_period}s resolution`,
    first.profile,
    first.scope,
    protocol.max_samples_per_window
      ? `limit ${protocol.max_samples_per_window}`
      : first.max_samples_per_window
        ? `limit ${first.max_samples_per_window}`
        : "full window",
    `target data: ${first.target_data_access || "not recorded"}`,
  ]
    .filter(Boolean)
    .join(" · ");
  digest.textContent = `cohort ${key.slice(0, 12)}`;
  digest.title = key;

  entries
    .slice()
    .sort(
      (left, right) =>
        (left.rank ?? Number.MAX_SAFE_INTEGER) -
          (right.rank ?? Number.MAX_SAFE_INTEGER) ||
        left.mae_mean - right.mae_mean,
    )
    .forEach((entry) => body.append(rowFor(entry)));

  section.dataset.cohort = key;
  return fragment;
}

function matchesFilters(entry) {
  const modelQuery = filters.model.value.trim().toLowerCase();
  return (
    (!filters.task.value || entry.task === filters.task.value) &&
    (!filters.appliance.value ||
      entry.appliance === filters.appliance.value) &&
    (!filters.scope.value || entry.scope === filters.scope.value) &&
    (!filters.status.value || entry.status === filters.status.value) &&
    (!modelQuery || String(entry.model).toLowerCase().includes(modelQuery))
  );
}

function render() {
  const entries = sourceEntries.filter(matchesFilters);
  const cohorts = new Map();
  for (const entry of entries) {
    const key = cohortKey(entry);
    if (!cohorts.has(key)) cohorts.set(key, []);
    cohorts.get(key).push(entry);
  }

  root.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No published results match these filters.";
    root.append(empty);
  } else {
    [...cohorts.entries()]
      .sort(([, left], [, right]) => {
        const leftLabel = `${left[0].task}/${left[0].appliance}`;
        const rightLabel = `${right[0].task}/${right[0].appliance}`;
        return leftLabel.localeCompare(rightLabel);
      })
      .forEach(([key, cohort]) => root.append(cohortFor(cohort, key)));
  }

  root.setAttribute("aria-busy", "false");
  boardStatus.textContent = `${entries.length} of ${sourceEntries.length} aggregates shown across ${cohorts.size} evaluation cohort${cohorts.size === 1 ? "" : "s"}.`;
}

function setScopeNotice(entries) {
  const notice = document.getElementById("scope-notice");
  const scopes = new Set(entries.map((entry) => entry.scope));
  notice.replaceChildren();
  const heading = document.createElement("strong");
  const detail = document.createTextNode(
    scopes.size === 1 && scopes.has("smoke")
      ? " Every published row currently uses a short, limited data window. These results verify the real GPU pipeline and enable quick comparisons; they do not replace the paper or full T1/T2/T3 benchmark."
      : " Smoke and full results are shown in separate evaluation cohorts and carry different verification labels.",
  );
  heading.textContent =
    scopes.size === 1 && scopes.has("smoke")
      ? "T0 smoke leaderboard — not a full benchmark claim."
      : "Mixed benchmark scopes are published.";
  notice.append(heading, detail);
}

function setStats(entries, runCount) {
  document.getElementById("stat-runs").textContent = runCount.toString();
  document.getElementById("stat-entries").textContent = entries.length.toString();
  document.getElementById("stat-models").textContent = new Set(
    entries.map((entry) => entry.model),
  ).size.toString();
  document.getElementById("stat-cohorts").textContent = new Set(
    entries.map(cohortKey),
  ).size.toString();
}

function configureFilters(entries) {
  addOptions(filters.task, uniqueValues(entries, "task"));
  addOptions(filters.appliance, uniqueValues(entries, "appliance"));
  addOptions(filters.scope, uniqueValues(entries, "scope"));
  addOptions(filters.status, uniqueValues(entries, "status"));

  document
    .getElementById("leaderboard-filters")
    .addEventListener("input", render);
}

async function loadLeaderboard() {
  try {
    const response = await fetch("leaderboard.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    sourceEntries = Array.isArray(payload.entries) ? payload.entries : [];
    sourceRunCount = Number(payload.source_result_count || 0);

    configureFilters(sourceEntries);
    setStats(sourceEntries, sourceRunCount);
    setScopeNotice(sourceEntries);
    render();
  } catch (error) {
    root.replaceChildren();
    root.setAttribute("aria-busy", "false");
    const message = document.createElement("div");
    message.className = "error-state";
    message.textContent =
      "Leaderboard artifact unavailable. Regenerate leaderboard.json from results/published and try again.";
    root.append(message);
    boardStatus.textContent = `Could not load leaderboard.json: ${error.message}`;
  }
}

loadLeaderboard();
