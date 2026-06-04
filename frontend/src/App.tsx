import { Layout } from "./components/Layout";
import { FunctionsPage } from "./pages/FunctionsPage";
import { InvocationDetailPage } from "./pages/InvocationDetailPage";
import { MetricsPage } from "./pages/MetricsPage";
import { WorkersPage } from "./pages/WorkersPage";

export default function App() {
  return (
    <Layout>
      <FunctionsPage />
      <InvocationDetailPage />
      <WorkersPage />
      <MetricsPage />
    </Layout>
  );
}
