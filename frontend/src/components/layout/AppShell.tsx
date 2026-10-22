import { Outlet } from "react-router";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";
import { SignalDrawerHost } from "@/components/signal/SignalDrawerHost";

/** The signed-in frame: top bar, navigation rail, the page, and the signal drawer any page can open. */
export function AppShell() {
  return (
    <div className="min-h-dvh bg-background">
      <a
        href="#main"
        className="sr-only rounded-md bg-card px-3 py-2 text-body font-medium shadow-floating ring-2 ring-ring focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-(--z-tooltip)"
      >
        Skip to content
      </a>
      <TopBar />
      <div className="flex">
        <aside className="sticky top-14 hidden h-[calc(100dvh-3.5rem)] w-60 shrink-0 overflow-y-auto border-r border-sidebar-border bg-sidebar md:block">
          <Sidebar />
        </aside>
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 outline-none">
          {/* 76rem for every page; the Briefing (`data-wide`) uses the width of the screen, up to 112rem. */}
          <div className="max-w-[76rem] px-4 py-8 has-[[data-wide]]:max-w-[112rem] md:px-6 lg:px-10">
            <Outlet />
          </div>
        </main>
      </div>
      <SignalDrawerHost />
    </div>
  );
}
