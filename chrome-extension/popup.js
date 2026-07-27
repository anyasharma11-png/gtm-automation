const BASE = "https://api.hubapi.com";

const setupScreen = document.getElementById("setup-screen");
const dashboardScreen = document.getElementById("dashboard-screen");
const loadingScreen = document.getElementById("loading-screen");

const TIER_COLOR = {
  "Top": ["#166534", "#DCFCE7"], "Mid": ["#92400E", "#FEF3C7"],
  "Low": ["#475569", "#F1F5F9"], "Replied - In Sales Cycle": ["#1E40AF", "#DBEAFE"],
};
const HEALTH_COLOR = { "Green": ["#166534", "#DCFCE7"], "Yellow": ["#92400E", "#FEF3C7"],
                        "Red": ["#991B1B", "#FEE2E2"], "Neutral": ["#475569", "#F1F5F9"] };

function showScreen(name) {
  setupScreen.style.display = name === "setup" ? "block" : "none";
  dashboardScreen.style.display = name === "dashboard" ? "block" : "none";
  loadingScreen.style.display = name === "loading" ? "block" : "none";
}

function getStoredKey() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["hubspot_api_key"], (result) => resolve(result.hubspot_api_key));
  });
}

function saveKey(key) {
  return new Promise((resolve) => {
    chrome.storage.local.set({ hubspot_api_key: key }, resolve);
  });
}

