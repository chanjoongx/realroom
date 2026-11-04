import { lazy, Suspense } from "react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Home } from "./pages/Home";
import { usePath } from "./lib/router";

/** The run viewer is its own chunk: the home page loads without it, and Home fetches it once idle. */
const StudioRoute = lazy(() => import("./pages/StudioRoute"));

export function App() {
  const path = usePath();
  const m = path.match(/^\/r\/([A-Za-z0-9_-]+)\/?$/);
  return (
    <ErrorBoundary key={path}>
      {m ? (
        <Suspense fallback={null}>
          <StudioRoute id={m[1]} />
        </Suspense>
      ) : (
        <Home />
      )}
    </ErrorBoundary>
  );
}
