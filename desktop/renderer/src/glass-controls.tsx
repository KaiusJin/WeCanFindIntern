import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { AppearanceControl, WindowToolbarControls } from "./layouts/AppChrome";
import "./features/discovery/PostingMenus";
import { ProfileEntries } from "./features/profile/ProfileEntries";
import { ProfileBasicsFields } from "./features/profile/ProfileFields";
import { ProfileImportSheet } from "./features/profile/ProfileImportSheet";
import { ApplicationForm } from "./features/applications/ApplicationRecord";
import { ApplicationsContents } from "./pages/Applications";
import { SettingsContents } from "./pages/Settings";
import { AssistantComposer, AssistantToolbar } from "./pages/Assistant";
import { AssistantDialogContents } from "./pages/AssistantDialogs";
import { mountDiscoveryControls } from "./features/discovery/mountDiscoveryControls";
import { mountCareerControls } from "./features/career/CareerControls";
import { FeedbackSheets } from "./components/FeedbackSheet";

// The existing DOM controllers bind listeners to these controls during main.js
// initialization. Mount the controls before that module runs.
const toolbar = document.querySelector<HTMLElement>(".window-toolbar")!;
flushSync(() => {
  createRoot(document.getElementById("feedback-sheets")!).render(<FeedbackSheets />);
  createRoot(toolbar).render(<WindowToolbarControls />);
  createRoot(document.getElementById("profile-repeat-sections")!).render(<ProfileEntries />);
  createRoot(document.getElementById("profile-basics-fields")!).render(<ProfileBasicsFields />);
  createRoot(document.getElementById("profile-import-sheet-controls")!).render(<ProfileImportSheet />);
  createRoot(document.getElementById("tab-tracker")!).render(<ApplicationsContents />);
  createRoot(document.getElementById("custom-job-dialog")!).render(<ApplicationForm />);
  createRoot(document.getElementById("settings-appearance")!).render(<AppearanceControl />);
  createRoot(document.getElementById("preferences-editor")!).render(<SettingsContents />);
  createRoot(document.getElementById("assistant-toolbar-controls")!).render(<AssistantToolbar />);
  createRoot(document.getElementById("assistant-composer-controls")!).render(<AssistantComposer />);
  createRoot(document.getElementById("assistant-dialog-controls")!).render(<AssistantDialogContents />);
  mountDiscoveryControls();
  mountCareerControls();
});
