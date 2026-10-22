import type { CSSProperties } from "react";
import { CircleCheckIcon, InfoIcon, Loader2Icon, OctagonXIcon, TriangleAlertIcon } from "lucide-react";
import { Toaster as Sonner, type ToasterProps } from "sonner";
import { useTheme } from "@/lib/theme";

function Toaster(props: ToasterProps) {
  const { theme } = useTheme();

  return (
    <Sonner
      theme={theme}
      className="toaster group"
      icons={{
        success: <CircleCheckIcon className="size-4 text-primary-ink" />,
        info: <InfoIcon className="size-4 text-muted-foreground" />,
        warning: <TriangleAlertIcon className="size-4 text-warning" />,
        error: <OctagonXIcon className="size-4 text-threat" />,
        loading: <Loader2Icon className="size-4 animate-spin text-muted-foreground" />,
      }}
      toastOptions={{
        classNames: {
          // sonner injects unlayered CSS (13px toasts, 12px buttons), which beats a layered
          // utility; `!` pins the type scale's sizes (DESIGN.md, the Four Sizes Rule).
          toast: "font-sans text-body! shadow-floating!",
          description: "text-muted-foreground!",
          actionButton: "text-meta!",
          cancelButton: "text-meta!",
        },
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
          zIndex: "var(--z-toast)",
        } as CSSProperties
      }
      {...props}
    />
  );
}

export { Toaster };
