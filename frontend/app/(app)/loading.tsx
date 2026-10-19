import { RouteProgressBar } from "@/components/ui/MascotLoader";
import { PageShellSkeleton } from "@/components/ui/PageShellSkeleton";

/**
 * Route-segment fallback while the next dashboard page loads: the page shell
 * with card skeletons (never a full-page loader), so navigation paints at once.
 */
export default function AppLoading() {
  return (
    <div className="relative h-full">
      <RouteProgressBar />
      <PageShellSkeleton />
    </div>
  );
}
