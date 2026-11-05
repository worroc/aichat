import { useCallback, useEffect, useState } from "react";
import { getOperatorKey, setOperatorKey, setUnauthorizedHandler } from "./api/client";
import { EventLog } from "./components/EventLog";
import { Toasts } from "./components/Toasts";
import { LiveProvider } from "./lib/live";
import { getHumanName, setHumanName } from "./lib/human";
import { useRoute } from "./lib/router";
import { Login } from "./pages/Login";
import { NamespaceLayout } from "./pages/NamespaceLayout";
import { NamespacesPage } from "./pages/NamespacesPage";

export function App() {
  const [key, setKey] = useState<string | null>(() => getOperatorKey());
  const [name, setName] = useState<string | null>(() => getHumanName());
  const route = useRoute();

  const logout = useCallback(() => {
    setOperatorKey(null);
    setKey(null);
  }, []);

  useEffect(() => setUnauthorizedHandler(logout), [logout]);

  const login = (value: string, humanName: string) => {
    setOperatorKey(value);
    setKey(value);
    setHumanName(humanName);
    setName(humanName);
  };

  // Clears only the in-memory name: the login page shows the saved one to edit.
  const changeName = useCallback(() => setName(null), []);

  if (!key || !name) return <Login initialKey={key} initialName={getHumanName()} onLogin={login} />;

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
          <NamespaceLayout route={route} humanName={name} onChangeName={changeName} />
        )}
      </div>
    </LiveProvider>
  );
}
