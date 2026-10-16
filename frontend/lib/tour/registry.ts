import type { PageTour, TourId } from "./types";
import { shellTour } from "./steps/shell";
import { dashboardTour } from "./steps/dashboard";
import { chatTour } from "./steps/chat";
import { documentsTour } from "./steps/documents";
import { visitorsTour } from "./steps/visitors";
import { usageTour } from "./steps/usage";
import { conversationsTour } from "./steps/conversations";
import { feedbackTour } from "./steps/feedback";
import { evaluationTour } from "./steps/evaluation";
import { governanceTour } from "./steps/governance";
import { analyticsTour } from "./steps/analytics";
import { userManagementTour } from "./steps/user-management";
import { settingsTour } from "./steps/settings";

export const TOURS: Record<TourId, PageTour> = {
  "shell": shellTour,
  "dashboard": dashboardTour,
  "chat": chatTour,
  "documents": documentsTour,
  "visitors": visitorsTour,
  "usage": usageTour,
  "conversations": conversationsTour,
  "feedback": feedbackTour,
  "evaluation": evaluationTour,
  "governance": governanceTour,
  "analytics": analyticsTour,
  "user-management": userManagementTour,
  "settings": settingsTour,
};
