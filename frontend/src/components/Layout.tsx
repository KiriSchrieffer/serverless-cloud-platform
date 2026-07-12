import type { ReactNode } from "react";

type LayoutProps = {
  children: ReactNode;
  onLogout: () => void;
};

export function Layout({ children, onLogout }: LayoutProps) {
  return (
    <main className="shell">
      <aside className="sidebar">
        <h1>Serverless</h1>
        <nav>
          <a href="#functions">Functions</a>
          <a href="#invocation">Invocation</a>
          <a href="#workers">Workers</a>
          <a href="#metrics">Metrics</a>
        </nav>
        <button className="logout-button" type="button" onClick={onLogout}>
          Sign out
        </button>
      </aside>
      <section className="content">{children}</section>
    </main>
  );
}
