# Requirements Document

## Introduction

This feature gives users explicit control over Project titles. Today, a Project's title is auto-generated from the first 50 characters of the story text whenever no title is supplied at creation time, and titles are never validated for uniqueness. The change makes a user-supplied Title required at Project creation, removes the auto-generation fallback entirely, adds an in-place edit affordance on the projects list page, and enforces uniqueness scoped to the owning user. Existing Projects whose Titles were previously auto-generated keep their stored Title as-is and are treated as user-editable Titles going forward, but no new Project will ever receive an auto-generated Title.

## Glossary

- **Editor**: The web-based video editor application consisting of a frontend UI and a Python backend API.
- **Project**: A user's working session containing story text, generated audio, subtitle data, styling configuration, and background image selection. Each Project has exactly one owner identified by `owner_id`.
- **Title**: A short human-readable label for a Project, displayed in the projects list and on the editing screen, distinct from the Project's internal identifier.
- **Owner**: The authenticated user (`owner_id`) who created a Project and is the only principal permitted to read or modify it.
- **Projects_Page**: The frontend view that lists all Projects belonging to the authenticated Owner.
- **Title_Validator**: The backend component that validates a Title against length, character, whitespace, and uniqueness rules before persisting it.

## Requirements

### Requirement 1: User-Supplied Title Required at Project Creation

**User Story:** As an Owner, I want to be required to specify a Title when I create a Project, so that every Project is identifiable by a label I chose rather than by a slice of the story text or by no label at all.

#### Acceptance Criteria

1. WHEN an Owner submits a Project creation request that includes a Title that passes all Title_Validator rules defined in Requirement 4, THE Editor SHALL store the trimmed Title on the new Project.
2. IF an Owner submits a Project creation request that omits the Title field, THEN THE Editor SHALL reject the request with an error indicating the Title is required.
3. IF an Owner submits a Project creation request whose Title is empty or contains only whitespace after trimming, THEN THE Editor SHALL reject the request with an error indicating the Title must not be empty.
4. IF an Owner submits a Project creation request whose Title fails any other Title_Validator rule defined in Requirement 4 or the uniqueness rule in Requirement 3, THEN THE Editor SHALL reject the request with the specific validation error returned by the Title_Validator and SHALL NOT persist the Project.

### Requirement 2: Title Editing from the Projects Page

**User Story:** As an Owner, I want to rename a Project directly from the projects list, so that I can correct or refine titles without opening the editor.

#### Acceptance Criteria

1. WHEN an Owner activates the rename control for a Project on the Projects_Page, THE Editor SHALL display an editable input pre-filled with the current Title.
2. WHEN an Owner submits a new Title from the Projects_Page rename control, THE Editor SHALL persist the new Title on the targeted Project and refresh the Projects_Page to display the updated Title.
3. IF an Owner submits a Title change that fails validation, THEN THE Editor SHALL reject the change, leave the stored Title unchanged, and display the specific validation error returned by the Title_Validator.
4. WHILE a rename request for a Project is in flight, THE Editor SHALL prevent the Owner from submitting an additional rename request for the same Project.

### Requirement 3: Title Uniqueness Within an Owner's Projects

**User Story:** As an Owner, I want each of my Projects to have a distinct Title, so that I can tell them apart in the projects list.

#### Acceptance Criteria

1. THE Title_Validator SHALL treat two Titles as duplicates when, after trimming leading and trailing whitespace, they are equal under a case-insensitive Unicode comparison.
2. WHEN an Owner attempts to create a Project with a Title that duplicates the Title of another Project owned by the same Owner, THE Editor SHALL reject the request with an error indicating the Title is already in use.
3. WHEN an Owner attempts to rename a Project to a Title that duplicates the Title of another Project owned by the same Owner, THE Editor SHALL reject the request with an error indicating the Title is already in use.
4. WHERE the candidate Title equals the Project's own current stored Title, THE Title_Validator SHALL accept the Title as non-duplicate so that no-op renames succeed.
5. THE Title_Validator SHALL scope uniqueness checks to Projects owned by the same Owner and SHALL NOT consider Projects owned by other Owners.

### Requirement 4: Title Validation Rules

**User Story:** As an Owner, I want the Editor to enforce sensible Title rules, so that Titles render predictably and cannot be empty, hidden, or unbounded.

#### Acceptance Criteria

1. THE Title_Validator SHALL trim leading and trailing whitespace from a candidate Title before applying any other validation rule.
2. IF the trimmed candidate Title has a length of 0 characters, THEN THE Title_Validator SHALL reject the Title with an error indicating the Title must not be empty.
3. IF the trimmed candidate Title has a length greater than 100 characters, THEN THE Title_Validator SHALL reject the Title with an error indicating the maximum length is 100 characters.
4. IF the trimmed candidate Title contains any character whose Unicode category is `Cc` (control characters) other than no characters, THEN THE Title_Validator SHALL reject the Title with an error indicating control characters are not permitted.
5. THE Title_Validator SHALL accept Titles containing letters, digits, whitespace internal to the Title, and printable punctuation in any Unicode script, including Chinese characters.

### Requirement 5: Backwards Compatibility for Existing Projects

**User Story:** As an Owner with Projects created before this feature shipped, I want my existing Projects to keep working with whatever Title they currently have, so that I am not forced to rename anything before I can use the Projects_Page.

#### Acceptance Criteria

1. THE Editor SHALL preserve the stored Title of every Project that existed before this feature shipped without modification, including Titles that were previously generated from the first 50 characters of `story_text`.
2. THE Editor SHALL treat the stored Title of every pre-existing Project as a user-editable Title that the Owner may keep, edit, or replace via Requirement 2.
3. THE Editor SHALL NOT auto-generate a Title for any Project created after this feature ships, regardless of the value of `story_text` or any other Project field.
4. WHEN an Owner first loads the Projects_Page after this feature ships and two or more pre-existing Projects owned by the same Owner have duplicate Titles under the comparison rule in Requirement 3.1, THE Editor SHALL display all such Projects without modifying their stored Titles and SHALL allow the Owner to rename any of them subject to Requirement 3.
5. WHEN an Owner attempts to create a new Project or rename an existing Project to a Title that duplicates a pre-existing Title under the same Owner, THE Editor SHALL reject the request per Requirement 3.2 or Requirement 3.3, regardless of whether the duplicate originated before this feature shipped.

### Requirement 6: Title Persistence and Retrieval

**User Story:** As an Owner, I want a Project's Title to be returned everywhere the Project appears, so that the rename I made on the Projects_Page is visible in the editor and in API responses.

#### Acceptance Criteria

1. WHEN the Editor returns a Project summary in response to a list-projects request, THE Editor SHALL include the Project's current stored Title.
2. WHEN the Editor returns a single Project in response to a get-project request, THE Editor SHALL include the Project's current stored Title.
3. WHEN an Owner successfully renames a Project, THE Editor SHALL update the Project's `updated_at` timestamp to the time the rename was persisted.
4. WHEN an Owner successfully renames a Project, THE Editor SHALL increment the Project's optimistic-concurrency `version` field by 1.
