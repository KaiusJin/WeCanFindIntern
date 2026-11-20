import { ProfileImportSheet } from "../features/profile/ProfileImportSheet";
import { ProfileBasicsFields } from "../features/profile/ProfileFields";
import { Icon } from "../layouts/AppChrome";

export function Profile() {
  return (
    <div id="tab-profile" className="tab-pane" hidden>
      <section className="profile-workspace">
        <header className="profile-identity">
          <div className="profile-signature">
            <span className="profile-monogram" aria-hidden="true">
              <span id="profile-initials"></span>
              <Icon name="profile" />
            </span>
            <div>
              <h1 id="profile-display-name">Your profile</h1>
              <div className="profile-byline">
                <span className="profile-completion" role="status" hidden>
                  <Icon name="check" />
                  <span id="profile-completion-label"></span>
                </span>
                <span id="profile-display-location" hidden></span>
              </div>
            </div>
          </div>
          <div className="profile-header-tools">
            <div id="profile-import-sheet-controls" className="profile-import-actions"><ProfileImportSheet /></div>
            <span id="profile-save-status" className="inline-save-status" role="status" aria-atomic="true" />
          </div>
        </header>
        <div className="profile-editor-panel">
          <div
            className="profile-chapter-tabs glass"
            id="profile-chapters"
            role="tablist"
            aria-label="Profile sections"
          ></div>
          <div className="profile-document">
            <section
              id="profile-panel-basics"
              className="profile-basics"
              role="tabpanel"
              aria-labelledby="profile-tab-basics"
              data-section="basics"
            >
              <div id="profile-basics-fields"><ProfileBasicsFields /></div>
            </section>
            <div id="profile-repeat-sections"></div>
          </div>
        </div>
      </section>
    </div>
  );
}
