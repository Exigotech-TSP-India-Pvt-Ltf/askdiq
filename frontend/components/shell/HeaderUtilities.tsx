"use client";

import React from "react";
import { Command } from "lucide-react";
import { useCommandCenter } from "@/components/shell/CommandCenter";
import { NotificationBell, type NotificationItem } from "@/components/shell/NotificationBell";
import { ProfileMenu } from "@/components/shell/ProfileMenu";
import { iconButton } from "@/components/ui/styles";

interface HeaderUtilitiesProps {
  notifications?: NotificationItem[];
  roleBadge?: React.ReactNode;
}

/** Palette button, notification bell and profile menu shared by every header. */
export function HeaderUtilities({ notifications, roleBadge }: HeaderUtilitiesProps) {
  const commands = useCommandCenter();
  return (
    <div className="flex items-center gap-2">
      {commands ? (
        <button
          type="button"
          onClick={commands.openPalette}
          aria-label="Open command palette"
          aria-keyshortcuts="Control+K Meta+K"
          title="Command palette (Ctrl/⌘ K)"
          className={`${iconButton} border border-line bg-surface-card shadow-sm`}
        >
          <Command className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
        </button>
      ) : null}
      <NotificationBell items={notifications} />
      <ProfileMenu roleBadge={roleBadge} />
    </div>
  );
}
