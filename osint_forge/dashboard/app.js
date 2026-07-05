const state = {
  events: [
    {
      id: "evt-001",
      source: "gdelt",
      confidence: 0.78,
      timestamp: "2026-07-01T08:42:00Z",
      location: "Gdansk, PL",
      x: 58,
      y: 37,
      severity: "high",
      event_type: "TRANSPORT",
      text: "Port access delays reported by two public sources.",
      provenance: ["gdelt:doc:gdansk-port-001", "raw:8c31"],
      sourceCount: 2,
    },
    {
      id: "evt-002",
      source: "telegram",
      confidence: 0.52,
      timestamp: "2026-07-01T09:10:00Z",
      location: "Klaipeda, LT",
      x: 63,
      y: 33,
      severity: "medium",
      event_type: "LOGISTICS",
      text: "Authorized channel reports rerouted truck queues.",
      provenance: ["telegram:-1007:4421"],
      sourceCount: 1,
    },
    {
      id: "evt-003",
      source: "reddit",
      confidence: 0.44,
      timestamp: "2026-07-01T09:18:00Z",
      location: "Riga, LV",
      x: 66,
      y: 30,
      severity: "low",
      event_type: "NORMAL_OPS",
      text: "Community post mentions normal ferry operations.",
      provenance: ["reddit:t3_sample"],
      sourceCount: 1,
    },
  ],
  missions: [
    { id: "m-100", name: "Baltic Port Watch", stage: "verification", gate: "SAT review", blocked: false },
    { id: "m-101", name: "Energy Protest Monitor", stage: "collection", gate: "source quota", blocked: true },
    { id: "m-102", name: "Airport Disruption Sweep", stage: "reporting", gate: "citations", blocked: false },
  ],
  sources: [
    { name: "GDELT", lag: "90s", errorRate: "0.01", quota: "ok", last: "09:19Z", state: "ok" },
    { name: "Reddit", lag: "18m", errorRate: "0.08", quota: "tight", last: "09:02Z", state: "degraded" },
    { name: "Telegram", lag: "4s", errorRate: "0.00", quota: "ok", last: "09:20Z", state: "ok" },
    { name: "X", lag: "n/a", errorRate: "1.00", quota: "blocked", last: "none", state: "error" },
  ],
  claims: [
    {
      id: "claim-1",
      text: "Port delays increased after 08:30Z.",
      supports: ["evt-001", "evt-002"],
      contradicts: ["evt-003"],
      confidence: 0.67,
    },
    {
      id: "claim-2",
      text: "Riga ferry operations remain normal.",
      supports: ["evt-003"],
      contradicts: ["evt-001"],
      confidence: 0.46,
    },
  ],
  agents: [
    { id: "collector.gdelt", credits: 42, rep: 0.81, bid: "retrieve", outcome: "+6 verified", action: "won memory retrieval for port-delay cluster" },
    { id: "verifier.sat", credits: 36, rep: 0.88, bid: "challenge", outcome: "+3 contradiction", action: "challenged single-source normal-ops claim" },
    { id: "summarizer.1", credits: 12, rep: 0.54, bid: "write", outcome: "-2 weak claim", action: "submitted report paragraph proposal" },
  ],
};

const el = (id) => document.getElementById(id);

function render() {
  document.querySelector(".app-shell").dataset.state = "ready";
  el("loadingState").classList.add("hidden");
  hydrateFilters();
  const events = filteredEvents();
  el("eventCount").textContent = String(events.length);
  el("clusterCount").textContent = "2";
  el("confidenceAvg").textContent = `${Math.round(avg(events.map((event) => event.confidence)) * 100)}%`;
  el("graphSummary").textContent = `${state.claims.length} claims`;
  el("marketSummary").textContent = `${state.agents.length} bids`;
  el("contradictionCount").textContent = `${state.claims.filter((claim) => claim.contradicts.length).length} open`;
  el("clock").textContent = new Date().toISOString().slice(11, 16) + " UTC";
  renderMap(events);
  renderMissions();
  renderSources();
  renderGraph();
  renderAgents();
  renderContradictions();
  renderReport();
  renderTimeline(events);
  el("emptyState").classList.toggle("hidden", events.length > 0);
  selectEvent(events[0] || state.events[0]);
}

