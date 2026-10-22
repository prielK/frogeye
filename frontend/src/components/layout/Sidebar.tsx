import { Building2Icon, GaugeIcon, Grid3x3Icon, HistoryIcon, NewspaperIcon, PackageIcon, RadarIcon, RadioTowerIcon, RssIcon, WorkflowIcon, type LucideIcon } from "lucide-react";
import { useId } from "react";
import { NavLink } from "react-router";
import { cn } from "@/lib/utils";

type NavItem = { to: string; label: string; icon: LucideIcon };

// The Briefing leads (PRODUCT.md: the flagship), then the signals, the two deep dives (each
// competitor, each JFrog product), the comparison of the two, and the raw feed they are built from.
const PRIMARY: NavItem[] = [
  { to: "/briefing", label: "Briefing", icon: NewspaperIcon },
  { to: "/signals", label: "Signals", icon: RadarIcon },
  { to: "/competitors", label: "Competitors", icon: Building2Icon },
  { to: "/products", label: "Products", icon: PackageIcon },
  { to: "/compare", label: "Compare", icon: Grid3x3Icon },
  { to: "/feed", label: "Feed", icon: RssIcon },
];
const ADMIN: NavItem[] = [
  { to: "/admin/sources", label: "Sources", icon: RadioTowerIcon },
  { to: "/admin/runs", label: "Runs", icon: HistoryIcon },
  { to: "/admin/agents", label: "Agent runs", icon: WorkflowIcon },
  { to: "/admin/evals", label: "Evals", icon: GaugeIcon },
];

function NavEntry({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  const Icon = item.icon;
  return (
    <li>
      <NavLink
        to={item.to}
        onClick={onNavigate}
        className={({ isActive }) =>
          cn(
            "group flex h-8 items-center gap-2.5 rounded-md px-2.5 text-body font-medium transition-colors duration-150",
            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
            isActive
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-foreground",
          )
        }
      >
        {({ isActive }) => (
          <>
            <Icon
              aria-hidden
              className={cn(
                "size-4 shrink-0 transition-colors duration-150",
                isActive ? "text-primary-ink" : "text-muted-foreground group-hover:text-foreground",
              )}
            />
            {item.label}
          </>
        )}
      </NavLink>
    </li>
  );
}

/** Primary navigation. Rendered in the desktop rail and inside the mobile sheet. */
export function Sidebar({ onNavigate, className }: { onNavigate?: () => void; className?: string }) {
  const adminId = useId();
  return (
    <nav aria-label="Main" className={cn("flex flex-col gap-6 p-3", className)}>
      <ul className="flex flex-col gap-0.5">
        {PRIMARY.map((item) => (
          <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
        ))}
      </ul>
      <div className="flex flex-col gap-1">
        <p id={adminId} className="px-2.5 text-meta font-medium text-muted-foreground">
          Admin
        </p>
        <ul aria-labelledby={adminId} className="flex flex-col gap-0.5">
          {ADMIN.map((item) => (
            <NavEntry key={item.to} item={item} onNavigate={onNavigate} />
          ))}
        </ul>
      </div>
    </nav>
  );
}