async function fetchContacts(key) {
  const body = {
    filterGroups: [{ filters: [{ propertyName: "source_event", operator: "HAS_PROPERTY" }] }],
    properties: ["firstname", "lastname", "company", "jobtitle", "lead_score", "outreach_tier", "assigned_rep", "reengagement_touch_number", "last_processed_date"],
    limit: 100,
  };
  const r = await fetch(`${BASE}/crm/v3/objects/contacts/search`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HubSpot error ${r.status}`);
  const data = await r.json();
  return data.results || [];
}

async function fetchDeals(key) {
  const params = new URLSearchParams({ properties: "dealname,amount,dealstage,ai_coaching_brief", limit: "100" });
  const r = await fetch(`${BASE}/crm/v3/objects/deals?${params}`, {
    headers: { "Authorization": `Bearer ${key}` },
  });
  if (!r.ok) throw new Error(`HubSpot error ${r.status}`);
  const data = await r.json();
  return data.results || [];
}

function deriveHealth(brief) {
  const match = brief.match(/HEALTH:\s*(\w+)/);
  if (!match) return "Yellow";
  const health = match[1].charAt(0).toUpperCase() + match[1].slice(1).toLowerCase();
  return HEALTH_COLOR[health] ? health : "Yellow";
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

const SECTION_LABELS = [
  "HEALTH", "SITUATION SUMMARY", "EXECUTIVE BRIEF", "USE CASE",
  "CURRENT ENVIRONMENT/TOOLING", "PAIN POINTS", "MEETING-HISTORY SUMMARY",
  "SOLUTIONS DEMOED", "PRIORITIZED NEXT STEPS", "TALK TRACK (primary contact)",
  "CONTACTS TO ENGAGE", "NEWLY-MATCHED EXTERNAL LEADS AT THIS ACCOUNT",
  "BUYING/STALL SIGNALS", "RISKS + MITIGATIONS", "CONTENT RECOMMENDATIONS",
  "NOTE", "DEAL VALUE", "USAGE TELEMETRY", "NPS/CSAT", "SUPPORT TICKETS",
  "RENEWAL RISK", "EXPANSION SIGNAL", "RECOMMENDED CS ACTION",
];

function parseBrief(rawText) {
  // Skip the "=== DEAL COACHING BRIEF: ... ===" or "=== POST-SALE HEALTH REPORT: ... ===" title line
  const body = rawText.replace(/^===.*?===\s*/s, "");
  const blocks = body.split(/\n\n+/).map((b) => b.trim()).filter(Boolean);

  let html = "";
  for (const block of blocks) {
    if (block.startsWith("NOTE:")) continue;  // disclaimer text, not useful as its own UI section
    const match = SECTION_LABELS.find((label) => block.startsWith(label + ":"));
    if (!match) {
      html += `<div class="brief-section"><div class="brief-content">${escapeHtml(block)}</div></div>`;
      continue;
    }
    let content = block.slice(match.length + 1).trim();

    // Special styling for the HEALTH line -- colored based on the rating word
    if (match === "HEALTH") {
      const colorClass = content.toUpperCase().includes("GREEN") ? "green"
        : content.toUpperCase().includes("RED") ? "red" : "yellow";
      html += `<div class="brief-section"><div class="brief-label">${match}</div>
                <div class="brief-content health-line ${colorClass}">${escapeHtml(content)}</div></div>`;
      continue;
    }

    // Lines starting with "  - " or "  1." become a bullet list
    const lines = content.split("\n").map((l) => l.trim()).filter(Boolean);
    const isList = lines.length > 1 && lines.every((l) => /^([-*]|\d+\.)/.test(l));
    if (isList) {
      const items = lines.map((l) => `<li>${escapeHtml(l.replace(/^([-*]|\d+\.)\s*/, ""))}</li>`).join("");
      html += `<div class="brief-section"><div class="brief-label">${match}</div>
                <div class="brief-content"><ul>${items}</ul></div></div>`;
    } else {
      html += `<div class="brief-section"><div class="brief-label">${match}</div>
                <div class="brief-content">${escapeHtml(content)}</div></div>`;
    }
  }
  return html;
}

// Hoisted to module scope so chat handlers can reuse fetched data --
// no extra API calls needed for any pre-built or free-text question.
let STATE = { contacts: [], deals: [] };

function renderDashboard(contacts, deals) {
  STATE.contacts = contacts;
  STATE.deals = deals;
  contacts.sort((a, b) => (parseInt(b.properties.lead_score) || 0) - (parseInt(a.properties.lead_score) || 0));

  const tierCounts = {};
  contacts.forEach((c) => {
    const t = c.properties.outreach_tier || "Unknown";
    tierCounts[t] = (tierCounts[t] || 0) + 1;
  });

  let healthCounts = { Green: 0, Yellow: 0, Red: 0 };
  const dealsWithHealth = deals.map((d) => {
    const brief = d.properties.ai_coaching_brief || "No coaching brief generated yet.";
    const health = deriveHealth(brief);
    healthCounts[health]++;
    return { ...d, _health: health, _brief: brief };
  });
  STATE.deals = dealsWithHealth;  // overwrite with health-augmented version for chat handlers

  document.getElementById("cards-container").innerHTML = `
    <div class="cards">
      <div class="card"><div class="num">${contacts.length}</div><div class="label">Contacts</div></div>
      <div class="card"><div class="num" style="color:#166534">${tierCounts["Top"] || 0}</div><div class="label">Top</div></div>
      <div class="card"><div class="num" style="color:#92400E">${tierCounts["Mid"] || 0}</div><div class="label">Mid</div></div>
      <div class="card"><div class="num" style="color:#475569">${tierCounts["Low"] || 0}</div><div class="label">Low</div></div>
      <div class="card"><div class="num">${deals.length}</div><div class="label">Deals</div></div>
      <div class="card"><div class="num" style="color:#166534">${healthCounts.Green}</div><div class="label">Healthy</div></div>
    </div>`;

  document.getElementById("deals-heading").textContent = `Deals (${deals.length})`;
  const dealsHtml = dealsWithHealth.map((d, i) => {
    const name = escapeHtml((d.properties.dealname || "").replace(" - AI Cardiac Monitor", ""));
    const [fg, bg] = HEALTH_COLOR[d._health];
    return `
      <div class="row deal-row" data-index="${i}">
        <div class="row-top">
          <strong>${name}</strong>
          <span class="badge" style="color:${fg};background:${bg}">${d._health}</span>
        </div>
        <div class="brief" id="brief-${i}">${parseBrief(d._brief)}</div>
      </div>`;
  }).join("");
  document.getElementById("deals-container").innerHTML = dealsHtml;

  document.querySelectorAll(".deal-row").forEach((row) => {
    row.addEventListener("click", () => {
      const brief = row.querySelector(".brief");
      brief.style.display = brief.style.display === "none" || !brief.style.display ? "block" : "none";
    });
  });

  const topContacts = contacts.slice(0, 8);
  document.getElementById("contacts-heading").textContent = `Top Contacts (showing ${topContacts.length} of ${contacts.length})`;
  const contactsHtml = topContacts.map((c) => {
    const p = c.properties;
    const name = escapeHtml(`${p.firstname || ""} ${p.lastname || ""}`);
    const company = escapeHtml(p.company || "");
    const tier = p.outreach_tier || "Unknown";
    const [fg, bg] = TIER_COLOR[tier] || ["#475569", "#F1F5F9"];
    return `
      <div class="row">
        <div class="row-top">
          <strong>${name}</strong>
          <span class="badge" style="color:${fg};background:${bg}">${escapeHtml(String(tier))}</span>
        </div>
        <div style="color:#64748B;margin-top:2px">${company} &middot; score ${p.lead_score || "?"}</div>
      </div>`;
  }).join("");
  document.getElementById("contacts-container").innerHTML = contactsHtml;
}

async function loadDashboard() {
  showScreen("loading");
  const key = await getStoredKey();
  if (!key) {
    showScreen("setup");
    return;
  }
  try {
    const [contacts, deals] = await Promise.all([fetchContacts(key), fetchDeals(key)]);
    renderDashboard(contacts, deals);
    showScreen("dashboard");
  } catch (e) {
    document.getElementById("setup-error").textContent = `Connection failed: ${e.message}. Check your key.`;
    showScreen("setup");
  }
}

document.getElementById("save-key-btn").addEventListener("click", async () => {
  const key = document.getElementById("api-key-input").value.trim();
  if (!key) return;
  document.getElementById("setup-error").textContent = "";
  await saveKey(key);
  loadDashboard();
});

document.getElementById("refresh-btn").addEventListener("click", loadDashboard);

document.getElementById("settings-btn").addEventListener("click", async () => {
  await new Promise((resolve) => chrome.storage.local.remove(["hubspot_api_key"], resolve));
  showScreen("setup");
});

// ==================== Q&A ENGINE ====================
// Everything below runs against STATE.contacts / STATE.deals already
// fetched -- no new API calls, no cost, same keyword-matching approach
// used throughout this whole system.

function dealName(d) {
  return (d.properties.dealname || "").replace(" - AI Cardiac Monitor", "");
}

function listDealsByHealth(health) {
  const matches = STATE.deals.filter((d) => d._health === health);
  if (!matches.length) return `No deals currently marked ${health}.`;
  return matches.map((d) => `- ${dealName(d)}`).join("\n");
}

function listContactsByTier(tier) {
  const matches = STATE.contacts.filter((c) => c.properties.outreach_tier === tier);
  if (!matches.length) return `No contacts in ${tier} tier.`;
  return matches.map((c) => `- ${c.properties.firstname} ${c.properties.lastname} (${c.properties.company || ""})`).join("\n");
}

function repliedContacts() {
  const matches = STATE.contacts.filter((c) => c.properties.outreach_tier === "Replied - In Sales Cycle");
  if (!matches.length) return "No one has replied yet.";
  return matches.map((c) => `- ${c.properties.firstname} ${c.properties.lastname} (score ${c.properties.lead_score})`).join("\n");
}

function noReplyContacts() {
  const matches = STATE.contacts.filter((c) => c.properties.outreach_tier !== "Replied - In Sales Cycle" && c.properties.outreach_tier === "Top");
  if (!matches.length) return "Everyone in Top tier has replied, or there are no Top-tier contacts.";
  return `${matches.length} Top-tier contact(s) haven't replied yet:\n` +
    matches.slice(0, 8).map((c) => `- ${c.properties.firstname} ${c.properties.lastname}`).join("\n");
}

function topBottleneckTheme() {
  const themes = {};
  STATE.contacts.forEach((c) => {
    const ctx = c.properties.deal_context || "";
    const m = ctx.match(/Bottleneck:\s*(.+?)\.?$/);
    if (!m) return;
    const b = m[1].toLowerCase();
    let theme = "Other";
    if (b.includes("budget") || b.includes("capital")) theme = "Budget/capital approval";
    else if (b.includes("fda") || b.includes("compliance") || b.includes("security")) theme = "FDA/compliance concerns";
    else if (b.includes("ehr") || b.includes("integration")) theme = "IT/EHR integration";
    else if (b.includes("bid") || b.includes("procurement") || b.includes("competitive")) theme = "Procurement process";
    else if (b.includes("training") || b.includes("alarm") || b.includes("workflow")) theme = "Staff training/workflow";
    themes[theme] = (themes[theme] || 0) + 1;
  });
  const sorted = Object.entries(themes).sort((a, b) => b[1] - a[1]);
  if (!sorted.length) return "No bottleneck data available yet.";
  return sorted.map(([t, n]) => `- ${t}: ${n} deal(s)`).join("\n");
}

function dealsByRep(rep) {
  const matches = STATE.contacts.filter((c) => (c.properties.assigned_rep || "").toLowerCase() === rep.toLowerCase());
  if (!matches.length) return `No contacts assigned to ${rep}.`;
  return matches.map((c) => `- ${c.properties.firstname} ${c.properties.lastname} (${c.properties.outreach_tier})`).join("\n");
}

function highestValueDeal() {
  if (!STATE.deals.length) return "No deals found.";
  const sorted = [...STATE.deals].sort((a, b) => (parseFloat(b.properties.amount) || 0) - (parseFloat(a.properties.amount) || 0));
  const top = sorted[0];
  return `${dealName(top)} -- $${parseInt(top.properties.amount || 0).toLocaleString()} (${top._health})`;
}

function averageScore() {
  const scores = STATE.contacts.map((c) => parseInt(c.properties.lead_score) || 0);
  if (!scores.length) return "No contacts found.";
  const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
  return `Average lead score: ${avg.toFixed(1)} across ${scores.length} contact(s).`;
}

function stalledDeals() {
  const matches = STATE.contacts.filter((c) => (c.properties.company || "").length &&
    STATE.deals.some((d) => dealName(d) === c.properties.company && d.properties.dealstage === "qualifiedtobuy" && d._health !== "Green"));
  return matches.length ? matches.map((c) => `- ${c.properties.firstname} ${c.properties.lastname}`).join("\n")
    : "No clearly stalled deals detected right now.";
}

function dealsWithBuyingSignals() {
  const matches = STATE.deals.filter((d) => (d._brief || "").includes("Detected signal") &&
    (d._health === "Green"));
  if (!matches.length) return "No deals currently show a detected buying signal.";
  return matches.map((d) => `- ${dealName(d)}`).join("\n");
}

function dealsWithRiskSignals() {
  const matches = STATE.deals.filter((d) => d._health === "Red");
  if (!matches.length) return "No deals currently show a detected risk signal.";
  return matches.map((d) => `- ${dealName(d)}`).join("\n");
}

function dealsWithDemoLogged() {
  const matches = STATE.deals.filter((d) => (d._brief || "").toLowerCase().includes("demo/walkthrough appears"));
  if (!matches.length) return "No deals have a logged demo yet.";
  return matches.map((d) => `- ${dealName(d)}`).join("\n");
}

function unmatchedSlackAccounts() {
  const matches = STATE.deals.filter((d) => (d._brief || "").includes("No matching Slack channel"));
  if (!matches.length) return "Every deal has a matched Slack channel.";
  return `${matches.length} deal(s) with no Slack channel match:\n` + matches.map((d) => `- ${dealName(d)}`).join("\n");
}

function dealsByStage(stageKeyword) {
  const matches = STATE.deals.filter((d) => (d.properties.dealstage || "").toLowerCase().includes(stageKeyword));
  if (!matches.length) return `No deals found at that stage.`;
  return matches.map((d) => `- ${dealName(d)} ($${parseInt(d.properties.amount || 0).toLocaleString()})`).join("\n");
}

function totalPipelineValue() {
  const total = STATE.deals.reduce((sum, d) => sum + (parseFloat(d.properties.amount) || 0), 0);
  return `Total pipeline value: $${total.toLocaleString()} across ${STATE.deals.length} deal(s).`;
}

function repliedRate() {
  const replied = STATE.contacts.filter((c) => c.properties.outreach_tier === "Replied - In Sales Cycle").length;
  const pct = STATE.contacts.length ? ((replied / STATE.contacts.length) * 100).toFixed(1) : 0;
  return `${replied} of ${STATE.contacts.length} contacts have replied (${pct}%).`;
}

function replyAttribution() {
  const replied = STATE.contacts.filter((c) => c.properties.outreach_tier === "Replied - In Sales Cycle");
  if (!replied.length) return "No replies yet to attribute.";
  const byTouch = {};
  replied.forEach((c) => {
    const t = c.properties.reengagement_touch_number || "0";
    byTouch[t] = (byTouch[t] || 0) + 1;
  });
  const lines = Object.entries(byTouch).sort((a, b) => a[0] - b[0])
    .map(([touch, count]) => `- After Touch ${touch}: ${count} repl(y/ies)`);
  return `Reply attribution by touch number:\n${lines.join("\n")}`;
}

function contactsMissingEmail() {
  const matches = STATE.contacts.filter((c) => !c.properties.email);
  if (!matches.length) return "Every contact has an email on file.";
  return matches.map((c) => `- ${c.properties.firstname} ${c.properties.lastname}`).join("\n");
}

// Question library: [trigger keywords, label for button, handler function]
// ==================== Persona & Enablement Engine (static reference content) ====================
const PERSONAS = {
  champion: `CLINICAL CHAMPION (Chief of Cardiology, CNO, VP Clinical Ops, CFO)

Cares about: patient outcomes, readmission rates, ROI/payback period.
Pain points: missed early-warning signs, pressure to cut length-of-stay, proving ROI to the board.
Common objection: "We already have telemetry monitors -- why do we need AI on top?"
What convinces them: peer-hospital outcome data, conservative ROI model.
Message angle: outcomes first, technology second.`,

  technical: `TECHNICAL GATEKEEPER (CMIO, VP Health IT, Director of Biomedical/Clinical Engineering)

Cares about: integration risk, data security/compliance, support burden.
Pain points: EHR/Epic integration complexity, FDA clearance requirements, limited team bandwidth.
Common objection: "What's your FDA clearance status, exactly?"
What convinces them: precise technical detail, real compliance docs, specific integration timeline.
Message angle: technical depth -- never oversimplify, this persona rewards precision.`,

  procurement: `PROCUREMENT LEAD (Director of Supply Chain, VP Procurement)

Cares about: process compliance, total cost of ownership.
Pain points: competitive bid requirements, GPO contract alignment, fiscal-year budget timing.
Common objection: "We need three competitive bids before this can move forward."
What convinces them: fast RFP response package, GPO fit clarified upfront.
Message angle: process-first -- make their job easier, don't push urgency.`,

  enduser: `END-USER / CLINICAL STAFF (ICU Medical Director, Cardiac Unit Nurse Manager)

Cares about: day-to-day workflow impact.
Pain points: alarm fatigue, staff training time, skepticism from past "innovative" tools.
Common objection: "Our last new system just meant more alarms to ignore."
What convinces them: live demo showing REDUCED alarm volume, peer reference from another nurse manager.
Message angle: concrete and hands-on -- show, don't tell.`,
};

const BATTLECARDS = {
  monitors: `BATTLECARD: "We already have telemetry monitors"

Don't say: "Ours is better." (invites a feature debate you may not win)
Do say: "Totally fair -- most hospitals already have solid telemetry. The gap we fill is the AI layer on top that catches early warning patterns telemetry alone doesn't flag. Want to see that on real (anonymized) patient data?"`,

  fda: `BATTLECARD: "What's your FDA clearance status?"

Don't say: anything vague -- this persona notices immediately and loses trust.
Do say: state the exact current status plainly. If pending, say so directly with a realistic timeline. Offer to connect them with your regulatory contact directly.`,

  budget: `BATTLECARD: "This wasn't in this year's capital budget"

Don't say: "Can we push for an exception?" (puts them in an awkward spot)
Do say: "Would it make sense to structure this as a pilot this year, with the full purchase timed to next fiscal year's capital planning cycle?"`,

  alarms: `BATTLECARD: "Our last new tool just added more alarms"

Don't say: a features list.
Do say: "That's exactly the problem we built this to solve -- happy to show a live comparison of alarm volume with and without the AI filtering layer, using your own unit's typical patient load."`,
};

const COMMITTEE_SIMULATION = `BUYING COMMITTEE DYNAMICS

1. The Clinical Champion gets excited first (usually after seeing outcome data).
2. They pull in the Technical Gatekeeper to vet feasibility -- THIS IS THE MOST COMMON STALL POINT.
3. Once technically cleared, Procurement gets looped in (a process gate, not a persuasion gate).
4. The End-User is consulted last -- but their buy-in quietly determines post-sale adoption.

Key insight: the Champion's enthusiasm alone doesn't move the deal. Prioritize getting the
Technical Gatekeeper comfortable EARLY, in parallel with the Champion, not after.`;

const OUTREACH_SEQUENCE = `COMMITTEE-WIDE OUTREACH SEQUENCE

Week 1: Personalized outreach to the Clinical Champion (outcomes-first).
Week 1-2 (parallel, not sequential): Reach the Technical Gatekeeper with a technical-first
  message -- most reps skip this until too late, causing the common stall pattern.
Week 2-3: Loop in Procurement proactively -- hand them the RFP template before they ask.
Week 3-4: Bring in the End-User for a short, concrete workflow-focused demo.`;

const QUESTIONS = [
  [["at-risk", "at risk", "red", "risky"], "At-risk deals", () => listDealsByHealth("Red")],
  [["healthy", "green", "good deals"], "Healthy deals", () => listDealsByHealth("Green")],
  [["yellow", "neutral", "unclear"], "Yellow/neutral deals", () => listDealsByHealth("Yellow")],
  [["replied", "responded", "in sales cycle"], "Who has replied?", repliedContacts],
  [["no reply", "haven't replied", "not responded", "waiting"], "Top contacts with no reply", noReplyContacts],
  [["reply rate", "response rate"], "Reply rate", repliedRate],
  [["bottleneck", "theme", "blocker", "common problem"], "Top bottleneck themes", topBottleneckTheme],
  [["top tier", "top contacts", "best leads"], "Top tier contacts", () => listContactsByTier("Top")],
  [["mid tier", "mid contacts"], "Mid tier contacts", () => listContactsByTier("Mid")],
  [["low tier", "low contacts"], "Low tier contacts", () => listContactsByTier("Low")],
  [["prateek"], "Prateek's contacts", () => dealsByRep("Prateek")],
  [["carole"], "Carole's contacts", () => dealsByRep("Carole")],
  [["shiwam"], "Shiwam's contacts", () => dealsByRep("Shiwam")],
  [["highest value", "biggest deal", "largest deal"], "Highest value deal", highestValueDeal],
  [["total value", "pipeline value", "total pipeline"], "Total pipeline value", totalPipelineValue],
  [["average score", "avg score"], "Average lead score", averageScore],
  [["stalled", "stuck"], "Stalled deals", stalledDeals],
  [["how many contacts", "total contacts"], "Total contacts", () => `${STATE.contacts.length} total contact(s).`],
  [["how many deals", "total deals"], "Total deals", () => `${STATE.deals.length} total deal(s).`],
  [["automated nurture", "nurture"], "Contacts in automated nurture", () => dealsByRep("Automated Nurture")],
  [["buying signal", "positive signal", "momentum"], "Deals with buying signals", dealsWithBuyingSignals],
  [["risk signal", "warning sign"], "Deals with risk signals", dealsWithRiskSignals],
  [["demo", "demoed", "walkthrough"], "Deals with a demo logged", dealsWithDemoLogged],
  [["unmatched", "no slack", "no channel"], "Accounts with no Slack match", unmatchedSlackAccounts],
  [["discovery stage", "in discovery"], "Deals in Discovery", () => dealsByStage("appointmentscheduled")],
  [["pilot proposed", "decision maker"], "Deals with pilot proposed", () => dealsByStage("decisionmakerboughtin")],
  [["missing email", "no email"], "Contacts missing email", contactsMissingEmail],
  [["attribution", "which touch", "what touch worked"], "Reply attribution by touch", replyAttribution],
  [["champion persona", "clinical champion", "chief of cardiology persona"], "Clinical Champion persona", () => PERSONAS.champion],
  [["technical persona", "gatekeeper", "cmio persona"], "Technical Gatekeeper persona", () => PERSONAS.technical],
  [["procurement persona"], "Procurement Lead persona", () => PERSONAS.procurement],
  [["end-user persona", "nurse persona", "clinical staff persona"], "End-User persona", () => PERSONAS.enduser],
  [["battlecard monitors", "already have monitors", "telemetry objection"], "Battlecard: existing monitors", () => BATTLECARDS.monitors],
  [["battlecard fda", "fda clearance objection"], "Battlecard: FDA clearance", () => BATTLECARDS.fda],
  [["battlecard budget", "not in budget"], "Battlecard: budget objection", () => BATTLECARDS.budget],
  [["battlecard alarm", "alarm fatigue objection"], "Battlecard: alarm fatigue", () => BATTLECARDS.alarms],
  [["committee dynamics", "buying committee", "who blocks the deal"], "Buying committee simulation", () => COMMITTEE_SIMULATION],
  [["sequencing plan", "outreach sequence", "committee sequence"], "Committee outreach sequence", () => OUTREACH_SEQUENCE],
  [["campaign reply rate", "reply rate by campaign", "which campaign works"], "Reply rate by campaign type", replyRateByCampaign],
  [["aged out", "reconciliation", "stale deals", "no activity"], "Aged-out deals (30+ days no activity)", agedOutDeals],
];

function askQuestion(text) {
  const t = text.toLowerCase();
  let best = null, bestScore = 0;
  for (const [keywords, label, fn] of QUESTIONS) {
    const score = keywords.filter((k) => t.includes(k)).length;
    if (score > bestScore) {
      bestScore = score;
      best = fn;
    }
  }
  const answerBox = document.getElementById("chat-answer");
  answerBox.style.display = "block";
  if (best) {
    answerBox.textContent = best();
  } else {
    answerBox.textContent = "Not sure how to answer that -- try one of the quick questions above, "
      + "or ask about: at-risk deals, replies, tiers, bottlenecks, reps, or deal values.";
  }
}

function renderQuickQuestions() {
  const container = document.getElementById("quick-questions");
  container.innerHTML = QUESTIONS.map((q, i) => `<button class="qbtn" data-index="${i}">${q[1]}</button>`).join("");
  container.querySelectorAll(".qbtn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const q = QUESTIONS[parseInt(btn.dataset.index)];
      const answerBox = document.getElementById("chat-answer");
      answerBox.style.display = "block";
      answerBox.textContent = q[2]();
    });
  });
}

