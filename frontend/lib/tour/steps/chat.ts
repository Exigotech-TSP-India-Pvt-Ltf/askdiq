import type { PageTour } from "../types";

export const chatTour: PageTour = {
  id: "chat",
  title: "AskDIQ",
  steps: [
    {
      placement: "center",
      title: "Chat with the assistant",
      body: "Ask questions in plain language and get answers grounded in your organisation's knowledge base. Anyone signed in can use it; every answer is checked before it is delivered and can be rated.",
    },
    {
      target: '[data-tour="chat-sessions"]',
      placement: "right",
      title: "Your chats",
      body: "Every conversation you have had, newest activity first. Chats are grouped by when they were last used, and each row shows the title (your first question) and its last activity. Open any chat, however old, to continue it. Hover a row to rename or delete it.",
    },
    {
      target: '[data-tour="chat-new"]',
      placement: "right",
      title: "Start a new chat",
      body: "Opens an empty conversation so earlier questions don't steer the next answer. Try it when you switch topics.",
    },
    {
      target: '[data-tour="chat-header"]',
      placement: "bottom",
      title: "Assistant and signed-in user",
      body: "The assistant's name and logo come from Settings > Branding. On the right is who you are signed in as; your chats are private to you.",
    },
    {
      target: '[data-tour="chat-prompts"]',
      placement: "top",
      title: "Suggested prompts",
      body: "An empty chat offers sample questions set by your admins. Click one to send it straight away; it is the quickest way to see what the assistant knows.",
    },
    {
      target: '[data-tour="chat-messages"]',
      placement: "left",
      title: "The conversation",
      body: "Your questions appear on the right and answers on the left. Three bouncing dots mean the answer is being prepared; it arrives in one piece.",
    },
    {
      target: '[data-tour="chat-answer"]',
      placement: "right",
      title: "An answer",
      body: "Answers are built only from documents in the knowledge base. If nothing relevant is found, the assistant says so rather than guessing.",
    },
    {
      target: '[data-tour="chat-sources"]',
      placement: "right",
      title: "Sources",
      body: "Chips name the documents that backed this answer. Hover one for an excerpt of the passage used. Answers that could not be grounded show no chips.",
    },
    {
      target: '[data-tour="chat-feedback"]',
      placement: "top",
      title: "Rate the answer",
      body: "Thumbs up marks it Helpful; thumbs down opens a short form for what went wrong and what it should have said. Ratings feed the Feedback page, where the team triages them.",
    },
    {
      target: '[data-tour="chat-message-actions"]',
      placement: "top",
      title: "Time, copy and delete",
      body: "When the message was sent (hover for the full date), Copy to put the text on your clipboard, and Delete to remove this question and its answer from the chat.",
    },
    {
      target: '[data-tour="chat-retry"]',
      placement: "right",
      title: "Retry a failed answer",
      body: "If a question times out or errors, the reason shows in red with a Retry button. Retry sends the same question again without retyping it.",
    },
    {
      target: '[data-tour="chat-composer"]',
      placement: "top",
      title: "Ask a question",
      body: "Type here and press Enter to send; Shift+Enter adds a new line. Questions can be up to 2,000 characters, and one question runs at a time.",
    },
    {
      placement: "center",
      title: "That's Chat",
      body: "Ask, check the sources, and rate what you get: your thumbs are how answers improve. Admins can see ratings on Feedback, full exchanges on Conversation Logs and the documents behind answers on Knowledge.",
    },
  ],
};
