"use client";

import { Suspense, useEffect, type JSX } from "react";
import { BrainCircuit, Link2, Network, Palette, Settings2, ShieldAlert, Tags } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { BrandingTab } from "@/components/admin/settings/BrandingTab";
import { ModelsTab } from "@/components/admin/settings/ModelsTab";
import { KnowledgeLinksTab } from "@/components/admin/settings/KnowledgeLinksTab";
import { TaxonomyTab } from "@/components/admin/settings/TaxonomyTab";
import { TestIpsTab } from "@/components/admin/settings/TestIpsTab";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageShellSkeleton } from "@/components/ui/PageShellSkeleton";
import { useDeepLinkParam } from "@/lib/use-deep-link";
import { useCan } from "@/lib/use-can";
import { prefetch } from "@/lib/query-cache";
import { MODELS_KEY, TAXONOMY_KEY, settingsApi } from "@/lib/settings-api";

type SettingsTab = "branding" | "taxonomy" | "models" | "knowledge" | "test-ips";

const SETTINGS_TABS: TabItem<SettingsTab>[] = [
  { id: "branding", label: "Branding", icon: Palette, description: "White-label names, colours, logos and login copy" },
  { id: "taxonomy", label: "Insights Taxonomy", icon: Tags, description: "Intents, bot-quality values and issues" },
  { id: "models", label: "Models & Pricing", icon: BrainCircuit, description: "LLM and embedding deployments, token prices" },
  { id: "knowledge", label: "Knowledge links", icon: Link2, description: "Allowed source URL domains and the upload rule" },
  { id: "test-ips", label: "Test IPs", icon: Network, description: "Manage IP addresses classified as test traffic" },
];

const TAB_PANELS: Record<SettingsTab, () => JSX.Element> = {
  branding: () => <BrandingTab key="branding" />,
  taxonomy: () => <TaxonomyTab key="taxonomy" />,
  models: () => <ModelsTab key="models" />,
  knowledge: () => <KnowledgeLinksTab key="knowledge" />,
  "test-ips": () => <TestIpsTab key="test-ips" />,
};

const isSettingsTab = (value: string | null): value is SettingsTab => SETTINGS_TABS.some((t) => t.id === value);

/** Permission-filtered Settings sections. */
export default function SettingsPage() {
  // useSearchParams (tab deep link) needs a Suspense boundary for static prerendering.
  return (
    <Suspense fallback={<PageShellSkeleton label="Loading settings" />}>
      <SettingsView />
    </Suspense>
  );
}

function SettingsView() {
  const canBrand = useCan("branding:write");
  const canTaxonomy = useCan("taxonomy:write");
  const canModels = useCan("models:write");
  const canKnowledge = useCan("knowledge:links");
  const canTestIps = useCan("testip:write");
  const [tabParam, setTabParam] = useDeepLinkParam("tab");
  const allowed: Record<SettingsTab, boolean> = {
    branding: canBrand,
    taxonomy: canTaxonomy,
    models: canModels,
    knowledge: canKnowledge,
    "test-ips": canTestIps,
  };
  const tabs = SETTINGS_TABS.filter((t) => allowed[t.id]);
  const fallback = tabs[0]?.id ?? "branding";
  const tab: SettingsTab = isSettingsTab(tabParam) && allowed[tabParam] ? tabParam : fallback;

  // Warm the other tabs when Settings opens (also on a direct link), so
  // Taxonomy and Models render from cache instead of a long skeleton (VA-37).
  useEffect(() => {
    if (canTaxonomy) void prefetch(TAXONOMY_KEY, (signal) => settingsApi.listTaxonomy(signal));
    if (canModels) void prefetch(MODELS_KEY, (signal) => settingsApi.getModelSettings(signal));
  }, [canTaxonomy, canModels]);

  return (
    <div className="flex min-h-full flex-col bg-surface-page text-fg">
      <div data-tour="settings-header">
        <DashboardHeader
          title="Settings"
          subtitle="Branding, insights taxonomy, models and pricing, knowledge links, and test IP configuration"
          icon={Settings2}
          showAudienceTabs={false}
        />
      </div>
      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-5 p-4 sm:p-6 lg:p-8">
        {!tabs.length ? (
          <EmptyState
            icon={ShieldAlert}
            title="Superadmin only"
            description="Branding, the insights taxonomy and model settings can only be changed by a superadmin."
          />
        ) : (
          <>
            <div data-tour="settings-tabs">
              <TabGroup
                tabs={tabs}
                activeTab={tab}
                onChange={(id) => setTabParam(id === fallback ? null : id)}
                ariaLabel="Settings sections"
              />
            </div>
            <div role="tabpanel" aria-label={SETTINGS_TABS.find((t) => t.id === tab)?.label}>
              {TAB_PANELS[tab]()}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
