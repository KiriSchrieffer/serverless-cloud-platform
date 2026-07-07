import { useEffect, useState } from "react";

import {
  type FunctionRead,
  type FunctionVersionRead,
  listFunctions,
  listFunctionVersions,
} from "../api/client";

type FunctionRow = {
  function: FunctionRead;
  versions: FunctionVersionRead[];
};

export function FunctionsPage() {
  const [rows, setRows] = useState<FunctionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadFunctions() {
    setLoading(true);
    setError(null);
    try {
      const functions = await listFunctions();
      const rowsWithVersions = await Promise.all(
        functions.map(async (functionItem) => ({
          function: functionItem,
          versions: await listFunctionVersions(functionItem.name),
        })),
      );
      setRows(rowsWithVersions);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load functions");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadFunctions();
  }, []);

  return (
    <section id="functions" className="panel">
      <div className="section-header">
        <div>
          <h2>Functions</h2>
          <p>{rows.length} registered</p>
        </div>
        <button type="button" onClick={loadFunctions} disabled={loading}>
          Refresh
        </button>
      </div>

      {error ? <div className="notice error">{error}</div> : null}

      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Versions</th>
              <th>Latest</th>
              <th>Runtime</th>
              <th>Handler</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={6}>Loading</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={6}>No functions</td>
              </tr>
            ) : (
              rows.map((row) => {
                const latestVersion = row.versions[row.versions.length - 1];
                return (
                  <tr key={row.function.id}>
                    <td className="strong">{row.function.name}</td>
                    <td>{row.versions.length}</td>
                    <td>{latestVersion ? `v${latestVersion.version_number}` : "-"}</td>
                    <td>{latestVersion?.runtime ?? "-"}</td>
                    <td className="mono">{latestVersion?.handler ?? "-"}</td>
                    <td>{formatDate(row.function.updated_at)}</td>
                  </tr>
                );
              })
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
