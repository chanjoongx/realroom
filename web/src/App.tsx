import { Home } from "./pages/Home";
import { Studio } from "./pages/Studio";
import { usePath } from "./lib/router";

export function App() {
  const path = usePath();
  const m = path.match(/^\/r\/([A-Za-z0-9_-]+)\/?$/);
  if (m) return <Studio key={m[1]} id={m[1]} />;
  return <Home />;
}
