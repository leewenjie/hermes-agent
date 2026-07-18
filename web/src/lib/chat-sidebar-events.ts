export interface ChatSessionCapabilityInfo {
  capability_preview?: boolean;
  cwd?: string;
  model?: string;
  provider?: string;
  credential_warning?: string;
  preloaded_skills?: string[];
  tools?: Record<string, string[]>;
  title?: string;
}

export interface ChatSessionIdentity {
  id: string;
  title: string | null;
}

export interface ResearchToolUse {
  context?: string;
  detail?: string;
  id: string;
  name: string;
  status: "running" | "complete";
}

export interface ResearchTraceState {
  openedSkills: string[];
  phase: "idle" | "running" | "complete";
  tools: ResearchToolUse[];
}

export interface ResearchToolSummary {
  count: number;
  detail?: string;
  label: string;
  name: string;
  running: boolean;
}

interface SessionCreateResult {
  info?: unknown;
  session_id: string;
}

function recordFromUnknown(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }

  if (typeof value !== "string" || !value.trim()) return null;

  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function chatSessionIdentityFromInfo(
  sessionId: unknown,
  payload: unknown,
): ChatSessionIdentity | null {
  const id = typeof sessionId === "string" ? sessionId.trim() : "";
  if (!id) return null;
  const record = recordFromUnknown(payload);
  const rawTitle = record?.title;
  const title = typeof rawTitle === "string" && rawTitle.trim()
    ? rawTitle.trim()
    : null;
  return { id, title };
}

const RESEARCH_TOOL_LABELS: Record<string, string> = {
  delegate_task: "Parallel review",
  memory: "Research memory",
  patch: "Artifact update",
  read_file: "File review",
  search_files: "Workspace search",
  session_search: "Prior research",
  skill_view: "Research method",
  terminal: "Data analysis",
  vision_analyze: "Chart and image review",
  web_extract: "Source review",
  web_search: "Source search",
  write_file: "Artifact creation",
};

const RESEARCH_METHOD_LABELS: Record<string, string> = {
  "investment-research": "Thesis and evidence review",
  "market-return-analysis": "Return and risk analysis",
  polymarket: "Prediction-market research",
  stocks: "Market data and quote provenance",
};

const RESEARCH_METHOD_DESCRIPTIONS: Record<string, string> = {
  "investment-research": "Structure claims, counter-evidence, risks, catalysts, and missing checks.",
  "market-return-analysis": "Compare returns, volatility, drawdowns, and distributions with stated assumptions.",
  polymarket: "Review event probabilities, price history, volume, and order books from public market data.",
  stocks: "Fetch timestamped quotes and adjusted history with source metadata and limitations.",
};

export interface ResearchCapabilitySummary {
  detail: string;
  label: string;
}

export function researchMethodLabel(skill: string): string {
  return RESEARCH_METHOD_LABELS[skill] ?? skill.replaceAll("-", " ");
}

export function researchMethodDescription(skill: string): string {
  return RESEARCH_METHOD_DESCRIPTIONS[skill] ?? "Loaded research guidance for this session.";
}

export function summarizeResearchCapabilities(
  tools: Record<string, string[]>,
): ResearchCapabilitySummary[] {
  const available = new Set(Object.keys(tools));
  const capabilities: Array<ResearchCapabilitySummary & { toolsets: string[] }> = [
    {
      label: "Evidence and sources",
      detail: "Search and review source pages while keeping links attached.",
      toolsets: ["web"],
    },
    {
      label: "Calculations and scripts",
      detail: "Run reproducible data checks and analysis in the workspace.",
      toolsets: ["terminal"],
    },
    {
      label: "Files and artifacts",
      detail: "Read, create, and update memos, datasets, charts, and reports.",
      toolsets: ["file"],
    },
    {
      label: "Saved research context",
      detail: "Reuse approved memory and search prior research sessions.",
      toolsets: ["memory", "session_search"],
    },
    {
      label: "Research coordination",
      detail: "Ask focused questions, track work, and delegate bounded checks.",
      toolsets: ["clarify", "todo", "delegation"],
    },
    {
      label: "Chart and image review",
      detail: "Inspect visual evidence when it is part of the research record.",
      toolsets: ["vision"],
    },
  ];

  return capabilities
    .filter((capability) => capability.toolsets.some((toolset) => available.has(toolset)))
    .map(({ label, detail }) => ({ label, detail }));
}

export function emptyResearchTrace(): ResearchTraceState {
  return { openedSkills: [], phase: "idle", tools: [] };
}

function shortText(value: unknown, max = 120): string | undefined {
  if (typeof value !== "string") return undefined;
  const compact = value.replace(/\s+/g, " ").trim();
  if (!compact) return undefined;
  return compact.length > max ? `${compact.slice(0, max - 1)}…` : compact;
}

function skillNameFromArgs(args: unknown): string | null {
  const record = recordFromUnknown(args);
  if (!record) return null;
  for (const key of ["skill", "skill_name", "name"] as const) {
    const value = shortText(record[key], 80);
    if (value) return value;
  }
  return null;
}

export function reduceResearchTrace(
  state: ResearchTraceState,
  type: string | undefined,
  payload: unknown,
): ResearchTraceState {
  if (type === "message.start") {
    return { openedSkills: [], phase: "running", tools: [] };
  }

  if (type === "message.complete") {
    return { ...state, phase: "complete" };
  }

  if (type !== "tool.start" && type !== "tool.complete") return state;
  const record = recordFromUnknown(payload);
  if (!record) return state;
  const name = shortText(record.name, 80);
  if (!name) return state;
  const id = shortText(record.tool_id, 120) ?? `${name}-${state.tools.length}`;
  const prior = state.tools.find((tool) => tool.id === id);
  const next: ResearchToolUse = {
    context: shortText(record.context, 100) ?? prior?.context,
    detail: shortText(record.summary, 120) ?? prior?.detail,
    id,
    name,
    status: type === "tool.complete" ? "complete" : "running",
  };
  const tools = prior
    ? state.tools.map((tool) => (tool.id === id ? next : tool))
    : [...state.tools, next];
  const openedSkill = name === "skill_view" ? skillNameFromArgs(record.args) : null;
  const openedSkills =
    openedSkill && !state.openedSkills.includes(openedSkill)
      ? [...state.openedSkills, openedSkill]
      : state.openedSkills;

  return {
    openedSkills,
    phase: "running",
    tools,
  };
}

export function summarizeResearchTools(
  tools: ResearchToolUse[],
): ResearchToolSummary[] {
  const grouped = new Map<string, ResearchToolSummary>();
  for (const tool of tools) {
    const prior = grouped.get(tool.name);
    grouped.set(tool.name, {
      count: (prior?.count ?? 0) + 1,
      detail: tool.detail ?? tool.context ?? prior?.detail,
      label: RESEARCH_TOOL_LABELS[tool.name] ?? tool.name.replaceAll("_", " "),
      name: tool.name,
      running: (prior?.running ?? false) || tool.status === "running",
    });
  }
  return [...grouped.values()];
}

export function capabilityInfoFromSessionCreate(
  result: SessionCreateResult,
): ChatSessionCapabilityInfo | null {
  return recordFromUnknown(result.info) as ChatSessionCapabilityInfo | null;
}

/** Display source for a completed image generation result. */
export function generatedImageFromToolResult(result: unknown): string | null {
  const record = recordFromUnknown(result);
  if (!record || record.success === false) return null;

  for (const key of ["host_image", "image"] as const) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export function isBrowserImageSource(source: string): boolean {
  return /^https:\/\//i.test(source) || /^data:image\//i.test(source);
}