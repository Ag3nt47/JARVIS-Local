(() => {
  "use strict";

  /*
   * JARVIS browser API contract (all responses are JSON unless noted):
   *   GET    /api/health                Health, Ollama, and telemetry snapshot
   *   GET    /api/nodes
   *   GET    /api/settings
   *   PATCH  /api/settings             Federation, integrations, and desktop globe preferences
   *   GET    /api/security/posture      Live layered defense posture
   *   GET    /api/security/angerona     Read-only Angerona installation/MCP status
   *   GET    /api/security/angerona/events?limit=30  Sanitized recent alerts on demand
   *   GET    /api/system-health         Live local metrics, sensor availability, policy state
   *   GET    /api/system-health/alerts?limit=40  Recent persisted threshold alerts
   *   PATCH  /api/system-health/settings  Monitoring thresholds and explicit online opt-in
   *   GET    /api/actions/catalog
   *   POST   /api/actions/requests      Structured, approval-gated actions
   *   GET    /api/projects?limit=100     User project metadata
   *   POST   /api/projects               Create metadata-only project workspace
   *   GET    /api/projects/:id           Project detail
   *   PATCH  /api/projects/:id           Update project metadata / allowed status transition
   *   GET    /api/communications         Masked route readiness and delivery policy
   *   PATCH  /api/communications/routes/:provider  Provider-specific, non-secret setup
   *   POST   /api/communications/routes/:provider/test  Read-only saved-setup verification
   *   POST   /api/communications/routes/:provider/credential  Write-only protected replace
   *   DELETE /api/communications/routes/:provider/credential  Clear protected credential
   *   PATCH  /api/communications/order   { provider_order: [signal, telegram, discord] }
   *   PATCH  /api/communications/failover { failover_enabled }
   *   GET    /api/skills
   *   GET    /api/coding/status         Approval, backup, root, and toolchain boundary
   *   GET    /api/live/status           Fixed-feed briefing and dashboard boundary
   *   POST   /api/chat                 { messages, skill_id, use_tools }
   *   GET    /api/tasks
   *   POST   /api/tasks                { title, priority: 1..5 }
   *   PATCH  /api/tasks/:id            { state: "open" | "done" }
   *   POST   /api/memory/search        { query, limit }
   *   POST   /api/memory               { content, source, metadata }
   *   GET    /api/approvals
   *   POST   /api/approvals/:id/approve { actor }
   *   POST   /api/approvals/:id/deny    { actor }
   *   GET    /api/audit?limit=30
   *
   * Collection responses may be a bare array or { items: [...] } using the
   * resource name. The app never auto-approves an external action and does not
   * offer task deletion because the local API intentionally has no task DELETE.
   */
  const API_BASE = "/api";
  const STORAGE = {
    tasks: "jarvis.hud.tasks.v1",
    memory: "jarvis.hud.memory.v1",
    settings: "jarvis.hud.settings.v1",
    audit: "jarvis.hud.audit.v1"
  };

  const BUILTIN_SKILLS = [
    { id: "executive_assistant", name: "Executive Assistant", description: "Prioritization, planning, reminders, and concise decision support.", enabled: true, icon: "EA", riskTier: "low" },
    { id: "engineering_debug", name: "Engineering & Debug", description: "Evidence-led diagnosis and safe source inspection.", enabled: true, icon: "ED", riskTier: "low" },
    { id: "defensive_sentry", name: "Defensive Sentry", description: "Local defensive monitoring and security triage.", enabled: true, icon: "DS", riskTier: "low" },
    { id: "angerona_defense", name: "Angerona Defense", description: "Target-locked observation, tactics, and locally approved defensive action.", enabled: true, icon: "AD", riskTier: "guarded" },
    { id: "spatial_files", name: "Spatial Files", description: "Voice, mouse, and gesture file discovery inside allowed roots.", enabled: true, icon: "SF", riskTier: "guarded" },
    { id: "research_rag", name: "Research & RAG", description: "Retrieval-grounded synthesis from local memory.", enabled: true, icon: "RR", riskTier: "low" },
    { id: "document_analysis", name: "Document Analysis", description: "Structured review, comparison, and fact extraction.", enabled: true, icon: "DA", riskTier: "low" },
    { id: "live_briefing", name: "Live Briefing", description: "Current world and finance headlines with approved visual dashboards.", enabled: true, icon: "LB", riskTier: "guarded" },
    { id: "system_health", name: "System Health", description: "Local CPU, memory, disk, process, and Ollama health interpretation.", enabled: true, icon: "SH", riskTier: "low" },
    { id: "software_engineer", name: "Software Engineer", description: "Approval-gated coding, testing, and local repository maintenance.", enabled: true, icon: "SE", riskTier: "high" },
    { id: "safe_automation", name: "Safe Automation", description: "Approval-gated changes and repeatable local workflows.", enabled: true, icon: "SA", riskTier: "guarded" }
  ];

  const INTEGRATION_CATALOG = [
    { id: "ollama", name: "Ollama", icon: "OL", description: "Local language and embedding models" },
    { id: "tailscale", name: "Tailscale", icon: "TS", description: "Private federation transport" },
    { id: "signal", name: "Signal", icon: "SG", description: "Private messaging bridge" },
    { id: "mdns", name: "mDNS", icon: "MD", description: "Local network device discovery" },
    { id: "wake_on_lan", name: "Wake-on-LAN", icon: "WL", description: "Wake trusted peer hardware" },
    { id: "ups", name: "UPS", icon: "UP", description: "Power and battery telemetry" },
    { id: "ddc_ci", name: "DDC/CI", icon: "DC", description: "Local display control" },
    { id: "vision", name: "Vision", icon: "VI", description: "Camera and visual analysis" },
    { id: "backups", name: "Backups", icon: "BK", description: "Protected backup monitoring" },
    { id: "gpt", name: "GPT", icon: "GP", description: "Optional cloud model provider" }
  ];

  const BRAIN_DRIVE_PATH = "F:\\BRAIN DRIVE\\_root";
  const BACKGROUND_GLOBE_SIZE = Object.freeze({ minimum: 280, maximum: 1200, default: 720 });
  const SYSTEM_HEALTH_API = {
    snapshot: "/system-health",
    alerts: "/system-health/alerts?limit=40",
    settings: "/system-health/settings"
  };

  const COMMUNICATION_PROVIDERS = ["signal", "telegram", "discord", "web_app"];
  const DELIVERY_PROVIDERS = ["signal", "telegram", "discord"];
  const COMMUNICATION_PROVIDER_META = {
    signal: {
      label: "Signal",
      icon: "SG",
      description: "Private Signal delivery through the locally installed signal-cli account."
    },
    telegram: {
      label: "Telegram Bot",
      icon: "TG",
      description: "Operator-configured Telegram bot delivery to one masked chat target."
    },
    discord: {
      label: "Discord",
      icon: "DC",
      description: "Discord webhook or bot delivery using a fixed local credential reference."
    },
    web_app: {
      label: "JARVIS Web App",
      icon: "WA",
      description: "Private interactive access readiness. This is not an outbound delivery route."
    }
  };

  const DEFENSE_STAGES = [
    { id: "devices", name: "Devices", detail: "Phone / Mac / Windows", aliases: ["device", "device_ingress", "clients"] },
    { id: "tailscale_transport", name: "Tailscale Transport", detail: "Private encrypted network path", aliases: ["tailscale", "transport"] },
    { id: "peer_authentication", name: "Peer Authentication", detail: "Verify the requesting node identity", aliases: ["peer_auth", "authentication", "auth"] },
    { id: "application_firewall", name: "Application Firewall", detail: "Request validation and traffic policy", aliases: ["app_firewall", "firewall"] },
    { id: "ids", name: "IDS", detail: "Detect suspicious activity", aliases: ["intrusion_detection"] },
    { id: "ips", name: "IPS", detail: "Block confirmed hostile activity", aliases: ["intrusion_prevention"] },
    { id: "capability_policy", name: "Capability Policy", detail: "Constrain actions to granted capabilities", aliases: ["capabilities", "capability"] },
    { id: "target_approval", name: "Target Approval", detail: "Require approval where the action runs", aliases: ["approval", "local_approval"] },
    { id: "action_compute_data", name: "Action / Compute / Data", detail: "Execute the approved bounded operation", aliases: ["action", "compute", "data", "execution"] },
    { id: "hash_chained_audit", name: "Hash-chained Audit", detail: "Record a tamper-evident outcome", aliases: ["audit", "audit_log", "hash_audit"] }
  ];

  const ACTION_DEFINITIONS = [
    {
      id: "list_windows",
      name: "List Windows",
      risk: "low",
      description: "Enumerate visible desktop windows without changing them. This read-only action is recorded and runs immediately."
    },
    {
      id: "read_clipboard",
      name: "Read Clipboard",
      risk: "guarded",
      description: "Read bounded clipboard text once after approval. JARVIS does not create clipboard history."
    },
    {
      id: "click_control",
      name: "Click Exact Control",
      risk: "guarded",
      description: "Target one named control in one exact window. The target device must approve the interaction."
    },
    {
      id: "run_command",
      name: "Run Bounded Program",
      risk: "high",
      description: "Launch one executable with a separate argument vector and working directory. No shell command text is accepted."
    }
  ];

  const state = {
    online: false,
    statusFailures: 0,
    chatBusy: false,
    chatController: null,
    telemetry: { cpu: 0, memory: 0, gpu: 0, coreLoad: 0 },
    systemTelemetryDisks: [],
    skills: [...BUILTIN_SKILLS],
    tasks: readLocal(STORAGE.tasks, []),
    memories: readLocal(STORAGE.memory, []),
    approvals: [],
    nodes: [],
    audit: readLocal(STORAGE.audit, []),
    settings: readLocal(STORAGE.settings, { lowMotion: false, compactTelemetry: false }),
    remoteSettings: null,
    settingsBusy: false,
    backgroundGlobeBusy: false,
    securityPosture: null,
    angeronaStatus: null,
    angeronaEvents: [],
    angeronaEventsLoaded: false,
    angeronaEventsError: "",
    angeronaStatusBusy: false,
    phoneStatus: null,
    systemHealth: null,
    systemHealthAlerts: [],
    systemHealthAlertsError: "",
    systemHealthLoaded: false,
    systemHealthBusy: false,
    systemHealthSeenAlerts: new Set(),
    systemHealthAlertBaselineReady: false,
    actionCatalog: [],
    actionCatalogLoaded: false,
    paletteEntries: [],
    paletteSelection: 0,
    projects: [],
    projectsLoaded: false,
    projectCapabilities: { create: false, update: false },
    projectEditingId: null,
    communications: null,
    communicationsLoaded: false,
    communicationEditingProvider: null,
    communicationOrderDraft: [...DELIVERY_PROVIDERS],
    communicationOrderDirty: false,
    communicationBusy: {},
    recognition: null,
    voiceListening: false,
    voiceStarting: false,
    voiceBaseDraft: "",
    voiceFinalTranscript: "",
    speaking: false,
    speechPulseAt: 0,
    micStream: null,
    micAudioContext: null,
    micSource: null,
    micAnalyser: null,
    micMonitorState: "idle",
    connectedAt: null,
    lastContact: null,
    timers: [],
    canvas: null,
    globeCanvas: null,
    globeVoice: null,
    gestureController: null,
    gestureDrag: null,
    lastGestureTarget: null,
    contextPanel: "",
    contextPinned: false,
    fileScale: 1,
    allowedRoots: []
  };

  const $ = (selector, parent = document) => parent.querySelector(selector);
  const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];
  const elements = {};

  function readLocal(key, fallback) {
    try {
      const value = localStorage.getItem(key);
      return value ? JSON.parse(value) : fallback;
    } catch {
      return fallback;
    }
  }

  function writeLocal(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  }

  function uid(prefix = "local") {
    if (globalThis.crypto?.randomUUID) return `${prefix}-${crypto.randomUUID()}`;
    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function asArray(payload, key) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.[key])) return payload[key];
    if (Array.isArray(payload?.items)) return payload.items;
    if (Array.isArray(payload?.data)) return payload.data;
    return [];
  }

  function clamp(value, min = 0, max = 100) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : min;
  }

  function formatTime(value = Date.now()) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "UNKNOWN";
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
  }

  function formatDateTime(value = Date.now()) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return "Unknown";
    return new Intl.DateTimeFormat(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"
    }).format(date);
  }

  function relativeTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "recently";
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 10) return "just now";
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let amount = bytes;
    let unit = -1;
    do { amount /= 1024; unit += 1; } while (amount >= 1024 && unit < units.length - 1);
    return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[unit]}`;
  }

  function formatUptime(seconds) {
    const total = Number(seconds);
    if (!Number.isFinite(total) || total < 0) return "—";
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (days) return `${days}D ${hours}H`;
    if (hours) return `${hours}H ${minutes}M`;
    return `${minutes}M`;
  }

  async function fetchApi(path, options = {}, timeoutMs = 9000) {
    const timeoutController = new AbortController();
    const timeoutId = setTimeout(() => timeoutController.abort("timeout"), timeoutMs);
    let signal = timeoutController.signal;
    if (options.signal && globalThis.AbortSignal?.any) {
      signal = AbortSignal.any([options.signal, timeoutController.signal]);
    } else if (options.signal) {
      if (options.signal.aborted) timeoutController.abort(options.signal.reason);
      options.signal.addEventListener("abort", () => timeoutController.abort(options.signal.reason), { once: true });
    }

    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    headers.set("Accept", options.accept || "application/json");

    try {
      const response = await fetch(`${API_BASE}${path}`, { ...options, headers, signal });
      state.lastContact = new Date();
      updateLastContact();
      if (!response.ok) {
        let detail = "";
        try {
          const payload = await response.clone().json();
          detail = payload?.detail || payload?.error || payload?.message || "";
        } catch {
          detail = (await response.text()).slice(0, 180);
        }
        throw new Error(detail || `Request failed (${response.status})`);
      }
      return response;
    } catch (error) {
      if (error.name === "AbortError" || timeoutController.signal.aborted) {
        throw new Error(options.signal?.aborted ? "Request cancelled" : "JARVIS core timed out");
      }
      throw error;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function fetchJson(path, options = {}, timeoutMs = 9000) {
    const response = await fetchApi(path, options, timeoutMs);
    if (response.status === 204) return null;
    try {
      return await response.json();
    } catch {
      throw new Error("Core returned an unreadable response");
    }
  }

  function setConnection(online, data = {}) {
    state.online = Boolean(online);
    if (online) {
      state.statusFailures = 0;
      state.connectedAt ||= new Date();
    }
    const degraded = Boolean(online && (String(data.status).toLowerCase() === "degraded" || data.ollama?.ready === false));
    const dot = $(".status-dot", elements.coreStatus);
    dot.className = `status-dot ${!online ? "status-dot--offline" : degraded ? "status-dot--warning" : "status-dot--online"}`;
    elements.coreStatus.lastChild.textContent = !online ? "OFFLINE" : degraded ? "DEGRADED" : "ONLINE";
    elements.offlineBanner.hidden = online;
    const privacyMode = Boolean(state.remoteSettings?.desktop?.privacy_mode_enabled);
    elements.coreModeBadge.textContent = privacyMode ? "PRIVATE MODE" : online && !degraded ? "OLLAMA ACTIVE" : "SAFE MODE";
    elements.coreModeBadge.classList.toggle("online", privacyMode || (online && !degraded));
    elements.dialogCoreHealth.textContent = !online ? "Offline / safe mode" : degraded ? "Degraded — API online" : "Healthy";

    const model = data.ollama?.chat_model;
    const modelText = model && data.ollama?.ready !== false
      ? `OLLAMA / ${String(model).toUpperCase()}`
      : online && !degraded ? "OLLAMA / READY" : "OLLAMA / UNAVAILABLE";
    elements.modelStatus.textContent = modelText;
    elements.dialogModel.textContent = model || (online ? "Ready" : "Unavailable");
    updateProjectSummaries();
  }

  function updateLastContact() {
    elements.lastContact.textContent = state.lastContact ? formatDateTime(state.lastContact) : "Never";
  }

  function toast(message, type = "info") {
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.setAttribute("role", type === "error" ? "alert" : "status");
    item.textContent = message;
    elements.toastRegion.append(item);
    setTimeout(() => item.remove(), 4600);
  }

  function addAudit(action, severity = "info", details = "") {
    const entry = { id: uid("audit"), action, severity, details, timestamp: new Date().toISOString(), local: true };
    state.audit.unshift(entry);
    state.audit = state.audit.slice(0, 60);
    writeLocal(STORAGE.audit, state.audit);
    renderAudit();
  }

  function setBusy(button, busy, busyLabel = "Working…") {
    if (!button) return;
    if (busy) {
      button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = busyLabel;
    } else {
      button.disabled = false;
      if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel;
    }
  }

  async function checkStatus({ quiet = false } = {}) {
    const started = performance.now();
    try {
      const status = await fetchJson("/health", { cache: "no-store" }, 5000);
      const latency = Math.max(1, Math.round(performance.now() - started));
      elements.latencyStatus.textContent = `${latency} MS`;
      setConnection(true, status || {});
      applyStatus(status || {});
      return true;
    } catch (error) {
      state.statusFailures += 1;
      elements.latencyStatus.textContent = "— MS";
      setConnection(false);
      if (!quiet) toast(error.message || "JARVIS core is unreachable", "error");
      return false;
    }
  }

  function applyStatus(status) {
    const telemetry = status.telemetry || {};
    const uptime = telemetry.process?.uptime_seconds;
    const context = status.context_tokens;
    const contextMax = status.context_window;
    const tokens = status.tokens_per_second;
    const queue = status.queue_size ?? 0;
    elements.uptimeMetric.textContent = typeof uptime === "string" ? uptime : formatUptime(uptime);
    elements.contextMetric.textContent = contextMax
      ? `${Number(context || 0).toLocaleString()}/${Number(contextMax).toLocaleString()}`
      : context ? Number(context).toLocaleString() : "—";
    elements.tokenMetric.textContent = Number.isFinite(Number(tokens)) ? Number(tokens).toFixed(1) : "—";
    elements.queueMetric.textContent = String(queue ?? 0);
    applyTelemetry(telemetry);
  }

  async function loadTelemetry() {
    if (document.hidden) return;
    try {
      const health = await fetchJson("/health", { cache: "no-store" }, 5000);
      setConnection(true, health || {});
      applyStatus(health || {});
    } catch {
      if (!state.online) return;
      updateMetric("cpu", state.telemetry.cpu, "Telemetry unavailable");
      updateMetric("memory", state.telemetry.memory, "Telemetry unavailable");
      updateMetric("gpu", state.telemetry.gpu, "Telemetry unavailable");
    }
  }

  function applyTelemetry(payload = {}) {
    const cpu = payload.cpu_percent ?? 0;
    const memory = payload.memory?.percent ?? 0;
    const gpu = 0;
    const coreLoad = cpu;
    state.telemetry = {
      cpu: clamp(cpu), memory: clamp(memory), gpu, coreLoad: clamp(coreLoad)
    };
    state.systemTelemetryDisks = Array.isArray(payload.disks) ? payload.disks
      .filter((disk) => disk && typeof disk === "object")
      .slice(0, 20)
      .map((disk) => ({
        path: String(disk.path || "Local volume").slice(0, 260),
        percent: Number.isFinite(Number(disk.percent)) ? clamp(disk.percent) : null,
        totalBytes: Number.isFinite(Number(disk.total_bytes)) ? Number(disk.total_bytes) : null,
        usedBytes: Number.isFinite(Number(disk.used_bytes)) ? Number(disk.used_bytes) : null,
        freeBytes: Number.isFinite(Number(disk.free_bytes)) ? Number(disk.free_bytes) : null
      })) : [];
    updateMetric("cpu", state.telemetry.cpu, "Host processor utilization");
    updateMetric("memory", state.telemetry.memory, memoryDetail(payload.memory));
    updateMetric("gpu", state.telemetry.gpu, "Not reported by the local API");
    updateCoreLoad(state.telemetry.coreLoad);
    if (state.systemHealth) renderSystemHealth();
  }

  function memoryDetail(memory) {
    if (!memory || typeof memory !== "object") return "System memory utilization";
    if (memory.used_bytes != null && memory.total_bytes != null) {
      return `${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}`;
    }
    return "System memory utilization";
  }

  function updateMetric(name, value, detail) {
    const normalized = clamp(value);
    elements[`${name}Value`].textContent = `${Math.round(normalized)}%`;
    elements[`${name}Meter`].style.width = `${normalized}%`;
    elements[`${name}Detail`].textContent = detail || "Live telemetry";
  }

  function updateCoreLoad(load) {
    const value = Math.round(clamp(load));
    elements.orbLoad.textContent = `${value}%`;
    elements.coreVisualDescription.textContent = `Core activity visualization. Current load is ${value} percent.`;
    if (elements.globeCoreLoad) elements.globeCoreLoad.textContent = `${value}%`;
  }

  function renderNodes(nodes) {
    const normalized = nodes;
    state.nodes = normalized;
    elements.nodeList.replaceChildren();
    if (!normalized.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No service nodes are reporting. Retry when the local core is running.";
      elements.nodeList.append(empty);
      elements.nodeSummary.textContent = "0 ONLINE";
      updateProjectSummaries();
      return;
    }
    let onlineCount = 0;
    normalized.forEach((node) => {
      const name = String(node.name || node.id || node.service || "Service node");
      const capabilities = Array.isArray(node.capabilities) ? node.capabilities.map(String) : [];
      const resources = Array.isArray(node.resources)
        ? node.resources
        : Array.isArray(node.shared_roots) ? node.shared_roots : [];
      const type = String(node.type || node.role || node.description || "registered node");
      const status = String(node.status || node.state || (node.online === false ? "offline" : "online")).toLowerCase();
      if (["online", "ready", "healthy", "active"].includes(status)) onlineCount += 1;
      const item = document.createElement("div");
      item.className = "node-item";
      const icon = document.createElement("span");
      icon.className = "node-icon";
      icon.textContent = name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
      const copy = document.createElement("span");
      copy.className = "node-name";
      const strong = document.createElement("strong");
      strong.textContent = name;
      const small = document.createElement("small");
      const platform = node.platform ? String(node.platform) : "";
      const endpoint = safeEndpointForDisplay(node.url || node.endpoint);
      small.textContent = [platform, type, endpoint].filter(Boolean).join(" · ").toUpperCase();
      copy.append(strong, small);
      if (capabilities.length || resources.length) {
        const capabilityList = document.createElement("span");
        capabilityList.className = "node-capability-list";
        capabilities.slice(0, 4).forEach((capability) => {
          const badge = document.createElement("span");
          badge.className = "node-capability";
          badge.textContent = capability;
          capabilityList.append(badge);
        });
        resources.slice(0, 3).forEach((resource) => {
          const badge = document.createElement("span");
          badge.className = "node-capability node-resource";
          badge.textContent = String(resource?.name || resource?.path || resource?.id || resource);
          capabilityList.append(badge);
        });
        copy.append(capabilityList);
      }
      const stateNode = document.createElement("span");
      stateNode.className = `node-state ${["online", "ready", "healthy", "active"].includes(status) ? "" : status === "offline" ? "offline" : "degraded"}`;
      stateNode.textContent = status.toUpperCase();
      item.append(icon, copy, stateNode);
      elements.nodeList.append(item);
    });
    elements.nodeSummary.textContent = `${onlineCount} ONLINE`;
    updateProjectSummaries();
  }

  function safeEndpointForDisplay(value) {
    if (!value) return "";
    try {
      const endpoint = new URL(String(value));
      endpoint.username = "";
      endpoint.password = "";
      endpoint.search = "";
      endpoint.hash = "";
      return `${endpoint.origin}${endpoint.pathname === "/" ? "" : endpoint.pathname}`;
    } catch {
      return "private endpoint";
    }
  }

  async function loadSkills() {
    try {
      const payload = await fetchJson("/skills", { cache: "no-store" });
      const skills = asArray(payload, "skills");
      if (skills.length) {
        state.skills = skills.map((skill, index) => ({
          id: String(skill.id || skill.slug || `skill-${index}`),
          name: String(skill.name || skill.title || "Unnamed skill"),
          description: String(skill.description || skill.summary || "Core capability"),
          enabled: skill.enabled !== false && skill.status !== "disabled" && skill.available !== false,
          riskTier: String(skill.risk_tier || skill.riskTier || "low").toLowerCase(),
          icon: String(skill.icon || skill.name || "SK").replace(/[^a-z0-9 ]/gi, "").split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase()
        }));
      }
    } catch {
      state.skills = [...BUILTIN_SKILLS];
    }
    renderSkills();
  }

  function renderSkills() {
    elements.skillGrid.replaceChildren();
    elements.chatSkill.replaceChildren(new Option("Auto route", "auto"));
    let enabledCount = 0;
    state.skills.forEach((skill) => {
      if (skill.enabled) enabledCount += 1;
      const card = document.createElement("article");
      card.className = "skill-card";
      const title = document.createElement("h3");
      title.textContent = skill.name;
      const description = document.createElement("p");
      description.textContent = skill.description;
      const icon = document.createElement("span");
      icon.className = "skill-icon";
      icon.textContent = skill.icon || "SK";
      const status = document.createElement("span");
      status.className = `skill-status ${skill.enabled ? "" : "disabled"}`;
      status.textContent = skill.enabled
        ? `AVAILABLE · ${String(skill.riskTier || "low").toUpperCase()}`
        : "NOT LOADED";
      card.append(title, description, icon, status);
      elements.skillGrid.append(card);
      if (skill.enabled) elements.chatSkill.add(new Option(skill.name, skill.id));
    });
    elements.skillCount.textContent = `${enabledCount} READY`;
  }

  function addMessage(role, text = "", options = {}) {
    const fragment = elements.messageTemplate.content.cloneNode(true);
    const message = $(".message", fragment);
    const avatar = $(".message-avatar", fragment);
    const name = $(".message-meta strong", fragment);
    const time = $(".message-meta time", fragment);
    const paragraph = $(".message-content p", fragment);
    const isUser = role === "user";
    message.classList.add(isUser ? "message--user" : "message--jarvis");
    if (options.error) message.classList.add("message--error");
    avatar.textContent = isUser ? "Y" : "J";
    name.textContent = isUser ? "YOU" : "JARVIS";
    time.textContent = formatTime();
    paragraph.textContent = text;
    if (options.typing) paragraph.classList.add("typing-cursor");
    elements.chatLog.append(fragment);
    elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
    return elements.chatLog.lastElementChild;
  }

  function setupVoiceControls() {
    const Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
    const speechAvailable = "speechSynthesis" in globalThis && "SpeechSynthesisUtterance" in globalThis;
    elements.speakRepliesToggle.checked = Boolean(state.settings.speakReplies);
    elements.speakRepliesToggle.disabled = !speechAvailable;
    if (!speechAvailable) {
      elements.speakRepliesHelp.textContent = "Speech playback is unavailable in this browser.";
      state.settings.speakReplies = false;
    }
    elements.speakRepliesToggle.addEventListener("change", () => {
      state.settings.speakReplies = elements.speakRepliesToggle.checked;
      writeLocal(STORAGE.settings, state.settings);
      if (!state.settings.speakReplies) cancelSpeech();
    });

    if (!Recognition) {
      setMicButtons(false, true);
      updateVoiceActivity("unavailable");
      return;
    }

    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    state.recognition = recognition;

    recognition.addEventListener("start", () => {
      state.voiceStarting = false;
      state.voiceListening = true;
      setMicButtons(true);
      updateVoiceActivity("listening");
    });
    recognition.addEventListener("result", (event) => {
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const transcript = event.results[index][0]?.transcript || "";
        if (event.results[index].isFinal) state.voiceFinalTranscript += `${transcript.trim()} `;
        else interim += transcript;
      }
      const draft = [state.voiceBaseDraft, state.voiceFinalTranscript.trim(), interim.trim()].filter(Boolean).join(" ");
      syncCommandDraft(draft);
    });
    recognition.addEventListener("error", (event) => {
      state.voiceStarting = false;
      if (!["aborted", "no-speech"].includes(event.error)) toast(`Voice input unavailable: ${event.error}`, "error");
    });
    recognition.addEventListener("end", () => {
      state.voiceStarting = false;
      state.voiceListening = false;
      stopMicrophoneAnalyser();
      setMicButtons(false);
      updateVoiceActivity(state.speaking ? "speaking" : "idle");
      activeCommandInput()?.focus();
    });
    elements.micButton.addEventListener("click", toggleVoiceTranscription);
    elements.globeMicButton.addEventListener("click", toggleVoiceTranscription);
  }

  function toggleVoiceTranscription() {
    if (!state.recognition || state.chatBusy) return;
    if (state.voiceListening || state.voiceStarting) {
      stopListening();
      return;
    }
    cancelSpeech();
    syncCommandDraft(activeCommandInput()?.value || elements.chatInput.value);
    state.voiceBaseDraft = elements.chatInput.value.trim();
    state.voiceFinalTranscript = "";
    state.voiceStarting = true;
    startMicrophoneAnalyser();
    try {
      state.recognition.start();
    } catch (error) {
      state.voiceStarting = false;
      stopMicrophoneAnalyser();
      setMicButtons(false);
      updateVoiceActivity("idle");
      toast(`Voice input could not start: ${error.message}`, "error");
    }
  }

  function stopListening() {
    if (!state.recognition || (!state.voiceListening && !state.voiceStarting)) return;
    try {
      if (state.voiceListening) state.recognition.stop();
      else state.recognition.abort();
    } catch { /* Recognition already stopped. */ }
    state.voiceStarting = false;
    stopMicrophoneAnalyser();
  }

  function setMicButtons(listening, unavailable = false) {
    [elements.micButton, elements.globeMicButton].forEach((button) => {
      if (!button) return;
      button.classList.toggle("listening", listening);
      button.disabled = unavailable || state.chatBusy;
      button.setAttribute("aria-pressed", String(listening));
      button.setAttribute("aria-label", unavailable
        ? "Voice transcription unavailable"
        : listening ? "Stop voice transcription" : "Start voice transcription");
      button.title = unavailable ? "Voice transcription is unavailable in this browser" : "Transcribe speech into the command draft";
    });
    $(".mic-label", elements.micButton).textContent = unavailable ? "Unavailable" : listening ? "Stop" : "Talk";
    $("span", elements.globeMicButton).textContent = unavailable ? "Unavailable" : listening ? "Stop" : "Talk";
  }

  async function startMicrophoneAnalyser() {
    const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!navigator.mediaDevices?.getUserMedia || !AudioContextClass) {
      state.micMonitorState = "unavailable";
      updateVoiceActivity("listening");
      return;
    }
    state.micMonitorState = "requesting";
    updateVoiceActivity("listening");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false
      });
      if (!state.voiceStarting && !state.voiceListening) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const audioContext = new AudioContextClass();
      if (audioContext.state === "suspended") await audioContext.resume();
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = .72;
      const source = audioContext.createMediaStreamSource(stream);
      source.connect(analyser);
      state.micStream = stream;
      state.micAudioContext = audioContext;
      state.micSource = source;
      state.micAnalyser = analyser;
      state.micMonitorState = "live";
      updateVoiceActivity("listening");
    } catch {
      state.micMonitorState = "unavailable";
      updateVoiceActivity(state.voiceListening || state.voiceStarting ? "listening" : "idle");
    }
  }

  function stopMicrophoneAnalyser() {
    state.micStream?.getTracks().forEach((track) => track.stop());
    state.micStream = null;
    try { state.micSource?.disconnect(); } catch { /* Source may already be disconnected. */ }
    state.micSource = null;
    state.micAnalyser = null;
    if (state.micAudioContext && state.micAudioContext.state !== "closed") {
      state.micAudioContext.close().catch(() => {});
    }
    state.micAudioContext = null;
    if (state.micMonitorState !== "unavailable") state.micMonitorState = "idle";
  }

  function updateVoiceActivity(mode) {
    elements.voiceActivity.className = `voice-activity ${mode === "listening" || mode === "speaking" ? mode : ""}`;
    const labels = {
      listening: "VOICE LISTENING",
      speaking: "VOICE SPEAKING",
      unavailable: "VOICE UNAVAILABLE",
      idle: "VOICE IDLE"
    };
    elements.voiceActivity.lastChild.textContent = labels[mode] || labels.idle;
    if (elements.jarvisPresence?.dataset.state !== "stopped" && !state.chatBusy) {
      setJarvisState(
        mode === "listening" ? "listening" : mode === "speaking" ? "acting" : "idle",
        mode
      );
    }
    if (elements.globeJarvisVoiceStatus) {
      elements.globeJarvisVoiceStatus.textContent = state.speaking ? "SPEAKING" : "VOICE IDLE";
      elements.globeMicStatus.textContent = !state.recognition
        ? "MIC UNAVAILABLE"
        : state.voiceListening || state.voiceStarting
          ? state.micMonitorState === "live" ? "LIVE LEVEL" : state.micMonitorState === "requesting" ? "REQUESTING LEVEL" : "LISTENING · LEVEL UNAVAILABLE"
          : state.micMonitorState === "unavailable" ? "MIC IDLE · LEVEL UNAVAILABLE" : "MIC IDLE";
      $(".globe-wave--jarvis", elements.globeVoiceDisplay).classList.toggle("active", state.speaking);
      $(".globe-wave--user", elements.globeVoiceDisplay).classList.toggle("active", state.voiceListening || state.voiceStarting);
      state.globeVoice?.start();
    }
  }

  function speakReply(text) {
    if (!state.settings.speakReplies || !("speechSynthesis" in globalThis) || !text.trim()) return;
    cancelSpeech();
    const utterance = new SpeechSynthesisUtterance(text.slice(0, 12000));
    utterance.lang = navigator.language || "en-US";
    utterance.addEventListener("start", () => {
      state.speaking = true;
      state.speechPulseAt = performance.now();
      updateVoiceActivity("speaking");
    });
    utterance.addEventListener("boundary", () => {
      state.speechPulseAt = performance.now();
    });
    const finish = () => {
      state.speaking = false;
      updateVoiceActivity(state.voiceListening ? "listening" : "idle");
    };
    utterance.addEventListener("end", finish);
    utterance.addEventListener("error", finish);
    speechSynthesis.speak(utterance);
  }

  function cancelSpeech() {
    if ("speechSynthesis" in globalThis) speechSynthesis.cancel();
    state.speaking = false;
    if (elements.voiceActivity) updateVoiceActivity(state.voiceListening ? "listening" : state.recognition ? "idle" : "unavailable");
  }

  function activeCommandInput() {
    return document.body.classList.contains("globe-mode") ? elements.globeCommandInput : elements.chatInput;
  }

  function syncCommandDraft(value, source = null) {
    const draft = String(value || "").slice(0, Number(elements.chatInput.maxLength) || 8000);
    if (source !== elements.chatInput) elements.chatInput.value = draft;
    if (source !== elements.globeCommandInput) elements.globeCommandInput.value = draft;
    resizeChatInput();
    resizeGlobeCommandInput();
  }

  function setJarvisState(mode, detail = "") {
    const allowed = new Set(["idle", "listening", "thinking", "acting", "defense", "stopped"]);
    const next = allowed.has(mode) ? mode : "idle";
    if (elements.jarvisPresence) elements.jarvisPresence.dataset.state = next;
    if (elements.jarvisStateLabel) {
      elements.jarvisStateLabel.textContent = `JARVIS · ${detail || next}`.toUpperCase();
    }
  }

  function contextElement(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  async function loadAllowedRoots() {
    if (state.allowedRoots.length) return state.allowedRoots;
    try {
      const payload = await fetchJson("/coding/status", { cache: "no-store" }, 8000);
      state.allowedRoots = Array.isArray(payload?.allowed_roots)
        ? payload.allowed_roots.map(String).filter(Boolean)
        : [];
    } catch { /* The panel will explain that roots are unavailable. */ }
    return state.allowedRoots;
  }

  async function searchSpatialFiles(form, results, status) {
    const data = new FormData(form);
    const query = String(data.get("query") || "").trim();
    const path = String(data.get("root") || "");
    const extension = String(data.get("extension") || "").trim();
    if (!path) {
      status.textContent = "No allowed file root is available.";
      return;
    }
    status.textContent = "SEARCHING ALLOWED ROOT…";
    setJarvisState("thinking", "searching files");
    try {
      const payload = await fetchJson("/files/find", {
        method: "POST",
        body: JSON.stringify({ path, query, extension: extension || null, max_results: 60 })
      }, 30000);
      const outcome = payload?.result || payload;
      const matches = Array.isArray(outcome?.matches) ? outcome.matches : [];
      results.replaceChildren();
      let selectedPath = "";
      matches.forEach((item) => {
        const card = contextElement("button", "spatial-file-card");
        card.type = "button";
        card.dataset.path = String(item.path || "");
        card.append(
          contextElement("strong", "", String(item.name || "Unnamed")),
          contextElement("small", "", String(item.relative_path || item.path || "")),
          contextElement("small", "", item.type === "directory" ? "FOLDER" : `${item.extension || "FILE"} · ${formatBytes(item.size_bytes || 0)}`)
        );
        card.addEventListener("click", () => {
          $$(".spatial-file-card", results).forEach((node) => node.classList.remove("selected"));
          card.classList.add("selected");
          selectedPath = card.dataset.path;
        });
        card.addEventListener("dblclick", () => requestFileReveal(card.dataset.path));
        results.append(card);
      });
      $(".spatial-file-reveal", elements.contextPanelBody)?.remove();
      const reveal = contextElement("button", "secondary-button compact-button spatial-file-reveal", "Reveal selected");
      reveal.type = "button";
      reveal.disabled = matches.length === 0;
      reveal.addEventListener("click", () => {
        if (selectedPath) requestFileReveal(selectedPath);
        else toast("Select one exact file first", "warning");
      });
      results.before(reveal);
      status.textContent = `${matches.length} RESULT${matches.length === 1 ? "" : "S"}${outcome?.truncated ? " · LIMITED" : ""}`;
    } catch (error) {
      results.replaceChildren(contextElement("p", "empty-state", error.message || "File search failed safely."));
      status.textContent = "SEARCH UNAVAILABLE";
    } finally {
      setJarvisState("idle");
    }
  }

  async function requestFileReveal(path) {
    if (!path) return;
    try {
      const outcome = await fetchJson("/files/reveal", {
        method: "POST",
        body: JSON.stringify({ path, query: "", max_results: 1 })
      });
      if (outcome?.status === "approval_required") {
        toast("File reveal is waiting for local approval", "warning");
        await loadApprovals();
      }
    } catch (error) {
      toast(`File reveal was not queued: ${error.message}`, "error");
    }
  }

  async function renderFilesContext(initialQuery = "") {
    const roots = await loadAllowedRoots();
    const form = contextElement("form", "spatial-file-form");
    const query = contextElement("input");
    query.name = "query";
    query.placeholder = "Search file and folder names…";
    query.autocomplete = "off";
    query.maxLength = 300;
    query.value = initialQuery;
    const root = contextElement("select");
    root.name = "root";
    root.setAttribute("aria-label", "Allowed search root");
    roots.forEach((path) => {
      const option = contextElement("option", "", path);
      option.value = path;
      root.append(option);
    });
    const extension = contextElement("input");
    extension.name = "extension";
    extension.placeholder = "Extension (optional)";
    extension.maxLength = 20;
    const submit = contextElement("button", "primary-button compact-button", "Search");
    submit.type = "submit";
    form.append(query, extension, root, submit);
    const controls = contextElement("div", "spatial-file-controls");
    const status = contextElement("span", "", roots.length ? "READY · ROOT-BOUNDED" : "NO ALLOWED ROOTS");
    const zoomLabel = contextElement("label", "", "RESULT SIZE ");
    const zoom = contextElement("input");
    zoom.type = "range";
    zoom.min = "70";
    zoom.max = "150";
    zoom.value = String(Math.round(state.fileScale * 100));
    zoom.addEventListener("input", () => {
      state.fileScale = Number(zoom.value) / 100;
      results.style.setProperty("--file-scale", String(state.fileScale));
    });
    zoomLabel.append(zoom);
    controls.append(status, zoomLabel);
    const results = contextElement("div", "spatial-file-results");
    results.style.setProperty("--file-scale", String(state.fileScale));
    results.append(contextElement("p", "empty-state", "Search by voice, keyboard, mouse, or hand control. No file contents are read by this view."));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      searchSpatialFiles(form, results, status);
    });
    elements.contextPanelBody.replaceChildren(form, controls, results);
    if (initialQuery && roots.length) form.requestSubmit();
  }

  function renderAngeronaControlContext(statusPayload, tacticsPayload) {
    const shell = contextElement("div");
    const copy = contextElement("div", "angerona-safety-note");
    copy.append(contextElement("p", "", `Mode: observe / recommend / approved execute. MCP: ${statusPayload?.mcp_ready ? "ready" : "not ready"}. Saved tactics: ${asArray(tacticsPayload, "tactics").length}. Every input is verified against one exact Angerona process and requires local approval.`));
    const inspectForm = contextElement("form", "spatial-file-form");
    const title = contextElement("input");
    title.placeholder = "Exact Angerona window title";
    title.maxLength = 300;
    title.value = "Angerona";
    const inspect = contextElement("button", "primary-button compact-button", "Inspect controls");
    inspect.type = "submit";
    inspectForm.append(title, inspect);
    const actionForm = contextElement("form", "spatial-file-form");
    const action = contextElement("select");
    ["focus", "move_to_control", "invoke", "set_text", "send_keys", "scroll"].forEach((name) => {
      const option = contextElement("option", "", name.replaceAll("_", " ").toUpperCase());
      option.value = name;
      action.append(option);
    });
    const actionValue = contextElement("input");
    actionValue.placeholder = "Text, safe key, or scroll steps";
    actionValue.maxLength = 2000;
    const queue = contextElement("button", "secondary-button compact-button", "Queue action");
    queue.type = "submit";
    actionForm.append(action, actionValue, queue);
    const targetStatus = contextElement("p", "defense-updated", "Inspect one exact window, then select an accessible control.");
    const controls = contextElement("div", "spatial-file-results");
    let selected = null;
    inspectForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      selected = null;
      controls.replaceChildren(contextElement("p", "empty-state", "Verifying Angerona process and controls…"));
      try {
        const payload = await fetchJson("/security/angerona/inspect", {
          method: "POST",
          body: JSON.stringify({ window_title: title.value.trim(), limit: 200 })
        }, 30000);
        const items = Array.isArray(payload?.result?.controls) ? payload.result.controls : [];
        controls.replaceChildren();
        items.forEach((item) => {
          const card = contextElement("button", "spatial-file-card");
          card.type = "button";
          card.append(
            contextElement("strong", "", String(item.name || "Unnamed control")),
            contextElement("small", "", `${item.control_type || "Control"} · ${item.automation_id || "no automation id"}`)
          );
          card.addEventListener("click", () => {
            $$(".spatial-file-card", controls).forEach((node) => node.classList.remove("selected"));
            card.classList.add("selected");
            selected = item;
            targetStatus.textContent = `TARGET: ${item.name} · ${item.control_type || "CONTROL"}`;
          });
          controls.append(card);
        });
        if (!items.length) controls.append(contextElement("p", "empty-state", "No visible named controls were reported."));
      } catch (error) {
        controls.replaceChildren(contextElement("p", "empty-state", error.message));
      }
    });
    actionForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const actionName = action.value;
      if (actionName !== "focus" && !selected) {
        toast("Select one exact Angerona control first", "warning");
        return;
      }
      const body = {
        window_title: title.value.trim(),
        action: actionName,
        control_name: selected?.name || null,
        automation_id: selected?.automation_id || null,
        control_type: selected?.control_type || null,
        text: actionName === "set_text" ? actionValue.value : null,
        keys: actionName === "send_keys" ? actionValue.value.toUpperCase() : null,
        scroll_steps: actionName === "scroll" ? Number(actionValue.value || 0) : 0
      };
      try {
        const payload = await fetchJson("/security/angerona/actions", { method: "POST", body: JSON.stringify(body) });
        if (payload?.status === "approval_required") {
          toast("Angerona action is waiting for local approval", "warning");
          await loadApprovals();
        }
      } catch (error) {
        toast(`Angerona action was not queued: ${error.message}`, "error");
      }
    });
    shell.append(copy, inspectForm, actionForm, targetStatus, controls);
    elements.contextPanelBody.replaceChildren(shell);
  }

  async function openContextPanel(kind, options = {}) {
    if (!elements.adaptiveDashboardToggle?.checked && !options.force) return;
    state.contextPanel = kind;
    elements.jarvisContextPanel.hidden = false;
    elements.contextLaunchers.classList.toggle("pinned", state.contextPinned);
    if (kind === "settings") {
      elements.jarvisContextPanel.hidden = true;
      elements.openSettings.click();
      return;
    }
    const titles = { files: "Spatial Files", health: "Live System Data", angerona: "Angerona Defense", memory: "Local Memory" };
    elements.contextPanelTitle.textContent = titles[kind] || "JARVIS Data";
    elements.contextPanelBody.replaceChildren(contextElement("p", "empty-state", "Loading local data…"));
    if (kind === "files") {
      await renderFilesContext(options.query || "");
    } else if (kind === "health") {
      const telemetry = state.systemHealth || state.telemetry || {};
      const summary = contextElement("dl", "defense-policy-list");
      const values = [
        ["CPU", `${Math.round(telemetry.cpu_percent ?? state.telemetry.cpu ?? 0)}%`],
        ["Memory", `${Math.round(telemetry.memory_percent ?? state.telemetry.memory ?? 0)}%`],
        ["Core state", state.online ? "Local core online" : "Local core degraded"],
        ["Privacy", "Local telemetry only"]
      ];
      values.forEach(([label, value]) => {
        const row = contextElement("div");
        row.append(contextElement("dt", "", label), contextElement("dd", "", value));
        summary.append(row);
      });
      elements.contextPanelBody.replaceChildren(summary);
    } else if (kind === "angerona") {
      setJarvisState("defense");
      try {
        const [statusPayload, tacticsPayload] = await Promise.all([
          fetchJson("/security/angerona", { cache: "no-store" }, 8000),
          fetchJson("/security/angerona/tactics", { cache: "no-store" }, 8000)
        ]);
        renderAngeronaControlContext(statusPayload, tacticsPayload);
      } catch (error) {
        elements.contextPanelBody.replaceChildren(contextElement("p", "empty-state", error.message));
      } finally {
        setJarvisState("idle");
      }
    } else if (kind === "memory") {
      const memories = state.memories.slice(0, 8);
      const list = contextElement("div", "memory-results");
      if (!memories.length) list.append(contextElement("p", "empty-state", "No cached memory items. Use the Memory workspace to add or search durable memory."));
      memories.forEach((item) => list.append(contextElement("p", "", String(item.content || item).slice(0, 280))));
      elements.contextPanelBody.replaceChildren(list);
    }
  }

  async function handleDashboardIntent(message) {
    const match = message.match(/^\s*(?:jarvis[, ]+)?(?:show|open|display|bring up|pull up)\s+(?:my\s+)?(.+)$/i);
    if (!match) return null;
    const request = match[1].trim();
    if (/settings|preferences/i.test(request)) {
      await openContextPanel("settings", { force: true });
      return "Settings are open on the dashboard.";
    }
    if (/angerona|defen[cs]e|security/i.test(request)) {
      await openContextPanel("angerona", { force: true });
      return "Angerona's defensive context is displayed. Input actions remain locally approval-gated.";
    }
    if (/memory|memories/i.test(request)) {
      await openContextPanel("memory", { force: true });
      return "Local memory context is displayed.";
    }
    if (/telemetry|system data|health|performance/i.test(request)) {
      await openContextPanel("health", { force: true });
      return "Live local system data is displayed.";
    }
    const fileMatch = request.match(/(?:files?|folders?)(?:\s+(?:named|matching|for))?\s*(.*)/i);
    if (fileMatch) {
      await openContextPanel("files", { force: true, query: fileMatch[1].trim() });
      return "Spatial file search is displayed inside the configured roots.";
    }
    return null;
  }

  function stopGestureControl(reason = "operator") {
    state.gestureController?.stop(reason);
    elements.gestureButton.setAttribute("aria-pressed", "false");
    elements.gestureButton.textContent = "HAND CONTROL OFF";
    if (reason !== "fist") setJarvisState("idle");
  }

  async function startGestureControl() {
    if (!window.JarvisGestureController) {
      toast("The local hand tracker is still loading", "warning");
      return;
    }
    if (!state.gestureController) {
      state.gestureController = new window.JarvisGestureController({
        target: elements.jarvisPresence,
        lowPower: elements.gestureLowPowerToggle.checked
      });
    }
    try {
      await fetchJson("/security/angerona/resume", { method: "POST" }, 8000);
      await state.gestureController.start();
      elements.gestureControlToggle.checked = true;
      elements.gestureButton.setAttribute("aria-pressed", "true");
      elements.gestureButton.textContent = "HAND CONTROL ON";
      setJarvisState("acting", "hand control");
      toast("Local hand control active · no frames stored or uploaded", "success");
    } catch (error) {
      stopGestureControl("start-failed");
      toast(`Hand control unavailable: ${error.message}`, "error");
    }
  }

  async function emergencyStopAll(reason = "operator") {
    stopGestureControl(reason);
    stopListening();
    state.chatController?.abort("emergency-stop");
    setJarvisState("stopped", "emergency stop");
    try { await fetchJson("/security/angerona/emergency-stop", { method: "POST" }, 8000); } catch { /* Local stop remains effective. */ }
    toast("Emergency stop engaged · gesture and Angerona inputs paused", "error");
  }

  function setupAdaptiveDashboard() {
    $$('[data-context-panel]', elements.contextLaunchers).forEach((button) => {
      const show = () => openContextPanel(button.dataset.contextPanel);
      button.addEventListener("click", show);
      button.addEventListener("focus", show);
      button.addEventListener("mouseenter", show);
    });
    elements.closeContextPanel.addEventListener("click", () => {
      elements.jarvisContextPanel.hidden = true;
      state.contextPinned = false;
      elements.pinContextPanel.setAttribute("aria-pressed", "false");
      elements.contextLaunchers.classList.remove("pinned");
    });
    elements.pinContextPanel.addEventListener("click", () => {
      state.contextPinned = !state.contextPinned;
      elements.pinContextPanel.setAttribute("aria-pressed", String(state.contextPinned));
      elements.contextLaunchers.classList.toggle("pinned", state.contextPinned);
    });
    elements.gestureButton.addEventListener("click", () => {
      if (state.gestureController?.running) stopGestureControl();
      else startGestureControl();
    });
    elements.emergencyStop.addEventListener("click", () => emergencyStopAll("operator"));

    window.addEventListener("jarvis:gesture-pointer", (event) => {
      const target = document.elementFromPoint(event.detail.x, event.detail.y);
      state.lastGestureTarget?.classList?.remove("gesture-hover");
      state.lastGestureTarget = target?.closest("button,input,select,textarea,.spatial-file-card") || null;
      state.lastGestureTarget?.classList?.add("gesture-hover");
    });
    window.addEventListener("jarvis:gesture-pinchstart", (event) => {
      const target = document.elementFromPoint(event.detail.x, event.detail.y);
      const header = target?.closest("#jarvisContextPanel > header");
      if (header) {
        const rectangle = elements.jarvisContextPanel.getBoundingClientRect();
        state.gestureDrag = { offsetX: event.detail.x - rectangle.left, offsetY: event.detail.y - rectangle.top };
        return;
      }
      const safeTarget = target?.closest("button,input,select,textarea,.spatial-file-card");
      if (safeTarget?.matches("input,select,textarea")) safeTarget.focus();
      else safeTarget?.click();
    });
    window.addEventListener("jarvis:gesture-drag", (event) => {
      if (!state.gestureDrag || elements.jarvisContextPanel.hidden) return;
      const parent = elements.jarvisPresence.getBoundingClientRect();
      const panel = elements.jarvisContextPanel.getBoundingClientRect();
      const left = clamp(event.detail.x - parent.left - state.gestureDrag.offsetX, 0, parent.width - panel.width);
      const top = clamp(event.detail.y - parent.top - state.gestureDrag.offsetY, 0, parent.height - panel.height);
      Object.assign(elements.jarvisContextPanel.style, { left: `${left}px`, top: `${top}px`, right: "auto", bottom: "auto" });
    });
    window.addEventListener("jarvis:gesture-pinchend", () => { state.gestureDrag = null; });
    window.addEventListener("jarvis:gesture-zoom", (event) => {
      state.fileScale = clamp(state.fileScale * event.detail.factor, .7, 1.5);
      $(".spatial-file-results", elements.contextPanelBody)?.style.setProperty("--file-scale", String(state.fileScale));
    });
    window.addEventListener("jarvis:gesture-scroll", (event) => {
      const scroller = elements.jarvisContextPanel.hidden ? document.scrollingElement : elements.jarvisContextPanel;
      scroller?.scrollBy({ top: event.detail.delta, behavior: "auto" });
    });
    window.addEventListener("jarvis:gesture-swipe", (event) => {
      const panels = ["files", "health", "angerona", "memory"];
      let index = Math.max(0, panels.indexOf(state.contextPanel));
      index = (index + (event.detail.direction === "left" ? 1 : -1) + panels.length) % panels.length;
      openContextPanel(panels[index], { force: true });
    });
    window.addEventListener("jarvis:emergency-stop", () => emergencyStopAll("fist"));
    window.addEventListener("jarvis:gesture-error", (event) => toast(event.detail.message, "error"));
  }

  async function handleChatSubmit(event) {
    event.preventDefault();
    const message = elements.chatInput.value.trim();
    if (!message || state.chatBusy) return;
    const dashboardReply = await handleDashboardIntent(message);
    if (dashboardReply) {
      addMessage("user", message);
      syncCommandDraft("");
      addMessage("jarvis", dashboardReply);
      addAudit("Dashboard context displayed", "info", message.slice(0, 140));
      speakReply(dashboardReply);
      return;
    }
    cancelSpeech();
    stopListening();
    const skill = elements.chatSkill.value;
    const lowered = message.toLowerCase();
    const skillId = skill !== "auto"
      ? skill
      : /angerona|defen[cs]e|security|threat/.test(lowered)
        ? "angerona_defense"
        : /\b(file|folder|directory|locate)\b/.test(lowered)
          ? "spatial_files"
          : /\b(code|program|build|test|debug|repository)\b/.test(lowered)
            ? "software_engineer"
            : "executive_assistant";
    addMessage("user", message);
    syncCommandDraft("");
    setChatBusy(true);
    setJarvisState("thinking");
    addAudit(`Command submitted${skill !== "auto" ? ` to ${skill}` : ""}`, "info", message.slice(0, 140));
    const reply = addMessage("jarvis", "", { typing: true });
    const target = $("p", reply);
    state.chatController = new AbortController();

    try {
      const response = await fetchApi("/chat", {
        method: "POST",
        body: JSON.stringify({
          messages: [{ role: "user", content: message }],
          skill_id: skillId,
          use_tools: true
        }),
        accept: "application/json",
        signal: state.chatController.signal
      }, 310000);
      await consumeChatResponse(response, target);
      if (!target.textContent.trim()) target.textContent = "The core completed without returning a message.";
      target.classList.remove("typing-cursor");
      addAudit("JARVIS response completed", "info");
      speakReply(target.textContent);
    } catch (error) {
      target.classList.remove("typing-cursor");
      reply.classList.add("message--error");
      target.textContent = state.chatController.signal.aborted
        ? "Generation stopped."
        : `I couldn't reach the local Ollama core. ${error.message || "Check that the JARVIS service is running."}`;
      addAudit("Command failed safely", "danger", error.message || "Connection error");
      if (!state.chatController.signal.aborted) {
        state.statusFailures += 1;
        setConnection(false);
      }
    } finally {
      setChatBusy(false);
      if (elements.jarvisPresence.dataset.state !== "stopped") setJarvisState("idle");
      state.chatController = null;
      elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
      activeCommandInput()?.focus();
    }
  }

  function setChatBusy(busy) {
    state.chatBusy = busy;
    elements.chatInput.disabled = busy;
    elements.globeCommandInput.disabled = busy;
    elements.chatSkill.disabled = busy;
    elements.sendMessage.disabled = false;
    elements.sendMessage.querySelector("span").textContent = busy ? "Stop" : "Transmit";
    elements.globeSendButton.querySelector("span").textContent = busy ? "Stop" : "Transmit";
    elements.sendMessage.classList.toggle("danger", busy);
    elements.globeSendButton.classList.toggle("danger", busy);
    setMicButtons(state.voiceListening, !state.recognition);
  }

  async function consumeChatResponse(response, target) {
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      const text = extractChatText(payload);
      await progressiveText(target, text);
      return;
    }
    if (!response.body) {
      await progressiveText(target, await response.text());
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const isSse = contentType.includes("text/event-stream");
    const isNdjson = contentType.includes("ndjson") || contentType.includes("jsonl");
    let pending = "";
    let rawText = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      const chunk = decoder.decode(value, { stream: true });
      if (!isSse && !isNdjson) {
        rawText += chunk;
        target.textContent += chunk;
        scrollChatIfNearBottom();
        continue;
      }
      pending += chunk;
      const lines = pending.split(/\r?\n/);
      pending = lines.pop() || "";
      for (const line of lines) appendStreamLine(line, target, isSse);
    }
    pending += decoder.decode();
    if (pending.trim()) appendStreamLine(pending, target, isSse);
    if (!isSse && !isNdjson && !rawText.trim()) target.textContent = "";
  }

  function appendStreamLine(line, target, isSse) {
    let data = line.trim();
    if (!data || data.startsWith(":")) return;
    if (isSse) {
      if (!data.startsWith("data:")) return;
      data = data.slice(5).trimStart();
    }
    if (data === "[DONE]") return;
    try {
      const payload = JSON.parse(data);
      const text = extractChatText(payload, true);
      if (text) target.textContent += text;
    } catch {
      target.textContent += data;
    }
    scrollChatIfNearBottom();
  }

  function extractChatText(payload, stream = false) {
    if (typeof payload === "string") return payload;
    if (!payload || typeof payload !== "object") return "";
    const value = payload.delta ?? payload.token ?? payload.content ?? payload.text ?? payload.response
      ?? (typeof payload.message === "string" ? payload.message : payload.message?.content)
      ?? payload.choices?.[0]?.delta?.content ?? payload.choices?.[0]?.message?.content;
    if (typeof value === "string") return value;
    if (stream && value != null) return String(value);
    return "";
  }

  async function progressiveText(target, text) {
    const value = String(text || "");
    if (!value) return;
    const reduce = state.settings.lowMotion || matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || value.length > 9000) {
      target.textContent = value;
      return;
    }
    const chunkSize = Math.max(2, Math.ceil(value.length / 160));
    for (let index = 0; index < value.length; index += chunkSize) {
      if (state.chatController?.signal.aborted) throw new DOMException("Stopped", "AbortError");
      target.textContent += value.slice(index, index + chunkSize);
      scrollChatIfNearBottom();
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }

  function scrollChatIfNearBottom() {
    const distance = elements.chatLog.scrollHeight - elements.chatLog.clientHeight - elements.chatLog.scrollTop;
    if (distance < 130) elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  }

  function resizeChatInput() {
    elements.chatInput.style.height = "auto";
    elements.chatInput.style.height = `${Math.min(170, elements.chatInput.scrollHeight)}px`;
  }

  function resizeGlobeCommandInput() {
    elements.globeCommandInput.style.height = "auto";
    elements.globeCommandInput.style.height = `${Math.min(92, elements.globeCommandInput.scrollHeight)}px`;
  }

  async function loadTasks() {
    try {
      const payload = await fetchJson("/tasks", { cache: "no-store" });
      const remote = asArray(payload, "tasks");
      if (remote.length || payload) {
        const localOnly = state.tasks.filter((task) => task.local && !task.synced);
        state.tasks = [...remote.map(normalizeTask), ...localOnly.filter((local) => !remote.some((item) => String(item.id) === String(local.id)))];
        writeLocal(STORAGE.tasks, state.tasks);
      }
    } catch {
      // Local tasks remain fully usable in safe offline mode.
    }
    renderTasks();
  }

  function normalizeTask(task) {
    const numericPriority = Number(task.priority);
    const priority = ["low", "normal", "high"].includes(task.priority)
      ? task.priority
      : numericPriority <= 1 ? "low" : numericPriority >= 4 ? "high" : "normal";
    return {
      id: String(task.id || uid("task")),
      title: String(task.title || task.name || "Untitled task"),
      priority,
      completed: Boolean(task.completed ?? task.done ?? task.state === "done"),
      created_at: task.created_at || task.createdAt || new Date().toISOString(),
      due_at: task.due_at || task.dueAt || null,
      local: Boolean(task.local)
    };
  }

  function renderTasks() {
    elements.taskList.replaceChildren();
    const ordered = [...state.tasks].sort((a, b) => Number(a.completed) - Number(b.completed) || new Date(b.created_at) - new Date(a.created_at));
    if (!ordered.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No tasks queued. Create one above; it will be kept locally if the core is offline.";
      elements.taskList.append(empty);
    }
    ordered.forEach((task) => {
      const item = document.createElement("div");
      const dueTime = task.due_at ? new Date(task.due_at) : null;
      const overdue = Boolean(!task.completed && dueTime && !Number.isNaN(dueTime.getTime()) && dueTime.getTime() <= Date.now());
      item.className = `task-item ${task.completed ? "completed" : ""} ${overdue ? "overdue" : ""}`;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = Boolean(task.completed);
      checkbox.setAttribute("aria-label", `Mark ${task.title} ${task.completed ? "incomplete" : "complete"}`);
      checkbox.addEventListener("change", () => updateTask(task.id, checkbox.checked));
      const copy = document.createElement("span");
      copy.className = "task-copy";
      const title = document.createElement("strong");
      title.textContent = task.title;
      const meta = document.createElement("small");
      const priority = document.createElement("span");
      priority.className = `priority ${task.priority}`;
      priority.textContent = task.priority;
      meta.append(priority, document.createTextNode(` · ${relativeTime(task.created_at)}${task.local ? " · LOCAL" : ""}`));
      if (dueTime && !Number.isNaN(dueTime.getTime())) {
        const due = document.createElement("span");
        due.className = "task-due";
        due.textContent = ` · ${overdue ? "OVERDUE" : "DUE"} ${formatDateTime(dueTime)}`;
        meta.append(due);
      }
      copy.append(title, meta);
      item.append(checkbox, copy);
      elements.taskList.append(item);
    });
    const pending = state.tasks.filter((task) => !task.completed).length;
    elements.taskCount.textContent = String(pending);
    updateProjectSummaries();
  }

  async function createTask(event) {
    event.preventDefault();
    const form = new FormData(elements.taskForm);
    const title = String(form.get("title") || "").trim();
    const priority = String(form.get("priority") || "normal");
    const dueInput = String(form.get("due_at") || "").trim();
    const dueAt = dueInput ? new Date(dueInput).toISOString() : null;
    const numericPriority = { low: 1, normal: 2, high: 4 }[priority] || 2;
    if (!title) return;
    const optimistic = normalizeTask({ id: uid("task"), title, priority, due_at: dueAt, local: true });
    state.tasks.unshift(optimistic);
    writeLocal(STORAGE.tasks, state.tasks);
    renderTasks();
    elements.taskForm.reset();
    addAudit(`Task created: ${title}`, "info");
    try {
      const payload = await fetchJson("/tasks", { method: "POST", body: JSON.stringify({ title, priority: numericPriority, due_at: dueAt }) });
      if (payload) {
        const remote = normalizeTask(payload.task || payload);
        state.tasks = state.tasks.map((task) => task.id === optimistic.id ? remote : task);
        writeLocal(STORAGE.tasks, state.tasks);
        renderTasks();
      }
      toast("Task added to JARVIS", "success");
    } catch {
      toast("Core offline — task saved locally", "info");
    }
  }

  async function updateTask(id, completed) {
    const index = state.tasks.findIndex((task) => String(task.id) === String(id));
    if (index < 0) return;
    const previous = state.tasks[index].completed;
    state.tasks[index].completed = completed;
    writeLocal(STORAGE.tasks, state.tasks);
    renderTasks();
    addAudit(`Task marked ${completed ? "complete" : "active"}: ${state.tasks[index].title}`, "info");
    if (state.tasks[index].local) return;
    try {
      await fetchJson(`/tasks/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ state: completed ? "done" : "open" })
      });
    } catch (error) {
      state.tasks[index].completed = previous;
      writeLocal(STORAGE.tasks, state.tasks);
      renderTasks();
      toast(`Task update failed: ${error.message}`, "error");
    }
  }

  async function searchMemory(event) {
    event.preventDefault();
    const query = elements.memorySearch.value.trim();
    if (!query) return;
    elements.memoryResults.replaceChildren(createEmpty("Searching semantic memory…"));
    try {
      const payload = await fetchJson("/memory/search", {
        method: "POST",
        body: JSON.stringify({ query, limit: 20 })
      }, 15000);
      renderMemoryResults(asArray(payload, "results"), query, false);
      addAudit(`Memory searched: ${query}`, "info");
    } catch {
      const localResults = searchLocalMemory(query);
      renderMemoryResults(localResults, query, true);
      toast("Core offline — searched local browser memory", "info");
    }
  }

  function searchLocalMemory(query) {
    const terms = tokenize(query);
    return state.memories
      .map((memory) => {
        const haystack = tokenize(`${memory.title} ${memory.content} ${(memory.tags || []).join(" ")}`);
        const matches = terms.filter((term) => haystack.includes(term)).length;
        const score = terms.length ? matches / terms.length : 0;
        return { ...memory, score };
      })
      .filter((memory) => memory.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 20);
  }

  function tokenize(text) {
    return [...new Set(String(text).toLowerCase().split(/[^a-z0-9]+/).filter((term) => term.length > 1))];
  }

  function renderMemoryResults(results, query, local) {
    elements.memoryResults.replaceChildren();
    if (!results.length) {
      elements.memoryResults.append(createEmpty(`No ${local ? "local " : ""}memories matched “${query}”.`));
      return;
    }
    results.forEach((memory) => {
      const card = document.createElement("article");
      card.className = "memory-card";
      const title = document.createElement("h3");
      title.textContent = memory.title || memory.metadata?.title || memory.name || "Memory";
      const content = document.createElement("p");
      content.textContent = memory.content || memory.text || memory.snippet || "No preview available.";
      card.append(title, content);
      const tags = Array.isArray(memory.tags)
        ? memory.tags
        : Array.isArray(memory.metadata?.tags) ? memory.metadata.tags : [];
      if (tags.length) {
        const tagList = document.createElement("div");
        tagList.className = "tag-list";
        tags.slice(0, 6).forEach((value) => {
          const tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = String(value);
          tagList.append(tag);
        });
        card.append(tagList);
      }
      const meta = document.createElement("div");
      meta.className = "memory-meta";
      const source = memory.source || (local ? "LOCAL INDEX" : "JARVIS INDEX");
      const score = memory.score != null ? `${Math.round(Number(memory.score) * (Number(memory.score) <= 1 ? 100 : 1))}% MATCH` : "SEMANTIC MATCH";
      meta.append(document.createTextNode(String(source).toUpperCase()), document.createTextNode(score));
      card.append(meta);
      elements.memoryResults.append(card);
    });
  }

  async function ingestMemory(event) {
    event.preventDefault();
    const form = new FormData(elements.memoryIngestForm);
    const memory = {
      id: uid("memory"),
      title: String(form.get("title") || "").trim(),
      content: String(form.get("content") || "").trim(),
      tags: String(form.get("tags") || "").split(",").map((tag) => tag.trim()).filter(Boolean),
      created_at: new Date().toISOString(),
      local: true
    };
    if (!memory.title || !memory.content) return;
    const submit = $("button[type=submit]", elements.memoryIngestForm);
    setBusy(submit, true, "Indexing…");
    try {
      const payload = await fetchJson("/memory", {
        method: "POST",
        body: JSON.stringify({
          content: memory.content,
          source: "hud",
          metadata: { title: memory.title, tags: memory.tags }
        })
      }, 20000);
      const remote = payload?.item || payload || memory;
      state.memories.unshift({ ...memory, ...remote, local: false });
      toast("Memory indexed by JARVIS", "success");
      addAudit(`Memory indexed: ${memory.title}`, "info");
    } catch {
      state.memories.unshift(memory);
      toast("Core offline — memory saved to this browser", "info");
      addAudit(`Memory saved locally: ${memory.title}`, "warning");
    } finally {
      state.memories = state.memories.slice(0, 300);
      writeLocal(STORAGE.memory, state.memories);
      elements.memoryIngestForm.reset();
      $("details", elements.memoryPane).open = false;
      setBusy(submit, false);
    }
  }

  async function loadApprovals() {
    try {
      const payload = await fetchJson("/approvals", { cache: "no-store" });
      state.approvals = asArray(payload, "approvals").filter((item) => !["approved", "denied", "resolved"].includes(String(item.status).toLowerCase()));
    } catch {
      state.approvals = [];
    }
    renderApprovals();
  }

  async function loadNodes() {
    try {
      const payload = await fetchJson("/nodes", { cache: "no-store" });
      renderNodes(asArray(payload, "nodes"));
    } catch {
      renderNodes([]);
    }
  }

  async function loadActionCatalog({ quiet = true } = {}) {
    try {
      const payload = await fetchJson("/actions/catalog", { cache: "no-store" }, 10000);
      const rawActions = Array.isArray(payload?.catalog) ? payload.catalog : asArray(payload, "actions");
      state.actionCatalog = rawActions
        .filter((action) => action && typeof action === "object")
        .map((action) => ({
          id: String(action.id || action.action || action.name || ""),
          name: String(action.name || action.title || action.id || "Guarded action"),
          description: String(action.description || action.detail || "Approval-gated action"),
          risk: String(action.risk || action.risk_level || "guarded").toLowerCase(),
          enabled: action.enabled !== false && action.available !== false && action.status !== "disabled",
          configured: action.configured !== false,
          requiresApproval: action.requires_approval !== false,
          status: String(action.status || (action.enabled === false ? "disabled" : "ready"))
        }))
        .filter((action) => action.id);
      state.actionCatalogLoaded = true;
      renderActionCatalog();
      renderIntegrations(state.remoteSettings?.integrations || []);
      updateProjectSummaries();
      return true;
    } catch (error) {
      state.actionCatalog = [];
      state.actionCatalogLoaded = false;
      renderActionCatalog();
      renderIntegrations(state.remoteSettings?.integrations || []);
      updateProjectSummaries();
      if (!quiet) toast(`Action catalog unavailable: ${error.message}`, "error");
      return false;
    }
  }

  function renderActionCatalog() {
    const previous = elements.actionSelect.value;
    elements.actionSelect.replaceChildren();
    const placeholder = new Option(
      state.actionCatalogLoaded ? "Select a guarded operation" : "Action catalog unavailable",
      ""
    );
    elements.actionSelect.add(placeholder);
    let firstAvailable = "";
    ACTION_DEFINITIONS.forEach((definition) => {
      const remote = findCatalogAction(definition.id);
      const option = new Option(`${definition.name}${remote?.enabled ? "" : " — unavailable"}`, definition.id);
      option.disabled = !remote?.enabled;
      elements.actionSelect.add(option);
      if (!firstAvailable && remote?.enabled) firstAvailable = definition.id;
    });
    const selected = findCatalogAction(previous)?.enabled ? previous : firstAvailable;
    elements.actionSelect.value = selected;
    elements.actionSelect.disabled = !firstAvailable;
    renderActionFields(selected);
    renderActionRisk(selected);
  }

  function findCatalogAction(id) {
    return state.actionCatalog.find((action) => action.id === id);
  }

  function renderActionFields(actionId) {
    elements.actionFields.replaceChildren();
    if (!actionId) {
      elements.actionFields.append(createEmpty("No guarded operation is currently available."));
      return;
    }
    if (actionId === "list_windows" || actionId === "read_clipboard") {
      const note = document.createElement("p");
      note.className = "empty-state action-field-note";
      note.textContent = actionId === "read_clipboard"
        ? "No parameters. Approval permits one bounded read; no clipboard history is stored."
        : "No parameters. The target returns only its currently visible window inventory.";
      elements.actionFields.append(note);
      return;
    }
    if (actionId === "click_control") {
      const grid = document.createElement("div");
      grid.className = "action-field-grid";
      grid.append(
        createActionField({ id: "actionWindowTitle", label: "Exact window title", name: "window_title", required: true, maxLength: 300, placeholder: "Settings" }),
        createActionField({ id: "actionControlName", label: "Exact control name", name: "control_name", required: true, maxLength: 300, placeholder: "Save" }),
        createActionField({ id: "actionAutomationId", label: "Automation ID", name: "automation_id", maxLength: 300, placeholder: "Optional exact identifier" }),
        createActionField({ id: "actionControlType", label: "Control type", name: "control_type", maxLength: 120, placeholder: "Optional, e.g. Button" })
      );
      elements.actionFields.append(grid);
      return;
    }
    if (actionId === "run_command") {
      const grid = document.createElement("div");
      grid.className = "action-field-grid";
      grid.append(
        createActionField({ id: "actionExecutable", label: "Executable", name: "executable", required: true, maxLength: 100, placeholder: "python", wide: true, help: "An allowlisted program name only — never a path or shell text." }),
        createActionField({ id: "actionArguments", label: "Arguments", name: "arguments", type: "textarea", maxLength: 12000, placeholder: "--mode\nsafe\n--verbose", help: "One literal argument per line. Lines are sent as an argument array." }),
        createActionField({ id: "actionCwd", label: "Working directory", name: "cwd", required: true, maxLength: 1000, placeholder: "D:\\Workspace", help: "Explicit directory used for this one process." })
      );
      elements.actionFields.append(grid);
      return;
    }
  }

  function createActionField({ id, label, name, type = "input", required = false, maxLength, placeholder = "", help = "", wide = false }) {
    const field = document.createElement("div");
    field.className = `action-field ${wide ? "action-field--wide" : ""}`;
    const fieldLabel = document.createElement("label");
    fieldLabel.htmlFor = id;
    fieldLabel.textContent = label;
    const input = document.createElement(type === "textarea" ? "textarea" : "input");
    input.id = id;
    input.name = name;
    input.required = required;
    input.maxLength = maxLength || 1000;
    input.placeholder = placeholder;
    input.autocomplete = "off";
    input.spellcheck = false;
    if (type === "textarea") input.rows = 4;
    field.append(fieldLabel, input);
    if (help) {
      const description = document.createElement("small");
      description.textContent = help;
      field.append(description);
    }
    return field;
  }

  function renderActionRisk(actionId) {
    const definition = ACTION_DEFINITIONS.find((action) => action.id === actionId);
    const remote = findCatalogAction(actionId);
    elements.actionRiskPreview.replaceChildren();
    const risk = remote?.risk || definition?.risk || "guarded";
    const badge = document.createElement("span");
    badge.className = `integration-status status-${risk === "low" ? "ready" : risk === "high" || risk === "critical" ? "error" : remote ? "warning" : "disabled"}`;
    badge.textContent = remote ? `${risk.toUpperCase()} RISK` : "UNAVAILABLE";
    const description = document.createElement("p");
    description.textContent = remote
      ? `${remote.description || definition.description} ${remote.requiresApproval ? "Queueing creates a reviewable request; it does not execute the operation." : "This read-only request runs immediately and is recorded in the audit log."}`
      : "Select an available operation to review its exact effect and approval boundary.";
    elements.actionRiskPreview.append(badge, description);
    elements.queueAction.disabled = !remote?.enabled;
    elements.queueAction.textContent = remote?.requiresApproval ? "Queue for approval" : "Run read-only action";
  }

  function collectActionArguments(actionId) {
    if (actionId === "list_windows" || actionId === "read_clipboard") return {};
    if (actionId === "click_control") {
      const argumentsValue = {
        window_title: $("#actionWindowTitle", elements.actionFields).value.trim(),
        control_name: $("#actionControlName", elements.actionFields).value.trim()
      };
      const automationId = $("#actionAutomationId", elements.actionFields).value.trim();
      const controlType = $("#actionControlType", elements.actionFields).value.trim();
      if (automationId) argumentsValue.automation_id = automationId;
      if (controlType) argumentsValue.control_type = controlType;
      return argumentsValue;
    }
    if (actionId === "run_command") {
      const executable = $("#actionExecutable", elements.actionFields).value.trim();
      const cwd = $("#actionCwd", elements.actionFields).value.trim();
      if (/\r|\n/.test(executable)) throw new Error("Executable must be one program path, not shell text");
      return {
        executable,
        arguments: $("#actionArguments", elements.actionFields).value.split(/\r?\n/).map((argument) => argument.trim()).filter(Boolean),
        cwd
      };
    }
    throw new Error("Unsupported guarded action");
  }

  async function queueGuardedAction(event) {
    event.preventDefault();
    const action = elements.actionSelect.value;
    const catalogAction = findCatalogAction(action);
    if (!catalogAction?.enabled) {
      toast("Select an available guarded action", "error");
      return;
    }
    if (!elements.actionForm.reportValidity()) return;
    let argumentsValue;
    try {
      argumentsValue = collectActionArguments(action);
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    setBusy(elements.queueAction, true, "Queueing…");
    try {
      const payload = await fetchJson("/actions/requests", {
        method: "POST",
        body: JSON.stringify({ action, arguments: argumentsValue })
      }, 20000);
      renderActionResult(payload, action);
      await Promise.allSettled([loadApprovals(), loadAudit()]);
      addAudit(`Guarded action queued: ${action}`, "warning", "APPROVAL REQUIRED");
    } catch (error) {
      renderActionFailure(error.message);
      toast(`Action was not queued: ${error.message}`, "error");
    } finally {
      setBusy(elements.queueAction, false);
    }
  }

  function renderActionResult(payload, action) {
    elements.actionResult.replaceChildren();
    const approval = payload?.approval || payload?.request?.approval || null;
    const request = payload?.request || null;
    const status = String(payload?.status || request?.status || approval?.status || "queued").toLowerCase();
    const pending = Boolean(approval) || ["pending", "queued", "approval_required", "awaiting_approval"].includes(status);
    const card = document.createElement("article");
    card.className = `action-result-card ${pending ? "pending" : status === "failed" || status === "error" ? "error" : "success"}`;
    const title = document.createElement("h4");
    title.textContent = pending ? "Approval request queued" : status === "failed" || status === "error" ? "Action request failed" : "Action result received";
    const detail = document.createElement("p");
    const result = payload?.result ?? payload?.outcome ?? payload?.message ?? request?.message;
    detail.textContent = pending
      ? "The operation has not run. Review it in the Approvals tab on the target device."
      : safeActionResultText(result);
    const meta = document.createElement("div");
    meta.className = "action-result-meta";
    [
      ["ACTION", action],
      ["STATUS", status],
      ["REQUEST", request?.id || payload?.request_id],
      ["APPROVAL", approval?.id || approval?.request_id]
    ].filter(([, value]) => value).forEach(([label, value]) => {
      const item = document.createElement("span");
      item.textContent = `${label}: ${value}`;
      meta.append(item);
    });
    card.append(title, detail, meta);
    elements.actionResult.append(card);
    toast(pending ? "Action queued for approval" : "Action response received", pending ? "info" : "success");
  }

  function safeActionResultText(result) {
    if (result == null) return "The backend returned no additional result details.";
    if (typeof result === "string" || typeof result === "number" || typeof result === "boolean") return String(result).slice(0, 6000);
    try {
      return JSON.stringify(result, (key, value) => /secret|token|password|credential|authorization/i.test(key) ? "[redacted]" : value, 2).slice(0, 6000);
    } catch {
      return "The structured result could not be displayed.";
    }
  }

  function renderActionFailure(message) {
    elements.actionResult.replaceChildren();
    const card = document.createElement("article");
    card.className = "action-result-card error";
    const title = document.createElement("h4");
    title.textContent = "Request not queued";
    const detail = document.createElement("p");
    detail.textContent = message || "The guarded action request failed safely.";
    card.append(title, detail);
    elements.actionResult.append(card);
  }

  function renderApprovals() {
    elements.approvalList.replaceChildren();
    elements.approvalCount.textContent = String(state.approvals.length);
    updateProjectSummaries();
    if (!state.approvals.length) {
      elements.approvalList.append(createEmpty(state.online ? "No actions are awaiting approval." : "Approval queue unavailable while the core is offline."));
      return;
    }
    state.approvals.forEach((approval) => {
      const id = String(approval.id || approval.request_id || "");
      const card = document.createElement("article");
      card.className = "approval-card";
      const title = document.createElement("h3");
      title.textContent = approval.title || approval.action || approval.tool || approval.tool_name || "External action";
      const description = document.createElement("p");
      description.textContent = approval.description || approval.reason || "JARVIS requests permission to perform this action.";
      card.append(title, description);
      const detail = approval.command || approval.parameters || approval.payload || approval.arguments;
      if (detail) {
        const code = document.createElement("code");
        code.textContent = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
        card.append(code);
      }
      const meta = document.createElement("div");
      meta.className = "approval-meta";
      meta.append(document.createTextNode(String(approval.risk || "review").toUpperCase()), document.createTextNode(formatDateTime(approval.created_at || Date.now())));
      const actions = document.createElement("div");
      actions.className = "approval-actions";
      const deny = document.createElement("button");
      deny.className = "deny-button";
      deny.type = "button";
      deny.textContent = "Deny";
      deny.addEventListener("click", () => resolveApproval(id, "deny", deny));
      const approve = document.createElement("button");
      approve.className = "approve-button";
      approve.type = "button";
      approve.textContent = "Approve";
      approve.addEventListener("click", () => resolveApproval(id, "approve", approve));
      actions.append(deny, approve);
      card.append(meta, actions);
      elements.approvalList.append(card);
    });
  }

  async function resolveApproval(id, decision, button) {
    if (!id) {
      toast("This approval is missing an identifier", "error");
      return;
    }
    setBusy(button, true, decision === "approve" ? "Approving…" : "Denying…");
    try {
      await fetchJson(`/approvals/${encodeURIComponent(id)}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ actor: "jarvis-hud" })
      }, 15000);
      const approval = state.approvals.find((item) => String(item.id || item.request_id) === id);
      state.approvals = state.approvals.filter((item) => String(item.id || item.request_id) !== id);
      renderApprovals();
      toast(`Action ${decision === "approve" ? "approved" : "denied"}`, decision === "approve" ? "success" : "info");
      addAudit(`${decision === "approve" ? "Approved" : "Denied"}: ${approval?.title || approval?.action || "external action"}`, decision === "approve" ? "warning" : "info");
    } catch (error) {
      setBusy(button, false);
      toast(`Approval failed safely: ${error.message}`, "error");
    }
  }

  async function loadAudit({ quiet = true } = {}) {
    try {
      const payload = await fetchJson("/audit?limit=30", { cache: "no-store" });
      const remote = asArray(payload, "entries");
      if (remote.length) {
        const local = state.audit.filter((entry) => entry.local);
        state.audit = [...remote.map(normalizeAudit), ...local].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)).slice(0, 60);
      }
    } catch (error) {
      if (!quiet) toast(`Audit refresh failed: ${error.message}`, "error");
    }
    renderAudit();
  }

  function normalizeAudit(entry) {
    return {
      id: entry.id || uid("audit"),
      action: String(entry.action || entry.event || entry.message || "System event"),
      details: String(entry.actor || entry.resource || entry.source || "JARVIS CORE"),
      severity: String(entry.severity || entry.level || "info").toLowerCase(),
      timestamp: entry.timestamp || entry.created_at || new Date().toISOString(),
      local: false
    };
  }

  function renderAudit() {
    elements.auditList.replaceChildren();
    const entries = [...state.audit].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)).slice(0, 30);
    if (!entries.length) {
      const empty = document.createElement("li");
      empty.className = "empty-state";
      empty.textContent = "No activity recorded yet.";
      elements.auditList.append(empty);
      return;
    }
    entries.forEach((entry) => {
      const item = document.createElement("li");
      const severity = ["error", "critical", "danger"].includes(entry.severity) ? "danger" : ["warning", "warn"].includes(entry.severity) ? "warning" : "";
      item.className = `audit-item ${severity}`;
      const action = document.createElement("strong");
      action.textContent = entry.action;
      const meta = document.createElement("span");
      meta.textContent = `${formatDateTime(entry.timestamp)} · ${entry.details || (entry.local ? "LOCAL HUD" : "JARVIS CORE")}`;
      item.append(action, meta);
      elements.auditList.append(item);
    });
  }

  function createEmpty(text) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = text;
    return empty;
  }

  async function loadProjects({ quiet = true } = {}) {
    try {
      const payload = await fetchJson("/projects?limit=100", { cache: "no-store" }, 10000);
      state.projects = asArray(payload, "projects").map(normalizeProject);
      state.projectsLoaded = true;
      state.projectCapabilities = { create: true, update: true };
      renderUserProjects();
      return true;
    } catch (error) {
      state.projects = [];
      state.projectsLoaded = false;
      state.projectCapabilities = { create: false, update: false };
      renderUserProjects();
      if (!quiet) toast(`Projects unavailable: ${error.message}`, "error");
      return false;
    }
  }

  function normalizeProject(project) {
    const statuses = ["planned", "active", "paused", "completed", "archived"];
    const status = statuses.includes(String(project.status).toLowerCase()) ? String(project.status).toLowerCase() : "planned";
    const tags = Array.isArray(project.tags) ? project.tags.map(String).filter(Boolean).slice(0, 20) : [];
    const accent = ["slate", "blue", "cyan", "green", "amber", "orange", "red", "violet", "pink"].includes(project.accent) ? project.accent : "cyan";
    return {
      id: String(project.id || uid("project")),
      title: String(project.title || "Untitled project"),
      summary: String(project.summary || ""),
      status,
      priority: clamp(project.priority || 2, 1, 5),
      progress: clamp(project.progress || 0),
      next_action: String(project.next_action || ""),
      tags,
      accent,
      pinned: Boolean(project.pinned),
      updated_at: project.updated_at || project.last_activity_at || project.created_at || new Date().toISOString(),
      allowed_transitions: Array.isArray(project.allowed_transitions)
        ? project.allowed_transitions.map(String).filter((value) => statuses.includes(value))
        : []
    };
  }

  function renderUserProjects() {
    elements.userProjectGrid.replaceChildren();
    elements.projectsCountBadge.textContent = `${state.projects.length} PROJECT${state.projects.length === 1 ? "" : "S"}`;
    elements.newProjectButton.disabled = !state.projectCapabilities.create;
    elements.newProjectButton.title = state.projectCapabilities.create
      ? "Create a project"
      : "Project creation requires the local projects service";
    elements.projectsSourceStatus.textContent = state.projectsLoaded
      ? "Real project metadata from the local JARVIS project service."
      : "Projects service is not available yet; safe workspace shortcuts remain below.";
    if (!state.projectsLoaded) {
      elements.userProjectGrid.append(createProjectEmpty("Projects service is offline", "No project data was invented. Refresh when the local service is ready."));
      updateProjectSummaries();
      return;
    }
    if (!state.projects.length) {
      elements.userProjectGrid.append(createProjectEmpty(
        "No projects yet",
        state.projectCapabilities.create ? "Use New project to create a metadata-only project workspace." : "Project creation is not exposed by the backend."
      ));
      updateProjectSummaries();
      return;
    }
    state.projects.forEach((project, index) => elements.userProjectGrid.append(createUserProjectCard(project, index)));
    updateProjectSummaries();
  }

  function createProjectEmpty(titleText, detailText) {
    const empty = document.createElement("div");
    empty.className = "project-empty-state";
    const title = document.createElement("strong");
    title.textContent = titleText;
    const detail = document.createElement("span");
    detail.textContent = detailText;
    empty.append(title, detail);
    return empty;
  }

  function createUserProjectCard(project, index) {
    const card = document.createElement("article");
    card.className = `project-card user-project-card project-accent-${project.accent}`;
    card.dataset.projectId = project.id;
    const detailId = `userProjectDetail-${index}`;
    const trigger = document.createElement("button");
    trigger.className = "project-trigger";
    trigger.type = "button";
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", detailId);
    const icon = document.createElement("span");
    icon.className = "project-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = project.title.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase() || "PJ";
    const title = document.createElement("span");
    title.className = "project-title";
    const strong = document.createElement("strong");
    strong.textContent = `${project.pinned ? "◆ " : ""}${project.title}`;
    const small = document.createElement("small");
    small.textContent = project.next_action ? `Next: ${project.next_action}` : `Updated ${relativeTime(project.updated_at)}`;
    title.append(strong, small);
    const status = document.createElement("span");
    status.className = `integration-status status-${projectStatusTone(project.status)}`;
    status.textContent = project.status.toUpperCase();
    const chevron = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chevron.classList.add("project-chevron");
    chevron.setAttribute("viewBox", "0 0 24 24");
    chevron.setAttribute("aria-hidden", "true");
    const chevronPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    chevronPath.setAttribute("d", "m8 10 4 4 4-4");
    chevron.append(chevronPath);
    const progress = document.createElement("span");
    progress.className = "project-progress";
    const track = document.createElement("span");
    track.className = "project-progress-track";
    const fill = document.createElement("i");
    fill.style.width = `${project.progress}%`;
    track.append(fill);
    const progressValue = document.createElement("span");
    progressValue.textContent = `${Math.round(project.progress)}%`;
    progress.append(track, progressValue);
    trigger.append(icon, title, status, chevron, progress);

    const detail = document.createElement("div");
    detail.id = detailId;
    detail.className = "project-detail";
    if (project.next_action) {
      const next = document.createElement("div");
      next.className = "project-next-step";
      const label = document.createElement("strong");
      label.textContent = "NEXT ACTION";
      const value = document.createElement("span");
      value.textContent = project.next_action;
      next.append(label, value);
      detail.append(next);
    }
    const notes = document.createElement("p");
    notes.className = "project-notes";
    notes.textContent = project.summary || "No summary or notes have been added.";
    detail.append(notes);
    if (project.tags.length) {
      const tagList = document.createElement("div");
      tagList.className = "project-tag-list";
      project.tags.forEach((tag) => {
        const item = document.createElement("span");
        item.textContent = tag;
        tagList.append(item);
      });
      detail.append(tagList);
    }
    const actions = document.createElement("div");
    actions.className = "project-actions";
    const detailsButton = document.createElement("button");
    detailsButton.type = "button";
    detailsButton.dataset.projectDetail = project.id;
    detailsButton.textContent = "Details";
    actions.append(detailsButton);
    if (state.projectCapabilities.update) {
      const edit = document.createElement("button");
      edit.type = "button";
      edit.dataset.projectEdit = project.id;
      edit.textContent = "Edit metadata";
      actions.append(edit);
    }
    detail.append(actions);
    card.append(trigger, detail);
    return card;
  }

  function projectStatusTone(status) {
    if (status === "active" || status === "completed") return "ready";
    if (status === "paused" || status === "planned") return "warning";
    return "disabled";
  }

  function setupProjects() {
    elements.projectsPanel.addEventListener("click", (event) => {
      const trigger = event.target.closest(".project-trigger");
      if (trigger) {
        toggleProjectCard(trigger.closest(".project-card"));
        return;
      }
      const detailButton = event.target.closest("[data-project-detail]");
      if (detailButton) {
        toggleProjectCard(detailButton.closest(".project-card"), true);
        return;
      }
      const editButton = event.target.closest("[data-project-edit]");
      if (editButton) {
        openProjectEditor(state.projects.find((project) => project.id === editButton.dataset.projectEdit));
        return;
      }
      const navigation = event.target.closest("[data-project-action]");
      if (navigation) navigateProjectShortcut(navigation.dataset.projectAction);
    });
    elements.refreshProjects.addEventListener("click", async () => {
      elements.refreshProjects.disabled = true;
      await loadProjects({ quiet: false });
      elements.refreshProjects.disabled = false;
    });
    elements.newProjectButton.addEventListener("click", () => openProjectEditor(null));
    elements.closeProjectDialog.addEventListener("click", closeProjectEditor);
    elements.cancelProjectEdit.addEventListener("click", closeProjectEditor);
    elements.projectForm.addEventListener("submit", saveProjectMetadata);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !elements.projectDialog.open) collapseProjectCards();
    });
  }

  function setupAppMenu() {
    const menu = document.getElementById("appMenu");
    if (!menu) return;
    menu.addEventListener("click", (event) => {
      const button = event.target.closest("[data-app-menu-action]");
      if (!button) return;
      const action = button.dataset.appMenuAction;
      menu.open = false;
      if (action === "capabilities") return openCommandPalette();
      if (action === "background-globe") return toggleBackgroundGlobe();
      if (action === "command") return navigateProjectShortcut("command");
      if (action === "actions" || action === "approvals") {
        return navigateProjectShortcut(action);
      }
      if (action === "projects") {
        elements.projectsPanel.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
        return requestAnimationFrame(() => elements.newProjectButton.focus({ preventScroll: true }));
      }
      if (action === "communications") {
        elements.openSettings.click();
        return requestAnimationFrame(() => {
          const target = document.getElementById("communicationsSettings");
          target?.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
          elements.refreshCommunications.focus({ preventScroll: true });
        });
      }
      if (action === "health") {
        elements.openSettings.click();
        return requestAnimationFrame(() => {
          const target = document.getElementById("systemHealthSettings");
          target?.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
          elements.refreshSystemHealth.focus({ preventScroll: true });
        });
      }
      if (action === "globe") return enterGlobeMode(true);
      if (action === "settings") return elements.openSettings.click();
    });
    document.addEventListener("pointerdown", (event) => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) menu.open = false;
    });
  }

  function openSettingsSection(sectionId) {
    elements.openSettings.click();
    requestAnimationFrame(() => {
      const section = document.getElementById(sectionId);
      section?.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
      const heading = $("h3", section);
      if (heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      }
    });
  }

  function capabilityEntries() {
    const entries = [
      { label: "Command JARVIS", detail: "Focus voice or text command input", key: "Ctrl+/", keywords: "chat ask speak", run: () => navigateProjectShortcut("command") },
      { label: "Projects", detail: "Open persistent project workspaces", keywords: "workspace planning", run: () => elements.projectsPanel.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" }) },
      { label: "Tasks & Reminders", detail: "Create tasks with optional due-time alerts", keywords: "timer due deadline", run: () => navigateProjectShortcut("tasks") },
      { label: "Memory", detail: "Search or add private semantic memory", keywords: "remember knowledge", run: () => navigateProjectShortcut("memory") },
      { label: "Spatial Files", detail: "Search allowed roots and zoom result cards", keywords: "find folder gesture pinch", run: () => openContextPanel("files", { force: true }) },
      { label: "System Health", detail: "CPU, memory, disk, temperature, and warnings", keywords: "telemetry data performance", run: () => openContextPanel("health", { force: true }) },
      { label: "Angerona Defense", detail: "Open defensive status and target-locked controls", keywords: "security tactics protection", run: () => openContextPanel("angerona", { force: true }) },
      { label: "Guarded Actions", detail: "Prepare clipboard, window, control, or program actions", keywords: "automation clipboard click run", run: () => navigateProjectShortcut("actions") },
      { label: "Approvals", detail: "Review pending local effects", keywords: "allow deny safety", run: () => navigateProjectShortcut("approvals") },
      { label: "Privacy Mode", detail: "Keep stored memory out of chat context", keywords: "private redaction secrets", run: () => openSettingsSection("interfaceSettings") },
      { label: "Phone Voice", detail: "Review authenticated voice-call gateway status", keywords: "call twilio mobile", run: () => openSettingsSection("phoneSettings") },
      { label: "Communications", detail: "Review private notification routes", keywords: "telegram discord signal", run: () => openSettingsSection("communicationsSettings") },
      { label: "All Settings", detail: "Open connection, interface, devices, and safety", keywords: "preferences configuration", run: () => elements.openSettings.click() }
    ];
    state.skills.forEach((skill) => entries.push({
      label: skill.name,
      detail: `Use ${skill.name} for the next command`,
      keywords: `skill ${skill.description || ""}`,
      run: () => {
        if ([...elements.chatSkill.options].some((option) => option.value === skill.id)) elements.chatSkill.value = skill.id;
        navigateProjectShortcut("command");
      }
    }));
    state.actionCatalog.filter((action) => action.enabled).forEach((action) => entries.push({
      label: action.name.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
      detail: action.description,
      keywords: `action ${action.id} ${action.risk}`,
      run: () => {
        navigateProjectShortcut("actions");
        elements.actionSelect.value = action.id;
        elements.actionSelect.dispatchEvent(new Event("change"));
      }
    }));
    return entries;
  }

  function renderCommandPalette() {
    const query = elements.commandPaletteInput.value.trim().toLowerCase();
    state.paletteEntries = capabilityEntries().filter((entry) =>
      `${entry.label} ${entry.detail} ${entry.key || ""} ${entry.keywords || ""}`.toLowerCase().includes(query)
    ).slice(0, 18);
    state.paletteSelection = 0;
    elements.commandPaletteResults.replaceChildren();
    if (!state.paletteEntries.length) {
      const empty = document.createElement("p");
      empty.className = "command-palette-empty";
      empty.textContent = "No matching JARVIS capability.";
      elements.commandPaletteResults.append(empty);
      return;
    }
    state.paletteEntries.forEach((entry, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `command-palette-result ${index === 0 ? "active" : ""}`;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(index === 0));
      button.dataset.paletteIndex = String(index);
      const label = document.createElement("strong");
      label.textContent = entry.label;
      const detail = document.createElement("small");
      detail.textContent = entry.detail;
      button.append(label, detail);
      if (entry.key) {
        const key = document.createElement("kbd");
        key.textContent = entry.key;
        button.append(key);
      }
      elements.commandPaletteResults.append(button);
    });
  }

  function selectPaletteEntry(index) {
    if (!state.paletteEntries.length) return;
    state.paletteSelection = (index + state.paletteEntries.length) % state.paletteEntries.length;
    $$(".command-palette-result", elements.commandPaletteResults).forEach((button, buttonIndex) => {
      const active = buttonIndex === state.paletteSelection;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      if (active) button.scrollIntoView({ block: "nearest" });
    });
  }

  function openCommandPalette() {
    const blockingDialog = $$('dialog[open]').find((dialog) => dialog !== elements.commandPaletteDialog);
    if (blockingDialog) {
      toast("Close the current dialog before opening capability search", "info");
      return;
    }
    elements.commandPaletteInput.value = "";
    renderCommandPalette();
    if (!elements.commandPaletteDialog.open) elements.commandPaletteDialog.showModal();
    requestAnimationFrame(() => elements.commandPaletteInput.focus());
  }

  function setupCommandPalette() {
    elements.openCommandPalette.addEventListener("click", openCommandPalette);
    elements.closeCommandPalette.addEventListener("click", () => elements.commandPaletteDialog.close());
    elements.commandPaletteInput.addEventListener("input", renderCommandPalette);
    elements.commandPaletteResults.addEventListener("click", (event) => {
      const button = event.target.closest("[data-palette-index]");
      if (!button) return;
      const entry = state.paletteEntries[Number(button.dataset.paletteIndex)];
      elements.commandPaletteDialog.close();
      entry?.run();
    });
    elements.commandPaletteInput.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        selectPaletteEntry(state.paletteSelection + (event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Enter") {
        event.preventDefault();
        $$(".command-palette-result", elements.commandPaletteResults)[state.paletteSelection]?.click();
      }
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (elements.commandPaletteDialog.open) elements.commandPaletteDialog.close();
        else openCommandPalette();
      }
    });
  }

  function toggleProjectCard(card, forceOpen = false) {
    if (!card) return;
    const open = forceOpen || !card.classList.contains("expanded");
    $$(".project-card", elements.projectsPanel).forEach((item) => {
      const selected = item === card && open;
      item.classList.toggle("expanded", selected);
      $(".project-trigger", item)?.setAttribute("aria-expanded", String(selected));
    });
  }

  function collapseProjectCards() {
    $$(".project-card.expanded", elements.projectsPanel).forEach((card) => {
      card.classList.remove("expanded");
      $(".project-trigger", card)?.setAttribute("aria-expanded", "false");
    });
  }

  function navigateProjectShortcut(action) {
    const mobile = matchMedia("(max-width: 780px)").matches;
    if (action === "command") {
      if (mobile) $("[data-mobile-target=commandPanel]")?.click();
      elements.commandPanel.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
      elements.chatInput.focus({ preventScroll: true });
      return;
    }
    const tabByAction = { tasks: "tasksTab", memory: "memoryTab", actions: "actionsTab", approvals: "approvalTab" };
    if (tabByAction[action]) {
      if (mobile) $("[data-mobile-target=operationsPanel]")?.click();
      const tab = document.getElementById(tabByAction[action]);
      activateTab(tab);
      elements.operationsPanel.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
      const focusTargets = { tasks: elements.taskTitle, memory: elements.memorySearch, actions: elements.actionSelect, approvals: elements.approvalList };
      requestAnimationFrame(() => focusTargets[action]?.focus({ preventScroll: true }));
      return;
    }
    if (action === "federation" || action === "defense") {
      elements.openSettings.click();
      requestAnimationFrame(() => {
        const target = document.getElementById(action === "federation" ? "federationSettings" : "defenseSettings");
        target?.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
        (action === "federation" ? elements.federationEnabledToggle : elements.refreshDefense).focus({ preventScroll: true });
      });
    }
  }

  function openProjectEditor(project) {
    if (project ? !state.projectCapabilities.update : !state.projectCapabilities.create) {
      toast("Project editing is not exposed by the local backend", "error");
      return;
    }
    state.projectEditingId = project?.id || null;
    elements.projectDialogTitle.textContent = project ? "Edit Project" : "New Project";
    elements.projectTitleInput.value = project?.title || "";
    configureProjectStatusOptions(project);
    elements.projectProgressInput.value = String(project?.progress || 0);
    elements.projectPriorityInput.value = String(project?.priority || 2);
    elements.projectAccentInput.value = project?.accent || "cyan";
    elements.projectNextStepInput.value = project?.next_action || "";
    elements.projectNotesInput.value = project?.summary || "";
    elements.projectTagsInput.value = (project?.tags || []).join(", ");
    elements.projectPinnedInput.checked = Boolean(project?.pinned);
    if (typeof elements.projectDialog.showModal === "function") elements.projectDialog.showModal();
    else elements.projectDialog.setAttribute("open", "");
    elements.projectTitleInput.focus();
  }

  function configureProjectStatusOptions(project) {
    const all = ["planned", "active", "paused", "completed", "archived"];
    const allowed = project ? [...new Set([project.status, ...project.allowed_transitions])] : all;
    elements.projectStatusInput.replaceChildren();
    allowed.forEach((status) => elements.projectStatusInput.add(new Option(formatPermission(status), status)));
    elements.projectStatusInput.value = project?.status || "planned";
  }

  function closeProjectEditor() {
    if (typeof elements.projectDialog.close === "function") elements.projectDialog.close();
    else elements.projectDialog.removeAttribute("open");
    state.projectEditingId = null;
  }

  async function saveProjectMetadata(event) {
    event.preventDefault();
    if (!elements.projectForm.reportValidity()) return;
    const tags = elements.projectTagsInput.value.split(",").map((tag) => tag.trim()).filter(Boolean);
    if (tags.length > 20 || tags.some((tag) => tag.length > 40)) {
      toast("Use at most 20 tags, each 40 characters or fewer", "error");
      elements.projectTagsInput.focus();
      return;
    }
    const body = {
      title: elements.projectTitleInput.value.trim(),
      summary: elements.projectNotesInput.value.trim(),
      status: elements.projectStatusInput.value,
      priority: clamp(elements.projectPriorityInput.value, 1, 5),
      progress: clamp(elements.projectProgressInput.value),
      next_action: elements.projectNextStepInput.value.trim(),
      tags,
      accent: elements.projectAccentInput.value,
      pinned: elements.projectPinnedInput.checked
    };
    const editing = state.projectEditingId;
    setBusy(elements.saveProject, true, "Saving…");
    try {
      await fetchJson(editing ? `/projects/${encodeURIComponent(editing)}` : "/projects", {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify(body)
      }, 15000);
      closeProjectEditor();
      await loadProjects({ quiet: true });
      toast(editing ? "Project updated" : "Project created", "success");
      addAudit(`${editing ? "Project updated" : "Project created"}: ${body.title}`, "info");
    } catch (error) {
      toast(`Project was not saved: ${error.message}`, "error");
    } finally {
      setBusy(elements.saveProject, false);
    }
  }

  function setupSettingsNavigation() {
    $$("[data-settings-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const section = document.getElementById(button.dataset.settingsTarget);
        if (!section) return;
        section.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
        const heading = $("h3", section);
        if (heading) {
          heading.tabIndex = -1;
          heading.focus({ preventScroll: true });
        }
      });
    });
  }

  function normalizeCommunicationDetail(value, fallback = "Status detail is unavailable.") {
    const detail = String(value || "").trim().slice(0, 260);
    if (!detail) return fallback;
    const resemblesSecret = /\/api\/webhooks\/|\b[0-9]{7,12}:[A-Za-z0-9_-]{20,}|(?:token|secret|credential|key)\s*[=:]\s*\S{8,}/i;
    return resemblesSecret.test(detail) ? "Sensitive provider detail was hidden by the HUD." : detail;
  }

  function maskCommunicationIdentifier(value) {
    const text = String(value || "").trim().slice(0, 80);
    if (!text) return "";
    if (/[•*]/.test(text)) return text;
    if (text.length <= 4) return "••••";
    return `${text.slice(0, 2)}${"•".repeat(Math.min(8, Math.max(3, text.length - 6)))}${text.slice(-4)}`;
  }

  function normalizeCommunicationRoute(raw, provider) {
    const meta = COMMUNICATION_PROVIDER_META[provider];
    const source = raw && typeof raw === "object" ? raw : {};
    const config = source.configuration && typeof source.configuration === "object" ? source.configuration : {};
    const configuration = provider === "signal" ? {
      account_configured: Boolean(config.account_configured),
      account_masked: maskCommunicationIdentifier(config.account_masked),
      recipient_configured: Boolean(config.recipient_configured),
      recipient_masked: maskCommunicationIdentifier(config.recipient_masked)
    } : provider === "telegram" ? {
      chat_id_configured: Boolean(config.chat_id_configured),
      chat_id_masked: maskCommunicationIdentifier(config.chat_id_masked)
    } : provider === "discord" ? {
      mode: config.mode === "bot" ? "bot" : "webhook",
      channel_id_configured: Boolean(config.channel_id_configured),
      channel_id_masked: maskCommunicationIdentifier(config.channel_id_masked)
    } : {
      base_url_configured: Boolean(config.base_url),
      network_scope: ["loopback", "private_lan", "tailscale"].includes(config.network_scope) ? config.network_scope : "private",
      auth_enabled: Boolean(config.auth_enabled)
    };
    const statuses = ["disabled", "setup_required", "ready", "unavailable", "unverified", "blocked", "error"];
    const status = statuses.includes(String(source.status).toLowerCase()) ? String(source.status).toLowerCase() : "unavailable";
    const prerequisites = Array.isArray(source.prerequisites) ? source.prerequisites
      .filter((item) => item && typeof item === "object")
      .slice(0, 12)
      .map((item) => ({
        id: String(item.id || "").slice(0, 100),
        label: String(item.label || "Provider prerequisite").slice(0, 140),
        ready: item.ready === true,
        configured: item.configured === true,
        source: String(item.source || "").slice(0, 40),
        environment_reference: /^[A-Z][A-Z0-9_]{2,80}$/.test(String(item.environment_reference || ""))
          ? String(item.environment_reference)
          : ""
      })) : [];
    const last = source.last_test && typeof source.last_test === "object" ? source.last_test : null;
    return {
      provider,
      label: meta.label,
      enabled: source.enabled === true,
      channel_kind: provider === "web_app" ? "interactive_access" : "outbound_delivery",
      delivery_supported: provider !== "web_app",
      priority: provider === "web_app" ? null : clamp(source.priority || DELIVERY_PROVIDERS.indexOf(provider) + 1, 1, 3),
      configuration,
      status,
      status_detail: normalizeCommunicationDetail(source.status_detail),
      prerequisites,
      last_test: last ? {
        tested_at: last.tested_at || null,
        status: statuses.includes(String(last.status).toLowerCase()) ? String(last.status).toLowerCase() : "unverified",
        detail: normalizeCommunicationDetail(last.detail, "Read-only verification completed."),
        credential_verified: typeof last.credential_verified === "boolean" ? last.credential_verified : null
      } : null,
      test_kind: String(source.test_kind || "read_only").slice(0, 60)
    };
  }

  function normalizeCommunicationsPayload(payload) {
    const routes = Array.isArray(payload?.routes) ? payload.routes : [];
    const normalizedRoutes = COMMUNICATION_PROVIDERS.map((provider) => {
      const route = routes.find((item) => item?.provider === provider);
      return normalizeCommunicationRoute(route, provider);
    });
    const requestedOrder = Array.isArray(payload?.policy?.delivery_order)
      ? payload.policy.delivery_order.filter((provider) => DELIVERY_PROVIDERS.includes(provider))
      : [];
    const deliveryOrder = [...new Set([...requestedOrder, ...DELIVERY_PROVIDERS])].slice(0, 3);
    const refs = payload?.secrets?.environment_references && typeof payload.secrets.environment_references === "object"
      ? Object.fromEntries(Object.entries(payload.secrets.environment_references)
        .filter(([, value]) => /^[A-Z][A-Z0-9_]{2,80}$/.test(String(value)))
        .map(([key, value]) => [String(key), String(value)]))
      : {};
    return {
      routes: normalizedRoutes,
      policy: {
        failover_enabled: payload?.policy?.failover_enabled === true,
        delivery_order: deliveryOrder,
        semantics: normalizeCommunicationDetail(
          payload?.policy?.semantics,
          "Priority preference only. No outbound send endpoint is exposed by this setup center."
        )
      },
      secrets: {
        accepted_via_api: payload?.secrets?.accepted_via_api === true,
        write_only_replace_supported: payload?.secrets?.write_only_replace_supported === true,
        plaintext_returned: false,
        storage: String(payload?.secrets?.storage || "environment_references_only").slice(0, 100),
        environment_references: refs
      },
      updatedAt: new Date()
    };
  }

  function communicationRoute(provider) {
    return state.communications?.routes.find((route) => route.provider === provider) || null;
  }

  function communicationStatusTone(status) {
    if (status === "ready") return "ready";
    if (["setup_required", "unverified", "blocked"].includes(status)) return "warning";
    if (["error", "unavailable"].includes(status)) return "error";
    return "disabled";
  }

  function communicationSetupConfigured(route) {
    if (!route) return false;
    const config = route.configuration;
    const identifierReady = route.provider === "signal"
      ? config.account_configured && config.recipient_configured
      : route.provider === "telegram"
        ? config.chat_id_configured
        : route.provider === "discord"
          ? config.mode === "webhook" || config.channel_id_configured
          : config.base_url_configured;
    const credentialPrerequisites = route.prerequisites.filter((item) => item.id.startsWith("credential:") || item.id.startsWith("env:"));
    return Boolean(identifierReady && credentialPrerequisites.every((item) => item.ready));
  }

  function communicationCredentialConfigured(route) {
    return route?.prerequisites.some((item) => (item.id.startsWith("credential:") || item.id.startsWith("env:")) && item.configured) || false;
  }

  function communicationTargetLabel(route) {
    const config = route.configuration;
    if (route.provider === "signal") return config.recipient_masked || "Not configured";
    if (route.provider === "telegram") return config.chat_id_masked || "Not configured";
    if (route.provider === "discord") {
      return config.mode === "webhook"
        ? communicationCredentialConfigured(route) ? "Protected webhook" : "Webhook not configured"
        : config.channel_id_masked || "Channel not configured";
    }
    return config.base_url_configured ? `${formatPermission(config.network_scope)} private origin` : "Private origin not configured";
  }

  function communicationTestDisclosure(route) {
    if (route?.provider === "signal") return "Checks local Signal prerequisites only; it does not invoke signal-cli or send a message.";
    if (["telegram", "discord"].includes(route?.provider)) return "Reads saved provider identity from a fixed vendor host; it never sends a visible message.";
    return "Validates local Web App policy only; it does not deploy, fetch, or prove the app is reachable.";
  }

  function addCommunicationMetric(parent, label, value) {
    const item = document.createElement("div");
    const key = document.createElement("span");
    key.textContent = label;
    const copy = document.createElement("strong");
    copy.textContent = value;
    copy.title = value;
    item.append(key, copy);
    parent.append(item);
  }

  function renderCommunicationProviderCard(route) {
    const meta = COMMUNICATION_PROVIDER_META[route.provider];
    const card = document.createElement("article");
    card.className = `communication-provider-card${route.delivery_supported ? "" : " communication-access-card"}`;
    card.dataset.provider = route.provider;

    const header = document.createElement("div");
    header.className = "communication-provider-header";
    const icon = document.createElement("span");
    icon.className = "communication-provider-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = meta.icon;
    const copy = document.createElement("span");
    copy.className = "communication-provider-copy";
    const name = document.createElement("strong");
    name.textContent = route.label;
    const kind = document.createElement("small");
    kind.textContent = route.delivery_supported
      ? `DELIVERY ROUTE · PRIORITY ${route.priority}`
      : "ACCESS CHANNEL · SETUP STATUS ONLY";
    copy.append(name, kind);
    const badge = document.createElement("span");
    setStatusBadge(badge, formatPermission(route.status).toUpperCase(), communicationStatusTone(route.status));
    header.append(icon, copy, badge);

    const detail = document.createElement("div");
    detail.className = "communication-provider-detail";
    addCommunicationMetric(detail, "CONFIGURED", communicationSetupConfigured(route) ? "YES" : "INCOMPLETE");
    addCommunicationMetric(detail, "TESTED", route.last_test ? `${formatPermission(route.last_test.status)} · ${relativeTime(route.last_test.tested_at)}` : "NEVER");
    addCommunicationMetric(detail, route.delivery_supported ? "MASKED TARGET" : "PRIVATE ACCESS", communicationTargetLabel(route));
    addCommunicationMetric(detail, "ENABLED", route.enabled ? "YES" : "NO");

    const prerequisite = document.createElement("p");
    prerequisite.className = "communication-provider-prerequisite";
    const unmet = route.prerequisites.find((item) => !item.ready);
    prerequisite.textContent = unmet ? `Needs: ${unmet.label}.` : route.status_detail;

    const actions = document.createElement("div");
    actions.className = "communication-provider-actions";
    const setup = document.createElement("button");
    setup.type = "button";
    setup.className = "secondary-button compact-button";
    setup.dataset.communicationSetup = route.provider;
    setup.textContent = "Setup";
    const test = document.createElement("button");
    test.type = "button";
    test.className = "text-button compact-button";
    test.dataset.communicationTest = route.provider;
    test.disabled = Boolean(state.communicationBusy[`${route.provider}:test`]);
    test.textContent = test.disabled ? "Testing…" : "Test saved setup";
    test.title = communicationTestDisclosure(route);
    const enableLabel = document.createElement("label");
    enableLabel.className = "provider-enable";
    const enableCopy = document.createElement("span");
    enableCopy.textContent = "ENABLED";
    const enable = document.createElement("input");
    enable.type = "checkbox";
    enable.setAttribute("role", "switch");
    enable.checked = route.enabled;
    enable.disabled = Boolean(state.communicationBusy[`${route.provider}:enable`]);
    enable.dataset.communicationEnable = route.provider;
    enable.setAttribute("aria-label", `Enable ${route.label}`);
    enableLabel.append(enableCopy, enable);
    actions.append(setup, test, enableLabel);
    card.append(header, detail, prerequisite, actions);
    return card;
  }

  function renderCommunicationOrder() {
    elements.communicationRouteList.replaceChildren();
    if (!state.communicationsLoaded || !state.communications) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Delivery priority is unavailable until the local communications service responds.";
      elements.communicationRouteList.append(empty);
      elements.saveCommunicationOrder.disabled = true;
      elements.communicationFailoverEnabled.disabled = true;
      return;
    }
    state.communicationOrderDraft.forEach((provider, index) => {
      const route = communicationRoute(provider);
      const meta = COMMUNICATION_PROVIDER_META[provider];
      const item = document.createElement("div");
      item.className = "communication-route-item";
      item.setAttribute("role", "listitem");
      const rank = document.createElement("span");
      rank.className = "communication-route-rank";
      rank.textContent = String(index + 1);
      const icon = document.createElement("span");
      icon.className = "communication-provider-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = meta.icon;
      const copy = document.createElement("span");
      copy.className = "communication-route-copy";
      const name = document.createElement("strong");
      name.textContent = meta.label;
      const status = document.createElement("small");
      status.textContent = `${route?.enabled ? "Enabled" : "Disabled"} · ${formatPermission(route?.status || "unavailable")} · delivery route`;
      copy.append(name, status);
      const controls = document.createElement("span");
      controls.className = "communication-route-controls";
      [
        { direction: -1, label: `Move ${meta.label} earlier`, text: "↑", disabled: index === 0 },
        { direction: 1, label: `Move ${meta.label} later`, text: "↓", disabled: index === state.communicationOrderDraft.length - 1 }
      ].forEach((control) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.communicationMove = provider;
        button.dataset.direction = String(control.direction);
        button.setAttribute("aria-label", control.label);
        button.title = control.label;
        button.textContent = control.text;
        button.disabled = control.disabled || Boolean(state.communicationBusy.order);
        controls.append(button);
      });
      item.append(rank, icon, copy, controls);
      elements.communicationRouteList.append(item);
    });
    elements.saveCommunicationOrder.disabled = !state.communicationOrderDirty || Boolean(state.communicationBusy.order);
    elements.communicationFailoverEnabled.disabled = Boolean(state.communicationBusy.failover);
    elements.communicationFailoverEnabled.checked = state.communications.policy.failover_enabled;
  }

  function renderCommunications() {
    if (!state.communicationsLoaded || !state.communications) {
      setStatusBadge(elements.communicationsStatusBadge, "UNAVAILABLE", "error");
      elements.communicationsOverview.textContent = "The local communications service is unavailable. No provider readiness or connectivity is being claimed.";
      elements.communicationProviderList.replaceChildren();
      const empty = document.createElement("p");
      empty.className = "empty-state communication-provider-offline";
      empty.textContent = "Provider setup is safely offline. Start or reconnect the local JARVIS core, then refresh routes.";
      elements.communicationProviderList.append(empty);
      elements.communicationProviderList.setAttribute("aria-busy", "false");
      elements.communicationsLastUpdated.textContent = "Communications status is unavailable.";
      renderCommunicationOrder();
      return;
    }
    const deliveryRoutes = state.communications.routes.filter((route) => route.delivery_supported);
    const readyCount = deliveryRoutes.filter((route) => route.status === "ready").length;
    const configuredCount = deliveryRoutes.filter(communicationSetupConfigured).length;
    const access = communicationRoute("web_app");
    const anyError = state.communications.routes.some((route) => ["error", "unavailable"].includes(route.status));
    setStatusBadge(
      elements.communicationsStatusBadge,
      readyCount ? `${readyCount} READY` : configuredCount ? "SETUP PENDING" : "CONFIGURE",
      readyCount ? "ready" : anyError ? "error" : "warning"
    );
    elements.communicationsOverview.textContent = `${readyCount} of 3 delivery routes report ready; ${configuredCount} are configured. Web App is an ${formatPermission(access.status).toLowerCase()} access channel, not a delivery provider. This setup center never sends a message.`;
    elements.communicationProviderList.replaceChildren(...state.communications.routes.map(renderCommunicationProviderCard));
    elements.communicationProviderList.setAttribute("aria-busy", "false");
    elements.communicationsLastUpdated.textContent = `Last refreshed ${formatDateTime(state.communications.updatedAt)} · Credentials are masked and never returned.`;
    renderCommunicationOrder();
  }

  async function loadCommunications({ quiet = true } = {}) {
    try {
      const payload = await fetchJson("/communications", { cache: "no-store" }, 12000);
      state.communications = normalizeCommunicationsPayload(payload || {});
      state.communicationsLoaded = true;
      state.communicationOrderDraft = [...state.communications.policy.delivery_order];
      state.communicationOrderDirty = false;
      renderCommunications();
      return true;
    } catch (error) {
      state.communications = null;
      state.communicationsLoaded = false;
      state.communicationOrderDirty = false;
      renderCommunications();
      if (!quiet) toast(`Communications unavailable: ${normalizeCommunicationError(error)}`, "error");
      return false;
    }
  }

  function normalizeCommunicationError(error) {
    const detail = normalizeCommunicationDetail(error?.message, "The local core did not complete the request.");
    if (/rate.?limit|temporarily/i.test(detail)) return "Verification is rate limited. Wait before trying again.";
    if (/credential storage|dpapi/i.test(detail)) return "Protected credential storage is unavailable on this device.";
    if (/timed out|unreachable|failed to fetch|network/i.test(detail)) return "The local communications service is unreachable.";
    return detail;
  }

  function replaceCommunicationRoute(rawRoute) {
    const provider = rawRoute?.provider;
    if (!COMMUNICATION_PROVIDERS.includes(provider) || !state.communications) return;
    const index = state.communications.routes.findIndex((route) => route.provider === provider);
    if (index >= 0) state.communications.routes[index] = normalizeCommunicationRoute(rawRoute, provider);
    state.communications.updatedAt = new Date();
  }

  function setCommunicationBusy(key, busy) {
    if (busy) state.communicationBusy[key] = true;
    else delete state.communicationBusy[key];
    renderCommunications();
    if (state.communicationEditingProvider) {
      const provider = state.communicationEditingProvider;
      elements.testCommunicationProvider.disabled = Boolean(state.communicationBusy[`${provider}:test`]);
      elements.saveCommunicationProvider.disabled = Boolean(state.communicationBusy[`${provider}:save`]);
    }
  }

  async function updateCommunicationRoute(provider, changes, action = "update") {
    const key = `${provider}:${action}`;
    setCommunicationBusy(key, true);
    try {
      const payload = await fetchJson(`/communications/routes/${encodeURIComponent(provider)}`, {
        method: "PATCH",
        body: JSON.stringify(changes)
      }, 15000);
      replaceCommunicationRoute(payload?.route);
      renderCommunications();
      return payload?.route || null;
    } finally {
      setCommunicationBusy(key, false);
    }
  }

  function credentialReferenceFor(route) {
    const prerequisite = route?.prerequisites.find((item) => item.id.startsWith("credential:") || item.id.startsWith("env:"));
    return prerequisite?.environment_reference || prerequisite?.label || "a fixed local environment reference";
  }

  function appendCommunicationField(parent, { id, label, help, type = "text", placeholder = "", pattern = "", value = "" }) {
    const field = document.createElement("div");
    field.className = "communication-field";
    const labelNode = document.createElement("label");
    labelNode.htmlFor = id;
    labelNode.textContent = label;
    const optional = document.createElement("small");
    optional.textContent = "BLANK KEEPS SAVED VALUE";
    labelNode.append(optional);
    const input = document.createElement("input");
    input.id = id;
    input.name = id;
    input.type = type;
    input.value = value;
    input.placeholder = placeholder;
    input.autocomplete = "off";
    if (pattern) input.pattern = pattern;
    input.maxLength = 300;
    const helpNode = document.createElement("small");
    helpNode.className = "communication-field-help";
    helpNode.textContent = help;
    field.append(labelNode, input, helpNode);
    parent.append(field);
    return input;
  }

  function renderCommunicationSetupFields(route) {
    const parent = elements.communicationSetupFields;
    parent.replaceChildren();
    if (route.provider === "signal") {
      appendCommunicationField(parent, {
        id: "communicationSignalAccount",
        label: "Signal account",
        help: "Use the locally registered signal-cli account in E.164 format. The saved value remains masked.",
        type: "tel",
        placeholder: route.configuration.account_configured ? route.configuration.account_masked : "+15551234567",
        pattern: "\\+[1-9][0-9]{7,14}"
      });
      appendCommunicationField(parent, {
        id: "communicationSignalRecipient",
        label: "Signal recipient",
        help: "One explicit E.164 destination. This setup does not link Signal or send a message.",
        type: "tel",
        placeholder: route.configuration.recipient_configured ? route.configuration.recipient_masked : "+15557654321",
        pattern: "\\+[1-9][0-9]{7,14}"
      });
    } else if (route.provider === "telegram") {
      appendCommunicationField(parent, {
        id: "communicationTelegramChat",
        label: "Telegram chat ID",
        help: "Enter the exact destination identifier. It is blank on every reopen and masked after save.",
        placeholder: route.configuration.chat_id_configured ? route.configuration.chat_id_masked : "-1001234567890",
        pattern: "-?[1-9][0-9]{4,19}"
      });
    } else if (route.provider === "discord") {
      const modeField = document.createElement("div");
      modeField.className = "communication-field";
      const modeLabel = document.createElement("label");
      modeLabel.htmlFor = "communicationDiscordMode";
      modeLabel.textContent = "Discord route mode";
      const mode = document.createElement("select");
      mode.id = "communicationDiscordMode";
      mode.name = "communicationDiscordMode";
      mode.add(new Option("Webhook", "webhook"));
      mode.add(new Option("Bot", "bot"));
      mode.value = route.configuration.mode;
      const help = document.createElement("small");
      help.className = "communication-field-help";
      help.textContent = "Webhook URLs are accepted only as protected credentials below; arbitrary webhook URL fields are not exposed.";
      modeField.append(modeLabel, mode, help);
      parent.append(modeField);
      appendCommunicationField(parent, {
        id: "communicationDiscordChannel",
        label: "Bot channel ID",
        help: "Required only for bot mode. Blank preserves an already saved masked channel.",
        placeholder: route.configuration.channel_id_configured ? route.configuration.channel_id_masked : "123456789012345678",
        pattern: "[1-9][0-9]{16,19}"
      });
    } else {
      appendCommunicationField(parent, {
        id: "communicationWebOrigin",
        label: "Private Web App origin",
        help: "Loopback, RFC1918 private-LAN, or tailnet HTTP(S) origin only. Non-loopback access requires HTTPS and authentication.",
        type: "url",
        placeholder: route.configuration.base_url_configured ? "Saved private origin (hidden)" : "http://127.0.0.1:8787"
      });
      const authRow = document.createElement("label");
      authRow.className = "toggle-row communication-auth-row";
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = "Require Web App token authentication";
      const small = document.createElement("small");
      small.textContent = "Required for private-LAN and tailnet origins. This configures readiness only; it does not create a session or phone-control channel.";
      copy.append(strong, small);
      const toggle = document.createElement("input");
      toggle.id = "communicationWebAuth";
      toggle.type = "checkbox";
      toggle.role = "switch";
      toggle.checked = route.configuration.auth_enabled;
      authRow.append(copy, toggle);
      parent.append(authRow);
    }

    if (route.provider !== "signal") {
      renderCommunicationCredentialField(route, parent);
      if (route.provider === "web_app") {
        const credential = document.getElementById("communicationCredentialInput");
        const auth = document.getElementById("communicationWebAuth");
        if (credential && auth && !auth.checked) {
          credential.disabled = true;
          credential.placeholder = "Enable Web App token authentication to replace a credential";
        }
      }
    }
  }

  function renderCommunicationCredentialField(route, parent) {
    const supported = state.communications?.secrets.write_only_replace_supported === true;
    const configured = communicationCredentialConfigured(route);
    const prerequisite = route.prerequisites.find((item) => item.id.startsWith("credential:") || item.id.startsWith("env:"));
    const field = document.createElement("div");
    field.className = "communication-field communication-credential-field";
    const label = document.createElement("label");
    label.htmlFor = "communicationCredentialInput";
    label.textContent = "Replacement credential";
    const stateLabel = document.createElement("small");
    stateLabel.textContent = configured ? "CONFIGURED · VALUE HIDDEN" : "NOT CONFIGURED";
    label.append(stateLabel);
    const input = document.createElement("input");
    input.id = "communicationCredentialInput";
    input.name = "communicationCredentialInput";
    input.type = "password";
    input.autocomplete = "new-password";
    input.spellcheck = false;
    input.maxLength = 512;
    input.placeholder = supported ? "Enter only to replace the protected value" : "Protected replacement unavailable on this platform";
    input.disabled = !supported;
    input.value = "";
    const help = document.createElement("small");
    help.className = "communication-field-help";
    help.textContent = supported
      ? `Write-only Windows user-scope protection. The value is never returned. An environment override may also use ${credentialReferenceFor(route)}.${prerequisite?.source === "environment" ? " The active value comes from the environment, so clearing local protected storage cannot remove it." : ""}`
      : `Configure ${credentialReferenceFor(route)} in the local JARVIS environment. No credential value can be submitted from this device.`;
    field.append(label, input, help);
    if (configured && supported) {
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "text-button communication-clear-credential";
      clear.dataset.communicationClearCredential = route.provider;
      clear.textContent = "Clear protected credential";
      clear.title = prerequisite?.source === "environment"
        ? "The active environment value will remain configured"
        : "Environment overrides are not changed";
      field.append(clear);
    }
    parent.append(field);
  }

  function openCommunicationSetup(provider) {
    const route = communicationRoute(provider);
    if (!route) {
      toast("That communications provider is unavailable", "error");
      return;
    }
    state.communicationEditingProvider = provider;
    const meta = COMMUNICATION_PROVIDER_META[provider];
    elements.communicationSetupTitle.textContent = `${meta.label} Setup`;
    elements.communicationSetupIcon.textContent = meta.icon;
    elements.communicationSetupIcon.dataset.provider = provider;
    elements.communicationSetupProvider.textContent = meta.label;
    elements.communicationSetupDescription.textContent = meta.description;
    setStatusBadge(elements.communicationSetupBadge, formatPermission(route.status).toUpperCase(), communicationStatusTone(route.status));
    elements.communicationPrerequisiteList.replaceChildren();
    const prerequisites = route.prerequisites.length ? route.prerequisites : [{ label: "No prerequisites were reported by the local core", ready: false }];
    prerequisites.forEach((item) => {
      const entry = document.createElement("li");
      entry.className = item.ready ? "ready" : "pending";
      const credentialItem = item.id?.startsWith("credential:") || item.id?.startsWith("env:");
      entry.textContent = `${item.ready ? "Ready" : "Needed"} — ${item.label}${credentialItem ? ` (value hidden${item.source ? ` · ${formatPermission(item.source)}` : ""})` : ""}`;
      elements.communicationPrerequisiteList.append(entry);
    });
    renderCommunicationSetupFields(route);
    elements.communicationProviderEnabled.checked = route.enabled;
    elements.communicationSetupResult.className = "communication-setup-result";
    elements.communicationSetupResult.textContent = route.last_test
      ? `Last read-only verification: ${formatPermission(route.last_test.status)} ${relativeTime(route.last_test.tested_at)}. ${route.last_test.detail} ${communicationTestDisclosure(route)}`
      : `${route.status_detail} No saved-setup verification has run yet. ${communicationTestDisclosure(route)}`;
    elements.testCommunicationProvider.disabled = Boolean(state.communicationBusy[`${provider}:test`]);
    elements.saveCommunicationProvider.disabled = Boolean(state.communicationBusy[`${provider}:save`]);
    try {
      if (typeof elements.communicationSetupDialog.showModal === "function") elements.communicationSetupDialog.showModal();
      else elements.communicationSetupDialog.setAttribute("open", "");
    } catch {
      elements.communicationSetupDialog.setAttribute("open", "");
    }
    requestAnimationFrame(() => $("input:not([disabled]), select", elements.communicationSetupFields)?.focus());
  }

  function closeCommunicationSetup() {
    const credential = document.getElementById("communicationCredentialInput");
    if (credential) credential.value = "";
    if (typeof elements.communicationSetupDialog.close === "function" && elements.communicationSetupDialog.open) {
      elements.communicationSetupDialog.close();
    } else {
      elements.communicationSetupDialog.removeAttribute("open");
    }
    elements.communicationSetupFields.replaceChildren();
    elements.communicationSetupForm.reset();
    state.communicationEditingProvider = null;
  }

  function communicationSetupRequest(route) {
    const body = { enabled: elements.communicationProviderEnabled.checked };
    const value = (id) => String(document.getElementById(id)?.value || "").trim();
    if (route.provider === "signal") {
      if (value("communicationSignalAccount")) body.account = value("communicationSignalAccount");
      if (value("communicationSignalRecipient")) body.recipient = value("communicationSignalRecipient");
    } else if (route.provider === "telegram") {
      if (value("communicationTelegramChat")) body.chat_id = value("communicationTelegramChat");
    } else if (route.provider === "discord") {
      body.mode = value("communicationDiscordMode") === "bot" ? "bot" : "webhook";
      if (body.mode === "bot" && value("communicationDiscordChannel")) body.channel_id = value("communicationDiscordChannel");
      if (body.mode === "bot" && !route.configuration.channel_id_configured && !body.channel_id) {
        throw new Error("Discord bot mode requires a channel ID");
      }
    } else {
      if (value("communicationWebOrigin")) body.base_url = value("communicationWebOrigin");
      body.auth_enabled = Boolean(document.getElementById("communicationWebAuth")?.checked);
    }
    return body;
  }

  async function saveCommunicationProvider(event) {
    event.preventDefault();
    const provider = state.communicationEditingProvider;
    const route = communicationRoute(provider);
    if (!route || !elements.communicationSetupForm.reportValidity()) return;
    let body;
    try {
      body = communicationSetupRequest(route);
    } catch (error) {
      elements.communicationSetupResult.className = "communication-setup-result error";
      elements.communicationSetupResult.textContent = error.message;
      return;
    }
    const credentialInput = document.getElementById("communicationCredentialInput");
    let replacementCredential = credentialInput && !credentialInput.disabled
      && (provider !== "web_app" || document.getElementById("communicationWebAuth")?.checked)
      ? credentialInput.value
      : "";
    setCommunicationBusy(`${provider}:save`, true);
    try {
      const payload = await fetchJson(`/communications/routes/${encodeURIComponent(provider)}`, {
        method: "PATCH",
        body: JSON.stringify(body)
      }, 15000);
      replaceCommunicationRoute(payload?.route);
      if (replacementCredential) {
        const credentialPayload = await fetchJson(`/communications/routes/${encodeURIComponent(provider)}/credential`, {
          method: "POST",
          body: JSON.stringify({ credential: replacementCredential })
        }, 15000);
        replaceCommunicationRoute(credentialPayload?.route);
      }
      replacementCredential = "";
      if (credentialInput) credentialInput.value = "";
      renderCommunications();
      closeCommunicationSetup();
      toast(`${COMMUNICATION_PROVIDER_META[provider].label} setup saved`, "success");
      addAudit(`Communications setup updated: ${COMMUNICATION_PROVIDER_META[provider].label}`, "warning", "CREDENTIAL VALUES EXCLUDED");
    } catch (error) {
      replacementCredential = "";
      if (credentialInput) credentialInput.value = "";
      elements.communicationSetupResult.className = "communication-setup-result error";
      elements.communicationSetupResult.textContent = `Setup was not fully saved: ${normalizeCommunicationError(error)} No credential value was retained by the HUD.`;
      await loadCommunications({ quiet: true });
    } finally {
      setCommunicationBusy(`${provider}:save`, false);
    }
  }

  async function testCommunicationProvider(provider) {
    const route = communicationRoute(provider);
    if (!route || state.communicationBusy[`${provider}:test`]) return;
    setCommunicationBusy(`${provider}:test`, true);
    if (state.communicationEditingProvider === provider) {
      elements.communicationSetupResult.className = "communication-setup-result";
      elements.communicationSetupResult.textContent = "Running read-only saved-setup verification. No visible message will be sent…";
    }
    try {
      const payload = await fetchJson(`/communications/routes/${encodeURIComponent(provider)}/test`, { method: "POST" }, 20000);
      const test = payload?.test && typeof payload.test === "object" ? payload.test : {};
      route.last_test = {
        tested_at: test.tested_at || new Date().toISOString(),
        status: String(test.status || "unverified").toLowerCase(),
        detail: normalizeCommunicationDetail(test.detail, "Read-only verification completed."),
        credential_verified: typeof test.credential_verified === "boolean" ? test.credential_verified : null
      };
      if (state.communicationEditingProvider === provider) {
        const credential = route.last_test.credential_verified == null
          ? "Credential identity was not applicable or not verified."
          : route.last_test.credential_verified ? "Saved credential identity verified." : "Saved credential identity not verified.";
        elements.communicationSetupResult.className = `communication-setup-result ${route.last_test.status === "ready" ? "success" : route.last_test.status === "error" ? "error" : "warning"}`;
        elements.communicationSetupResult.textContent = `Read-only verification: ${formatPermission(route.last_test.status)}. ${route.last_test.detail} ${credential} No message was sent.`;
      }
      renderCommunications();
      toast(`${COMMUNICATION_PROVIDER_META[provider].label} verification: ${formatPermission(route.last_test.status)}`, route.last_test.status === "error" ? "error" : "info");
      addAudit(`Communications route verified: ${COMMUNICATION_PROVIDER_META[provider].label}`, "info", "READ-ONLY · NO MESSAGE SENT");
    } catch (error) {
      const message = normalizeCommunicationError(error);
      if (state.communicationEditingProvider === provider) {
        elements.communicationSetupResult.className = "communication-setup-result error";
        elements.communicationSetupResult.textContent = `Verification did not complete: ${message} No message was sent.`;
      }
      toast(`${COMMUNICATION_PROVIDER_META[provider].label}: ${message}`, "error");
    } finally {
      setCommunicationBusy(`${provider}:test`, false);
    }
  }

  async function clearCommunicationCredential(button, provider) {
    if (!button.dataset.confirmed) {
      button.dataset.confirmed = "true";
      button.textContent = "Confirm clear protected credential";
      setTimeout(() => {
        if (button.isConnected && button.dataset.confirmed) {
          delete button.dataset.confirmed;
          button.textContent = "Clear protected credential";
        }
      }, 6000);
      return;
    }
    button.disabled = true;
    button.textContent = "Clearing…";
    try {
      const payload = await fetchJson(`/communications/routes/${encodeURIComponent(provider)}/credential`, { method: "DELETE" }, 15000);
      replaceCommunicationRoute(payload?.route);
      renderCommunications();
      openCommunicationSetup(provider);
      elements.communicationSetupResult.className = "communication-setup-result warning";
      elements.communicationSetupResult.textContent = "Protected local credential cleared. Any configured environment override is unchanged. No message was sent.";
      addAudit(`Protected communications credential cleared: ${COMMUNICATION_PROVIDER_META[provider].label}`, "warning", "ENVIRONMENT OVERRIDES UNCHANGED");
    } catch (error) {
      button.disabled = false;
      button.textContent = "Clear protected credential";
      elements.communicationSetupResult.className = "communication-setup-result error";
      elements.communicationSetupResult.textContent = `Credential was not cleared: ${normalizeCommunicationError(error)}`;
    }
  }

  async function toggleCommunicationProvider(provider, enabled) {
    try {
      await updateCommunicationRoute(provider, { enabled }, "enable");
      toast(`${COMMUNICATION_PROVIDER_META[provider].label} ${enabled ? "enabled" : "disabled"}`, "success");
      addAudit(`Communications route ${enabled ? "enabled" : "disabled"}: ${COMMUNICATION_PROVIDER_META[provider].label}`, "warning");
    } catch (error) {
      renderCommunications();
      toast(`Route was not changed: ${normalizeCommunicationError(error)}`, "error");
    }
  }

  function moveCommunicationProvider(provider, direction) {
    const index = state.communicationOrderDraft.indexOf(provider);
    const next = index + Number(direction);
    if (index < 0 || next < 0 || next >= state.communicationOrderDraft.length) return;
    [state.communicationOrderDraft[index], state.communicationOrderDraft[next]] = [state.communicationOrderDraft[next], state.communicationOrderDraft[index]];
    state.communicationOrderDirty = true;
    renderCommunicationOrder();
    requestAnimationFrame(() => {
      const moved = $(`[data-communication-move="${provider}"][data-direction="${direction}"]`, elements.communicationRouteList)
        || $(`[data-communication-move="${provider}"]`, elements.communicationRouteList);
      moved?.focus();
    });
  }

  async function saveCommunicationOrder() {
    if (!state.communicationOrderDirty) return;
    setCommunicationBusy("order", true);
    try {
      const payload = await fetchJson("/communications/order", {
        method: "PATCH",
        body: JSON.stringify({ provider_order: [...state.communicationOrderDraft] })
      }, 15000);
      state.communications = normalizeCommunicationsPayload(payload || {});
      state.communicationOrderDraft = [...state.communications.policy.delivery_order];
      state.communicationOrderDirty = false;
      renderCommunications();
      toast("Delivery priority saved", "success");
      addAudit("Communications delivery priority updated", "warning", "WEB APP EXCLUDED");
    } catch (error) {
      toast(`Delivery priority was not saved: ${normalizeCommunicationError(error)}`, "error");
    } finally {
      setCommunicationBusy("order", false);
    }
  }

  async function updateCommunicationFailover(enabled) {
    setCommunicationBusy("failover", true);
    try {
      const payload = await fetchJson("/communications/failover", {
        method: "PATCH",
        body: JSON.stringify({ failover_enabled: enabled })
      }, 15000);
      state.communications = normalizeCommunicationsPayload(payload || {});
      state.communicationOrderDraft = [...state.communications.policy.delivery_order];
      state.communicationOrderDirty = false;
      renderCommunications();
      toast(`Provider failover ${enabled ? "enabled" : "disabled"}`, "success");
      addAudit(`Communications failover ${enabled ? "enabled" : "disabled"}`, "warning", "PRIVACY BOUNDARY ACKNOWLEDGED");
    } catch (error) {
      renderCommunications();
      toast(`Failover was not changed: ${normalizeCommunicationError(error)}`, "error");
    } finally {
      setCommunicationBusy("failover", false);
    }
  }

  function setupCommunications() {
    elements.refreshCommunications.addEventListener("click", async () => {
      setBusy(elements.refreshCommunications, true, "Refreshing…");
      await loadCommunications({ quiet: false });
      setBusy(elements.refreshCommunications, false);
    });
    elements.communicationProviderList.addEventListener("click", (event) => {
      const setup = event.target.closest("[data-communication-setup]");
      if (setup) return openCommunicationSetup(setup.dataset.communicationSetup);
      const test = event.target.closest("[data-communication-test]");
      if (test) testCommunicationProvider(test.dataset.communicationTest);
    });
    elements.communicationProviderList.addEventListener("change", (event) => {
      const toggle = event.target.closest("[data-communication-enable]");
      if (toggle) toggleCommunicationProvider(toggle.dataset.communicationEnable, toggle.checked);
    });
    elements.communicationRouteList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-communication-move]");
      if (button) moveCommunicationProvider(button.dataset.communicationMove, button.dataset.direction);
    });
    elements.saveCommunicationOrder.addEventListener("click", saveCommunicationOrder);
    elements.communicationFailoverEnabled.addEventListener("change", () => updateCommunicationFailover(elements.communicationFailoverEnabled.checked));
    elements.closeCommunicationSetup.addEventListener("click", closeCommunicationSetup);
    elements.cancelCommunicationSetup.addEventListener("click", closeCommunicationSetup);
    elements.communicationSetupDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      closeCommunicationSetup();
    });
    elements.communicationSetupForm.addEventListener("submit", saveCommunicationProvider);
    elements.testCommunicationProvider.addEventListener("click", () => {
      if (state.communicationEditingProvider) testCommunicationProvider(state.communicationEditingProvider);
    });
    elements.communicationSetupFields.addEventListener("click", (event) => {
      const clear = event.target.closest("[data-communication-clear-credential]");
      if (clear) clearCommunicationCredential(clear, clear.dataset.communicationClearCredential);
    });
    elements.communicationSetupFields.addEventListener("change", (event) => {
      if (event.target.id !== "communicationWebAuth") return;
      const credential = document.getElementById("communicationCredentialInput");
      if (!credential) return;
      const supported = state.communications?.secrets.write_only_replace_supported === true;
      credential.disabled = !supported || !event.target.checked;
      if (credential.disabled) credential.value = "";
      credential.placeholder = !event.target.checked
        ? "Enable Web App token authentication to replace a credential"
        : supported ? "Enter only to replace the protected value" : "Protected replacement unavailable on this platform";
    });
    renderCommunications();
  }

  function updateProjectSummaries() {
    if (!elements.projectPlanningMetric) return;
    const pendingTasks = state.tasks.filter((task) => !task.completed).length;
    elements.projectPlanningMetric.textContent = pendingTasks ? `${pendingTasks} active task${pendingTasks === 1 ? "" : "s"}` : "Task workspace clear";
    setStatusBadge(elements.projectPlanningStatus, state.online ? "READY" : "LOCAL", state.online ? "ready" : "disabled");
    elements.projectKnowledgeMetric.textContent = state.memories.length
      ? `${state.memories.length} local memor${state.memories.length === 1 ? "y" : "ies"}`
      : "Semantic search and ingest";
    setStatusBadge(elements.projectKnowledgeStatus, state.online ? "INDEXED" : "LOCAL", state.online ? "ready" : "disabled");
    const enabledActions = state.actionCatalog.filter((action) => action.enabled && action.id !== "angerona_launch").length;
    elements.projectOperationsMetric.textContent = `${enabledActions} guarded action${enabledActions === 1 ? "" : "s"} · ${state.approvals.length} pending`;
    setStatusBadge(elements.projectOperationsStatus, state.approvals.length ? "REVIEW" : "GUARDED", state.approvals.length ? "warning" : "ready");
    const onlineNodes = state.nodes.filter((node) => ["online", "ready", "healthy", "active"].includes(String(node.status || node.state).toLowerCase())).length;
    const defense = state.securityPosture?.status || "checking";
    elements.projectFederationMetric.textContent = `${onlineNodes} peer${onlineNodes === 1 ? "" : "s"} · defense ${defense}`;
    setStatusBadge(elements.projectFederationStatus, defense.toUpperCase(), defenseTone(defense));
    if (elements.globeProjectsMetric) {
      elements.globeProjectsMetric.textContent = state.projectsLoaded
        ? `${state.projects.length} tracked project${state.projects.length === 1 ? "" : "s"}`
        : "Projects service unavailable";
      elements.globeFederationMetric.textContent = `${onlineNodes} trusted peer${onlineNodes === 1 ? "" : "s"} · ${defense}`;
      elements.globeModelMetric.textContent = elements.dialogModel?.textContent || "Detecting local model…";
      const angerona = state.angeronaStatus;
      const angeronaCatalog = state.actionCatalog.find((action) => action.id === "angerona_launch" || action.id === "angerona");
      elements.globeAngeronaMetric.textContent = angerona
        ? !angerona.installed ? "Not installed" : angerona.mcpReady ? "Read-only MCP ready" : angerona.running ? "Running · MCP locked" : "Installed · operator start required"
        : angeronaCatalog ? `Read-only · ${angeronaCatalog.status || "observed"}` : "Status unavailable from local core";
      elements.globeCoreLoad.textContent = `${Math.round(state.telemetry.coreLoad)}%`;
    }
  }

  function setupTabs() {
    const tabs = $$("[role=tab]", elements.operationsPanel);
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateTab(tab));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        activateTab(tabs[next]);
        tabs[next].focus();
      });
    });
  }

  function activateTab(active) {
    $$("[role=tab]", elements.operationsPanel).forEach((tab) => {
      const selected = tab === active;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      panel.hidden = !selected;
    });
  }

  function setupMobileNavigation() {
    $$("[data-mobile-target]").forEach((button) => {
      button.addEventListener("click", () => {
        const targetId = button.dataset.mobileTarget;
        $$("[data-mobile-target]").forEach((item) => item.classList.toggle("active", item === button));
        $$(".dashboard-column").forEach((column) => column.classList.remove("mobile-active"));
        if (targetId === "skillsPanel") {
          elements.commandPanel.classList.add("mobile-active");
          requestAnimationFrame(() => elements.skillsPanel.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" }));
        } else {
          document.getElementById(targetId)?.classList.add("mobile-active");
          window.scrollTo({ top: 0, behavior: state.settings.lowMotion ? "auto" : "smooth" });
        }
      });
    });
  }

  async function loadRemoteSettings({ quiet = true } = {}) {
    if (state.settingsBusy) return false;
    state.settingsBusy = true;
    elements.saveSettings.disabled = true;
    try {
      const payload = await fetchJson("/settings", { cache: "no-store" }, 10000);
      applyRemoteSettingsPayload(payload);
      return true;
    } catch (error) {
      state.remoteSettings = null;
      renderRemoteSettingsUnavailable();
      if (!quiet) toast(`Settings unavailable: ${error.message}`, "error");
      return false;
    } finally {
      state.settingsBusy = false;
      elements.saveSettings.disabled = !state.remoteSettings;
    }
  }

  function applyRemoteSettingsPayload(payload) {
    const raw = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
    const federation = raw.federation && typeof raw.federation === "object" ? raw.federation : {};
    const desktop = raw.desktop && typeof raw.desktop === "object" ? raw.desktop : {};
    const runtime = desktop.runtime && typeof desktop.runtime === "object" ? desktop.runtime : {};
    const globeSizeMinimum = clamp(desktop.transparent_globe_size_min || BACKGROUND_GLOBE_SIZE.minimum, 200, 1600);
    const globeSizeMaximum = clamp(desktop.transparent_globe_size_max || BACKGROUND_GLOBE_SIZE.maximum, globeSizeMinimum, 2000);
    const peerUrls = Array.isArray(federation.peer_urls)
      ? federation.peer_urls.map(sanitizePeerUrl).filter(Boolean)
      : [];
    const sharedRoots = Array.isArray(federation.shared_roots)
      ? federation.shared_roots
        .filter((root) => root && typeof root === "object" && typeof root.path === "string")
        .map((root) => ({
          path: String(root.path),
          available: Boolean(root.available),
          permissions: String(root.permissions || "read")
        }))
      : [];
    const integrations = Array.isArray(raw.integrations)
      ? raw.integrations
        .filter((integration) => integration && typeof integration === "object" && integration.id)
        .map((integration) => ({
          id: String(integration.id),
          name: String(integration.name || integration.id),
          enabled: Boolean(integration.enabled),
          configured: Boolean(integration.configured),
          status: String(integration.status || ""),
          description: String(integration.description || "")
        }))
      : [];

    state.remoteSettings = {
      instance_name: String(raw.instance_name || "JARVIS"),
      instance_id: String(raw.instance_id || ""),
      platform: String(raw.platform || ""),
      federation: {
        enabled: Boolean(federation.enabled),
        transport: String(federation.transport || "tailscale"),
        transport_available: Boolean(federation.transport_available),
        signing_key_configured: Boolean(federation.signing_key_configured),
        capability_endpoint_ready: Boolean(federation.capability_endpoint_ready),
        transport_exposed: Boolean(federation.transport_exposed),
        ready: Boolean(federation.ready),
        peer_urls: peerUrls,
        compute_enabled: Boolean(federation.compute_enabled),
        data_enabled: Boolean(federation.data_enabled),
        admin_capable: Boolean(federation.admin_capable),
        require_target_approval: federation.require_target_approval === true,
        shared_roots: sharedRoots
      },
      desktop: {
        adaptive_dashboard_enabled: desktop.adaptive_dashboard_enabled !== false,
        gesture_control_enabled: Boolean(desktop.gesture_control_enabled),
        gesture_low_power_mode: Boolean(desktop.gesture_low_power_mode),
        privacy_mode_enabled: Boolean(desktop.privacy_mode_enabled),
        camera_frames_stored: Boolean(desktop.camera_frames_stored),
        transparent_globe_enabled: Boolean(desktop.transparent_globe_enabled),
        transparent_globe_size_min: globeSizeMinimum,
        transparent_globe_size_max: globeSizeMaximum,
        transparent_globe_size: clamp(desktop.transparent_globe_size || BACKGROUND_GLOBE_SIZE.default, globeSizeMinimum, globeSizeMaximum),
        transparent_globe_position: ["bottom_left", "bottom_right"].includes(desktop.transparent_globe_position)
          ? desktop.transparent_globe_position
          : "bottom_right",
        transparent_globe_supported: Boolean(desktop.transparent_globe_supported),
        live_toggle_available: Boolean(desktop.live_toggle_available),
        restart_required: Boolean(desktop.restart_required),
        return_hotkey: String(desktop.return_hotkey || "Ctrl+Alt+J"),
        interaction_note: String(desktop.interaction_note || ""),
        runtime: {
          active: Boolean(runtime.active),
          mode: String(runtime.mode || "inactive"),
          companion_available: Boolean(runtime.companion_available),
          companion_visible: Boolean(runtime.companion_visible ?? runtime.active),
          desktop_attached: Boolean(runtime.desktop_attached),
          true_alpha: Boolean(runtime.true_alpha),
          alpha_pixel_verified: Boolean(runtime.alpha_pixel_verified),
          non_activating: Boolean(runtime.non_activating),
          taskbar_hidden: Boolean(runtime.taskbar_hidden),
          hotkey_registered: Boolean(runtime.hotkey_registered),
          fallback_reason: String(runtime.fallback_reason || ""),
          updated_at: runtime.updated_at || null
        }
      },
      integrations,
      restart_required: Boolean(payload?.restart_required || desktop.restart_required)
    };
    renderRemoteSettings();
  }

  function sanitizePeerUrl(value) {
    try {
      const url = new URL(String(value));
      if (!["http:", "https:"].includes(url.protocol)) return "";
      if (url.username || url.password || url.search || url.hash) return "";
      return url.href.replace(/\/$/, "");
    } catch {
      return "";
    }
  }

  function parsePeerUrls(value) {
    const endpoints = [];
    for (const entry of String(value).split(/\r?\n/).map((line) => line.trim()).filter(Boolean)) {
      let url;
      try {
        url = new URL(entry);
      } catch {
        throw new Error(`Invalid peer endpoint: ${entry}`);
      }
      if (!["http:", "https:"].includes(url.protocol)) {
        throw new Error("Peer endpoints must use HTTP or HTTPS");
      }
      if (url.username || url.password || url.search || url.hash) {
        throw new Error("Peer endpoints cannot contain credentials, query tokens, or fragments");
      }
      endpoints.push(url.href.replace(/\/$/, ""));
    }
    return [...new Set(endpoints)];
  }

  function renderRemoteSettings() {
    const settings = state.remoteSettings;
    if (!settings) return;
    const federation = settings.federation;
    elements.instanceNameInput.value = settings.instance_name;
    elements.instanceIdValue.textContent = settings.instance_id || "Unavailable";
    elements.instancePlatformValue.textContent = settings.platform || "Unavailable";
    elements.federationTransportValue.textContent = `${federation.transport || "tailscale"} / ${federation.transport_available ? "available" : "not installed"}`;
    elements.federationEnabledToggle.checked = federation.enabled;
    elements.peerUrlsInput.value = federation.peer_urls.join("\n");
    elements.computeSharingToggle.checked = federation.compute_enabled;
    elements.dataSharingToggle.checked = federation.data_enabled;
    elements.adminCapableToggle.checked = federation.admin_capable && federation.require_target_approval;
    elements.adminCapableToggle.disabled = !federation.require_target_approval;
    elements.restartRequiredNotice.hidden = !settings.restart_required;

    setStatusBadge(
      elements.federationStatusBadge,
      federation.ready ? "FEDERATED" : federation.enabled ? "SETUP REQUIRED" : "STANDALONE",
      federation.ready ? "ready" : federation.enabled ? "warning" : "disabled"
    );
    const signingBadge = document.createElement("span");
    signingBadge.className = `integration-status status-${federation.signing_key_configured ? "ready" : "disabled"}`;
    signingBadge.textContent = federation.signing_key_configured ? "CONFIGURED" : "NOT CONFIGURED";
    elements.signingKeyStatus.replaceChildren(signingBadge);
    setStatusBadge(
      elements.targetApprovalStatus,
      federation.require_target_approval ? "ENFORCED" : "NOT ENFORCED",
      federation.require_target_approval ? "ready" : "error"
    );
    renderSharedRoots(federation.shared_roots);
    renderIntegrations(settings.integrations);
    elements.adaptiveDashboardToggle.checked = settings.desktop.adaptive_dashboard_enabled;
    elements.gestureControlToggle.checked = settings.desktop.gesture_control_enabled;
    elements.gestureLowPowerToggle.checked = settings.desktop.gesture_low_power_mode;
    elements.privacyModeToggle.checked = settings.desktop.privacy_mode_enabled;
    document.documentElement.classList.toggle("privacy-mode", settings.desktop.privacy_mode_enabled);
    elements.coreModeBadge.textContent = settings.desktop.privacy_mode_enabled
      ? "PRIVATE MODE"
      : state.online ? "OLLAMA ACTIVE" : "SAFE MODE";
    document.documentElement.classList.toggle(
      "adaptive-dashboard-disabled",
      !settings.desktop.adaptive_dashboard_enabled
    );
    state.gestureController?.setLowPower(settings.desktop.gesture_low_power_mode);
    renderDesktopGlobeSettings(settings.desktop);
  }

  function renderRemoteSettingsUnavailable() {
    elements.instanceNameInput.value = "";
    elements.instanceIdValue.textContent = "Unavailable";
    elements.instancePlatformValue.textContent = "Unavailable";
    elements.federationTransportValue.textContent = "Tailscale / unavailable";
    elements.peerUrlsInput.value = "";
    elements.federationEnabledToggle.checked = false;
    elements.computeSharingToggle.checked = false;
    elements.dataSharingToggle.checked = false;
    elements.adminCapableToggle.checked = false;
    elements.adminCapableToggle.disabled = true;
    elements.privacyModeToggle.checked = false;
    document.documentElement.classList.remove("privacy-mode");
    elements.restartRequiredNotice.hidden = true;
    setStatusBadge(elements.federationStatusBadge, "UNAVAILABLE", "error");
    setStatusBadge(elements.targetApprovalStatus, "UNAVAILABLE", "error");
    const signingBadge = document.createElement("span");
    signingBadge.className = "integration-status status-disabled";
    signingBadge.textContent = "UNAVAILABLE";
    elements.signingKeyStatus.replaceChildren(signingBadge);
    renderSharedRoots([]);
    renderIntegrations([]);
    renderDesktopGlobeSettings(null);
  }

  function renderDesktopGlobeSettings(desktop) {
    const available = Boolean(desktop);
    const supported = Boolean(desktop?.transparent_globe_supported);
    const runtime = desktop?.runtime || {};
    const enabled = Boolean(desktop?.transparent_globe_enabled);
    const nativeApi = backgroundGlobeNativeApi();
    const liveToggle = Boolean(desktop?.live_toggle_available && nativeApi);
    const visible = Boolean(runtime.companion_visible ?? runtime.active);
    const fallback = visible && String(runtime.mode || "").includes("fallback");
    elements.transparentGlobeToggle.checked = Boolean(desktop?.transparent_globe_enabled);
    elements.transparentGlobeToggle.disabled = !available || !supported || state.backgroundGlobeBusy;
    const sizeMinimum = desktop?.transparent_globe_size_min || BACKGROUND_GLOBE_SIZE.minimum;
    const sizeMaximum = desktop?.transparent_globe_size_max || BACKGROUND_GLOBE_SIZE.maximum;
    elements.transparentGlobeSize.min = String(sizeMinimum);
    elements.transparentGlobeSize.max = String(sizeMaximum);
    elements.transparentGlobeSize.value = String(clamp(desktop?.transparent_globe_size || BACKGROUND_GLOBE_SIZE.default, sizeMinimum, sizeMaximum));
    elements.transparentGlobeSize.disabled = !available || !supported || state.backgroundGlobeBusy;
    elements.transparentGlobeSizeValue.value = `${elements.transparentGlobeSize.value} px`;
    elements.transparentGlobeSizeValue.textContent = `${elements.transparentGlobeSize.value} px`;
    elements.transparentGlobePosition.value = desktop?.transparent_globe_position || "bottom_right";
    elements.transparentGlobePosition.disabled = !available || !supported || state.backgroundGlobeBusy;
    elements.globeRuntimeMode.textContent = formatPermission(runtime.mode || "unavailable");
    elements.globeReturnHotkey.textContent = desktop?.return_hotkey || "Ctrl+Alt+J";
    elements.globeInteractionNote.textContent = desktop?.interaction_note
      || runtime.fallback_reason
      || "Ctrl+Alt+J and Open full dashboard always recover the main HUD.";
    elements.backgroundGlobeMenuToggle.disabled = !available || !supported || state.backgroundGlobeBusy;
    elements.backgroundGlobeQuickToggle.disabled = !available || !supported || state.backgroundGlobeBusy;
    if (!available) {
      elements.globeSupportText.textContent = "Browser preview is available; saved and live desktop state could not be loaded.";
      setStatusBadge(elements.globeRuntimeBadge, "UNAVAILABLE", "disabled");
      setBackgroundGlobeMenuState(false, "OFF", "Desktop state unavailable", false);
      elements.backgroundGlobeQuickToggle.textContent = "Unavailable";
      setBackgroundGlobeSaveState("Desktop state is unavailable. Preview does not change background settings.");
      return;
    }
    const alphaState = runtime.true_alpha
      ? runtime.alpha_pixel_verified ? "alpha pixels verified" : "alpha composition path selected; live visual validation pending"
      : String(runtime.mode || "").includes("fallback")
        ? "secure floating fallback active"
        : "desktop composition will be evaluated when launched";
    elements.globeSupportText.textContent = supported
      ? `Large desktop companion supported · ${alphaState}. ${liveToggle ? "Quick visibility changes are live." : "Browser changes are saved for the next desktop launch."}`
      : "Desktop background mode is unsupported here; the secure browser preview remains available.";

    let badge = "OFF";
    let tone = "disabled";
    let menuLabel = "Off";
    let statusCopy = "Background Globe is off. Browser preview remains available.";
    let statusClass = "";
    if (!supported) {
      badge = "UNSUPPORTED";
      menuLabel = "Desktop layer unsupported";
    } else if (visible && fallback) {
      badge = "FALLBACK";
      tone = "warning";
      menuLabel = "Visible in safe floating fallback";
      statusCopy = `Visible in a secure floating fallback${runtime.fallback_reason ? ` · ${runtime.fallback_reason}` : ""}. Use ${desktop.return_hotkey || "Ctrl+Alt+J"} to recover the full HUD.`;
      statusClass = "fallback";
    } else if (visible) {
      badge = "LIVE";
      tone = "ready";
      menuLabel = "Live behind desktop apps";
      statusCopy = `Live on the desktop background${runtime.desktop_attached ? " layer" : ""}. Use ${desktop.return_hotkey || "Ctrl+Alt+J"} to open the full HUD.`;
      statusClass = "live";
    } else if (enabled && liveToggle && runtime.companion_available) {
      badge = "PAUSED";
      tone = "warning";
      menuLabel = "Paused while full HUD is open";
      statusCopy = "The companion is ready but hidden while the full HUD is open. Choose Show now to return it to the desktop.";
      statusClass = "pending";
    } else if (enabled) {
      badge = "NEXT LAUNCH";
      tone = "warning";
      menuLabel = "Saved for next desktop launch";
      statusCopy = "Enabled in saved settings, but not live in this window. It will start on the next native desktop launch.";
      statusClass = "pending";
    }
    setStatusBadge(elements.globeRuntimeBadge, badge, tone);
    const pressed = liveToggle ? visible : enabled;
    setBackgroundGlobeMenuState(pressed, visible ? fallback ? "SAFE" : "LIVE" : enabled ? "ON" : "OFF", menuLabel, enabled && !visible);
    elements.backgroundGlobeQuickToggle.textContent = liveToggle
      ? visible ? "Hide" : enabled ? "Show now" : "Turn on"
      : enabled ? "Turn off" : "Turn on";
    setBackgroundGlobeSaveState(statusCopy, statusClass);
  }

  function backgroundGlobeNativeApi() {
    const api = globalThis.pywebview?.api;
    return api && typeof api.set_companion_visibility === "function" ? api : null;
  }

  function setBackgroundGlobeMenuState(pressed, label, detail, pending = false) {
    elements.backgroundGlobeMenuToggle.setAttribute("aria-pressed", String(Boolean(pressed)));
    elements.backgroundGlobeMenuToggle.classList.toggle("pending", Boolean(pending));
    elements.backgroundGlobeMenuBadge.textContent = label;
    elements.backgroundGlobeMenuStatus.textContent = detail;
  }

  function setBackgroundGlobeSaveState(message, tone = "") {
    elements.backgroundGlobeSaveState.className = `globe-save-state${tone ? ` ${tone}` : ""}`;
    elements.backgroundGlobeSaveState.textContent = message;
  }

  function applyBackgroundGlobeBridgeResult(result, requestedVisible) {
    if (!state.remoteSettings?.desktop || !result || typeof result !== "object") return;
    const runtime = result.runtime && typeof result.runtime === "object" ? result.runtime : {};
    const desktop = state.remoteSettings.desktop;
    desktop.transparent_globe_enabled = typeof result.preference_enabled === "boolean"
      ? result.preference_enabled : requestedVisible;
    desktop.live_toggle_available = true;
    desktop.restart_required = false;
    desktop.runtime = {
      ...desktop.runtime,
      active: Boolean(runtime.active),
      mode: String(runtime.mode || desktop.runtime.mode || "inactive"),
      companion_available: Boolean(runtime.companion_available),
      companion_visible: Boolean(runtime.companion_visible),
      desktop_attached: Boolean(runtime.desktop_attached),
      true_alpha: Boolean(runtime.true_alpha),
      alpha_pixel_verified: Boolean(runtime.alpha_pixel_verified),
      non_activating: Boolean(runtime.non_activating),
      taskbar_hidden: Boolean(runtime.taskbar_hidden),
      hotkey_registered: Boolean(runtime.hotkey_registered),
      fallback_reason: String(runtime.fallback_reason || ""),
      updated_at: runtime.updated_at || null
    };
    renderDesktopGlobeSettings(desktop);
  }

  async function setBackgroundGlobeVisibility(requestedVisible) {
    if (state.backgroundGlobeBusy || !state.remoteSettings?.desktop) return false;
    const desktop = state.remoteSettings.desktop;
    if (!desktop.transparent_globe_supported) {
      toast("Background Globe is unsupported on this platform; browser preview remains available", "error");
      return false;
    }
    state.backgroundGlobeBusy = true;
    renderDesktopGlobeSettings(desktop);
    setBackgroundGlobeSaveState("Applying Background Globe preference…", "pending");
    const nativeApi = desktop.live_toggle_available ? backgroundGlobeNativeApi() : null;
    let liveFailure = "";
    try {
      if (nativeApi) {
        try {
          const result = await nativeApi.set_companion_visibility(Boolean(requestedVisible));
          if (result?.applied !== true) throw new Error("Native desktop did not apply the visibility change");
          applyBackgroundGlobeBridgeResult(result, Boolean(requestedVisible));
          toast(requestedVisible ? "Background Globe is live" : "Background Globe hidden", "success");
          addAudit(`Background Globe ${requestedVisible ? "shown" : "hidden"}`, "info", "NATIVE DESKTOP");
          return true;
        } catch (error) {
          liveFailure = normalizeCommunicationError(error);
        }
      }
      const payload = await fetchJson("/settings", {
        method: "PATCH",
        body: JSON.stringify({ desktop: { transparent_globe_enabled: Boolean(requestedVisible) } })
      }, 15000);
      applyRemoteSettingsPayload(payload);
      const message = requestedVisible
        ? "Background Globe saved for the next native desktop launch"
        : "Background Globe disabled for the next native desktop launch";
      toast(liveFailure ? `${message}; live change was unavailable` : message, liveFailure ? "info" : "success");
      addAudit(`Background Globe ${requestedVisible ? "enabled" : "disabled"}`, "info", "SAVED FOR DESKTOP LAUNCH");
      return true;
    } catch (error) {
      renderDesktopGlobeSettings(state.remoteSettings?.desktop || null);
      toast(`Background Globe was not changed: ${normalizeCommunicationError(error)}`, "error");
      return false;
    } finally {
      state.backgroundGlobeBusy = false;
      renderDesktopGlobeSettings(state.remoteSettings?.desktop || null);
    }
  }

  function toggleBackgroundGlobe() {
    const desktop = state.remoteSettings?.desktop;
    if (!desktop) return;
    const liveToggle = Boolean(desktop.live_toggle_available && backgroundGlobeNativeApi());
    const visible = Boolean(desktop.runtime?.companion_visible ?? desktop.runtime?.active);
    setBackgroundGlobeVisibility(liveToggle ? !visible : !desktop.transparent_globe_enabled);
  }

  function renderSharedRoots(roots) {
    const normalized = roots.map((root) => ({ ...root }));
    const brainIndex = normalized.findIndex((root) => normalizePath(root.path) === normalizePath(BRAIN_DRIVE_PATH));
    if (brainIndex < 0) {
      normalized.push({ path: BRAIN_DRIVE_PATH, available: false, permissions: "read", configured: false });
    }
    elements.sharedRootList.replaceChildren();
    normalized.forEach((root) => {
      const item = document.createElement("div");
      item.className = "shared-root-item";
      item.dataset.path = root.path;
      const enabled = document.createElement("input");
      enabled.className = "shared-root-toggle";
      enabled.type = "checkbox";
      enabled.checked = root.configured !== false;
      enabled.setAttribute("aria-label", `Share ${root.path}`);
      const copy = document.createElement("span");
      copy.className = "shared-root-copy";
      const title = document.createElement("strong");
      title.textContent = normalizePath(root.path) === normalizePath(BRAIN_DRIVE_PATH) ? "Brain Drive" : root.path;
      const path = document.createElement("small");
      path.textContent = root.path;
      copy.append(title, path);
      const badge = document.createElement("span");
      badge.className = `integration-status status-${root.available ? "ready" : "warning"}`;
      badge.textContent = root.available ? "AVAILABLE" : "UNAVAILABLE";
      const permission = document.createElement("select");
      permission.className = "shared-root-permission";
      permission.setAttribute("aria-label", `Permissions for ${root.path}`);
      const values = [...new Set([root.permissions, "read", "read_write"].filter(Boolean))];
      values.forEach((value) => permission.add(new Option(formatPermission(value), value)));
      permission.value = root.permissions;
      item.append(enabled, copy, badge, permission);
      elements.sharedRootList.append(item);
    });
  }

  function normalizePath(value) {
    return String(value || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  }

  function formatPermission(value) {
    const text = String(value || "read").replace(/[_-]+/g, " ");
    return text.replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function renderIntegrations(remoteIntegrations) {
    const byId = new Map(remoteIntegrations.map((integration) => [integration.id, integration]));
    const merged = INTEGRATION_CATALOG.map((catalog) => ({ ...catalog, ...(byId.get(catalog.id) || {}) }));
    remoteIntegrations.forEach((integration) => {
      if (!INTEGRATION_CATALOG.some((catalog) => catalog.id === integration.id)) merged.push(integration);
    });
    const angeronaCatalog = state.actionCatalog.find((action) => action.id === "angerona_launch" || action.id === "angerona");
    if (angeronaCatalog && !merged.some((integration) => integration.id === "angerona")) {
      merged.push({
        id: "angerona",
        name: "Angerona",
        icon: "AN",
        description: "Observed read-only over operator-enabled loopback MCP; operator-started only.",
        enabled: true,
        configured: angeronaCatalog.configured,
        status: angeronaCatalog.status || "observed",
        readonly: true
      });
    }
    if (state.angeronaStatus) {
      const status = state.angeronaStatus;
      let integration = merged.find((item) => String(item.id).toLowerCase() === "angerona");
      if (!integration) {
        integration = { id: "angerona", name: "Angerona", icon: "AN" };
        merged.push(integration);
      }
      Object.assign(integration, {
        enabled: status.installed,
        configured: status.tokenConfigured,
        status: status.mcpReady ? "active" : status.running ? "locked" : "offline",
        readonly: true,
        description: "Observed read-only over operator-enabled loopback MCP; operator-started only."
      });
    }
    merged.forEach((integration) => {
      if (String(integration.id).toLowerCase().includes("angerona")) {
        integration.readonly = true;
        integration.description = "Observed read-only over operator-enabled loopback MCP; operator-started only.";
      }
    });
    elements.integrationList.replaceChildren();
    merged.forEach((integration) => {
      const item = document.createElement("label");
      item.className = "integration-item";
      const toggle = document.createElement("input");
      toggle.className = "integration-toggle";
      toggle.type = "checkbox";
      toggle.checked = Boolean(integration.enabled);
      toggle.disabled = Boolean(integration.readonly);
      toggle.dataset.integrationId = integration.id;
      toggle.setAttribute("aria-label", integration.readonly ? `${integration.name} is read-only and operator-started` : `Enable ${integration.name}`);
      const icon = document.createElement("span");
      icon.className = "integration-icon";
      icon.textContent = integration.icon || String(integration.name).replace(/[^a-z0-9 ]/gi, "").split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
      const copy = document.createElement("span");
      copy.className = "integration-copy";
      const name = document.createElement("strong");
      name.textContent = integration.name;
      const description = document.createElement("small");
      description.textContent = integration.description || "Optional local integration";
      copy.append(name, description);
      const status = integrationStatus(integration);
      const badge = document.createElement("span");
      badge.className = `integration-status status-${status.tone}`;
      badge.textContent = status.label;
      item.append(toggle, icon, copy, badge);
      elements.integrationList.append(item);
    });
  }

  function integrationStatus(integration) {
    if (!integration.enabled) return { label: "DISABLED", tone: "disabled" };
    const raw = String(integration.status || "").toLowerCase();
    if (["ready", "online", "healthy", "connected", "active", "available"].includes(raw)) {
      return { label: raw.toUpperCase(), tone: "ready" };
    }
    if (["error", "offline", "failed", "unavailable"].includes(raw)) {
      return { label: raw.toUpperCase(), tone: "error" };
    }
    if (!integration.configured) return { label: "SETUP NEEDED", tone: "warning" };
    return { label: (raw || "CONFIGURED").toUpperCase(), tone: "warning" };
  }

  function setStatusBadge(element, label, tone) {
    element.className = `integration-status status-${tone}`;
    element.textContent = label;
  }

  function normalizeAngeronaStatus(payload) {
    const raw = payload && typeof payload === "object" ? payload : {};
    const status = ["active", "locked", "offline"].includes(String(raw.status).toLowerCase())
      ? String(raw.status).toLowerCase()
      : raw.mcp_ready ? "active" : raw.running ? "locked" : "offline";
    return {
      installed: raw.installed === true,
      running: raw.running === true,
      mcpReady: raw.mcp_ready === true,
      tokenConfigured: raw.token_configured === true,
      status,
      controlMode: raw.control_mode === "read_only_mcp" ? "read_only_mcp" : "unavailable",
      externalActionsSupported: raw.external_actions_supported === true,
      operatorStartRequired: raw.operator_start_required !== false,
      updatedAt: new Date()
    };
  }

  async function loadAngeronaStatus({ quiet = true } = {}) {
    if (state.angeronaStatusBusy) return false;
    state.angeronaStatusBusy = true;
    try {
      const payload = await fetchJson("/security/angerona", { cache: "no-store" }, 7000);
      state.angeronaStatus = normalizeAngeronaStatus(payload);
      renderAngeronaStatus();
      if (state.remoteSettings) renderIntegrations(state.remoteSettings.integrations || []);
      updateProjectSummaries();
      return true;
    } catch (error) {
      state.angeronaStatus = null;
      renderAngeronaStatusUnavailable();
      if (!quiet) toast(`Angerona status unavailable: ${normalizeCommunicationError(error)}`, "error");
      return false;
    } finally {
      state.angeronaStatusBusy = false;
    }
  }

  function renderAngeronaStatus() {
    const status = state.angeronaStatus;
    if (!status) return renderAngeronaStatusUnavailable();
    const label = !status.installed ? "NOT INSTALLED" : status.mcpReady ? "MCP READY" : status.running ? "LOCKED" : "OFFLINE";
    const tone = status.mcpReady ? "ready" : status.running ? "warning" : "disabled";
    setStatusBadge(elements.angeronaStatusBadge, label, tone);
    elements.angeronaInstalled.textContent = status.installed ? "Installed" : "Not installed";
    elements.angeronaRunning.textContent = status.running ? "Running" : "Not running";
    elements.angeronaMcp.textContent = status.mcpReady ? "Ready on loopback" : status.running ? "Unavailable / locked" : "Offline";
    elements.angeronaToken.textContent = status.tokenConfigured ? "Configured · value hidden" : "Not configured";
    elements.angeronaControlMode.textContent = "Observe / recommend / approved UIA";
    elements.angeronaExternalActions.textContent = status.externalActionsSupported ? "MCP mutation reported · blocked" : "Local target-locked approval only";
    elements.angeronaStatusDetail.textContent = !status.installed
      ? "Angerona is not installed at its configured local project path."
      : status.mcpReady
        ? "Operator-started loopback MCP is available for observation; exact UI input can be queued for local approval."
        : status.running && !status.tokenConfigured
          ? "Angerona is running, but its 32-byte-or-longer MCP token is not configured."
          : status.running
            ? "Angerona is running, but the read-only loopback MCP did not report ready."
            : `Angerona is installed but not running.${status.operatorStartRequired ? " The operator must start it locally." : ""}`;
    elements.loadAngeronaEvents.disabled = !status.mcpReady;
    elements.loadAngeronaEvents.title = status.mcpReady
      ? "Load sanitized recent alerts through read-only MCP"
      : "Recent alerts require the operator-started read-only MCP";
    elements.angeronaLastUpdated.textContent = `Status refreshed ${formatDateTime(status.updatedAt)} · No lifecycle controls exposed.`;
  }

  function renderAngeronaStatusUnavailable() {
    setStatusBadge(elements.angeronaStatusBadge, "UNAVAILABLE", "error");
    elements.angeronaInstalled.textContent = "Unknown";
    elements.angeronaRunning.textContent = "Unknown";
    elements.angeronaMcp.textContent = "Unavailable";
    elements.angeronaToken.textContent = "Hidden / unknown";
    elements.angeronaControlMode.textContent = "UI target verification required";
    elements.angeronaExternalActions.textContent = "Local approval required";
    elements.angeronaStatusDetail.textContent = "The local core did not provide Angerona status. No process or MCP readiness is being claimed.";
    elements.loadAngeronaEvents.disabled = true;
    elements.loadAngeronaEvents.title = "Angerona status must be available before alerts can load";
    elements.angeronaLastUpdated.textContent = "Angerona status is unavailable from the local core.";
  }

  function normalizeAngeronaEvent(raw, index) {
    const safe = raw && typeof raw === "object" ? raw : {};
    const numericTime = Number(safe.timestamp);
    const timestamp = Number.isFinite(numericTime)
      ? new Date(numericTime < 10_000_000_000 ? numericTime * 1000 : numericTime)
      : new Date(safe.timestamp || "");
    const severity = String(safe.severity || "unknown").toLowerCase().replace(/[^a-z]/g, "").slice(0, 12) || "unknown";
    const message = String(safe.message || "Alert detail was not provided").replace(/[\u0000-\u001f]+/g, " ").trim().slice(0, 1000);
    const moduleName = String(safe.module || "Angerona").replace(/[\u0000-\u001f]+/g, " ").trim().slice(0, 200);
    const mitreTags = Array.isArray(safe.mitre_tags)
      ? safe.mitre_tags.map(String).filter((tag) => /^T\d{4}(?:\.\d{3})?$/.test(tag)).slice(0, 8)
      : [];
    return {
      id: String(safe.id || `event-${index}`).slice(0, 100),
      timestamp: Number.isNaN(timestamp.getTime()) ? null : timestamp,
      severity,
      message,
      module: moduleName,
      mitreTags,
      integrityVerified: safe.integrity_verified === true
    };
  }

  async function loadAngeronaEvents() {
    if (!state.angeronaStatus?.mcpReady) {
      toast("Angerona alerts require its operator-started read-only MCP", "error");
      return false;
    }
    setBusy(elements.loadAngeronaEvents, true, "Loading…");
    elements.angeronaEventList.setAttribute("aria-busy", "true");
    try {
      const payload = await fetchJson("/security/angerona/events?limit=30", { cache: "no-store" }, 12000);
      state.angeronaEvents = (Array.isArray(payload?.events) ? payload.events : []).slice(0, 30).map(normalizeAngeronaEvent);
      state.angeronaEventsLoaded = true;
      state.angeronaEventsError = "";
      renderAngeronaEvents();
      toast(`Loaded ${state.angeronaEvents.length} sanitized Angerona alert${state.angeronaEvents.length === 1 ? "" : "s"}`, "info");
      return true;
    } catch (error) {
      state.angeronaEvents = [];
      state.angeronaEventsLoaded = false;
      state.angeronaEventsError = normalizeCommunicationError(error);
      renderAngeronaEvents();
      toast(`Angerona alerts unavailable: ${state.angeronaEventsError}`, "error");
      return false;
    } finally {
      elements.angeronaEventList.setAttribute("aria-busy", "false");
      setBusy(elements.loadAngeronaEvents, false);
      elements.loadAngeronaEvents.disabled = !state.angeronaStatus?.mcpReady;
    }
  }

  function renderAngeronaEvents() {
    elements.angeronaEventList.replaceChildren();
    if (state.angeronaEventsError) {
      const error = document.createElement("p");
      error.className = "empty-state";
      error.textContent = `Recent alerts are unavailable: ${state.angeronaEventsError}`;
      elements.angeronaEventList.append(error);
      return;
    }
    if (!state.angeronaEventsLoaded) {
      const idle = document.createElement("p");
      idle.className = "empty-state";
      idle.textContent = "Recent alerts load only when requested. No monitoring or message action is started from this HUD.";
      elements.angeronaEventList.append(idle);
      return;
    }
    if (!state.angeronaEvents.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Angerona returned no recent alerts. This does not prove the endpoint had no security activity.";
      elements.angeronaEventList.append(empty);
      return;
    }
    state.angeronaEvents.forEach((event) => {
      const item = document.createElement("article");
      item.className = "angerona-event";
      const severity = document.createElement("span");
      severity.className = `angerona-event-severity severity-${event.severity}`;
      severity.textContent = event.severity.toUpperCase();
      const copy = document.createElement("div");
      copy.className = "angerona-event-copy";
      const moduleName = document.createElement("strong");
      moduleName.textContent = event.module;
      const message = document.createElement("p");
      message.textContent = event.message;
      const meta = document.createElement("small");
      meta.textContent = [event.timestamp ? formatDateTime(event.timestamp) : "Time unavailable", ...event.mitreTags].join(" · ");
      copy.append(moduleName, message, meta);
      const integrity = document.createElement("span");
      integrity.className = `angerona-event-integrity${event.integrityVerified ? "" : " unverified"}`;
      integrity.textContent = event.integrityVerified ? "INTEGRITY VERIFIED" : "UNVERIFIED";
      item.append(severity, copy, integrity);
      elements.angeronaEventList.append(item);
    });
  }

  function finiteMetric(value) {
    if (typeof value === "boolean" || value == null) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function normalizeHealthMetric(raw, fallbackReason) {
    const source = raw && typeof raw === "object" ? raw : {};
    const value = finiteMetric(source.value);
    return {
      available: source.available === true && value != null,
      value,
      unit: String(source.unit || "").slice(0, 30),
      source: String(source.source || "").slice(0, 80),
      unavailableReason: String(source.unavailable_reason || fallbackReason).slice(0, 240)
    };
  }

  function normalizeSystemHealth(payload, alertsPayload) {
    const sample = payload?.sample && typeof payload.sample === "object" ? payload.sample : {};
    const metrics = sample.metrics && typeof sample.metrics === "object" ? sample.metrics : {};
    const rawSettings = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
    const rawThresholds = rawSettings.thresholds && typeof rawSettings.thresholds === "object" ? rawSettings.thresholds : {};
    const thresholds = {};
    ["cpu_percent", "memory_percent", "disk_percent", "temperature_celsius"].forEach((metric) => {
      const policy = rawThresholds[metric] && typeof rawThresholds[metric] === "object" ? rawThresholds[metric] : {};
      const value = finiteMetric(policy.threshold);
      thresholds[metric] = { enabled: policy.enabled !== false, threshold: value };
    });
    const temperature = normalizeHealthMetric(metrics.temperature_celsius, "Temperature sensors did not report a value");
    temperature.samples = Array.isArray(metrics.temperature_celsius?.samples)
      ? metrics.temperature_celsius.samples.filter((item) => item && typeof item === "object").slice(0, 64).map((item) => ({
        label: String(item.label || "Temperature sensor").replace(/[\u0000-\u001f]+/g, " ").slice(0, 200),
        current: finiteMetric(item.current_celsius),
        high: finiteMetric(item.high_celsius),
        critical: finiteMetric(item.critical_celsius)
      })).filter((item) => item.current != null)
      : [];
    const delivery = Array.isArray(payload?.delivery) ? payload.delivery
      .filter((route) => route && typeof route === "object" && ["web_app", "telegram", "discord", "signal"].includes(route.provider))
      .map((route) => ({
        provider: String(route.provider),
        supported: route.supported === true,
        channelKind: String(route.channel_kind || "").slice(0, 40),
        selected: route.selected === true,
        status: String(route.status || "unavailable").toLowerCase().slice(0, 40),
        detail: String(route.detail || "").replace(/[\u0000-\u001f]+/g, " ").slice(0, 240)
      })) : [];
    const selectedRoutes = Array.isArray(rawSettings.notification_routes)
      ? rawSettings.notification_routes.filter((route) => ["telegram", "discord"].includes(route))
      : [];
    const alertRows = Array.isArray(alertsPayload?.alerts) ? alertsPayload.alerts : [];
    return {
      status: ["normal", "warning", "partial", "monitor_disabled"].includes(payload?.status) ? payload.status : "unavailable",
      evaluation: String(payload?.evaluation || "").slice(0, 100),
      breachedMetrics: Array.isArray(payload?.breached_metrics)
        ? payload.breached_metrics.filter((metric) => ["cpu_percent", "memory_percent", "disk_percent", "temperature_celsius"].includes(metric))
        : [],
      metrics: {
        cpu: normalizeHealthMetric(metrics.cpu_percent, "CPU usage is unavailable"),
        memory: normalizeHealthMetric(metrics.memory_percent, "Memory usage is unavailable"),
        disk: normalizeHealthMetric(metrics.disk_percent, "JARVIS data-volume usage is unavailable"),
        uptime: normalizeHealthMetric(metrics.uptime_seconds, "System uptime is unavailable"),
        temperature
      },
      collectedAt: sample.collected_at || null,
      settings: {
        monitorEnabled: rawSettings.monitor_enabled === true,
        onlineNotificationsEnabled: rawSettings.online_notifications_enabled === true,
        intervalSeconds: Math.round(clamp(rawSettings.interval_seconds || 60, 10, 3600)),
        cooldownSeconds: Math.round(clamp(rawSettings.cooldown_seconds || 900, 60, 86400)),
        thresholds,
        notificationRoutes: [...new Set(selectedRoutes)]
      },
      delivery,
      alerts: alertRows.slice(0, 40).map(normalizeSystemHealthAlert),
      updatedAt: new Date()
    };
  }

  function normalizeSystemHealthAlert(raw, index) {
    const source = raw && typeof raw === "object" ? raw : {};
    const metric = ["cpu_percent", "memory_percent", "disk_percent", "temperature_celsius"].includes(source.metric)
      ? source.metric : "cpu_percent";
    const eventType = ["triggered", "ongoing", "recovered"].includes(source.event_type) ? source.event_type : "ongoing";
    const delivery = Array.isArray(source.delivery) ? source.delivery.filter((item) => item && typeof item === "object").slice(0, 4).map((item) => ({
      provider: ["web_app", "telegram", "discord", "signal"].includes(item.provider) ? String(item.provider) : "provider",
      status: String(item.status || "unknown").replace(/[^a-z_-]/gi, "").slice(0, 30)
    })) : [];
    return {
      id: String(source.id || `health-alert-${index}`).slice(0, 100),
      metric,
      eventType,
      severity: source.severity === "warning" ? "warning" : "info",
      value: finiteMetric(source.value),
      threshold: finiteMetric(source.threshold),
      createdAt: source.created_at || null,
      delivery
    };
  }

  function systemHealthMetricLabel(metric) {
    return {
      cpu_percent: "CPU usage",
      memory_percent: "Memory usage",
      disk_percent: "JARVIS data-volume usage",
      temperature_celsius: "Maximum reported temperature"
    }[metric] || "System metric";
  }

  function systemHealthMetricUnit(metric) {
    return metric === "temperature_celsius" ? "°C" : "%";
  }

  function updateSystemHealthMetric(name, metric, threshold, detail, meterScale = 100) {
    const card = elements[`systemHealth${name}Value`].closest(".system-health-metric");
    const valueNode = elements[`systemHealth${name}Value`];
    const detailNode = elements[`systemHealth${name}Detail`];
    const meter = elements[`systemHealth${name}Meter`];
    card.classList.remove("health-warning", "health-critical", "health-unavailable");
    if (!metric.available || metric.value == null) {
      card.classList.add("health-unavailable");
      valueNode.textContent = "Unavailable";
      detailNode.textContent = metric.unavailableReason;
      if (meter) meter.style.width = "0%";
      return;
    }
    const unit = metric.unit === "celsius" ? "°C" : metric.unit === "percent" ? "%" : "";
    valueNode.textContent = `${metric.value.toFixed(metric.value >= 10 ? 1 : 2)}${unit}`;
    const breached = threshold?.enabled && threshold.threshold != null && metric.value >= threshold.threshold;
    if (breached) card.classList.add("health-warning");
    const thresholdText = threshold?.enabled && threshold.threshold != null
      ? ` · warning at ${Number(threshold.threshold).toFixed(Number(threshold.threshold) % 1 ? 1 : 0)}${unit}`
      : " · threshold disabled";
    detailNode.textContent = `${detail}${thresholdText}`;
    if (meter) meter.style.width = `${clamp(metric.value / meterScale * 100)}%`;
  }

  function renderSystemHealth() {
    const health = state.systemHealth;
    if (!state.systemHealthLoaded || !health) return renderSystemHealthUnavailable();
    const statusLabel = health.status === "warning" ? "WARNING" : health.status === "partial" ? "PARTIAL" : health.status === "monitor_disabled" ? "MONITOR OFF" : "NORMAL";
    const statusTone = health.status === "warning" || health.status === "partial" ? "warning" : health.status === "normal" ? "ready" : "disabled";
    setStatusBadge(elements.systemHealthStatusBadge, statusLabel, statusTone);
    setTopHealthStatus(health.status, true);
    updateSystemHealthMetric("Cpu", health.metrics.cpu, health.settings.thresholds.cpu_percent, "Local processor load");
    updateSystemHealthMetric("Memory", health.metrics.memory, health.settings.thresholds.memory_percent, "Physical memory utilization");
    updateSystemHealthMetric("Temperature", health.metrics.temperature, health.settings.thresholds.temperature_celsius, health.metrics.temperature.source ? `Maximum reported sensor · ${formatPermission(health.metrics.temperature.source)}` : "Maximum reported sensor", 120);
    const uptimeCard = elements.systemHealthUptimeValue.closest(".system-health-metric");
    uptimeCard.classList.toggle("health-unavailable", !health.metrics.uptime.available);
    elements.systemHealthUptimeValue.textContent = health.metrics.uptime.available ? formatUptime(health.metrics.uptime.value) : "Unavailable";
    elements.systemHealthUptimeDetail.textContent = health.metrics.uptime.available ? "Host boot uptime reported by the OS" : health.metrics.uptime.unavailableReason;
    renderSystemHealthDisks(health);
    renderSystemHealthTemperatures(health.metrics.temperature);
    renderSystemHealthAlerts(health.alerts);
    renderSystemHealthPolicy(health);
    elements.systemHealthLastUpdated.textContent = `Sampled ${health.collectedAt ? formatDateTime(health.collectedAt) : formatDateTime(health.updatedAt)} · Available sensors only.`;
  }

  function setTopHealthStatus(status, available = true) {
    const dot = $(".status-dot", elements.healthStatus);
    const tone = !available || status === "unavailable" || status === "monitor_disabled"
      ? "status-dot--offline" : status === "warning" || status === "partial" ? "status-dot--warning" : "status-dot--online";
    dot.className = `status-dot ${tone}`;
    elements.healthStatus.lastChild.textContent = !available ? "UNAVAILABLE" : status === "warning" ? "WARNING" : status === "partial" ? "PARTIAL" : status === "monitor_disabled" ? "MONITOR OFF" : "NORMAL";
  }

  function renderSystemHealthUnavailable() {
    setStatusBadge(elements.systemHealthStatusBadge, "UNAVAILABLE", "error");
    setTopHealthStatus("unavailable", false);
    ["Cpu", "Memory", "Temperature"].forEach((name) => {
      const value = elements[`systemHealth${name}Value`];
      value.textContent = "Unavailable";
      value.closest(".system-health-metric").className = "system-health-metric health-unavailable";
      elements[`systemHealth${name}Meter`].style.width = "0%";
    });
    elements.systemHealthCpuDetail.textContent = "System health service is unavailable";
    elements.systemHealthMemoryDetail.textContent = "System health service is unavailable";
    elements.systemHealthTemperatureDetail.textContent = "No temperature availability is being inferred";
    elements.systemHealthUptimeValue.textContent = "Unavailable";
    elements.systemHealthUptimeDetail.textContent = "System health service is unavailable";
    elements.systemHealthDiskList.replaceChildren(Object.assign(document.createElement("p"), { className: "empty-state", textContent: "Disk telemetry is unavailable." }));
    elements.systemHealthTemperatureList.replaceChildren(Object.assign(document.createElement("p"), { className: "empty-state", textContent: "Temperature sensors are unavailable; no values are estimated." }));
    elements.systemHealthAlertList.replaceChildren(Object.assign(document.createElement("p"), { className: "empty-state", textContent: "Recent health alerts are unavailable." }));
    setStatusBadge(elements.systemHealthAlertCount, "UNAVAILABLE", "error");
    setSystemHealthPolicyEnabled(false);
    elements.systemHealthSettingsState.textContent = "Health policy is unavailable from the local core.";
    elements.systemHealthLastUpdated.textContent = "System health is unavailable from the local core.";
  }

  function renderSystemHealthDisks(health) {
    elements.systemHealthDiskList.replaceChildren();
    const disks = state.systemTelemetryDisks.length ? state.systemTelemetryDisks : health.metrics.disk.available ? [{
      path: "JARVIS data volume",
      percent: health.metrics.disk.value,
      totalBytes: null,
      usedBytes: null,
      freeBytes: null
    }] : [];
    if (!disks.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = health.metrics.disk.unavailableReason;
      elements.systemHealthDiskList.append(empty);
      return;
    }
    disks.forEach((disk) => {
      const item = document.createElement("div");
      item.className = "system-health-disk";
      const name = document.createElement("strong");
      name.textContent = disk.path;
      name.title = disk.path;
      const value = document.createElement("span");
      value.textContent = disk.percent == null ? "Unavailable" : `${disk.percent.toFixed(1)}% used`;
      const detail = document.createElement("small");
      detail.textContent = disk.totalBytes != null
        ? `${formatBytes(disk.usedBytes)} used · ${formatBytes(disk.freeBytes)} free · ${formatBytes(disk.totalBytes)} total`
        : "Threshold evaluation covers the JARVIS data volume.";
      item.append(name, value, detail);
      elements.systemHealthDiskList.append(item);
    });
  }

  function renderSystemHealthTemperatures(temperature) {
    elements.systemHealthTemperatureList.replaceChildren();
    if (!temperature.available || !temperature.samples.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = temperature.unavailableReason || "No supported temperature sensors reported.";
      elements.systemHealthTemperatureList.append(empty);
      return;
    }
    temperature.samples.forEach((sample) => {
      const item = document.createElement("div");
      item.className = "system-health-temperature";
      const name = document.createElement("strong");
      name.textContent = sample.label;
      const value = document.createElement("span");
      value.textContent = `${sample.current.toFixed(1)}°C`;
      const detail = document.createElement("small");
      detail.textContent = [sample.high != null ? `sensor high ${sample.high.toFixed(1)}°C` : "", sample.critical != null ? `sensor critical ${sample.critical.toFixed(1)}°C` : ""].filter(Boolean).join(" · ") || "No sensor-provided limits";
      item.append(name, value, detail);
      elements.systemHealthTemperatureList.append(item);
    });
  }

  function renderSystemHealthAlerts(alerts) {
    elements.systemHealthAlertList.replaceChildren();
    if (state.systemHealthAlertsError) {
      setStatusBadge(elements.systemHealthAlertCount, "UNAVAILABLE", "error");
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "Recent alerts could not be loaded. Live resource telemetry remains available.";
      elements.systemHealthAlertList.append(empty);
      return;
    }
    const warnings = alerts.filter((alert) => alert.severity === "warning");
    setStatusBadge(elements.systemHealthAlertCount, `${warnings.length} RECENT`, warnings.length ? "warning" : "ready");
    if (!alerts.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No recent threshold alerts were returned. This does not prove hardware health.";
      elements.systemHealthAlertList.append(empty);
      return;
    }
    alerts.forEach((alert) => {
      const item = document.createElement("article");
      item.className = "system-health-alert";
      const badge = document.createElement("span");
      setStatusBadge(badge, alert.eventType.toUpperCase(), alert.severity === "warning" ? "warning" : "ready");
      const copy = document.createElement("div");
      copy.className = "system-health-alert-copy";
      const title = document.createElement("strong");
      title.textContent = systemHealthMetricLabel(alert.metric);
      const detail = document.createElement("p");
      const unit = systemHealthMetricUnit(alert.metric);
      detail.textContent = alert.value != null && alert.threshold != null
        ? `${alert.value.toFixed(1)}${unit} observed · threshold ${alert.threshold.toFixed(1)}${unit}`
        : "Alert values were unavailable.";
      copy.append(title, detail);
      if (alert.delivery.length) {
        const delivery = document.createElement("small");
        delivery.textContent = `Delivery: ${alert.delivery.map((item) => `${formatPermission(item.provider)} ${formatPermission(item.status).toLowerCase()}`).join(" · ")}`;
        copy.append(delivery);
      }
      const time = document.createElement("time");
      time.dateTime = String(alert.createdAt || "");
      time.textContent = alert.createdAt ? relativeTime(alert.createdAt) : "Time unavailable";
      item.append(badge, copy, time);
      elements.systemHealthAlertList.append(item);
    });
  }

  function renderSystemHealthPolicy(health) {
    const settings = health.settings;
    elements.healthMonitorEnabled.checked = settings.monitorEnabled;
    elements.healthCpuThreshold.value = String(settings.thresholds.cpu_percent.threshold ?? 85);
    elements.healthMemoryThreshold.value = String(settings.thresholds.memory_percent.threshold ?? 85);
    elements.healthDiskThreshold.value = String(settings.thresholds.disk_percent.threshold ?? 90);
    elements.healthTemperatureThreshold.value = String(settings.thresholds.temperature_celsius.threshold ?? 85);
    elements.healthCooldownMinutes.value = String(Math.round(settings.cooldownSeconds / 60));
    elements.healthIntervalSeconds.value = String(settings.intervalSeconds);
    const onlineSelected = settings.onlineNotificationsEnabled;
    elements.healthOnlineNotificationsEnabled.checked = onlineSelected;
    renderSystemHealthRoutes(health.delivery, settings.notificationRoutes, onlineSelected);
    setSystemHealthPolicyEnabled(true);
    elements.systemHealthSettingsState.textContent = `${settings.monitorEnabled ? "Local monitoring enabled" : "Local monitoring off"} · ${onlineSelected ? "online notifications opted in" : "online notifications off"}.`;
  }

  function renderSystemHealthRoutes(routes, selected, onlineEnabled) {
    elements.healthNotificationRouteList.replaceChildren();
    if (!routes.length) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "No health-alert destinations were reported by the local core.";
      elements.healthNotificationRouteList.append(empty);
      return;
    }
    routes.forEach((route) => {
      const option = document.createElement("label");
      option.className = "system-health-route-option";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.healthRoute = route.provider;
      input.dataset.healthSupported = String(route.supported);
      const onlineRoute = ["telegram", "discord"].includes(route.provider);
      const localFeed = route.channelKind === "local_feed" || route.provider === "web_app";
      input.checked = localFeed || selected.includes(route.provider) || route.selected;
      input.disabled = localFeed || !route.supported || (onlineRoute && !onlineEnabled);
      input.setAttribute("aria-label", `Use ${formatPermission(route.provider)} for health alerts`);
      const label = document.createElement("span");
      label.textContent = localFeed ? "Web App local feed" : formatPermission(route.provider);
      const status = document.createElement("small");
      status.textContent = route.supported ? formatPermission(route.status) : "Not supported";
      option.title = route.detail;
      option.append(input, label, status);
      elements.healthNotificationRouteList.append(option);
    });
  }

  function setSystemHealthPolicyEnabled(enabled) {
    [elements.healthMonitorEnabled, elements.healthCpuThreshold, elements.healthMemoryThreshold,
      elements.healthDiskThreshold, elements.healthTemperatureThreshold, elements.healthCooldownMinutes,
      elements.healthIntervalSeconds, elements.healthOnlineNotificationsEnabled, elements.saveSystemHealthSettings]
      .forEach((control) => { control.disabled = !enabled; });
    elements.healthNotificationRouteList.disabled = !enabled;
  }

  function observeNewSystemHealthAlerts(alerts) {
    const newAlerts = alerts.filter((alert) => !state.systemHealthSeenAlerts.has(alert.id));
    alerts.forEach((alert) => state.systemHealthSeenAlerts.add(alert.id));
    if (!state.systemHealthAlertBaselineReady) {
      state.systemHealthAlertBaselineReady = true;
      return;
    }
    const warning = newAlerts.find((alert) => alert.severity === "warning" && alert.eventType !== "recovered");
    if (warning) {
      const unit = systemHealthMetricUnit(warning.metric);
      toast(`System health warning: ${systemHealthMetricLabel(warning.metric)}${warning.value == null ? "" : ` ${warning.value.toFixed(1)}${unit}`}`, "error");
    }
  }

  async function loadSystemHealth({ quiet = true } = {}) {
    if (state.systemHealthBusy) return false;
    state.systemHealthBusy = true;
    try {
      const [snapshotResult, alertsResult] = await Promise.allSettled([
        fetchJson(SYSTEM_HEALTH_API.snapshot, { cache: "no-store" }, 9000),
        fetchJson(SYSTEM_HEALTH_API.alerts, { cache: "no-store" }, 9000)
      ]);
      if (snapshotResult.status === "rejected") throw snapshotResult.reason;
      const snapshot = snapshotResult.value;
      const alerts = alertsResult.status === "fulfilled" ? alertsResult.value : { alerts: [] };
      state.systemHealthAlertsError = alertsResult.status === "rejected"
        ? normalizeCommunicationError(alertsResult.reason) : "";
      state.systemHealth = normalizeSystemHealth(snapshot || {}, alerts || {});
      state.systemHealthAlerts = state.systemHealth.alerts;
      state.systemHealthLoaded = true;
      if (!state.systemHealthAlertsError) observeNewSystemHealthAlerts(state.systemHealth.alerts);
      renderSystemHealth();
      return true;
    } catch (error) {
      state.systemHealth = null;
      state.systemHealthLoaded = false;
      state.systemHealthAlertsError = "";
      renderSystemHealthUnavailable();
      if (!quiet) toast(`System health unavailable: ${normalizeCommunicationError(error)}`, "error");
      return false;
    } finally {
      state.systemHealthBusy = false;
    }
  }

  function integerField(input, minimum, maximum, label) {
    const value = Number(input.value);
    if (!Number.isInteger(value) || value < minimum || value > maximum) throw new Error(`${label} must be between ${minimum} and ${maximum}`);
    return value;
  }

  async function saveSystemHealthPolicy() {
    if (!state.systemHealthLoaded || !state.systemHealth) return;
    let body;
    try {
      const cpu = integerField(elements.healthCpuThreshold, 1, 100, "CPU threshold");
      const memory = integerField(elements.healthMemoryThreshold, 1, 100, "Memory threshold");
      const disk = integerField(elements.healthDiskThreshold, 1, 100, "Disk threshold");
      const temperature = integerField(elements.healthTemperatureThreshold, 20, 120, "Temperature threshold");
      const cooldownMinutes = integerField(elements.healthCooldownMinutes, 1, 1440, "Cooldown");
      const interval = integerField(elements.healthIntervalSeconds, 10, 3600, "Sample interval");
      const routes = $$('[data-health-route]:checked', elements.healthNotificationRouteList)
        .map((input) => input.dataset.healthRoute)
        .filter((route) => ["telegram", "discord"].includes(route));
      body = {
        monitor_enabled: elements.healthMonitorEnabled.checked,
        online_notifications_enabled: elements.healthOnlineNotificationsEnabled.checked,
        interval_seconds: interval,
        cooldown_seconds: cooldownMinutes * 60,
        thresholds: {
          cpu_percent: { threshold: cpu },
          memory_percent: { threshold: memory },
          disk_percent: { threshold: disk },
          temperature_celsius: { threshold: temperature }
        },
        notification_routes: routes
      };
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    setBusy(elements.saveSystemHealthSettings, true, "Saving…");
    try {
      await fetchJson(SYSTEM_HEALTH_API.settings, { method: "PATCH", body: JSON.stringify(body) }, 12000);
      await loadSystemHealth({ quiet: true });
      toast("System health policy saved; no notification was sent", "success");
      addAudit("System health alert policy updated", "warning", "NO TEST MESSAGE SENT");
    } catch (error) {
      toast(`Health policy was not saved: ${normalizeCommunicationError(error)}`, "error");
    } finally {
      setBusy(elements.saveSystemHealthSettings, false);
      elements.saveSystemHealthSettings.disabled = !state.systemHealthLoaded;
    }
  }

  async function loadSecurityPosture({ quiet = true } = {}) {
    try {
      const payload = await fetchJson("/security/posture", { cache: "no-store" }, 10000);
      applySecurityPosture(payload || {});
      return true;
    } catch (error) {
      state.securityPosture = null;
      renderSecurityUnavailable();
      if (!quiet) toast(`Defense posture unavailable: ${error.message}`, "error");
      return false;
    }
  }

  function applySecurityPosture(payload) {
    const counters = payload.counters && typeof payload.counters === "object" ? payload.counters : {};
    const policies = payload.policies && typeof payload.policies === "object" ? payload.policies : {};
    state.securityPosture = {
      status: normalizeDefenseStatus(payload.status),
      layers: Array.isArray(payload.layers)
        ? payload.layers.filter((layer) => layer && typeof layer === "object").map((layer) => ({
          id: String(layer.id || ""),
          name: String(layer.name || ""),
          status: normalizeDefenseStatus(layer.status),
          detail: String(layer.detail || ""),
          metric: layer.metric == null ? "" : String(layer.metric)
        }))
        : [],
      flow: Array.isArray(payload.flow)
        ? payload.flow.filter((edge) => edge && typeof edge === "object").map((edge) => ({
          from: String(edge.from || ""),
          to: String(edge.to || ""),
          label: String(edge.label || "")
        }))
        : [],
      counters: {
        requests: safeCounter(counters.requests),
        allowed: safeCounter(counters.allowed),
        blocked: safeCounter(counters.blocked),
        suspicious: safeCounter(counters.suspicious),
        rate_limited: safeCounter(counters.rate_limited)
      },
      policies: {
        loopback_only: Boolean(policies.loopback_only),
        request_size_limit: safeCounter(policies.request_size_limit),
        rate_limit_per_minute: safeCounter(policies.rate_limit_per_minute),
        block_seconds: safeCounter(policies.block_seconds),
        ids_enabled: Boolean(policies.ids_enabled),
        ips_enabled: Boolean(policies.ips_enabled),
        os_firewall: typeof policies.os_firewall === "boolean"
          ? policies.os_firewall
          : String(policies.os_firewall || "unavailable")
      },
      updatedAt: new Date()
    };
    renderSecurityPosture();
  }

  function safeCounter(value) {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? Math.floor(number) : 0;
  }

  function normalizeDefenseStatus(value) {
    const status = String(value || "disabled").toLowerCase().replace(/[\s-]+/g, "_");
    if (["active", "ready", "healthy", "online", "secure", "enabled"].includes(status)) return "active";
    if (["locked", "enforced", "protected", "blocking"].includes(status)) return "locked";
    if (["degraded", "warning", "partial", "limited"].includes(status)) return "degraded";
    return "disabled";
  }

  function renderSecurityPosture() {
    const posture = state.securityPosture;
    if (!posture) return;
    setTopSecurityStatus(posture.status);
    setStatusBadge(elements.defenseStatusBadge, posture.status.toUpperCase(), defenseTone(posture.status));
    elements.defenseRequests.textContent = formatCounter(posture.counters.requests);
    elements.defenseAllowed.textContent = formatCounter(posture.counters.allowed);
    elements.defenseBlocked.textContent = formatCounter(posture.counters.blocked);
    elements.defenseSuspicious.textContent = formatCounter(posture.counters.suspicious);
    elements.defenseRateLimited.textContent = formatCounter(posture.counters.rate_limited);
    renderDefenseFlow(posture.layers, posture.flow);
    renderDefensePolicies(posture.policies);
    elements.defenseLastUpdated.textContent = `Last refreshed ${formatDateTime(posture.updatedAt)}`;
    updateProjectSummaries();
  }

  function renderSecurityUnavailable() {
    setTopSecurityStatus("disabled", false);
    setStatusBadge(elements.defenseStatusBadge, "UNAVAILABLE", "error");
    [elements.defenseRequests, elements.defenseAllowed, elements.defenseBlocked, elements.defenseSuspicious, elements.defenseRateLimited]
      .forEach((element) => { element.textContent = "0"; });
    renderDefenseFlow([], []);
    renderDefensePolicies(null);
    elements.defenseLastUpdated.textContent = "Defense posture is unavailable from the local core.";
    updateProjectSummaries();
  }

  function setTopSecurityStatus(status, available = true) {
    const dot = $(".status-dot", elements.securityStatus);
    const tone = !available || status === "disabled"
      ? "status-dot--offline"
      : status === "degraded" ? "status-dot--warning" : "status-dot--online";
    dot.className = `status-dot ${tone}`;
    elements.securityStatus.lastChild.textContent = available ? status.toUpperCase() : "UNAVAILABLE";
  }

  function defenseTone(status) {
    if (status === "active" || status === "locked") return "ready";
    if (status === "degraded") return "warning";
    return "disabled";
  }

  function formatCounter(value) {
    return Number(value || 0).toLocaleString();
  }

  function renderDefenseFlow(layers, edges) {
    elements.defenseFlow.replaceChildren();
    DEFENSE_STAGES.forEach((stage, index) => {
      const layer = findDefenseLayer(layers, stage);
      const status = layer?.status || "disabled";
      const node = document.createElement("article");
      node.className = `defense-node status-${status}`;
      node.setAttribute("role", "listitem");
      node.style.gridColumn = String(index < 5 ? index * 2 + 1 : 9 - (index - 5) * 2);
      node.style.gridRow = String(index < 5 ? 1 : 3);
      const step = document.createElement("span");
      step.className = "defense-step";
      step.textContent = `LAYER ${String(index + 1).padStart(2, "0")}`;
      const name = document.createElement("h4");
      name.textContent = stage.name;
      const detail = document.createElement("p");
      detail.textContent = layer?.detail || stage.detail;
      const footer = document.createElement("div");
      footer.className = "defense-node-footer";
      const badge = document.createElement("span");
      badge.className = `integration-status status-${defenseTone(status)}`;
      badge.textContent = status.toUpperCase();
      const metric = document.createElement("span");
      metric.className = "defense-node-metric";
      metric.textContent = layer?.metric || "—";
      footer.append(badge, metric);
      node.append(step, name, detail, footer);
      elements.defenseFlow.append(node);

      if (index < DEFENSE_STAGES.length - 1) {
        const next = DEFENSE_STAGES[index + 1];
        const edge = findDefenseEdge(edges, stage, next);
        const connector = document.createElement("div");
        connector.className = "defense-connector";
        connector.setAttribute("aria-label", edge?.label ? `Then ${edge.label}` : `Then ${next.name}`);
        if (index < 4) {
          connector.style.gridColumn = String(index * 2 + 2);
          connector.style.gridRow = "1";
        } else if (index === 4) {
          connector.classList.add("defense-connector--down");
          connector.style.gridColumn = "9";
          connector.style.gridRow = "2";
        } else {
          connector.classList.add("defense-connector--reverse");
          connector.style.gridColumn = String(8 - (index - 5) * 2);
          connector.style.gridRow = "3";
        }
        if (edge?.label) {
          const label = document.createElement("span");
          label.className = "defense-edge-label";
          label.textContent = edge.label;
          connector.append(label);
        }
        elements.defenseFlow.append(connector);
      }
    });
  }

  function normalizeDefenseId(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  }

  function stageMatches(value, stage) {
    const id = normalizeDefenseId(value);
    return [stage.id, stage.name, ...(stage.aliases || [])].some((alias) => normalizeDefenseId(alias) === id);
  }

  function findDefenseLayer(layers, stage) {
    return layers.find((layer) => stageMatches(layer.id, stage) || stageMatches(layer.name, stage));
  }

  function findDefenseEdge(edges, from, to) {
    return edges.find((edge) => stageMatches(edge.from, from) && stageMatches(edge.to, to));
  }

  function renderDefensePolicies(policies) {
    elements.defensePolicyList.replaceChildren();
    const items = policies ? [
      ["Loopback-only API", policies.loopback_only ? "ENFORCED" : "DISABLED", policies.loopback_only ? "ready" : "warning"],
      ["Request size limit", formatBytes(policies.request_size_limit), "neutral"],
      ["Rate limit", `${formatCounter(policies.rate_limit_per_minute)} / MIN`, "neutral"],
      ["Temporary block", `${formatCounter(policies.block_seconds)} SECONDS`, "neutral"],
      ["Intrusion detection", policies.ids_enabled ? "ENABLED" : "DISABLED", policies.ids_enabled ? "ready" : "warning"],
      ["Intrusion prevention", policies.ips_enabled ? "ENABLED" : "DISABLED", policies.ips_enabled ? "ready" : "warning"],
      ["OS firewall", typeof policies.os_firewall === "boolean" ? (policies.os_firewall ? "ACTIVE" : "DISABLED") : String(policies.os_firewall).toUpperCase(), policies.os_firewall ? "ready" : "warning"]
    ] : [
      ["Loopback-only API", "UNKNOWN", "warning"],
      ["Request limits", "UNKNOWN", "warning"],
      ["IDS / IPS", "UNKNOWN", "warning"],
      ["OS firewall", "UNKNOWN", "warning"]
    ];
    items.forEach(([label, value, tone]) => {
      const item = document.createElement("div");
      item.className = "defense-policy-item";
      const term = document.createElement("dt");
      term.textContent = label;
      const definition = document.createElement("dd");
      definition.textContent = value;
      if (tone === "ready") definition.style.color = "var(--success)";
      if (tone === "warning") definition.style.color = "var(--warning)";
      item.append(term, definition);
      elements.defensePolicyList.append(item);
    });
  }

  async function saveRemoteSettings(event) {
    event.preventDefault();
    if (!state.remoteSettings || state.settingsBusy) {
      toast("Settings are not available from the JARVIS core", "error");
      return;
    }
    const instanceName = elements.instanceNameInput.value.trim();
    if (!instanceName) {
      elements.instanceNameInput.focus();
      toast("Device name is required", "error");
      return;
    }
    let peerUrls;
    try {
      peerUrls = parsePeerUrls(elements.peerUrlsInput.value);
    } catch (error) {
      elements.peerUrlsInput.focus();
      toast(error.message, "error");
      return;
    }

    const sharedRoots = $$(".shared-root-item", elements.sharedRootList)
      .filter((item) => $(".shared-root-toggle", item).checked)
      .map((item) => ({
        path: item.dataset.path,
        permissions: $(".shared-root-permission", item).value
      }));
    const integrations = Object.fromEntries(
      $$(".integration-toggle", elements.integrationList)
        .filter((toggle) => !toggle.disabled)
        .map((toggle) => [toggle.dataset.integrationId, toggle.checked])
    );
    const globeWasVisible = Boolean(state.remoteSettings.desktop?.runtime?.companion_visible ?? state.remoteSettings.desktop?.runtime?.active);
    const request = {
      instance_name: instanceName,
      federation: {
        enabled: elements.federationEnabledToggle.checked,
        peer_urls: peerUrls,
        compute_enabled: elements.computeSharingToggle.checked,
        data_enabled: elements.dataSharingToggle.checked,
        admin_capable: elements.adminCapableToggle.checked && state.remoteSettings.federation.require_target_approval,
        shared_roots: sharedRoots
      },
      desktop: {
        adaptive_dashboard_enabled: elements.adaptiveDashboardToggle.checked,
        gesture_control_enabled: elements.gestureControlToggle.checked,
        gesture_low_power_mode: elements.gestureLowPowerToggle.checked,
        privacy_mode_enabled: elements.privacyModeToggle.checked,
        transparent_globe_enabled: elements.transparentGlobeToggle.checked,
        transparent_globe_size: clamp(
          elements.transparentGlobeSize.value,
          state.remoteSettings.desktop.transparent_globe_size_min || BACKGROUND_GLOBE_SIZE.minimum,
          state.remoteSettings.desktop.transparent_globe_size_max || BACKGROUND_GLOBE_SIZE.maximum
        ),
        transparent_globe_position: elements.transparentGlobePosition.value
      },
      integrations
    };

    state.settingsBusy = true;
    setBusy(elements.saveSettings, true, "Saving…");
    try {
      const payload = await fetchJson("/settings", {
        method: "PATCH",
        body: JSON.stringify(request)
      }, 20000);
      applyRemoteSettingsPayload(payload);
      const nativeApi = backgroundGlobeNativeApi();
      if (globeWasVisible && request.desktop.transparent_globe_enabled && nativeApi) {
        try {
          const result = await nativeApi.set_companion_visibility(true);
          if (result?.applied === true) applyBackgroundGlobeBridgeResult(result, true);
          else setBackgroundGlobeSaveState("Size and position were saved, but the visible companion did not reapply them live.", "pending");
        } catch (error) {
          setBackgroundGlobeSaveState(`Size and position were saved for the next launch; live reapply was unavailable · ${normalizeCommunicationError(error)}`, "pending");
        }
      }
      toast(payload?.restart_required ? "Settings saved — restart required" : "Settings saved", "success");
      addAudit("System settings updated", "warning", "TARGET APPROVAL PRESERVED");
      await loadNodes();
    } catch (error) {
      toast(`Settings were not changed: ${error.message}`, "error");
    } finally {
      state.settingsBusy = false;
      setBusy(elements.saveSettings, false);
    }
  }

  async function loadPhoneStatus({ quiet = true } = {}) {
    try {
      state.phoneStatus = await fetchJson("/phone/status", { cache: "no-store" }, 8000);
      const ready = Boolean(state.phoneStatus.ready);
      setStatusBadge(
        elements.phoneStatusBadge,
        ready ? "READY" : state.phoneStatus.enabled ? "SETUP REQUIRED" : "DISABLED",
        ready ? "ready" : state.phoneStatus.enabled ? "warning" : "disabled"
      );
      elements.phoneHttpsStatus.textContent = state.phoneStatus.https_configured ? "Configured" : "Not configured";
      elements.phoneAuthStatus.textContent = state.phoneStatus.caller_allowlist_configured && state.phoneStatus.pin_hash_configured
        ? "Caller allowlist + hashed PIN configured"
        : "Caller allowlist or hashed PIN missing";
      elements.phoneStatusDetail.textContent = ready
        ? "Signed inbound voice is ready. Calls cannot approve guarded actions and raw audio is not stored."
        : "Set the documented environment values and expose only /api/phone/* through an exact HTTPS reverse-proxy URL.";
      return true;
    } catch (error) {
      setStatusBadge(elements.phoneStatusBadge, "UNAVAILABLE", "error");
      elements.phoneHttpsStatus.textContent = "Unavailable";
      elements.phoneAuthStatus.textContent = "Unavailable";
      elements.phoneStatusDetail.textContent = "The local core did not provide phone-gateway status.";
      if (!quiet) toast(`Phone status unavailable: ${error.message}`, "error");
      return false;
    }
  }

  function setupSettings() {
    elements.apiBaseValue.textContent = API_BASE;
    elements.lowMotionToggle.checked = Boolean(state.settings.lowMotion);
    elements.compactTelemetryToggle.checked = Boolean(state.settings.compactTelemetry);
    document.documentElement.classList.toggle("low-motion", state.settings.lowMotion);
    elements.openSettings.addEventListener("click", () => {
      if (typeof elements.settingsDialog.showModal === "function") elements.settingsDialog.showModal();
      else elements.settingsDialog.setAttribute("open", "");
      loadRemoteSettings({ quiet: true });
      loadSecurityPosture({ quiet: true });
      loadCommunications({ quiet: true });
      loadAngeronaStatus({ quiet: true });
      loadPhoneStatus({ quiet: true });
      loadSystemHealth({ quiet: true });
    });
    const closeDialog = () => {
      if (typeof elements.settingsDialog.close === "function") elements.settingsDialog.close();
      else elements.settingsDialog.removeAttribute("open");
    };
    elements.closeSettings.addEventListener("click", closeDialog);
    elements.settingsCloseButton.addEventListener("click", closeDialog);
    elements.settingsForm.addEventListener("submit", saveRemoteSettings);
    elements.lowMotionToggle.addEventListener("change", () => {
      state.settings.lowMotion = elements.lowMotionToggle.checked;
      document.documentElement.classList.toggle("low-motion", state.settings.lowMotion);
      writeLocal(STORAGE.settings, state.settings);
      state.canvas?.renderOnce();
      state.globeCanvas?.renderOnce();
      state.globeVoice?.renderOnce();
    });
    elements.compactTelemetryToggle.addEventListener("change", () => {
      state.settings.compactTelemetry = elements.compactTelemetryToggle.checked;
      writeLocal(STORAGE.settings, state.settings);
      restartPollers();
    });
    elements.adaptiveDashboardToggle.addEventListener("change", () => {
      document.documentElement.classList.toggle(
        "adaptive-dashboard-disabled",
        !elements.adaptiveDashboardToggle.checked
      );
    });
    elements.gestureControlToggle.addEventListener("change", () => {
      if (!elements.gestureControlToggle.checked) stopGestureControl("settings");
    });
    elements.gestureLowPowerToggle.addEventListener("change", () => {
      state.gestureController?.setLowPower(elements.gestureLowPowerToggle.checked);
    });
    elements.refreshPhoneStatus.addEventListener("click", async () => {
      setBusy(elements.refreshPhoneStatus, true, "Refreshing…");
      await loadPhoneStatus({ quiet: false });
      setBusy(elements.refreshPhoneStatus, false);
    });
    elements.transparentGlobeSize.addEventListener("input", () => {
      const value = clamp(elements.transparentGlobeSize.value, Number(elements.transparentGlobeSize.min), Number(elements.transparentGlobeSize.max));
      elements.transparentGlobeSizeValue.value = `${value} px`;
      elements.transparentGlobeSizeValue.textContent = `${value} px`;
    });
    elements.transparentGlobeToggle.addEventListener("change", () => {
      setBackgroundGlobeVisibility(elements.transparentGlobeToggle.checked);
    });
    elements.backgroundGlobeQuickToggle.addEventListener("click", toggleBackgroundGlobe);
    window.addEventListener("pywebviewready", () => {
      if (state.remoteSettings?.desktop) renderDesktopGlobeSettings(state.remoteSettings.desktop);
    });
  }

  function setupGlobeView() {
    elements.openGlobeMode.addEventListener("click", () => enterGlobeMode(true));
    elements.globeDashboardButton.addEventListener("click", () => exitGlobeMode(true));
    elements.globeExitButton.addEventListener("click", () => exitGlobeMode(true));
    elements.globeSettingsButton.addEventListener("click", () => openGlobeSettings("interfaceSettings"));
    $$('[data-globe-target]', elements.globeView).forEach((button) => {
      button.addEventListener("click", () => navigateGlobeTarget(button.dataset.globeTarget));
    });
    elements.globeCommandInput.addEventListener("input", () => syncCommandDraft(elements.globeCommandInput.value, elements.globeCommandInput));
    elements.globeCommandInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        elements.globeCommandForm.requestSubmit();
      }
    });
    elements.globeCommandForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (state.chatBusy) {
        cancelSpeech();
        state.chatController?.abort("user");
        return;
      }
      syncCommandDraft(elements.globeCommandInput.value);
      elements.chatForm.requestSubmit();
    });
    setupGlobeCanvas();
    setupGlobeVoiceVisualization();
    if (new URLSearchParams(location.search).get("view") === "globe") enterGlobeMode(false);
  }

  function enterGlobeMode(updateUrl) {
    if (elements.settingsDialog.open) elements.settingsDialog.close?.();
    elements.globeView.hidden = false;
    document.documentElement.classList.add("globe-mode-root");
    document.body.classList.add("globe-mode");
    if (updateUrl) setGlobeQuery(true);
    syncCommandDraft(elements.chatInput.value);
    updateVoiceActivity(state.speaking ? "speaking" : state.voiceListening || state.voiceStarting ? "listening" : state.recognition ? "idle" : "unavailable");
    updateProjectSummaries();
    state.globeCanvas?.start();
    state.globeVoice?.start();
    requestAnimationFrame(() => $('[data-globe-target="projects"]', elements.globeView)?.focus({ preventScroll: true }));
  }

  function exitGlobeMode(updateUrl) {
    state.globeCanvas?.stop();
    state.globeVoice?.stop();
    elements.globeView.hidden = true;
    document.documentElement.classList.remove("globe-mode-root");
    document.body.classList.remove("globe-mode");
    if (updateUrl) setGlobeQuery(false);
    requestAnimationFrame(() => elements.openGlobeMode.focus({ preventScroll: true }));
  }

  function setGlobeQuery(enabled) {
    try {
      const url = new URL(location.href);
      if (enabled) url.searchParams.set("view", "globe");
      else url.searchParams.delete("view");
      history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    } catch {
      // The visual mode remains usable if a restricted wrapper blocks history changes.
    }
  }

  function openGlobeSettings(sectionId) {
    elements.openSettings.click();
    requestAnimationFrame(() => {
      const section = document.getElementById(sectionId);
      section?.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
      $("h3", section)?.focus({ preventScroll: true });
    });
  }

  function navigateGlobeTarget(target) {
    if (target === "projects") {
      exitGlobeMode(true);
      requestAnimationFrame(() => {
        elements.projectsPanel.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
        elements.newProjectButton.focus({ preventScroll: true });
      });
      return;
    }
    const sectionByTarget = {
      angerona: "defenseSettings",
      models: "connectionSettings",
      federation: "federationSettings"
    };
    if (sectionByTarget[target]) openGlobeSettings(sectionByTarget[target]);
  }

  function setupGlobeCanvas() {
    const canvas = elements.globeCanvas;
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;
    const particles = Array.from({ length: 46 }, (_, index) => ({
      angle: Math.random() * Math.PI * 2,
      radius: .18 + Math.random() * .28,
      speed: (Math.random() * .0001 + .00002) * (index % 2 ? 1 : -1),
      size: Math.random() * 1.5 + .4,
      phase: Math.random() * Math.PI * 2
    }));
    let width = 1;
    let height = 1;
    let dpr = 1;
    let frame = 0;
    let lastTime = performance.now();

    function resize() {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(2, devicePixelRatio || 1);
      width = Math.max(1, rect.width);
      height = Math.max(1, rect.height);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      render(performance.now());
    }

    function render(time) {
      cancelAnimationFrame(frame);
      context.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height / 2;
      const min = Math.min(width, height);
      const reduced = state.settings.lowMotion || matchMedia("(prefers-reduced-motion: reduce)").matches;
      const elapsed = reduced ? 0 : time;
      const load = state.telemetry.coreLoad / 100;
      drawBackdrop(context, cx, cy, min, elapsed, state.online);
      drawNetwork(context, particles, cx, cy, min, elapsed, state.online);
      drawGlobeOrbits(context, cx, cy, min, elapsed);
      drawOrb(context, cx, cy, min * (.095 + load * .006), elapsed, load, state.online);
      lastTime = time;
      if (!reduced && !document.hidden && document.body.classList.contains("globe-mode")) {
        frame = requestAnimationFrame(render);
      }
    }

    function start() {
      resize();
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(render);
    }

    function stop() {
      cancelAnimationFrame(frame);
      frame = 0;
    }

    const observer = new ResizeObserver(() => {
      if (!elements.globeView.hidden) resize();
    });
    observer.observe(canvas);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else if (document.body.classList.contains("globe-mode")) start();
    });
    state.globeCanvas = { start, stop, renderOnce: () => render(lastTime), observer };
  }

  function setupGlobeVoiceVisualization() {
    const jarvisCanvas = elements.globeJarvisWaveform;
    const userCanvas = elements.globeUserWaveform;
    const jarvisContext = jarvisCanvas.getContext("2d", { alpha: true });
    const userContext = userCanvas.getContext("2d", { alpha: true });
    if (!jarvisContext || !userContext) return;
    let frame = 0;
    let lastTime = performance.now();
    let jarvisSize = { width: 1, height: 1 };
    let userSize = { width: 1, height: 1 };

    function sizeCanvas(canvas, context) {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(2, devicePixelRatio || 1);
      const size = { width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
      canvas.width = Math.round(size.width * dpr);
      canvas.height = Math.round(size.height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      return size;
    }

    function resize() {
      jarvisSize = sizeCanvas(jarvisCanvas, jarvisContext);
      userSize = sizeCanvas(userCanvas, userContext);
      render(performance.now());
    }

    function render(time) {
      cancelAnimationFrame(frame);
      const reduced = state.settings.lowMotion || matchMedia("(prefers-reduced-motion: reduce)").matches;
      drawJarvisVoiceWave(jarvisContext, jarvisSize, reduced ? 0 : time);
      drawUserVoiceWave(userContext, userSize);
      lastTime = time;
      const active = state.speaking || state.voiceListening || state.voiceStarting;
      if (active && !reduced && !document.hidden && document.body.classList.contains("globe-mode")) {
        frame = requestAnimationFrame(render);
      }
    }

    function start() {
      resize();
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(render);
    }

    function stop() {
      cancelAnimationFrame(frame);
      frame = 0;
    }

    const observer = new ResizeObserver(() => {
      if (!elements.globeView.hidden) resize();
    });
    observer.observe(elements.globeVoiceDisplay);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else if (document.body.classList.contains("globe-mode")) start();
    });
    state.globeVoice = { start, stop, renderOnce: () => render(lastTime), observer };
    render(lastTime);
  }

  function drawJarvisVoiceWave(context, size, time) {
    const { width, height } = size;
    context.clearRect(0, 0, width, height);
    const center = height / 2;
    const boundaryBoost = state.speaking ? Math.max(0, 1 - (performance.now() - state.speechPulseAt) / 320) : 0;
    const amplitude = state.speaking ? .38 + boundaryBoost * .28 : .025;
    const phase = time * .008;
    context.save();
    context.strokeStyle = state.speaking ? "rgba(97,230,255,.94)" : "rgba(97,230,255,.28)";
    context.lineWidth = state.speaking ? 1.65 : 1;
    context.shadowColor = "#61e6ff";
    context.shadowBlur = state.speaking ? 10 : 2;
    context.beginPath();
    for (let pixel = 0; pixel <= width; pixel += 2) {
      const position = pixel / width;
      const envelope = Math.pow(Math.sin(Math.PI * position), .72);
      const carrier = Math.sin(position * Math.PI * 18 + phase)
        + .46 * Math.sin(position * Math.PI * 31 - phase * .73)
        + .22 * Math.sin(position * Math.PI * 53 + phase * 1.32);
      const y = center + carrier * height * amplitude * envelope * .26;
      if (!pixel) context.moveTo(pixel, y);
      else context.lineTo(pixel, y);
    }
    context.stroke();
    context.restore();
  }

  function drawUserVoiceWave(context, size) {
    const { width, height } = size;
    context.clearRect(0, 0, width, height);
    const center = height / 2;
    const analyser = state.micAnalyser;
    const samples = analyser ? new Uint8Array(analyser.fftSize) : null;
    if (samples) analyser.getByteTimeDomainData(samples);
    context.save();
    context.strokeStyle = state.voiceListening || state.voiceStarting ? "rgba(105,245,189,.9)" : "rgba(105,245,189,.24)";
    context.lineWidth = state.voiceListening || state.voiceStarting ? 1.35 : 1;
    context.shadowColor = "#69f5bd";
    context.shadowBlur = analyser ? 7 : 1;
    context.beginPath();
    for (let pixel = 0; pixel <= width; pixel += 2) {
      const position = pixel / width;
      const sampleIndex = samples ? Math.min(samples.length - 1, Math.floor(position * samples.length)) : 0;
      const level = samples ? (samples[sampleIndex] - 128) / 128 : 0;
      const envelope = Math.pow(Math.sin(Math.PI * position), .58);
      const y = center + level * height * .72 * envelope;
      if (!pixel) context.moveTo(pixel, y);
      else context.lineTo(pixel, y);
    }
    context.stroke();
    context.restore();
  }

  function drawGlobeOrbits(ctx, cx, cy, min, time) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = state.online ? "rgba(97,230,255,.18)" : "rgba(131,164,179,.09)";
    ctx.lineWidth = 1;
    [-.52, .52].forEach((rotation, index) => {
      ctx.setLineDash(index ? [2, 10] : [5, 8]);
      ctx.lineDashOffset = time * (index ? -.006 : .006);
      ctx.beginPath();
      ctx.ellipse(0, 0, min * .42, min * .26, rotation, 0, Math.PI * 2);
      ctx.stroke();
    });
    ctx.setLineDash([]);
    ctx.restore();
  }

  function setupCanvas() {
    const canvas = elements.coreCanvas;
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;
    const particles = Array.from({ length: 54 }, (_, index) => ({
      angle: Math.random() * Math.PI * 2,
      radius: .19 + Math.random() * .3,
      speed: (Math.random() * .00012 + .000025) * (index % 2 ? 1 : -1),
      size: Math.random() * 1.7 + .45,
      phase: Math.random() * Math.PI * 2
    }));
    let width = 0;
    let height = 0;
    let dpr = 1;
    let animationId = 0;
    let lastTime = performance.now();

    function resize() {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(2, devicePixelRatio || 1);
      width = Math.max(1, rect.width);
      height = Math.max(1, rect.height);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      render(performance.now());
    }

    function render(time) {
      context.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height / 2;
      const min = Math.min(width, height);
      const reduced = state.settings.lowMotion || matchMedia("(prefers-reduced-motion: reduce)").matches;
      const elapsed = reduced ? 0 : time;
      const load = state.telemetry.coreLoad / 100;
      const orbRadius = min * (.14 + load * .008);

      drawBackdrop(context, cx, cy, min, elapsed, state.online);
      drawNetwork(context, particles, cx, cy, min, elapsed, state.online);
      drawOrb(context, cx, cy, orbRadius, elapsed, load, state.online);

      if (!reduced && !document.hidden) animationId = requestAnimationFrame(render);
      lastTime = time;
    }

    function start() {
      cancelAnimationFrame(animationId);
      lastTime = performance.now();
      animationId = requestAnimationFrame(render);
    }

    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    document.addEventListener("visibilitychange", () => document.hidden ? cancelAnimationFrame(animationId) : start());
    matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", start);
    state.canvas = { start, renderOnce: () => render(lastTime), observer };
    resize();
    start();
  }

  function drawBackdrop(ctx, cx, cy, min, time, online) {
    ctx.save();
    ctx.translate(cx, cy);
    ctx.strokeStyle = online ? "rgba(76, 216, 248, .12)" : "rgba(120, 154, 166, .08)";
    ctx.lineWidth = 1;
    [0.22, 0.32, 0.43].forEach((scale, index) => {
      const radius = min * scale;
      ctx.beginPath();
      ctx.setLineDash(index === 1 ? [4, 9] : index === 2 ? [1, 12] : [2, 4]);
      ctx.lineDashOffset = (index % 2 ? 1 : -1) * time * .004;
      ctx.arc(0, 0, radius, 0, Math.PI * 2);
      ctx.stroke();
    });
    ctx.setLineDash([]);
    for (let spoke = 0; spoke < 12; spoke += 1) {
      const angle = spoke * Math.PI / 6 + time * .00002;
      ctx.beginPath();
      ctx.moveTo(Math.cos(angle) * min * .19, Math.sin(angle) * min * .19);
      ctx.lineTo(Math.cos(angle) * min * .42, Math.sin(angle) * min * .42);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawNetwork(ctx, particles, cx, cy, min, time, online) {
    const points = particles.map((particle) => {
      const drift = Math.sin(time * .0005 + particle.phase) * min * .012;
      const angle = particle.angle + time * particle.speed;
      const radius = min * particle.radius + drift;
      return { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius, size: particle.size };
    });
    ctx.save();
    for (let i = 0; i < points.length; i += 1) {
      for (let j = i + 1; j < points.length; j += 1) {
        const dx = points[i].x - points[j].x;
        const dy = points[i].y - points[j].y;
        const distance = Math.hypot(dx, dy);
        const threshold = min * .14;
        if (distance < threshold) {
          ctx.strokeStyle = online ? `rgba(79, 213, 244, ${(.11 * (1 - distance / threshold)).toFixed(3)})` : "rgba(108, 137, 148, .025)";
          ctx.lineWidth = .7;
          ctx.beginPath();
          ctx.moveTo(points[i].x, points[i].y);
          ctx.lineTo(points[j].x, points[j].y);
          ctx.stroke();
        }
      }
    }
    points.forEach((point, index) => {
      const pulse = .65 + Math.sin(time * .002 + index) * .35;
      ctx.fillStyle = online ? `rgba(112, 235, 255, ${.45 + pulse * .45})` : "rgba(120, 150, 160, .22)";
      ctx.shadowColor = online ? "#61e6ff" : "transparent";
      ctx.shadowBlur = online ? 5 : 0;
      ctx.beginPath();
      ctx.arc(point.x, point.y, point.size, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.restore();
  }

  function drawOrb(ctx, cx, cy, radius, time, load, online) {
    ctx.save();
    const glow = ctx.createRadialGradient(cx, cy, radius * .05, cx, cy, radius * 1.55);
    if (online) {
      glow.addColorStop(0, `rgba(210, 253, 255, ${.3 + load * .3})`);
      glow.addColorStop(.35, "rgba(64, 208, 240, .20)");
      glow.addColorStop(.72, "rgba(29, 133, 173, .08)");
      glow.addColorStop(1, "rgba(0, 0, 0, 0)");
    } else {
      glow.addColorStop(0, "rgba(131, 164, 179, .13)");
      glow.addColorStop(1, "rgba(0, 0, 0, 0)");
    }
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.55, 0, Math.PI * 2);
    ctx.fill();

    for (let ring = 0; ring < 4; ring += 1) {
      ctx.strokeStyle = online ? `rgba(113, 236, 255, ${.55 - ring * .1})` : `rgba(131, 164, 179, ${.18 - ring * .03})`;
      ctx.lineWidth = ring === 0 ? 1.4 : .8;
      ctx.setLineDash(ring % 2 ? [5, 8] : [2, 4]);
      ctx.lineDashOffset = time * .012 * (ring % 2 ? 1 : -1);
      ctx.beginPath();
      ctx.ellipse(cx, cy, radius * (1 + ring * .14), radius * (.34 + ring * .1), time * .00015 + ring * .55, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.strokeStyle = online ? "rgba(127, 241, 255, .56)" : "rgba(131, 164, 179, .2)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * .8, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  function restartPollers() {
    state.timers.forEach(clearInterval);
    state.timers.length = 0;
    const telemetryInterval = state.settings.compactTelemetry ? 15000 : 5000;
    state.timers.push(setInterval(() => checkStatus({ quiet: true }), 12000));
    state.timers.push(setInterval(loadTelemetry, telemetryInterval));
    state.timers.push(setInterval(() => {
      loadApprovals();
      loadNodes();
    }, 20000));
    state.timers.push(setInterval(loadSecurityPosture, 15000));
    state.timers.push(setInterval(() => loadSystemHealth({ quiet: true }), 30000));
    state.timers.push(setInterval(() => {
      if (elements.settingsDialog.open || document.body.classList.contains("globe-mode")) loadAngeronaStatus({ quiet: true });
    }, 30000));
    state.timers.push(setInterval(() => {
      if (elements.settingsDialog.open && !elements.communicationSetupDialog.open) loadCommunications({ quiet: true });
    }, 30000));
  }

  function cacheElements() {
    [
      "offlineBanner", "retryConnection", "coreStatus", "healthStatus", "modelStatus", "latencyStatus", "openSettings",
      "commandPanel", "skillsPanel", "operationsPanel", "clearChat", "chatLog", "chatForm", "chatInput",
      "chatSkill", "sendMessage", "skillGrid", "skillCount", "coreModeBadge", "coreCanvas", "orbLoad",
      "coreVisualDescription", "uptimeMetric", "contextMetric", "tokenMetric", "queueMetric", "cpuValue",
      "cpuMeter", "cpuDetail", "memoryValue", "memoryMeter", "memoryDetail", "gpuValue", "gpuMeter",
      "gpuDetail", "nodeList", "nodeSummary", "taskForm", "taskTitle", "taskPriority", "taskDueAt", "taskList",
      "taskCount", "memorySearchForm", "memorySearch", "memoryResults", "memoryIngestForm", "memoryPane",
      "approvalList", "approvalCount", "approvalPane", "refreshAudit", "auditList", "toastRegion",
      "settingsDialog", "settingsTitle", "apiBaseValue", "dialogCoreHealth", "dialogModel", "lastContact",
      "systemHealthStatusBadge", "refreshSystemHealth", "systemHealthOverview", "systemHealthCpuValue",
      "systemHealthCpuMeter", "systemHealthCpuDetail", "systemHealthMemoryValue", "systemHealthMemoryMeter",
      "systemHealthMemoryDetail", "systemHealthTemperatureValue", "systemHealthTemperatureMeter",
      "systemHealthTemperatureDetail", "systemHealthUptimeValue", "systemHealthUptimeDetail",
      "systemHealthDiskList", "systemHealthTemperatureList", "systemHealthAlertCount", "systemHealthAlertList",
      "healthMonitorEnabled", "healthCpuThreshold", "healthMemoryThreshold", "healthDiskThreshold",
      "healthTemperatureThreshold", "healthCooldownMinutes", "healthIntervalSeconds",
      "healthOnlineNotificationsEnabled", "healthNotificationRouteList", "systemHealthSettingsState",
      "saveSystemHealthSettings", "systemHealthLastUpdated",
      "lowMotionToggle", "compactTelemetryToggle", "privacyModeToggle", "dialogRetry", "messageTemplate", "settingsForm",
      "closeSettings", "settingsCloseButton", "saveSettings", "restartRequiredNotice", "instanceNameInput",
      "instanceIdValue", "instancePlatformValue", "federationTransportValue", "signingKeyStatus",
      "federationStatusBadge", "federationEnabledToggle", "peerUrlsInput", "computeSharingToggle",
      "dataSharingToggle", "sharedRootList", "adminCapableToggle", "targetApprovalStatus", "integrationList",
      "communicationsStatusBadge", "refreshCommunications", "communicationsOverview", "communicationProviderList",
      "saveCommunicationOrder", "communicationFailoverEnabled", "communicationRouteList", "communicationsLastUpdated",
      "communicationSetupDialog", "communicationSetupForm", "communicationSetupTitle", "closeCommunicationSetup",
      "communicationSetupIcon", "communicationSetupProvider", "communicationSetupDescription", "communicationSetupBadge",
      "communicationPrerequisiteList", "communicationSetupFields", "communicationProviderEnabled",
      "communicationSetupResult", "cancelCommunicationSetup", "testCommunicationProvider", "saveCommunicationProvider",
      "securityStatus", "defenseStatusBadge", "refreshDefense", "defenseRequests", "defenseAllowed",
      "defenseBlocked", "defenseSuspicious", "defenseRateLimited", "defenseFlow", "defensePolicyList",
      "defenseLastUpdated", "angeronaStatusDetail", "angeronaStatusBadge", "angeronaInstalled",
      "angeronaRunning", "angeronaMcp", "angeronaToken", "angeronaControlMode", "angeronaExternalActions",
      "refreshAngerona", "loadAngeronaEvents", "goToAngeronaIntegration", "goToDefenseFlow",
      "angeronaEventList", "angeronaLastUpdated", "micButton", "voiceActivity", "speakRepliesToggle", "speakRepliesHelp",
      "actionsTab", "actionsPane", "actionForm", "actionSelect", "actionFields", "actionRiskPreview",
      "queueAction", "actionResult", "refreshActionCatalog", "projectsPanel", "projectsSourceStatus",
      "projectsCountBadge", "newProjectButton", "refreshProjects", "userProjectGrid", "projectPlanningMetric",
      "projectPlanningStatus", "projectKnowledgeMetric", "projectKnowledgeStatus", "projectOperationsMetric",
      "projectOperationsStatus", "projectFederationMetric", "projectFederationStatus", "projectDialog",
      "projectForm", "projectDialogTitle", "closeProjectDialog", "cancelProjectEdit", "saveProject",
      "projectTitleInput", "projectStatusInput", "projectProgressInput", "projectPriorityInput",
      "projectAccentInput", "projectNextStepInput", "projectNotesInput", "projectTagsInput", "projectPinnedInput",
      "jarvisPresence", "jarvisStateLabel", "contextLaunchers", "jarvisContextPanel",
      "contextPanelTitle", "contextPanelBody", "pinContextPanel", "closeContextPanel",
      "gestureButton", "emergencyStop", "adaptiveDashboardToggle", "gestureControlToggle",
      "gestureLowPowerToggle",
      "openCommandPalette", "commandPaletteDialog", "commandPaletteTitle", "closeCommandPalette",
      "commandPaletteInput", "commandPaletteResults",
      "phoneStatusBadge", "refreshPhoneStatus", "phoneHttpsStatus", "phoneAuthStatus",
      "phoneStatusDetail",
      "openGlobeMode", "globeView", "globeCanvas", "globeCoreLoad", "globeProjectsMetric",
      "globeAngeronaMetric", "globeModelMetric", "globeFederationMetric", "globeDashboardButton",
      "globeSettingsButton", "globeExitButton", "globeSupportText", "globeRuntimeBadge",
      "backgroundGlobeMenuToggle", "backgroundGlobeMenuBadge", "backgroundGlobeMenuStatus",
      "backgroundGlobeQuickToggle", "backgroundGlobeSaveState",
      "transparentGlobeToggle", "transparentGlobeSize", "transparentGlobeSizeValue",
      "transparentGlobePosition", "globeRuntimeMode", "globeReturnHotkey", "globeInteractionNote",
      "globeVoiceDisplay", "globeJarvisWaveform", "globeUserWaveform", "globeJarvisVoiceStatus",
      "globeMicStatus", "globeVoicePrivacy", "globeCommandForm", "globeCommandInput",
      "globeMicButton", "globeSendButton", "globeCommandHint"
    ].forEach((id) => { elements[id] = document.getElementById(id); });
  }

  function bindEvents() {
    elements.retryConnection.addEventListener("click", async () => {
      const success = await checkStatus();
      if (success) {
        toast("JARVIS core connection restored", "success");
        await Promise.allSettled([
          loadTelemetry(), loadSkills(), loadApprovals(), loadNodes(), loadAudit(), loadSecurityPosture(),
          loadAngeronaStatus({ quiet: true }), loadActionCatalog(), loadProjects({ quiet: true }),
          loadCommunications({ quiet: true }), loadSystemHealth({ quiet: true }), loadPhoneStatus({ quiet: true })
        ]);
      }
    });
    elements.dialogRetry.addEventListener("click", async () => {
      setBusy(elements.dialogRetry, true, "Testing…");
      const success = await checkStatus();
      if (success) await Promise.allSettled([
        loadRemoteSettings({ quiet: true }), loadSecurityPosture({ quiet: true }), loadActionCatalog({ quiet: true }),
        loadCommunications({ quiet: true }), loadAngeronaStatus({ quiet: true }), loadSystemHealth({ quiet: true }),
        loadPhoneStatus({ quiet: true })
      ]);
      setBusy(elements.dialogRetry, false);
      toast(success ? "Core connection is healthy" : "Core is still unreachable", success ? "success" : "error");
    });
    elements.chatForm.addEventListener("submit", handleChatSubmit);
    elements.chatInput.addEventListener("input", () => syncCommandDraft(elements.chatInput.value, elements.chatInput));
    elements.chatInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        elements.chatForm.requestSubmit();
      }
    });
    elements.sendMessage.addEventListener("click", (event) => {
      if (state.chatBusy && state.chatController) {
        event.preventDefault();
        cancelSpeech();
        state.chatController.abort("user");
      }
    });
    elements.clearChat.addEventListener("click", () => {
      cancelSpeech();
      stopListening();
      elements.chatLog.replaceChildren();
      addMessage("jarvis", "Command channel cleared. Local audit history was preserved.");
      addAudit("Command channel cleared", "info");
    });
    elements.taskForm.addEventListener("submit", createTask);
    elements.memorySearchForm.addEventListener("submit", searchMemory);
    elements.memoryIngestForm.addEventListener("submit", ingestMemory);
    elements.refreshAudit.addEventListener("click", () => loadAudit({ quiet: false }));
    elements.refreshDefense.addEventListener("click", async () => {
      setBusy(elements.refreshDefense, true, "Refreshing…");
      await loadSecurityPosture({ quiet: false });
      setBusy(elements.refreshDefense, false);
    });
    elements.refreshSystemHealth.addEventListener("click", async () => {
      setBusy(elements.refreshSystemHealth, true, "Refreshing…");
      await loadSystemHealth({ quiet: false });
      setBusy(elements.refreshSystemHealth, false);
    });
    elements.saveSystemHealthSettings.addEventListener("click", saveSystemHealthPolicy);
    elements.healthOnlineNotificationsEnabled.addEventListener("change", () => {
      const onlineEnabled = elements.healthOnlineNotificationsEnabled.checked;
      $$('[data-health-route]', elements.healthNotificationRouteList).forEach((input) => {
        if (["telegram", "discord"].includes(input.dataset.healthRoute)) {
          input.disabled = input.dataset.healthSupported !== "true" || !onlineEnabled;
        }
      });
      elements.systemHealthSettingsState.textContent = onlineEnabled
        ? "Online notifications opted in; select only configured providers."
        : "Online notifications off; local monitoring and the Web App feed are unchanged.";
    });
    document.getElementById("systemHealthSettings").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.matches("input[type='number']")) {
        event.preventDefault();
        saveSystemHealthPolicy();
      }
    });
    elements.refreshAngerona.addEventListener("click", async () => {
      setBusy(elements.refreshAngerona, true, "Refreshing…");
      await loadAngeronaStatus({ quiet: false });
      setBusy(elements.refreshAngerona, false);
    });
    elements.loadAngeronaEvents.addEventListener("click", loadAngeronaEvents);
    elements.goToAngeronaIntegration.addEventListener("click", () => {
      const section = document.getElementById("integrationSettings");
      section?.scrollIntoView({ block: "start", behavior: state.settings.lowMotion ? "auto" : "smooth" });
      const heading = $("h3", section);
      if (heading) {
        heading.tabIndex = -1;
        requestAnimationFrame(() => heading.focus({ preventScroll: true }));
      }
    });
    elements.goToDefenseFlow.addEventListener("click", () => {
      elements.defenseFlow.scrollIntoView({ block: "center", behavior: state.settings.lowMotion ? "auto" : "smooth" });
      elements.defenseFlow.tabIndex = -1;
      requestAnimationFrame(() => elements.defenseFlow.focus({ preventScroll: true }));
    });
    elements.refreshActionCatalog.addEventListener("click", async () => {
      elements.refreshActionCatalog.disabled = true;
      await loadActionCatalog({ quiet: false });
      elements.refreshActionCatalog.disabled = false;
    });
    elements.actionSelect.addEventListener("change", () => {
      renderActionFields(elements.actionSelect.value);
      renderActionRisk(elements.actionSelect.value);
    });
    elements.actionForm.addEventListener("submit", queueGuardedAction);
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "/") {
        event.preventDefault();
        elements.chatInput.focus();
      }
      if (event.key === "Escape" && state.chatBusy && !elements.settingsDialog.open) state.chatController?.abort("user");
    });
  }

  async function initialize() {
    cacheElements();
    setupSettings();
    setupAdaptiveDashboard();
    setupCommunications();
    setupVoiceControls();
    setupTabs();
    setupMobileNavigation();
    setupProjects();
    setupAppMenu();
    setupCommandPalette();
    setupSettingsNavigation();
    bindEvents();
    renderSkills();
    renderTasks();
    renderApprovals();
    renderAudit();
    renderNodes([]);
    renderActionCatalog();
    renderUserProjects();
    renderAngeronaEvents();
    renderSystemHealthUnavailable();
    setupCanvas();
    setupGlobeView();
    addAudit("HUD initialized", "info", "LOCAL INTERFACE");
    updateLastContact();

    const statusPromise = checkStatus({ quiet: true });
    await Promise.allSettled([
      statusPromise, loadSkills(), loadTasks(), loadAudit(), loadApprovals(), loadNodes(),
      loadRemoteSettings(), loadSecurityPosture(), loadAngeronaStatus(), loadActionCatalog(), loadProjects(),
      loadCommunications(), loadSystemHealth(), loadPhoneStatus()
    ]);
    if (state.online) await loadTelemetry();
    restartPollers();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
