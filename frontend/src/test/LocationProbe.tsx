import { useEffect } from "react";
import { useLocation, type Location } from "react-router";

/** Reports the in-memory router's location, so tests can assert the URL (path and search). */
export function LocationProbe({ onChange }: { onChange: (location: Location) => void }) {
  const location = useLocation();
  useEffect(() => {
    onChange(location);
  }, [location, onChange]);
  return null;
}
