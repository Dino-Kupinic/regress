export type ProjectInfo = {
  root: string;
  name: string;
  config_file: string | null;
  packages: { name: string; version: string | null }[];
  toolchain: {
    runner: "bun" | "npx";
    vitest: string;
    stryker: string;
    vitest_runner: string;
  } | null;
  problems: string[];
  ready: boolean;
  api_key_set: boolean;
  active_run: string | null;
};
export type SourceFile = { path: string; size: number };
export type SourceDetail = {
  path: string;
  test_file: string;
  test_file_exists: boolean;
  import_path: string;
  lines: number;
};
export type Settings = {
  model: string;
  ask_model: boolean;
  reasoning_effort: string | null;
  rounds: number;
  max_repairs: number;
  max_mutants: number;
  runner: "auto" | "bun" | "npx";
  llm_timeout: number;
  vitest_timeout: number;
  stryker_timeout: number;
};
export type SettingsInfo = {
  effective: Settings;
  model_source: string;
  user_config_path: string;
  user_config: Partial<Settings>;
  project_config_path: string | null;
};
export type ModelList = {
  models: {
    id: string;
    created_date: string;
    newest: boolean;
    default: boolean;
  }[];
  source: string;
  description: string;
  note: string | null;
  verified: boolean;
  default_model: string;
  model_source: string;
  hidden: number;
};
export type RunSummary = {
  id: string;
  created_at: string;
  source_file: string;
  test_file: string;
  model: string;
  status: "running" | "completed" | "failed" | "cancelled";
  error: string | null;
  kept_stage: string | null;
  duration_seconds: number;
  baseline_score: number | null;
  reference_score: number | null;
  kept_score: number | null;
  improvement: number | null;
  tests_before: number;
  tests_after: number | null;
  active: boolean;
};
export type Mutant = {
  id: string;
  mutator: string;
  status: string;
  replacement: string;
  original: string;
  start_line: number;
  start_column: number;
  end_line: number;
  end_column: number;
  killed_by: string[];
  covered_by: string[];
  status_reason: string | null;
  detected: boolean;
  equivalent: boolean;
  original_line: string | null;
  mutated_line: string | null;
  summary: string;
};
export type Mutation = {
  index: number;
  mutants: Mutant[];
  duration_seconds: number;
  killed: number;
  survived: number;
  no_coverage: number;
  timeout: number;
  errors: number;
  score: number;
};
export type Stage = {
  kind: "baseline" | "generated" | "improved";
  label: string;
  test_file_snapshot: string | null;
  test_count: number;
  test_names: string[];
  mutation: Mutation | null;
  summary: string | null;
  attempts: number;
  llm_seconds: number;
  equivalent_mutants: string[];
  targeted_mutants: string[];
  rejected: boolean;
  problems: string[];
};
export type RunReport = {
  id: string;
  created_at: string;
  project_root: string;
  source_file: string;
  test_file: string;
  test_file_existed: boolean;
  model: string;
  status: RunSummary["status"];
  error: string | null;
  stages: Stage[];
  kept_stage: string | null;
  usage: { input_tokens: number; output_tokens: number; calls: number };
  duration_seconds: number;
};
export type LiveState = {
  started_at: string;
  elapsed_seconds: number;
  activity: string | null;
  activity_status: string | null;
  activity_started_at: string | null;
  cancel_requested: boolean;
  last_event: number;
};
export type RunDetail = {
  summary: RunSummary;
  report: RunReport;
  live: LiveState | null;
};
export type RunEvent = {
  seq: number;
  time: string;
  type: string;
  message: string;
  data: Record<string, unknown>;
};
export type RunRequest = {
  source: string;
  test?: string;
  model?: string;
  rounds?: number;
  baseline?: boolean;
  generate?: boolean;
  runner?: Settings["runner"];
};
export type StageDiff = {
  stage: number;
  label: string;
  before_stage: number | null;
  before_label: string | null;
  test_file: string;
  before: string;
  after: string;
  unified: string;
  added_tests: string[];
};
export type StageMutants = {
  stage: number;
  label: string;
  source_file: string;
  total: number;
  mutants: Mutant[];
};
export type LLMCall = {
  name: string;
  kind: Stage["kind"];
  attempt: number;
  input_tokens: number;
  output_tokens: number;
  summary: string;
  new_tests: string[];
  equivalent_mutants: string[];
};
export type LLMCallDetail = LLMCall & { prompt: string; test_file: string };
export type Artifact = { path: string; size: number };
export type EvalStage = {
  label: string;
  tests: number;
  score: number | null;
  caught: string[];
  missed: string[];
};
export type EvalResult = {
  created_at: string;
  mode: string;
  model: string | null;
  rounds: number | null;
  modules: {
    module: string;
    stages: EvalStage[];
    error: string | null;
    note: string | null;
    run_id: string | null;
  }[];
  output_dir: string | null;
  // Totals count only these modules, the ones with a result in every column.
  counted_modules: string[];
  totals: EvalTotal[];
};
export type EvalTotal = {
  label: string;
  score: number | null;
  caught: number;
  bugs_total: number;
};
export type BugSuite = {
  name: string;
  source: string;
  test: string;
  oracle: string | null;
  bugs: { id: string; description: string; find: string; replace: string }[];
};
export type EvalJob = {
  status: "idle" | "running" | "completed" | "failed";
  mode: "oracle" | "full" | null;
  started_at: string | null;
  exit_code: number | null;
  output: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public output?: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      ...(init?.body ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: string;
      output?: string;
    };
    throw new ApiError(
      body.detail ?? `Request failed (${response.status})`,
      response.status,
      body.output,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  project: () => request<ProjectInfo>("/project"),
  initProject: () =>
    request<{
      project: ProjectInfo;
      installed: string[];
      config_written: boolean;
    }>("/project/init", { method: "POST" }),
  sources: () => request<SourceFile[]>("/project/sources"),
  source: (path: string) =>
    request<SourceDetail>(`/project/sources/${encodePath(path)}`),
  file: (path: string) =>
    request<{ path: string; content: string }>(
      `/project/files/${encodePath(path)}`,
    ),
  settings: () => request<SettingsInfo>("/settings"),
  saveSettings: (update: Partial<Settings>) =>
    request<SettingsInfo>("/settings", {
      method: "PATCH",
      body: JSON.stringify(update),
    }),
  models: (all = false, refresh = false) =>
    request<ModelList>(`/models?all=${all}&refresh=${refresh}`),
  runs: () => request<RunSummary[]>("/runs"),
  run: (id: string) => request<RunDetail>(`/runs/${encodeURIComponent(id)}`),
  startRun: (payload: RunRequest) =>
    request<RunDetail>("/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelRun: (id: string) =>
    request<RunDetail>(`/runs/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  deleteRun: (id: string) =>
    request<void>(`/runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  events: (id: string) =>
    request<RunEvent[]>(`/runs/${encodeURIComponent(id)}/events`),
  diff: (id: string) =>
    request<StageDiff>(`/runs/${encodeURIComponent(id)}/diff`),
  stageDiff: (id: string, index: number) =>
    request<StageDiff>(`/runs/${encodeURIComponent(id)}/stages/${index}/diff`),
  mutants: (id: string, index: number, status = "all") =>
    request<StageMutants>(
      `/runs/${encodeURIComponent(id)}/stages/${index}/mutants?status=${status}`,
    ),
  llm: (id: string) =>
    request<LLMCall[]>(`/runs/${encodeURIComponent(id)}/llm`),
  llmCall: (id: string, name: string) =>
    request<LLMCallDetail>(
      `/runs/${encodeURIComponent(id)}/llm/${encodeURIComponent(name)}`,
    ),
  artifacts: (id: string) =>
    request<Artifact[]>(`/runs/${encodeURIComponent(id)}/artifacts`),
  evaluations: () => request<EvalResult[]>("/evaluations"),
  evaluationSuites: () => request<BugSuite[]>("/evaluations/suites"),
  evaluationJob: () => request<EvalJob>("/evaluations/job"),
  startEvaluation: (mode: "oracle" | "full") =>
    request<EvalJob>("/evaluations", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
};

function encodePath(path: string) {
  return path.split("/").map(encodeURIComponent).join("/");
}
