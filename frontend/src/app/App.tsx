import { useRoutes } from "react-router";
import { routes } from "./router";

/** Route tree. The router itself (browser or memory) is supplied by the caller. */
export function App() {
  return useRoutes(routes);
}
