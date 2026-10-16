import { MascotLoader, RouteProgressBar } from "@/components/ui/MascotLoader";

/** Route-segment fallback while the next dashboard page loads. */
export default function AppLoading() {
  return (
    <div className="relative h-full">
      <RouteProgressBar />
      <MascotLoader variant="page" label="Loading page" />
    </div>
  );
}
