"use client";

import { Palette, Settings2, ShieldAlert, Tags } from "lucide-react";
import { DashboardHeader } from "@/components/admin/DashboardHeader";
import { TabGroup, type TabItem } from "@/components/admin/TabGroup";
import { BrandingTab } from "@/components/admin/settings/BrandingTab";
import { TaxonomyTab } from "@/components/admin/settings/TaxonomyTab";
import { EmptyState } from "@/components/ui/EmptyState";
import { useDeepLinkParam } from "@/lib/use-deep-link";
import { useCan } from "@/lib/use-can";

type SettingsTab = "branding" | "taxonomy";

const SETTINGS_TABS: TabItem<SettingsTab>[] = [
  { id: "branding", label: "Branding", icon: Palette, description: "White-label names, colours, logos and login copy" },
  { id: "taxonomy", label: "Insights Taxonomy", icon: Tags, description: "Intents, bot-quality values and issues" },
];

const isSettingsTab = (value: string | null): value is SettingsTab => value === "branding" || value === "taxonomy";

/** Superadmin Settings (newplan §14, planchanges4 F7): Branding and Insights Taxonomy. */
export default function SettingsPage() {
  const canBrand = useCan("branding:write");
  const canTaxonomy = useCan("taxonomy:write");
  const [tabParam, setTabParam] = useDeepLinkParam("tab");
  const tab: SettingsTab = isSettingsTab(tabParam) ? tabParam : "branding";

  return (
    <div className="flex min-h-full flex-col bg-surface-page text-fg">
      <DashboardHeader
        title="Settings"
        subtitle="White-label branding and the insights taxonomy (superadmin)"
        icon={Settings2}
        showAudienceTabs={false}
      />
      <div className="mx-auto w-full max-w-[1600px] flex-1 space-y-5 p-4 sm:p-6 lg:p-8">
        {!canBrand && !canTaxonomy ? (
          <EmptyState
            icon={ShieldAlert}
            title="Superadmin only"
            description="Branding and the insights taxonomy can only be changed by a superadmin."
          />
        ) : (
          <>
            <TabGroup
              tabs={SETTINGS_TABS}
              activeTab={tab}
              onChange={(id) => setTabParam(id === "branding" ? null : id)}
              ariaLabel="Settings sections"
            />
            <div role="tabpanel" aria-label={SETTINGS_TABS.find((t) => t.id === tab)?.label}>
              {tab === "branding" ? <BrandingTab key="branding" /> : <TaxonomyTab key="taxonomy" />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
