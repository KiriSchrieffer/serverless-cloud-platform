import { useEffect, useState } from "react";

import { type WorkerRead, listWorkers } from "../api/client";

export function WorkersPage() {
  const [workers, setWorkers] = useState<WorkerRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadWorkers() {
    setLoading(true);
    setError(null);
    try {
      setWorkers(await listWorkers());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load workers");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadWorkers();
  }, []);

  return (
    <section id="workers" className="panel">
      <div className="section-header">
        <div>
          <h2>Workers</h2>
          <p>{workers.length} registered</p>
        </div>
        <button type="button" onClick={loadWorkers} disabled={loading}>
          Refresh
        </button>
      </div>

      {error ? <div className="notice error">{error}</div> : null}

      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Hostname</th>
              <th>Status</th>
              <th>Heartbeat</th>
              <th>Active</th>
              <th>Concurrency</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6}>Loading</td>
              </tr>
            ) : workers.length === 0 ? (
              <tr>
                <td colSpan={6}>No workers</td>
              </tr>
            ) : (
              workers.map((worker) => (
                <tr key={worker.id}>
                  <td className="strong">{worker.hostname}</td>
                  <td>
                    <span
                      className={`badge ${
                        worker.stale ? "stale" : worker.status.toLowerCase()
                      }`}
                    >
                      {worker.stale ? "STALE" : worker.status}
                    </span>
                  </td>
                  <td>{worker.heartbeat_age_seconds}s ago</td>
                  <td>{worker.active_invocations}</td>
                  <td>{worker.max_concurrency}</td>
                  <td>{formatDate(worker.started_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
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
