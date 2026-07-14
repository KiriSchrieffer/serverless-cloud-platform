import { useEffect, useState } from "react";

import { AUTH_REQUIRED_EVENT, clearAccessToken, getAccessToken } from "./api/client";
import { Layout } from "./components/Layout";
import { AuthPage } from "./pages/AuthPage";
import { FunctionsPage } from "./pages/FunctionsPage";
import { InvocationDetailPage } from "./pages/InvocationDetailPage";
import { MetricsPage } from "./pages/MetricsPage";
import { WorkersPage } from "./pages/WorkersPage";

export default function App() {
  const [authenticated, setAuthenticated] = useState(() => getAccessToken() !== null);
  const [selectedInvocationId, setSelectedInvocationId] = useState<string | null>(null);

  useEffect(() => {
    const requireAuthentication = () => setAuthenticated(false);
    window.addEventListener(AUTH_REQUIRED_EVENT, requireAuthentication);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireAuthentication);
  }, []);

  if (!authenticated) {
    return <AuthPage onAuthenticated={() => setAuthenticated(true)} />;
  }

  function logout() {
    clearAccessToken();
    setAuthenticated(false);
  }

  return (
    <Layout onLogout={logout}>
      <FunctionsPage onInvocationAccepted={setSelectedInvocationId} />
      <InvocationDetailPage requestedInvocationId={selectedInvocationId} />
      <WorkersPage />
      <MetricsPage />
    </Layout>
  );
}
