import { type FormEvent, useEffect, useState } from "react";

import {
  createFunction,
  type FunctionRead,
  type FunctionVersionRead,
  type JsonValue,
  invokeFunction,
  listFunctions,
  listFunctionVersions,
  uploadFunctionVersion,
} from "../api/client";

type FunctionRow = {
  function: FunctionRead;
  versions: FunctionVersionRead[];
};

type FunctionsPageProps = {
  onInvocationAccepted: (invocationId: string) => void;
};

export function FunctionsPage({ onInvocationAccepted }: FunctionsPageProps) {
  const [rows, setRows] = useState<FunctionRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedFunction, setSelectedFunction] = useState("");
  const [newFunctionName, setNewFunctionName] = useState("");
  const [packageFile, setPackageFile] = useState<File | null>(null);
  const [handler, setHandler] = useState("main.handler");
  const [memoryLimitMb, setMemoryLimitMb] = useState(256);
  const [cpuLimit, setCpuLimit] = useState(0.5);
  const [timeoutSeconds, setTimeoutSeconds] = useState(30);
  const [payloadText, setPayloadText] = useState('{"name":"Ada"}');
  const [versionNumber, setVersionNumber] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [action, setAction] = useState<"create" | "upload" | "invoke" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

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
      setSelectedFunction((current) => current || functions[0]?.name || "");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load functions");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadFunctions();
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newFunctionName.trim();
    if (!name) {
      setActionError("Function name is required");
      return;
    }
    beginAction("create");
    try {
      const created = await createFunction(name);
      setSelectedFunction(created.name);
      setNewFunctionName("");
      setActionMessage(`Function ${created.name} created`);
      await loadFunctions();
    } catch (createError) {
      setActionError(errorMessage(createError, "Failed to create function"));
    } finally {
      setAction(null);
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFunction || !packageFile) {
      setActionError("Select a function and ZIP package");
      return;
    }
    beginAction("upload");
    try {
      const version = await uploadFunctionVersion(selectedFunction, packageFile, {
        handler,
        memoryLimitMb,
        cpuLimit,
        timeoutSeconds,
      });
      setActionMessage(
        `${selectedFunction} version ${version.version_number} uploaded`,
      );
      await loadFunctions();
    } catch (uploadError) {
      setActionError(errorMessage(uploadError, "Failed to upload version"));
    } finally {
      setAction(null);
    }
  }

  async function handleInvoke(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFunction) {
      setActionError("Select a function to invoke");
      return;
    }
    beginAction("invoke");
    try {
      const payload = JSON.parse(payloadText) as JsonValue;
      const accepted = await invokeFunction(selectedFunction, payload, {
        versionNumber: versionNumber ? Number(versionNumber) : undefined,
        idempotencyKey: idempotencyKey.trim() || undefined,
      });
      setActionMessage(`Invocation ${accepted.invocation_id} accepted`);
      onInvocationAccepted(accepted.invocation_id);
    } catch (invokeError) {
      setActionError(errorMessage(invokeError, "Payload must be valid JSON"));
    } finally {
      setAction(null);
    }
  }

  function beginAction(nextAction: "create" | "upload" | "invoke") {
    setAction(nextAction);
    setActionError(null);
    setActionMessage(null);
  }

  return (
    <>
      <section id="workflow" className="panel">
        <div className="section-header">
          <div>
            <h2>Deploy &amp; Invoke</h2>
            <p>Create a function, upload a ZIP package, then submit an invocation.</p>
          </div>
        </div>

        {actionError ? <div className="notice error">{actionError}</div> : null}
        {actionMessage ? <div className="notice success">{actionMessage}</div> : null}

        <div className="action-grid">
          <form className="action-card" onSubmit={handleCreate}>
            <div>
              <span className="step-label">Step 1</span>
              <h3>Create function</h3>
            </div>
            <label>
              Function name
              <input
                value={newFunctionName}
                onChange={(event) => setNewFunctionName(event.target.value)}
                placeholder="hello"
                pattern="[A-Za-z][A-Za-z0-9_-]*"
                maxLength={128}
                required
              />
            </label>
            <button type="submit" disabled={action !== null}>
              {action === "create" ? "Creating" : "Create"}
            </button>
          </form>

          <form className="action-card" onSubmit={handleUpload}>
            <div>
              <span className="step-label">Step 2</span>
              <h3>Upload version</h3>
            </div>
            <FunctionSelect
              rows={rows}
              value={selectedFunction}
              onChange={setSelectedFunction}
            />
            <label>
              ZIP package
              <input
                type="file"
                accept=".zip,application/zip"
                onChange={(event) => setPackageFile(event.target.files?.[0] ?? null)}
                required
              />
            </label>
            <label>
              Handler
              <input value={handler} onChange={(event) => setHandler(event.target.value)} />
            </label>
            <div className="compact-fields">
              <NumberField
                label="Memory MB"
                value={memoryLimitMb}
                min={64}
                max={1024}
                step={64}
                onChange={setMemoryLimitMb}
              />
              <NumberField
                label="CPU"
                value={cpuLimit}
                min={0.1}
                max={2}
                step={0.1}
                onChange={setCpuLimit}
              />
              <NumberField
                label="Timeout sec"
                value={timeoutSeconds}
                min={1}
                max={300}
                step={1}
                onChange={setTimeoutSeconds}
              />
            </div>
            <button type="submit" disabled={action !== null || rows.length === 0}>
              {action === "upload" ? "Uploading" : "Upload version"}
            </button>
          </form>

          <form className="action-card" onSubmit={handleInvoke}>
            <div>
              <span className="step-label">Step 3</span>
              <h3>Invoke</h3>
            </div>
            <FunctionSelect
              rows={rows}
              value={selectedFunction}
              onChange={setSelectedFunction}
            />
            <label>
              JSON payload
              <textarea
                value={payloadText}
                onChange={(event) => setPayloadText(event.target.value)}
                rows={5}
                required
              />
            </label>
            <div className="compact-fields two-columns">
              <label>
                Version (optional)
                <input
                  type="number"
                  min={1}
                  value={versionNumber}
                  onChange={(event) => setVersionNumber(event.target.value)}
                />
              </label>
              <label>
                Idempotency key
                <input
                  value={idempotencyKey}
                  onChange={(event) => setIdempotencyKey(event.target.value)}
                  maxLength={255}
                  placeholder="optional"
                />
              </label>
            </div>
            <button type="submit" disabled={action !== null || rows.length === 0}>
              {action === "invoke" ? "Invoking" : "Invoke"}
            </button>
          </form>
        </div>
      </section>

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
                <tr><td colSpan={6}>Loading</td></tr>
              ) : rows.length === 0 ? (
                <tr><td colSpan={6}>No functions</td></tr>
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
    </>
  );
}

type FunctionSelectProps = {
  rows: FunctionRow[];
  value: string;
  onChange: (value: string) => void;
};

function FunctionSelect({ rows, value, onChange }: FunctionSelectProps) {
  return (
    <label>
      Function
      <select value={value} onChange={(event) => onChange(event.target.value)} required>
        <option value="">Select a function</option>
        {rows.map((row) => (
          <option key={row.function.id} value={row.function.name}>
            {row.function.name}
          </option>
        ))}
      </select>
    </label>
  );
}

type NumberFieldProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
};

function NumberField({ label, value, min, max, step, onChange }: NumberFieldProps) {
  return (
    <label>
      {label}
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
        required
      />
    </label>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
