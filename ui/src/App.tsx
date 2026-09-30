import { useCallback, useEffect, useState } from "react";
import { getOperatorKey, setOperatorKey, setUnauthorizedHandler } from "./api/client";
import { EventLog } from "./components/EventLog";
import { Toasts } from "./components/Toasts";
import { LiveProvider } from "./lib/live";
import { useRoute } from "./lib/router";
import { Login } from "./pages/Login";
import { NamespaceLayout } from "./pages/NamespaceLayout";
import { NamespacesPage } from "./pages/NamespacesPage";

export function App() {
  const [key, setKey] = useState<string | null>(() => getOperatorKey());
  const route = useRoute();

  const logout = useCallback(() => {
    setOperatorKey(null);
    setKey(null);
  }, []);

  useEffect(() => setUnauthorizedHandler(logout), [logout]);

  const login = (value: string) => {
    setOperatorKey(value);
    setKey(value);
  };

  if (!key) return <Login onLogin={login} />;

  return (
    <LiveProvider operatorKey={key} onUnauthorized={logout}>
      <div className="app">
        <header className="topbar">
          <a href="#/" className="brand">
            aichat operator
          </a>
          <span className="grow" />
          <button className="link" onClick={logout}>
            sign out
          </button>
        </header>
        <Toasts />
        {route.page === "namespaces" ? (
          <div className="ns-layout no-sidebar">
            <main className="content">
              <NamespacesPage />
            </main>
            <EventLog />
          </div>
        ) : (
          <NamespaceLayout route={route} />
        )}
      </div>
    </LiveProvider>
  );
}
