"use client";

import { useState } from "react";
import { Network } from "lucide-react";
import { TestIpManager } from "@/components/admin/usage/TestIpManager";
import { HeaderIconButton } from "@/components/ui/HeaderIconButton";
import { useAuth } from "@/lib/auth-context";
import { invalidate } from "@/lib/query-cache";
import { SettingsCard } from "./fields";

export function TestIpsTab() {
  const { token } = useAuth();
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <SettingsCard
        title="Test IPs"
        description="Manage IP addresses used to identify test traffic and exclude it from production visitor metrics."
        icon={Network}
        actions={
          <HeaderIconButton
            icon={Network}
            label="Manage Test IPs"
            tooltip="Configure test IPs"
            showLabel
            aria-haspopup="dialog"
            onClick={() => setIsOpen(true)}
          />
        }
      >
        <p className="text-xs text-fg-muted">
          Configure the IP addresses that should be classified as test traffic.
        </p>
      </SettingsCard>
      {token && (
        <TestIpManager
          token={token}
          isOpen={isOpen}
          onClose={() => setIsOpen(false)}
          onConfigChanged={() => {
            invalidate("admin/usage/summary");
            invalidate("visitors/");
            invalidate("conversations/");
          }}
        />
      )}
    </>
  );
}