function renderMap(events) {
  const map = el("eventMap");
  map.innerHTML = "";
  events.forEach((event) => {
    const dot = document.createElement("button");
    dot.className = `event-dot ${event.severity}`;
    dot.style.left = `${event.x}%`;
    dot.style.top = `${event.y}%`;
    dot.title = `${event.source} ${Math.round(event.confidence * 100)}% ${event.location}`;
    dot.addEventListener("click", () => selectEvent(event));
    map.appendChild(dot);
  });
}

function renderMissions() {
  el("missionBoard").innerHTML = state.missions
    .map(
      (mission) => `
        <div class="row-card">
          <div>
            <div class="row-title">${mission.name}</div>
            <div class="row-meta">${mission.stage} | gate: ${mission.gate}</div>
          </div>
          <span class="tag">${mission.blocked ? "blocked" : "open"}</span>
        </div>
      `,
    )
    .join("");
}

function renderSources() {
  el("sourceHealth").innerHTML = state.sources
    .map(
      (source) => `
        <div class="health-row ${source.state}">
          <div class="row-title">${source.name}</div>
          <div class="health-meta">lag ${source.lag} | err ${source.errorRate}</div>
          <div class="health-meta">quota ${source.quota} | last ${source.last}</div>
        </div>
      `,
    )
    .join("");
}

function renderGraph() {
  const graph = el("memoryGraph");
  const nodes = [
    { id: "evt-001", label: "Gdansk delay", type: "event", x: 12, y: 26 },
    { id: "evt-002", label: "Truck queues", type: "event", x: 40, y: 48 },
    { id: "claim-1", label: "Delay increase", type: "claim", x: 62, y: 25 },
    { id: "claim-2", label: "Normal ops", type: "contradiction", x: 72, y: 62 },
  ];
  graph.innerHTML = nodes
    .map(
      (node) => `
        <button class="graph-node ${node.type}" style="left:${node.x}%;top:${node.y}%;" data-node="${node.id}">
          ${node.label}
        </button>
      `,
    )
    .join("");
  graph.querySelectorAll(".graph-node").forEach((node) => {
    node.addEventListener("click", () => {
      const claim = state.claims.find((item) => item.id === node.dataset.node);
      if (claim) selectClaim(claim);
    });
  });
}

function renderAgents() {
  el("agentMarket").innerHTML = state.agents
    .map(
      (agent) => `
        <button class="agent-row" type="button" data-agent="${agent.id}">
          <div>
            <div class="row-title">${agent.id}</div>
            <div class="agent-meta">${agent.bid} | ${agent.outcome}</div>
          </div>
          <span class="tag">${agent.credits} cr</span>
          <span class="tag">rep ${agent.rep}</span>
        </button>
      `,
    )
    .join("");
  el("agentMarket").querySelectorAll("[data-agent]").forEach((row) => {
    row.addEventListener("click", () => selectAgent(state.agents.find((agent) => agent.id === row.dataset.agent)));
  });
}

function renderContradictions() {
  el("contradictionQueue").innerHTML = state.claims
    .filter((claim) => claim.contradicts.length)
    .map(
      (claim) => `
        <div class="claim-card clickable" role="button" tabindex="0" data-claim="${claim.id}">
          <div class="row-title">${claim.text}</div>
          <div class="evidence-line">supports ${evidenceLinks(claim.supports)} | contradicts ${evidenceLinks(claim.contradicts)}</div>
          <div class="evidence-line">confidence ${Math.round(claim.confidence * 100)}%</div>
        </div>
      `,
    )
    .join("");
  el("contradictionQueue").querySelectorAll("[data-claim]").forEach((card) => {
    card.addEventListener("click", () => selectClaim(state.claims.find((claim) => claim.id === card.dataset.claim)));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        selectClaim(state.claims.find((claim) => claim.id === card.dataset.claim));
      }
    });
  });
}

function renderReport() {
  el("reportBuilder").innerHTML = state.claims
    .map(
      (claim) => `
        <div class="claim-card">
          <div class="row-title">${claim.text}</div>
          <div class="evidence-line">confidence ${Math.round(claim.confidence * 100)}%</div>
          <div class="evidence-line">supporting: ${evidenceLinks(claim.supports)}</div>
          <div class="evidence-line">contradicting: ${evidenceLinks(claim.contradicts)}</div>
        </div>
      `,
    )
    .join("");
  document.querySelectorAll("[data-evidence]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.stopPropagation();
      selectEvent(state.events.find((item) => item.id === link.dataset.evidence));
    });
  });
}