document.getElementById("ask-btn").addEventListener("click", () => {
  const input = document.getElementById("chat-input");
  if (input.value.trim()) askQuestion(input.value.trim());
});
document.getElementById("chat-input").addEventListener("keypress", (e) => {
  if (e.key === "Enter" && e.target.value.trim()) askQuestion(e.target.value.trim());
});

renderQuickQuestions();

// ==================== Tab switching ====================
function switchTab(tab) {
  document.getElementById("overview-panel").style.display = tab === "overview" ? "block" : "none";
  document.getElementById("trends-panel").style.display = tab === "trends" ? "block" : "none";
  document.getElementById("ask-panel").style.display = tab === "ask" ? "block" : "none";
  document.getElementById("tab-overview").classList.toggle("active", tab === "overview");
  document.getElementById("tab-trends").classList.toggle("active", tab === "trends");
  document.getElementById("tab-ask").classList.toggle("active", tab === "ask");
  if (tab === "trends") renderTrends();
}
document.getElementById("tab-overview").addEventListener("click", () => switchTab("overview"));
document.getElementById("tab-trends").addEventListener("click", () => switchTab("trends"));
document.getElementById("tab-ask").addEventListener("click", () => switchTab("ask"));

// ==================== Trends narrative (per template 2.5) ====================
function renderTrends() {
  const container = document.getElementById("trends-container");
  if (!STATE.deals.length) {
    container.innerHTML = `<div class="answer-box">No deals yet to analyze.</div>`;
    return;
  }

  const healthCounts = { Green: 0, Yellow: 0, Red: 0, Neutral: 0 };
  STATE.deals.forEach((d) => { healthCounts[d._health] = (healthCounts[d._health] || 0) + 1; });
  const atRisk = healthCounts.Red || 0;
  const strong = healthCounts.Green || 0;

  const themeText = topBottleneckTheme();
  const focusTheme = themeText.split("\n")[0] || "No clear theme yet";

  const healthSummary = `${strong} deal(s) showing strong momentum (Green), ${healthCounts.Yellow || 0} neutral (Yellow), and ${atRisk} at-risk (Red) out of ${STATE.deals.length} total.`;
  const recommendedFocus = atRisk > 0
    ? `Prioritize the ${atRisk} at-risk deal(s) -- check "At-risk deals" in the Ask tab for the list.`
    : `No deals currently at risk -- focus on advancing the ${healthCounts.Yellow || 0} neutral deal(s) toward a clearer signal.`;
  const stageVelocity = `${STATE.deals.length} deal(s) tracked. Stage-entry timestamps aren't tracked yet, so average time-in-stage can't be computed -- this would need a stage-change history log to add.`;

  const sections = [
    ["Pipeline Health", healthSummary],
    ["Top Trend", focusTheme],
    ["Recommended Weekly Focus", recommendedFocus],
    ["Stage Velocity", stageVelocity],
  ];

  const html = sections.map(([label, content]) => `
    <div class="brief-section">
      <div class="brief-label">${escapeHtml(label)}</div>
      <div class="brief-content">${escapeHtml(content)}</div>
    </div>`).join("");

  container.innerHTML = `<div class="answer-box">${html}</div>`;
}

