import { ChevronDownIcon, LogOutIcon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth";
import { initials, roleLabel } from "@/lib/roles";

/** Who is reading: name and role, with sign-out. */
export function PersonaMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);

  if (!user) return null;

  async function signOut() {
    setSigningOut(true);
    try {
      await logout();
      navigate("/login", { replace: true });
    } catch {
      toast.error("Couldn't sign out", { description: "Check your connection and try again." });
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-9 gap-2 px-1.5 data-[state=open]:bg-accent sm:pr-2"
          aria-label={`Account: ${user.name}, ${roleLabel(user.role)}`}
        >
          <Avatar size="sm" aria-hidden>
            <AvatarFallback>{initials(user.name)}</AvatarFallback>
          </Avatar>
          <span className="hidden items-baseline gap-1.5 sm:flex">
            <span className="text-body font-medium text-foreground">{user.name}</span>
            <span className="text-meta font-normal text-muted-foreground">{roleLabel(user.role)}</span>
          </span>
          <ChevronDownIcon aria-hidden className="size-3.5 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="flex items-center gap-3 py-2">
          <Avatar size="lg" aria-hidden>
            <AvatarFallback>{initials(user.name)}</AvatarFallback>
          </Avatar>
          <span className="flex min-w-0 flex-col gap-1">
            <span className="truncate text-body font-semibold">{user.name}</span>
            <span className="truncate text-meta font-normal text-muted-foreground">{user.email}</span>
          </span>
        </DropdownMenuLabel>
        <div className="flex items-center justify-between px-2 pb-2 text-meta text-muted-foreground">
          Reading as
          <Badge variant="primary">{roleLabel(user.role)}</Badge>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={signingOut}
          onSelect={(event) => {
            event.preventDefault();
            void signOut();
          }}
        >
          <LogOutIcon aria-hidden />
          {signingOut ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