function renderTimeline(events) {
  el("timeline").innerHTML = events
    .map(
      (event) => `
        <button class="timeline-event" type="button" data-event="${event.id}">
          <strong>${event.timestamp.slice(11, 16)} ${event.source}</strong>
          <span>${event.text}</span>
          <span>${event.location} | ${event.event_type} | conf ${Math.round(event.confidence * 100)}% | sources ${event.sourceCount}</span>
          <span>prov ${event.provenance.join(" | ")}</span>
        </button>
      `,
    )
    .join("");
  el("timeline").querySelectorAll("[data-event]").forEach((item) => {
    item.addEventListener("click", () => selectEvent(state.events.find((event) => event.id === item.dataset.event)));
  });
}

function selectEvent(event) {
  if (!event) return;
  el("selectedType").textContent = "event";
  el("inspector").innerHTML = [
    field("ID", event.id),
    field("Source", event.source),
    field("Confidence", `${Math.round(event.confidence * 100)}%`),
    field("Timestamp", event.timestamp),
    field("Location", event.location),
    field("Source Count", String(event.sourceCount)),
    field("Provenance", event.provenance.join(" | ")),
    field("Text", event.text),
  ].join("");
}

function selectClaim(claim) {
  if (!claim) return;
  el("selectedType").textContent = "claim";
  el("inspector").innerHTML = [
    field("ID", claim.id),
    field("Confidence", `${Math.round(claim.confidence * 100)}%`),
    field("Supports", claim.supports.join(" | ")),
    field("Contradicts", claim.contradicts.join(" | ")),
    field("Claim", claim.text),
  ].join("");
}

function selectAgent(agent) {
  if (!agent) return;
  el("selectedType").textContent = "agent";
  el("inspector").innerHTML = [
    field("ID", agent.id),
    field("Bid", agent.bid),
    field("Credits", String(agent.credits)),
    field("Reputation", String(agent.rep)),
    field("Outcome", agent.outcome),
    field("Action", agent.action),
  ].join("");
}

function evidenceLinks(ids) {
  return ids.map((id) => `<button class="inline-link" type="button" data-evidence="${id}">${id}</button>`).join(" ");
}

function field(label, value) {
  return `<div class="field"><label>${label}</label><span>${value}</span></div>`;
}

function avg(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

el("globalSearch").addEventListener("input", (event) => {
  render();
});

["sourceFilter", "timeFilter", "confidenceFilter", "eventTypeFilter"].forEach((id) => {
  el(id).addEventListener("input", () => {
    el("confidenceFilterValue").textContent = `${el("confidenceFilter").value}%`;
    render();
  });
});

document.querySelectorAll(".rail-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".rail-item").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
  });
});

el("draftReport").addEventListener("click", () => {
  selectClaim(state.claims[0]);
});

window.addEventListener("error", (event) => {
  el("errorState").classList.remove("hidden");
  el("errorState").textContent = event.message;
});

setTimeout(render, 180);

function hydrateFilters() {
  hydrateSelect("sourceFilter", unique(state.events.map((event) => event.source)));
  hydrateSelect("eventTypeFilter", unique(state.events.map((event) => event.event_type)));
  el("confidenceFilterValue").textContent = `${el("confidenceFilter").value}%`;
}

function hydrateSelect(id, values) {
  const select = el(id);
  const current = select.value;
  const first = select.querySelector("option");
  select.innerHTML = "";
  select.appendChild(first || new Option("All", ""));
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  select.value = values.includes(current) ? current : "";
}

function filteredEvents() {
  const query = el("globalSearch").value.toLowerCase();
  const source = el("sourceFilter").value;
  const type = el("eventTypeFilter").value;
  const minConfidence = Number(el("confidenceFilter").value) / 100;
  const hours = Number(el("timeFilter").value || 0);
  const cutoff = hours ? Date.now() - hours * 60 * 60 * 1000 : 0;
  return state.events.filter((event) => {
    if (query && !JSON.stringify(event).toLowerCase().includes(query)) return false;
    if (source && event.source !== source) return false;
    if (type && event.event_type !== type) return false;
    if (event.confidence < minConfidence) return false;
    if (cutoff && new Date(event.timestamp).getTime() < cutoff) return false;
    return true;
  });
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean))).sort();
}
