# Profile and Resume Import Reference

The detailed module guide is [Profile and Resume Import](modules/profile.md). This file remains the stable contract and security reference for Profile uploads.

## Profile v1

The saved profile has `schema_version=profile.v1` and these sections:

- `basics`: name, preferred name, email, phone, city, region, country, LinkedIn, GitHub, and portfolio links;
- `education`;
- `work_experience`;
- `projects`;
- `skills`;
- `certifications`;
- `languages`;
- `awards`.

The persistence layer stores the profile root and repeated sections in PostgreSQL child tables. The repository returns a structured profile and completion percentage for the UI and career tools.

## Accepted resume inputs

Only English text-based `.pdf` and `.tex` resumes are accepted.

### PDF validation

- filename extension and declared MIME must identify PDF;
- content must begin with `%PDF-` and contain a valid trailer;
- maximum size is 8 MB;
- page count must be within the configured limit;
- encrypted/password-protected files are rejected;
- malformed files and active content are rejected;
- extracted text must stay within the expansion limit and meet the meaningful-text threshold;
- image-only/scanned resumes are rejected;
- PDF HTTP/HTTPS/mailto link annotations are appended when missing from extracted text.

### LaTeX validation

- maximum size is 1 MB;
- content must be UTF-8 text with no NUL bytes;
- source must have recognizable document/resume structure;
- file-access and executable commands are rejected;
- comments, formatting wrappers, environments, and common escapes are converted to plain text;
- source is parsed as text and is never compiled or executed.

## Import behavior

```text
upload → validate → extract text → parse profile draft → review → confirm → save profile
```

An import creates a draft and does not silently overwrite the saved profile. Confirmation applies the accepted field values. Users can discard drafts and delete resume documents/import records.

## HTTP routes

- `GET /api/v1/profile`
- `PUT /api/v1/profile`
- `GET /api/v1/profile/export`
- `POST /api/v1/profile/resumes`
- `GET /api/v1/profile/resumes`
- `DELETE /api/v1/profile/resumes/{resume_id}`
- `POST /api/v1/profile/imports/{import_id}/confirm`

Invalid extension, MIME mismatch, magic bytes, structure, language, active-content, or extraction checks produce user-readable 422 errors. Missing resources produce 404.

## Privacy boundary

Resume text and Profile values are personal data. They are sent to a remote AI provider only when a user explicitly invokes an AI career feature and selects that provider. Provider API keys are held in browser settings and are not stored in Profile tables.
