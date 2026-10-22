import { useAuth } from "@/lib/auth";
import { editionFor } from "@/lib/roles";
import { useSignalParam } from "./param";
import { SignalDrawer } from "./SignalDrawer";

/**
 * The app's one signal drawer, the quick look, rendered by the signed-in shell (AppShell) and
 * opened by `?signal=<id>` on any route (see ./param). Its actions are the reader's own
 * edition's. The full analysis is the page at `/signals/:id`.
 */
export function SignalDrawerHost() {
  const { signalId, closeSignal } = useSignalParam();
  const { user } = useAuth();
  return <SignalDrawer signalId={signalId} edition={editionFor(user?.role)} onClose={closeSignal} />;
}
