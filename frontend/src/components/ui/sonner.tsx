import { Toaster as Sonner } from "sonner";
import { useTheme } from "@/hooks/useTheme";

/** App-wide toast outlet, themed to match the current light/dark mode. */
function Toaster() {
  const { theme } = useTheme();
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      closeButton
      toastOptions={{
        classNames: {
          toast: "group border bg-background text-foreground shadow-lg",
          description: "text-muted-foreground",
        },
      }}
    />
  );
}

export { Toaster };
