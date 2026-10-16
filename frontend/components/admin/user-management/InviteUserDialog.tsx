"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { primaryButton, secondaryButton } from "@/components/ui/styles";
import {
  EMAIL_MAX_LENGTH,
  EMAIL_RE,
  NAME_MAX_LENGTH,
  ROLES,
  ROLE_LABEL,
  isSafeText,
  type Role,
} from "@/lib/user-management-api";
import { fieldClass } from "./parts";
import { useInvite } from "./use-user-actions";

/** Invite (pre-provision) a user by email; they sign in with SSO later. */
export function InviteUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invite = useInvite();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("operator");
  const [touched, setTouched] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const emailId = useId();
  const nameId = useId();
  const roleId = useId();
  const errorId = useId();

  useEffect(() => {
    if (!open) {
      setEmail("");
      setName("");
      setRole("operator");
      setTouched(false);
    }
  }, [open]);

  const trimmed = email.trim();
  const emailError =
    trimmed.length === 0
      ? "Enter an email address."
      : trimmed.length > EMAIL_MAX_LENGTH || !EMAIL_RE.test(trimmed)
        ? "Enter a valid email address."
        : null;
  const nameError = name.length > NAME_MAX_LENGTH || !isSafeText(name) ? "Name contains unsupported characters." : null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (emailError || nameError || invite.isPending) return;
    const created = await invite.mutate({ email: trimmed, role, ...(name.trim() ? { name: name.trim() } : {}) });
    if (created) onClose();
  }

  return (
    <Modal
      open={open}
      onClose={invite.isPending ? undefined : onClose}
      labelledBy={titleId}
      initialFocusRef={emailRef}
      className="max-w-md p-5"
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <div>
          <h2 id={titleId} className="text-sm font-semibold text-fg">
            Invite user
          </h2>
          <p className="mt-1 text-xs text-fg-muted">
            Creates an active account with this role. It links to their Microsoft identity on first sign-in.
          </p>
        </div>
        <div className="space-y-1.5">
          <label htmlFor={emailId} className="block text-xs font-medium text-fg-muted">
            Email
          </label>
          <input
            id={emailId}
            ref={emailRef}
            type="email"
            value={email}
            maxLength={EMAIL_MAX_LENGTH}
            autoComplete="off"
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={touched && Boolean(emailError)}
            aria-describedby={touched && emailError ? errorId : undefined}
            className={`w-full ${fieldClass}`}
          />
          {touched && emailError ? (
            <p id={errorId} className="text-[11px] text-status-danger-fg">
              {emailError}
            </p>
          ) : null}
        </div>
        <div className="space-y-1.5">
          <label htmlFor={nameId} className="block text-xs font-medium text-fg-muted">
            Name <span className="text-fg-subtle">(optional)</span>
          </label>
          <input
            id={nameId}
            value={name}
            maxLength={NAME_MAX_LENGTH}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={Boolean(nameError)}
            className={`w-full ${fieldClass}`}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor={roleId} className="block text-xs font-medium text-fg-muted">
            Role
          </label>
          <select id={roleId} value={role} onChange={(e) => setRole(e.target.value as Role)} className={`w-full ${fieldClass}`}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} disabled={invite.isPending} className={secondaryButton}>
            Cancel
          </button>
          <button type="submit" disabled={invite.isPending} aria-busy={invite.isPending} className={primaryButton}>
            Send invite
          </button>
        </div>
      </form>
    </Modal>
  );
}
