import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import { BarChart3, Columns3, FolderOpen, Menu, MessageSquare, Moon, Search, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useBids } from "@/hooks/useBids";
import { useTheme } from "@/hooks/useTheme";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Bids", icon: FolderOpen, end: true },
  { to: "/search", label: "Search", icon: Search, end: false },
  { to: "/ask", label: "Ask", icon: MessageSquare, end: false },
  { to: "/compare", label: "Compare", icon: Columns3, end: false },
  { to: "/eval", label: "Evaluation", icon: BarChart3, end: false },
];

/** Product mark + name, shared by the sidebar and the mobile header. */
function Brand({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
        R
      </span>
      <span className="truncate font-semibold">RFP Intelligence</span>
    </div>
  );
}

/** Vertical navigation list; used in the sidebar and the mobile drawer. */
function NavLinks({ onNavigate, large = false }: { onNavigate?: () => void; large?: boolean }) {
  return (
    <nav className="flex flex-col gap-1" aria-label="Main">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-md px-3 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              large ? "py-2.5 text-base" : "py-2 text-sm",
              isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )
          }
        >
          <Icon className="h-4 w-4 shrink-0" />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

/** Hamburger + slide-in drawer with the navigation, shown below the lg breakpoint. */
function MobileNav() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Close the drawer whenever the route changes (back/forward included).
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  return (
    <>
      <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}>
        <Menu />
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-[82vw] max-w-xs p-4 sm:p-5">
          <SheetHeader>
            <SheetTitle asChild>
              <Brand />
            </SheetTitle>
            <SheetDescription className="sr-only">Site navigation</SheetDescription>
          </SheetHeader>
          <NavLinks large onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
    </>
  );
}

/** Top-bar bid switcher: navigates to /bids/:bidId. */
function BidSwitcher() {
  const bids = useBids();
  const navigate = useNavigate();
  const { bidId } = useParams<{ bidId: string }>();
  const location = useLocation();
  const items = bids.data ?? [];
  const current = bidId && items.some((b) => b.bid_id === bidId) ? bidId : undefined;

  const onChange = (value: string) => {
    const target = `/bids/${encodeURIComponent(value)}`;
    const onSearch = location.pathname.startsWith("/search");
    navigate(onSearch ? `/search?bid_id=${encodeURIComponent(value)}` : target);
  };

  return (
    <Select value={current ?? ""} onValueChange={onChange} disabled={items.length === 0}>
      <SelectTrigger className="w-full min-w-0 max-w-[15rem] sm:w-56" aria-label="Switch bid">
        <SelectValue placeholder={bids.isLoading ? "Loading bids..." : items.length ? "Switch bid" : "No bids"} />
      </SelectTrigger>
      <SelectContent>
        {items.map((b) => (
          <SelectItem key={b.bid_id} value={b.bid_id}>
            <span className="font-mono">{b.bid_id}</span>
            {b.title ? <span className="text-muted-foreground"> · {b.title}</span> : null}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" onClick={toggle} aria-label={label}>
          {theme === "dark" ? <Sun /> : <Moon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/**
 * App frame. Desktop (lg and up): fixed left sidebar + top bar.
 * Smaller screens: top bar with a hamburger that opens the navigation drawer.
 */
export function Layout() {
  return (
    <div className="flex min-h-dvh w-full">
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r bg-card lg:flex">
        <div className="flex h-14 items-center border-b px-4">
          <Brand />
        </div>
        <div className="p-3">
          <NavLinks />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-card/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-card/80 sm:gap-3 sm:px-4 lg:px-6">
          <MobileNav />
          <Brand className="lg:hidden" />
          <div className="flex min-w-0 flex-1 items-center justify-end gap-2 lg:justify-start">
            <BidSwitcher />
          </div>
          <ThemeToggle />
        </header>
        <main className="mx-auto w-full max-w-screen-2xl flex-1 px-4 py-4 sm:px-6 sm:py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
