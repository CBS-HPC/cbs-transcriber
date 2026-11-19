"use strict";

// Branch view (ADR 0182): each saved result is a lane of its versions, oldest on the left.
// Derived results branch from their exact source version; Apply safe updates, conflict
// decisions and wording adoption draw edges from the version they took updates from.
// Metadata only: text appears just in the selected version's "What changed".
const graphNamespace = "http://www.w3.org/2000/svg";
const graphLayout = { left: 230, step: 36, top: 34, lane: 56 };
let historyGraphView = null,
  graphRequest = 0,
  graphDetailRequest = 0;

function graphElement(name, attributes = {}, text = null) {
  const node = document.createElementNS(graphNamespace, name);
  for (const [key, value] of Object.entries(attributes))
    node.setAttribute(key, String(value));
  if (text !== null) node.textContent = text;
  return node;
}
function showHistoryTab(graph) {
  document
    .getElementById("history-dialog")
    .classList.toggle("showing-graph", graph);
  document.getElementById("history-graph").hidden = !graph;
  for (const [id, selected] of [
    ["history-list-tab", !graph],
    ["history-graph-tab", graph],
  ]) {
    const tab = document.getElementById(id);
    tab.setAttribute("aria-selected", String(selected));
    if (selected) tab.removeAttribute("tabindex");
    else tab.setAttribute("tabindex", "-1");
  }
}
function graphLaneName(lane) {
  return lane.source?.kind === "provider"
    ? configurationLabel(lane.label, lane.source.model)
    : lane.label;
}
function graphLaneLabel(lane, data) {
  const parts = [
    `${graphLaneName(lane)} · ${new Date(lane.created).toLocaleDateString("en-GB")}`,
  ];
  if (lane.id === data.active_result_id) parts.push("Reviewing");
  if (lane.trashed) parts.push("Trash");
  return parts.join(" · ");
}
function graphVersion(data, versionId) {
  for (const lane of data.results)
    for (const version of lane.versions)
      if (version.id === versionId) return { lane, version };
  return null;
}
// Collapse runs of ordinary versions; keep ends, Working, links and undo/redo visible.
function graphItems(view) {
  const { data, expanded } = view,
    working = data.working?.version_id,
    anchors = new Set();
  for (const edge of data.edges)
    anchors.add(edge.from.version_id).add(edge.to.version_id);
  for (const lane of data.results)
    if (lane.branch && typeof lane.branch === "object")
      anchors.add(lane.branch.version_id);
  const items = [];
  data.results.forEach((lane, laneIndex) => {
    let run = [];
    const flush = () => {
      if (run.length >= 3)
        items.push({ lane, laneIndex, versions: run, key: `run:${run[0].id}` });
      else
        run.forEach((version) =>
          items.push({ lane, laneIndex, versions: [version], key: version.id }),
        );
      run = [];
    };
    lane.versions.forEach((version, index) => {
      const keep =
        index === 0 ||
        index === lane.versions.length - 1 ||
        version.id === working ||
        anchors.has(version.id) ||
        version.action === "change_undo" ||
        version.action === "change_redo" ||
        expanded.has(version.id);
      if (!keep) {
        run.push(version);
        return;
      }
      flush();
      items.push({ lane, laneIndex, versions: [version], key: version.id });
    });
    flush();
  });
  [...items]
    .sort(
      (a, b) =>
        (Date.parse(a.versions[0].observed_at) || 0) -
          (Date.parse(b.versions[0].observed_at) || 0) ||
        a.laneIndex - b.laneIndex ||
        a.versions[0].sequence - b.versions[0].sequence,
    )
    .forEach((item, column) => {
      item.column = column;
    });
  return items;
}
function graphItemLabel(item, data) {
  const lane = graphLaneLabel(item.lane, data),
    [first] = item.versions,
    last = item.versions.at(-1);
  if (item.versions.length > 1)
    return `${item.versions.length} versions, ${first.sequence} to ${last.sequence}, in ${lane}. Expand.`;
  return `Version ${first.sequence} · ${historyChangeName(first.action)} · ${lane}${first.id === data.working?.version_id ? " · Working" : ""}`;
}
function drawHistoryGraph(view) {
  const svg = document.getElementById("history-graph-svg"),
    { left, step, top, lane: laneHeight } = graphLayout,
    items = graphItems(view),
    byVersion = new Map(),
    x = (item) => left + item.column * step,
    y = (item) => top + item.laneIndex * laneHeight;
  view.items = new Map(items.map((item) => [item.key, item]));
  items.forEach((item) =>
    item.versions.forEach((version) => byVersion.set(version.id, item)),
  );
  const width = left + items.length * step + 40,
    height = top + view.data.results.length * laneHeight;
  svg.replaceChildren();
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const marker = graphElement("marker", {
    id: "history-graph-arrow",
    viewBox: "0 0 10 10",
    refX: 9,
    refY: 5,
    markerWidth: 6,
    markerHeight: 6,
    orient: "auto",
  });
  marker.append(
    graphElement("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "graph-arrow" }),
  );
  const defs = graphElement("defs");
  defs.append(marker);
  svg.append(defs);
  const curve = (from, to) => {
    const x1 = x(from),
      y1 = y(from),
      x2 = x(to) - 9,
      y2 = y(to);
    return `M ${x1} ${y1} C ${x1 + 20} ${y1}, ${x2 - 20} ${y2}, ${x2} ${y2}`;
  };
  view.data.results.forEach((lane, laneIndex) => {
    const laneItems = items.filter((item) => item.laneIndex === laneIndex),
      row = top + laneIndex * laneHeight,
      group = graphElement("g", {
        class: `graph-lane${lane.trashed ? " graph-trashed" : ""}${lane.id === view.data.active_result_id ? " graph-active" : ""}`,
      });
    const name = graphLaneLabel(lane, view.data);
    const label = graphElement(
      "text",
      { x: 8, y: row + 4, class: "graph-label" },
      name.length > 34 ? `${name.slice(0, 33)}…` : name,
    );
    label.append(graphElement("title", {}, name));
    group.append(label);
    if (lane.truncated)
      group.append(
        graphElement(
          "text",
          { x: left - 26, y: row - 10, class: "graph-tag" },
          `+${lane.total_versions - lane.versions.length} earlier`,
        ),
      );
    if (laneItems.length > 1)
      group.append(
        graphElement("line", {
          x1: x(laneItems[0]),
          y1: row,
          x2: x(laneItems.at(-1)),
          y2: row,
          class: "graph-line",
        }),
      );
    svg.append(group);
    const first = laneItems[0];
    if (!first) return;
    if (lane.branch === "unknown")
      svg.append(
        graphElement("line", {
          x1: x(first) - 28,
          y1: row,
          x2: x(first) - 9,
          y2: row,
          class: "graph-edge graph-unknown",
        }),
      );
    else if (lane.branch && byVersion.has(lane.branch.version_id))
      svg.append(
        graphElement("path", {
          d: curve(byVersion.get(lane.branch.version_id), first),
          class: "graph-edge",
          "marker-end": "url(#history-graph-arrow)",
        }),
      );
  });
  for (const edge of view.data.edges) {
    const from = byVersion.get(edge.from.version_id),
      to = byVersion.get(edge.to.version_id);
    if (from && to)
      svg.append(
        graphElement("path", {
          d: curve(from, to),
          class: "graph-edge graph-merge",
          "marker-end": "url(#history-graph-arrow)",
        }),
      );
  }
  const working = view.data.working?.version_id;
  for (const item of items) {
    const [version] = item.versions,
      cx = x(item),
      cy = y(item),
      label = graphItemLabel(item, view.data),
      node = graphElement("g", {
        class: `graph-node${view.selected === item.key ? " graph-selected" : ""}`,
        role: "button",
        tabindex: 0,
        "data-graph-key": item.key,
        "aria-label": label,
      });
    if (item.versions.length > 1)
      node.append(
        graphElement("rect", {
          x: cx - 14,
          y: cy - 9,
          width: 28,
          height: 18,
          rx: 9,
          class: "graph-run",
        }),
        graphElement(
          "text",
          { x: cx, y: cy + 4, "text-anchor": "middle", class: "graph-count" },
          String(item.versions.length),
        ),
      );
    else if (
      version.action === "change_undo" ||
      version.action === "change_redo"
    )
      node.append(
        graphElement("rect", {
          x: cx - 6,
          y: cy - 6,
          width: 12,
          height: 12,
          transform: `rotate(45 ${cx} ${cy})`,
          class: "graph-revert",
        }),
      );
    else
      node.append(
        graphElement("circle", {
          cx,
          cy,
          r: 7,
          class: `graph-dot${version.id === working ? " graph-working" : ""}`,
        }),
      );
    if (item.versions.length === 1 && version.id === working)
      node.append(
        graphElement(
          "text",
          { x: cx, y: cy - 13, "text-anchor": "middle", class: "graph-tag" },
          "Working",
        ),
      );
    node.append(graphElement("title", {}, label));
    svg.append(node);
  }
}
function graphNote(view, version) {
  const describe = (id) => {
    const found = graphVersion(view.data, id);
    return found
      ? `version ${found.version.sequence} of ${graphLaneLabel(found.lane, view.data)}`
      : "an earlier version";
  };
  if (version.action === "change_undo" && version.undone_version_id)
    return `Undid ${describe(version.undone_version_id)}.`;
  if (version.action === "change_redo" && version.redone_version_id)
    return `Redid ${describe(version.redone_version_id)}.`;
  const edge = view.data.edges.find(
    (item) => item.to.version_id === version.id,
  );
  if (edge) return `Took updates from ${describe(edge.from.version_id)}.`;
  const lane = view.data.results.find((item) =>
    item.versions.some((entry) => entry.id === version.id),
  );
  if (version.sequence === 1 && lane?.branch === "unknown")
    return "Made from an unknown earlier version.";
  if (version.sequence === 1 && lane?.branch?.version_id)
    return `Made from ${describe(lane.branch.version_id)}.`;
  return "";
}
async function showGraphVersion(view, item) {
  const [version] = item.versions,
    lane = item.lane,
    detail = document.getElementById("history-graph-detail"),
    box = document.getElementById("history-graph-changes");
  view.detail = { lane, version };
  detail.hidden = false;
  document.getElementById("history-graph-title").textContent =
    `Version ${version.sequence} · ${historyChangeName(version.action)}`;
  document.getElementById("history-graph-meta").textContent =
    `${graphLaneName(lane)} · ${new Date(version.observed_at).toLocaleString("en-GB")}${lane.trashed ? " · Trash" : ""}`;
  const other =
    !lane.trashed && lane.id !== state.recording?.active_transcript_id;
  document.getElementById("history-graph-review").hidden = !other;
  document.getElementById("history-graph-compare").hidden = !other;
  document.getElementById("history-graph-working").disabled =
    version.id === state.recording?.result_roles?.working?.version_id;
  box.replaceChildren(document.createTextNode("Loading what changed…"));
  const request = ++graphDetailRequest;
  try {
    const result = await api(
      `/recordings/${view.context.id}/transcripts/${lane.id}/versions/${version.id}/changes`,
      { background: true },
    );
    if (
      historyGraphView !== view ||
      request !== graphDetailRequest ||
      !currentHistory(view.context)
    )
      return;
    renderChangeResult(box, result, graphNote(view, version));
  } catch (error) {
    if (historyGraphView === view && request === graphDetailRequest)
      box.textContent = `What changed is unavailable. ${error.message}`;
  }
}
async function loadHistoryGraph(context) {
  const status = document.getElementById("history-graph-status"),
    request = ++graphRequest;
  status.textContent = "Loading branch view…";
  document.getElementById("history-graph-detail").hidden = true;
  let data;
  try {
    data = await api(`/recordings/${context.id}/version-graph`, {
      background: true,
    });
  } catch (error) {
    if (currentHistory(context) && request === graphRequest)
      status.textContent = error.message;
    return;
  }
  if (!currentHistory(context) || request !== graphRequest) return;
  historyGraphView = {
    context,
    data,
    expanded: new Set(),
    selected: null,
    items: new Map(),
  };
  drawHistoryGraph(historyGraphView);
  status.textContent = data.limited
    ? "Only the first 50 results are shown."
    : "";
}
function activateGraphNode(target) {
  const view = historyGraphView,
    key = target?.closest?.("[data-graph-key]")?.dataset.graphKey;
  if (!view || !key || !currentHistory(view.context)) return;
  const item = view.items.get(key);
  if (!item) return;
  if (item.versions.length > 1) {
    item.versions.forEach((version) => view.expanded.add(version.id));
    drawHistoryGraph(view);
    document
      .querySelector(
        `#history-graph-svg [data-graph-key="${item.versions[0].id}"]`,
      )
      ?.focus();
    return;
  }
  view.selected = key;
  drawHistoryGraph(view);
  document
    .querySelector(`#history-graph-svg [data-graph-key="${key}"]`)
    ?.focus();
  return showGraphVersion(view, item);
}
const graphSvgElement = document.getElementById("history-graph-svg");
graphSvgElement.addEventListener(
  "click",
  handle((event) => activateGraphNode(event.target)),
);
graphSvgElement.addEventListener(
  "keydown",
  handle((event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    return activateGraphNode(event.target);
  }),
);
document.getElementById("history-graph-tab").addEventListener(
  "click",
  handle(async () => {
    const context = historyContext;
    if (!context || !currentHistory(context) || !featureOn("undo_last_change"))
      return;
    showHistoryTab(true);
    await loadHistoryGraph(context);
  }),
);
document
  .getElementById("history-list-tab")
  .addEventListener("click", () => showHistoryTab(false));
document.getElementById("history-dialog").addEventListener("close", () => {
  historyGraphView = null;
  graphRequest++;
  graphDetailRequest++;
  showHistoryTab(false);
});
// Existing actions only: review or compare another result, or mark a version Working.
function graphSelection() {
  const view = historyGraphView;
  return view?.detail && currentHistory(view.context) ? view.detail : null;
}
document.getElementById("history-graph-review").addEventListener(
  "click",
  handle(async () => {
    const selection = graphSelection(),
      select = document.getElementById("result-select");
    if (
      !selection ||
      ![...select.options].some((option) => option.value === selection.lane.id)
    )
      return;
    document.getElementById("history-dialog").close();
    select.value = selection.lane.id;
    select.dispatchEvent(new Event("change"));
  }),
);
document.getElementById("history-graph-compare").addEventListener(
  "click",
  handle(async () => {
    const selection = graphSelection(),
      select = document.getElementById("compare-result");
    if (
      !selection ||
      ![...select.options].some((option) => option.value === selection.lane.id)
    )
      return;
    document.getElementById("history-dialog").close();
    select.value = selection.lane.id;
    select.dispatchEvent(new Event("change"));
  }),
);
document.getElementById("history-graph-working").addEventListener(
  "click",
  handle(async () => {
    const selection = graphSelection();
    if (selection) await previewWorkingVersion(selection.version.id);
  }),
);
// A Working change made from the branch view redraws its Working tag.
document
  .getElementById("working-version-dialog")
  .addEventListener("close", () => {
    const view = historyGraphView;
    if (
      view &&
      currentHistory(view.context) &&
      !document.getElementById("history-graph").hidden
    )
      loadHistoryGraph(view.context).catch(() => {});
  });
