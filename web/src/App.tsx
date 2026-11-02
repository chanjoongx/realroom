import { MotionConfig } from "motion/react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Home } from "./pages/Home";
import { Studio } from "./pages/Studio";
import { usePath } from "./lib/router";

export function App() {
  const path = usePath();
  const m = path.match(/^\/r\/([A-Za-z0-9_-]+)\/?$/);
  return (
    <MotionConfig reducedMotion="user">
      <ErrorBoundary key={path}>{m ? <Studio key={m[1]} id={m[1]} /> : <Home />}</ErrorBoundary>
    </MotionConfig>
  );
}