// ==================== Campaign-level attribution (fix #4) ====================
function replyRateByCampaign() {
  const buckets = { "Top-tier nurture": [0, 0], "Mid-tier sequence": [0, 0], "Re-engagement": [0, 0] };
  STATE.contacts.forEach((c) => {
    const p = c.properties;
    const isReengagement = false; // source_event not fetched in this view; approximate via touch tracking below
    const bucket = (p.assigned_rep && p.assigned_rep !== "Automated Nurture") ? "Top-tier nurture"
      : (p.reengagement_touch_number && parseInt(p.reengagement_touch_number) > 0 && p.outreach_tier !== "Mid") ? "Re-engagement"
      : "Mid-tier sequence";
    buckets[bucket][1]++;
    if (p.outreach_tier === "Replied - In Sales Cycle") buckets[bucket][0]++;
  });
  const lines = Object.entries(buckets).map(([name, [replied, total]]) =>
    `- ${name}: ${replied}/${total} replied (${total ? ((replied / total) * 100).toFixed(1) : 0}%)`);
  return `Reply rate by campaign type:\n${lines.join("\n")}\n\nNote: this is an approximation based on assigned rep and touch-number fields, not a true campaign ID -- accurate enough for directional comparison, not precise attribution.`;
}

// ==================== Aged-out deal reconciliation (fix #5) ====================
function agedOutDeals() {
  const now = Date.now();
  const THIRTY_DAYS = 30 * 24 * 60 * 60 * 1000;
  const companiesWithRecentActivity = new Set();
  STATE.contacts.forEach((c) => {
    const lastProcessed = c.properties.last_processed_date;
    if (lastProcessed) {
      const ts = new Date(lastProcessed).getTime();
      if (now - ts < THIRTY_DAYS) companiesWithRecentActivity.add(c.properties.company);
    }
  });
  const staleDeals = STATE.deals.filter((d) => !companiesWithRecentActivity.has(dealName(d)));
  if (!staleDeals.length) return "No deals appear aged-out -- all have recent contact activity within 30 days.";
  return `${staleDeals.length} deal(s) with no contact activity in 30+ days (aged out, needs reconciliation):\n`
    + staleDeals.map((d) => `- ${dealName(d)}`).join("\n");
}

loadDashboard();
