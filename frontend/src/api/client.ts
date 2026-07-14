export const API_BASE_URL = "/api";
const ACCESS_TOKEN_KEY = "serverless.access-token";
export const AUTH_REQUIRED_EVENT = "serverless:auth-required";

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

export type InvocationAccepted = {
  invocation_id: string;
  status: InvocationStatus;
  status_url: string;
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
    terminal: number;
    queued: number;
    running: number;
    retrying: number;
    succeeded: number;
    failed: number;
    timeout: number;
    canceled: number;
    success_rate: number;
    error_rate: number;
    retry_count: number;
    throughput_per_minute: number;
    average_latency_ms: number | null;
    p50_latency_ms: number | null;
    p95_latency_ms: number | null;
    p99_latency_ms: number | null;
    average_execution_ms: number | null;
    p95_execution_ms: number | null;
  };
  queue: {
    depth: number;
    oldest_age_seconds: number | null;
    pending_dispatches: number;
    oldest_dispatch_age_seconds: number | null;
  };
  workers: {
    total: number;
    active: number;
    stale: number;
    offline: number;
    active_invocations: number;
  };
};

export type UserRead = {
  id: string;
  email: string;
  created_at: string;
};

export type TokenResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
};

export function getAccessToken(): string | null {
  return window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

export function setAccessToken(token: string): void {
  window.sessionStorage.setItem(ACCESS_TOKEN_KEY, token);
}

export function clearAccessToken(): void {
  window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
}

export function register(email: string, password: string): Promise<UserRead> {
  return requestJson<UserRead>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function login(email: string, password: string): Promise<TokenResponse> {
  return requestJson<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function fetchJson<T>(path: string): Promise<T> {
  return requestJson<T>(path);
}

export async function fetchText(path: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: requestHeaders("text/plain"),
  });

  handleUnauthorized(response);
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }

  return response.text();
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = requestHeaders("application/json");
  if (init.body instanceof FormData) {
    delete headers["Content-Type"];
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...headers,
      ...init.headers,
    },
  });

  handleUnauthorized(response);
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  return response.json() as Promise<T>;
}

function requestHeaders(accept: string): Record<string, string> {
  const headers: Record<string, string> = { Accept: accept };
  const token = getAccessToken();
  if (accept === "application/json") {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function handleUnauthorized(response: Response): void {
  if (response.status === 401 && getAccessToken()) {
    clearAccessToken();
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
  }
}

export function listFunctions(): Promise<FunctionRead[]> {
  return fetchJson<FunctionRead[]>("/functions");
}

export function createFunction(name: string): Promise<FunctionRead> {
  return requestJson<FunctionRead>("/functions", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function uploadFunctionVersion(
  functionName: string,
  packageFile: File,
  options: {
    handler: string;
    memoryLimitMb: number;
    cpuLimit: number;
    timeoutSeconds: number;
  },
): Promise<FunctionVersionRead> {
  const form = new FormData();
  form.set("package", packageFile);
  form.set("runtime", "python3.11");
  form.set("handler", options.handler);
  form.set("memory_limit_mb", String(options.memoryLimitMb));
  form.set("cpu_limit", String(options.cpuLimit));
  form.set("timeout_seconds", String(options.timeoutSeconds));
  return requestJson<FunctionVersionRead>(
    `/functions/${encodeURIComponent(functionName)}/versions/upload`,
    { method: "POST", body: form },
  );
}

export function invokeFunction(
  functionName: string,
  payload: JsonValue,
  options: { versionNumber?: number; idempotencyKey?: string } = {},
): Promise<InvocationAccepted> {
  return requestJson<InvocationAccepted>(
    `/functions/${encodeURIComponent(functionName)}/invoke`,
    {
      method: "POST",
      body: JSON.stringify({
        payload,
        version_number: options.versionNumber,
        idempotency_key: options.idempotencyKey,
      }),
    },
  );
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
