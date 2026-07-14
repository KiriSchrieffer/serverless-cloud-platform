import { type FormEvent, useEffect, useState } from "react";

import {
  type InvocationRead,
  getInvocation,
  getInvocationLogs,
} from "../api/client";

type InvocationDetailPageProps = {
  requestedInvocationId?: string | null;
};

export function InvocationDetailPage({ requestedInvocationId }: InvocationDetailPageProps) {
  const [invocationId, setInvocationId] = useState("");
  const [invocation, setInvocation] = useState<InvocationRead | null>(null);
  const [logs, setLogs] = useState("");
  const [logsError, setLogsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (requestedInvocationId) {
      setInvocationId(requestedInvocationId);
      void loadInvocation(requestedInvocationId);
    }
  }, [requestedInvocationId]);

  async function loadInvocation(id: string) {
    setLoading(true);
    setError(null);
    setLogs("");
    setLogsError(null);
    try {
      const loadedInvocation = await getInvocation(id);
      setInvocation(loadedInvocation);
      try {
        setLogs(await getInvocationLogs(id));
      } catch (loadLogsError) {
        setLogsError(
          loadLogsError instanceof Error ? loadLogsError.message : "Failed to load logs",
        );
      }
    } catch (loadError) {
      setInvocation(null);
      setError(loadError instanceof Error ? loadError.message : "Failed to load invocation");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedId = invocationId.trim();
    if (!trimmedId) {
      setError("Invocation ID is required");
      return;
    }
    void loadInvocation(trimmedId);
  }

  return (
    <section id="invocation" className="panel">
      <div className="section-header">
        <div>
          <h2>Invocation Detail</h2>
          <p>{invocation ? invocation.status : "Lookup"}</p>
        </div>
        {invocation ? (
          <button
            type="button"
            onClick={() => void loadInvocation(invocation.id)}
            disabled={loading}
          >
            Refresh
          </button>
        ) : null}
      </div>

      <form className="lookup-form" onSubmit={handleSubmit}>
        <label htmlFor="invocation-id">Invocation ID</label>
        <input
          id="invocation-id"
          value={invocationId}
          onChange={(event) => setInvocationId(event.target.value)}
          placeholder="85f16abf-9e18-4faf-b50a-dd97f52766cc"
        />
        <button type="submit" disabled={loading}>
          {loading ? "Loading" : "Load"}
        </button>
      </form>

      {error ? <div className="notice error">{error}</div> : null}

      {invocation ? (
        <div className="detail-grid">
          <div className="detail-list">
            <div>
              <span>Status</span>
              <strong className={`badge ${invocation.status.toLowerCase()}`}>
                {invocation.status}
              </strong>
            </div>
            <div>
              <span>Attempts</span>
              <strong>{invocation.attempt_count}</strong>
            </div>
            <div>
              <span>Queued</span>
              <strong>{formatDate(invocation.queued_at)}</strong>
            </div>
            <div>
              <span>Duration</span>
              <strong>{formatDuration(invocation.started_at, invocation.completed_at)}</strong>
            </div>
            <div>
              <span>Version ID</span>
              <strong className="mono truncate">{invocation.function_version_id}</strong>
            </div>
            <div>
              <span>Idempotency</span>
              <strong className="mono truncate">{invocation.idempotency_key ?? "-"}</strong>
            </div>
          </div>

          <div className="json-columns">
            <JsonBlock title="Payload" value={invocation.payload_inline} />
            <JsonBlock title="Result" value={invocation.result_inline} />
            <JsonBlock
              title="Error"
              value={{
                type: invocation.error_type,
                message: invocation.error_message,
              }}
            />
            <div className="json-block">
              <div className="block-title">Logs</div>
              {logsError ? <div className="notice compact">{logsError}</div> : null}
              <pre>{logs || "No logs"}</pre>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

type JsonBlockProps = {
  title: string;
  value: unknown;
};

function JsonBlock({ title, value }: JsonBlockProps) {
  return (
    <div className="json-block">
      <div className="block-title">{title}</div>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt || !completedAt) {
    return "-";
  }
  const durationMs = new Date(completedAt).getTime() - new Date(startedAt).getTime();
  return `${Math.max(0, durationMs)} ms`;
}
