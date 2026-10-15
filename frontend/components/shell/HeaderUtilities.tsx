"use client";

import React from "react";
import { Command } from "lucide-react";
import { useCommandCenter } from "@/components/shell/CommandCenter";
import { NotificationBell, type NotificationItem } from "@/components/shell/NotificationBell";
import { ProfileMenu } from "@/components/shell/ProfileMenu";
import { RoleBadge } from "@/components/shell/RoleBadge";
import { iconButton } from "@/components/ui/styles";
import { useAuth } from "@/lib/auth-context";
import { useShellNotifications } from "@/lib/use-shell-notifications";

interface HeaderUtilitiesProps {
  notifications?: NotificationItem[];
  roleBadge?: React.ReactNode;
}

/**
 * Palette button, notification bell and profile menu shared by every header.
 * Defaults: the signed-in user's role badge and the shell notification feed.
 */
export function HeaderUtilities({ notifications, roleBadge }: HeaderUtilitiesProps) {
  const commands = useCommandCenter();
  const { role } = useAuth();
  const feed = useShellNotifications();
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
      <NotificationBell items={notifications ?? feed} />
      <ProfileMenu roleBadge={roleBadge ?? (role ? <RoleBadge role={role} /> : undefined)} />
    </div>
  );
}
