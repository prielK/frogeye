import { Navigate, type RouteObject } from "react-router";
import { ComparePage, CompetitorPage, CompetitorsPage, ProductPage, ProductsPage } from "@/app/lazyPages";
import { AppShell } from "@/components/layout/AppShell";
import { LoginPage } from "@/features/auth/LoginPage";
import { AgentRunDetail } from "@/features/admin/AgentRunDetail";
import { AgentRunsPage } from "@/features/admin/AgentRunsPage";
import { EvalsPage } from "@/features/admin/EvalsPage";
import { RunsPage } from "@/features/admin/RunsPage";
import { SourcesPage } from "@/features/admin/SourcesPage";
import { SignalAnalysisPage } from "@/features/analysis/SignalAnalysisPage";
import { BriefingPage } from "@/features/briefing/BriefingPage";
import { FeedPage } from "@/features/feed/FeedPage";
import { NotFoundPage } from "@/features/placeholder/PlaceholderPage";
import { SignalsPage } from "@/features/signals/SignalsPage";
import { RequireAuth } from "@/lib/auth";

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/briefing" replace /> },
      { path: "briefing/:date?", element: <BriefingPage /> },
      { path: "signals", element: <SignalsPage /> },
      // One signal's full analysis; the quick look stays the app's one drawer (`?signal=<id>`).
      { path: "signals/:id", element: <SignalAnalysisPage /> },
      // Who matters and who is moving; each competitor's and each JFrog product's deep dive.
      { path: "competitors", element: <CompetitorsPage /> },
      { path: "competitors/:slug", element: <CompetitorPage /> },
      { path: "products", element: <ProductsPage /> },
      { path: "products/:slug", element: <ProductPage /> },
      // JFrog's products against every competitor's, on one screen.
      { path: "compare", element: <ComparePage /> },
      { path: "feed", element: <FeedPage /> },
      { path: "admin/sources", element: <SourcesPage /> },
      { path: "admin/runs", element: <RunsPage /> },
      { path: "admin/agents", element: <AgentRunsPage /> },
      { path: "admin/agents/:id", element: <AgentRunDetail /> },
      { path: "admin/evals", element: <EvalsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
