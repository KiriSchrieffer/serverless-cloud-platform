import { useEffect, useState } from "react";

import { type MetricsSummary, getMetricsSummary } from "../api/client";

export function MetricsPage() {
  const [summary, setSummary] = useState<MetricsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadMetrics() {
    setLoading(true);
    setError(null);
    try {
      setSummary(await getMetricsSummary());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load metrics");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadMetrics();
  }, []);

  return (
    <section id="metrics" className="panel">
      <div className="section-header">
        <div>
          <h2>Metrics</h2>
          <p>{summary ? `${summary.invocations.total} invocations` : "Waiting"}</p>
        </div>
        <button type="button" onClick={loadMetrics} disabled={loading}>
          Refresh
        </button>
      </div>

      {error ? <div className="notice error">{error}</div> : null}

      <div className="metric-grid">
        <MetricTile
          label="Success rate"
          value={summary ? formatPercent(summary.invocations.success_rate) : "-"}
        />
        <MetricTile
          label="Error rate"
          value={summary ? formatPercent(summary.invocations.error_rate) : "-"}
        />
        <MetricTile
          label="Throughput / min"
          value={summary?.invocations.throughput_per_minute ?? "-"}
        />
        <MetricTile label="Retries" value={summary?.invocations.retry_count ?? "-"} />
        <MetricTile label="Queue depth" value={summary?.queue.depth ?? "-"} />
        <MetricTile
          label="Oldest queued"
          value={formatSeconds(summary?.queue.oldest_age_seconds)}
        />
        <MetricTile
          label="Pending dispatch"
          value={summary?.queue.pending_dispatches ?? "-"}
        />
        <MetricTile
          label="Oldest dispatch"
          value={formatSeconds(summary?.queue.oldest_dispatch_age_seconds)}
        />
        <MetricTile
          label="Avg latency"
          value={formatMilliseconds(summary?.invocations.average_latency_ms)}
        />
        <MetricTile
          label="p50 latency"
          value={formatMilliseconds(summary?.invocations.p50_latency_ms)}
        />
        <MetricTile
          label="p95 latency"
          value={formatMilliseconds(summary?.invocations.p95_latency_ms)}
        />
        <MetricTile
          label="p99 latency"
          value={formatMilliseconds(summary?.invocations.p99_latency_ms)}
        />
        <MetricTile label="Succeeded" value={summary?.invocations.succeeded ?? "-"} />
        <MetricTile label="Failed" value={summary?.invocations.failed ?? "-"} />
        <MetricTile label="Timeout" value={summary?.invocations.timeout ?? "-"} />
        <MetricTile label="Queued" value={summary?.invocations.queued ?? "-"} />
        <MetricTile label="Running" value={summary?.invocations.running ?? "-"} />
        <MetricTile
          label="Avg execution"
          value={formatMilliseconds(summary?.invocations.average_execution_ms)}
        />
        <MetricTile
          label="p95 execution"
          value={formatMilliseconds(summary?.invocations.p95_execution_ms)}
        />
        <MetricTile label="Workers" value={summary?.workers.total ?? "-"} />
        <MetricTile label="Active workers" value={summary?.workers.active ?? "-"} />
        <MetricTile label="Stale workers" value={summary?.workers.stale ?? "-"} />
        <MetricTile label="Active tasks" value={summary?.workers.active_invocations ?? "-"} />
      </div>
    </section>
  );
}

type MetricTileProps = {
  label: string;
  value: string | number;
};

function MetricTile({ label, value }: MetricTileProps) {
  return (
    <div className="metric-tile">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatMilliseconds(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value} ms`;
}

function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value} s`;
}
