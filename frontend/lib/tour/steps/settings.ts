import type { PageTour } from "../types";

const t = (id: string): string => `[data-tour="settings-${id}"]`;

export const settingsTour: PageTour = {
  id: "settings",
  title: "Settings",
  steps: [
    {
      title: "Settings",
      body: "Superadmin-only controls for the whole product: the white-label brand everyone sees, and the insights taxonomy used to label conversations.",
      placement: "center",
    },
    {
      target: t("tabs"),
      title: "Two sections",
      body: "Branding sets names, colours, logos and login copy. Insights Taxonomy manages the intents, bot-quality values and issues the classifier assigns.",
      placement: "bottom",
      tab: "branding",
    },
    {
      target: t("identity"),
      title: "Names and contacts",
      body: "Product, assistant and company names appear across the console, login page and chat. The access contact email is shown to people waiting for access.",
      placement: "right",
    },
    {
      target: t("colours"),
      title: "Colours with contrast checks",
      body: "Primary generates the full 50 to 950 brand ramp. Badges check WCAG AA live: Primary must reach 4.5:1 in light and dark or the save is rejected; Accent (3:1) and Tint (4.5:1) are advisory. Try this: pick a lighter primary and watch the badges.",
      placement: "right",
    },
    {
      target: t("logos"),
      title: "Logos and favicon",
      body: "Drop or choose files for the light and dark logo, the light and dark mark, and the favicon. Uploads happen immediately, but users only see them after you save; the bin icon returns a slot to the default.",
      placement: "right",
    },
    {
      target: t("login-copy"),
      title: "Login page copy",
      body: "Eyebrow, headline, supporting copy and card description for the sign-in page. Leave a field empty to hide it.",
      placement: "right",
    },
    {
      target: t("links"),
      title: "Privacy, terms and help links",
      body: "Use https:// or a relative path such as /privacy. The Help URL powers the help link users see; empty links are hidden.",
      placement: "right",
    },
    {
      target: t("sample-questions"),
      title: "Chat sample questions",
      body: "One to eight suggested prompts shown on an empty chat, to help new users get started.",
      placement: "right",
    },
    {
      target: t("preview"),
      title: "Live preview",
      body: "Your unsaved draft rendered in light and dark side by side, whatever theme you are in. The header shows Unsaved changes, or the saved version number.",
      placement: "left",
    },
    {
      target: t("save-bar"),
      title: "Save, discard or reset",
      body: "Save changes makes the brand live for everyone at once; Discard drops your edits. Reset to defaults restores the seeded brand after a confirmation. If someone else saved first, the latest version is reloaded and your edits are discarded.",
      placement: "top",
    },
    {
      target: t("taxonomy-kinds"),
      title: "Taxonomy kinds",
      body: "Intents (what the visitor asked about), Bot quality (how well the assistant answered) and Issues (why an answer fell short). Badges count active entries.",
      placement: "bottom",
      tab: "taxonomy",
    },
    {
      target: t("taxonomy-table"),
      title: "Edit, reorder and archive",
      body: "Edit a label, colour or (for intents) group, then press the row's Save. Use the arrows to reorder. The archive icon removes an entry from filters and the classifier while old labels keep showing; core codes cannot be archived and codes are never reused.",
      placement: "top",
    },
    {
      target: t("taxonomy-show-archived"),
      title: "Show archived",
      body: "Reveal archived entries, dimmed and read-only. There is no unarchive, so archive with care.",
      placement: "left",
    },
    {
      target: t("taxonomy-add"),
      title: "Add an entry",
      body: "Give a permanent code (capitals, digits and _, e.g. BILLING), a label and an optional colour or group. The new entry is offered to the classifier from then on.",
      placement: "top",
    },
    {
      title: "You're set",
      body: "Brand changes go live for everyone on save, and taxonomy changes shape how conversations are labelled. Related: Analytics and Conversations use these labels, User Management controls who can open Settings.",
      placement: "center",
    },
  ],
};
