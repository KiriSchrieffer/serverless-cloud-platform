import type { ReactNode } from "react";

type LayoutProps = {
  children: ReactNode;
};

export function Layout({ children }: LayoutProps) {
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
      </aside>
      <section className="content">{children}</section>
    </main>
  );
}
