import { Skeleton, SkeletonCard, SkeletonGroup } from "@/components/ui/Skeleton";

/**
 * Immediate stand-in for a dashboard page while its route loads: the header
 * bar, a tab row and the card grid, in the page's own layout, so a click
 * paints at once and data fills in card by card (no full-page loader).
 */
export function PageShellSkeleton({ label = "Loading page", cards = 4 }: { label?: string; cards?: number }) {
  return (
    <SkeletonGroup label={label} className="flex min-h-full flex-col">
      <div className="flex h-16 items-center gap-3 border-b border-line bg-surface-card px-6">
        <Skeleton className="h-8 w-8 rounded-lg" />
        <div className="space-y-1.5">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-64" />
        </div>
      </div>
      <div className="mx-auto w-full max-w-[1400px] space-y-4 px-4 py-5 sm:px-6">
        <div className="flex gap-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-24 rounded-lg" />
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: cards }, (_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    </SkeletonGroup>
  );
}
