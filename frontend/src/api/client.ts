export const API_BASE_URL = "/api";

export type JsonValue =
  | Record<string, unknown>
  | unknown[]
  | string
  | number
  | boolean
  | null;

export type FunctionRead = {
  id: string;
  owner_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
};

export type FunctionVersionRead = {
  id: string;
  function_id: string;
  version_number: number;
  runtime: string;
  handler: string;
  package_uri: string;
  package_hash: string;
  memory_limit_mb: number;
  cpu_limit: number;
  timeout_seconds: number;
  created_at: string;
};

export type InvocationRead = {
  id: string;
  owner_id: string;
  function_version_id: string;
  idempotency_key: string | null;
  status: InvocationStatus;
  payload_ref: string | null;
  payload_inline: JsonValue;
  result_ref: string | null;
  result_inline: JsonValue;
  error_type: string | null;
  error_message: string | null;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  deadline_at: string;
  attempt_count: number;
  created_at: string;
  updated_at: string;
};

export type InvocationStatus =
  | "QUEUED"
  | "RUNNING"
  | "RETRYING"
  | "SUCCEEDED"
  | "FAILED"
  | "TIMEOUT"
  | "CANCELED";

export type WorkerRead = {
  id: string;
  hostname: string;
  consumer_name: string | null;
  status: WorkerStatus;
  last_heartbeat: string;
  heartbeat_age_seconds: number;
  stale: boolean;
  active_invocations: number;
  max_concurrency: number;
  started_at: string;
  created_at: string;
  updated_at: string;
};

export type WorkerStatus = "IDLE" | "RUNNING" | "DRAINING" | "OFFLINE";

export type MetricsSummary = {
  invocations: {
    total: number;
    queued: number;
    running: number;
    retrying: number;
    succeeded: number;
    failed: number;
    timeout: number;
    canceled: number;
    success_rate: number;
    average_execution_ms: number | null;
    p95_execution_ms: number | null;
  };
  workers: {
    total: number;
    active: number;
    stale: number;
    offline: number;
    active_invocations: number;
  };
};

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }

  return response.json() as Promise<T>;
}

export async function fetchText(path: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: "text/plain",
    },
  });

  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }

  return response.text();
}

export function listFunctions(): Promise<FunctionRead[]> {
  return fetchJson<FunctionRead[]>("/functions");
}

export function listFunctionVersions(functionName: string): Promise<FunctionVersionRead[]> {
  return fetchJson<FunctionVersionRead[]>(
    `/functions/${encodeURIComponent(functionName)}/versions`,
  );
}

export function getInvocation(invocationId: string): Promise<InvocationRead> {
  return fetchJson<InvocationRead>(`/invocations/${encodeURIComponent(invocationId)}`);
}

export function getInvocationLogs(invocationId: string): Promise<string> {
  return fetchText(`/invocations/${encodeURIComponent(invocationId)}/logs`);
}

export function listWorkers(): Promise<WorkerRead[]> {
  return fetchJson<WorkerRead[]>("/workers");
}

export function getMetricsSummary(): Promise<MetricsSummary> {
  return fetchJson<MetricsSummary>("/metrics/summary");
}

async function responseErrorMessage(response: Response): Promise<string> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  }
  return `API request failed: ${response.status}`;
}
