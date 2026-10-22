import { MoonIcon, SunIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTheme } from "@/lib/theme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const Icon = theme === "dark" ? SunIcon : MoonIcon;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Toggle theme"
          onClick={toggle}
          className="text-muted-foreground hover:text-foreground"
        >
          <Icon key={theme} className="animate-in duration-200 ease-out-expo fade-in-0 spin-in-[-30deg]" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}</TooltipContent>
    </Tooltip>
  );
}
