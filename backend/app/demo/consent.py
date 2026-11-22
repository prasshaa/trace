"""The synthetic demo consent document. Installed (title and SHA-256 only) by scripts/demo_setup.py; its text is
shown by the API only when the installed document's hash matches this text. It is not legal text."""
import hashlib

# v1 ("trace-demo-synthetic-resume-processing-v1", a synthetic-resume placeholder) is retired: it did not describe what
# this version stores. v2 describes the implemented data flow (D48) for LOCAL PRODUCT TESTING; it is not legal text and
# an external release still needs a legal and privacy review.
LEGACY_VERSIONS = ("trace-demo-synthetic-resume-processing-v1",)
VERSION = "trace-local-review-resume-processing-v2"
TITLE = "Local testing notice: reading your resume to build your private career profile (not reviewed by a lawyer)"
BODY = (
    "This notice is for local product testing of Trace. It describes how this version works. It has not been reviewed "
    "by a lawyer.\n\n"
    "If you agree, Trace reads the resume you upload to build your private career profile. Your file is read on Trace's "
    "own server, in memory, and isn't sent to any other company or AI service. The file and its name aren't kept. Trace "
    "keeps the file's size, its type and a fingerprint (a SHA-256 hash): the fingerprint shows whether the same file is "
    "uploaded again, but the file can't be rebuilt from it.\n\n"
    "Before you confirm anything, Trace saves a private draft of the career details it found (jobs, dates, descriptions, "
    "education, skills, certifications and projects) so you can review it now or when you come back. Your name, email "
    "address, phone number and links are removed from these details first. Only you can see the draft. It isn't part "
    "of your profile and isn't used for your plan. It stays, also when you sign out, until you confirm it, discard it, "
    "replace it with another resume or withdraw this agreement. There is no automatic deletion date.\n\n"
    "When you confirm, the details you kept become your profile, together with your corrections and a record of which "
    "upload they came from (its fingerprint, not the file), and the draft is deleted.\n\n"
    "Your Trace account details are kept separately from your resume. Sharing de-identified career patterns to help "
    "other users is a separate choice with its own notice; agreeing here doesn't include it. Only if you make that "
    "choice does Trace keep an encrypted copy of the name, email addresses, phone numbers and links found in your resume, "
    "to check that they never appear in what you share. That copy is deleted when you stop sharing.\n\n"
    "You can withdraw this agreement at any time. Withdrawing stops any further reading of your resumes and deletes any "
    "unconfirmed draft. It doesn't delete your confirmed profile; ask whoever runs this Trace server if you want that "
    "deleted.")
BODY_SHA256 = hashlib.sha256(BODY.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- production notice (release, D52)
# v3 is the notice for the public site, after the owner's privacy and legal review of real resume processing (recorded
# in docs/DECISIONS.md D52). It describes the same tested lifecycle as v2, plus replacement and clearing, for a server on
# which sharing with other users is NOT offered (TRACE_CONTRIBUTION_POLICY=disabled), so no contact details are kept.
# Installed in the production database by scripts/install_production_notice.py; never by demo_setup.py.
PRODUCTION_VERSION = "trace-resume-processing-v3"
PRODUCTION_TITLE = "Reading your resume to build your private career profile"
PRODUCTION_BODY = (
    "If you agree, Trace reads the resume you upload to build your private career profile and your career plan.\n\n"
    "Your file. Trace reads your PDF or Word file on its own server, in memory, in a short-lived process. The file and "
    "its name aren't saved anywhere, and the file isn't sent to any other company or AI service. Trace keeps the file's "
    "size, its type and a fingerprint (a SHA-256 hash). The fingerprint shows whether the same file is uploaded again, "
    "but the file can't be rebuilt from it. Trace also keeps a short processing record when reading fails or when you "
    "confirm: the date and the outcome, with no content from your resume.\n\n"
    "Before you confirm. Trace saves the career details it found as a private draft: your jobs, dates, descriptions, "
    "education, skills, certifications and projects. Your name, email address, phone number and links are removed from "
    "them first and aren't kept. Only you can see the draft. It isn't part of your profile and isn't used for your plan. "
    "It stays, also when you sign out, until you confirm it, discard it, replace it with another resume or withdraw this "
    "agreement. There is no automatic deletion date.\n\n"
    "After you confirm. The details you kept become your profile, with your corrections and a record of which upload "
    "they came from (its fingerprint, not the file), and the draft is deleted. Your profile and your plans are private "
    "to you: other users can't see them.\n\n"
    "Updating from a new resume. You can upload a newer resume from your profile page. It becomes a new private draft "
    "that you review. Nothing in your profile changes until you confirm. When you do, the draft replaces the career "
    "details in your profile and the plans built from them, in one step. If you discard the draft, or anything fails, "
    "your profile stays as it was.\n\n"
    "Clearing your profile. You can clear your career profile at any time from your profile page. This deletes your "
    "jobs, employers, descriptions, skills, education, certifications and projects, role matches, corrections, saved "
    "plans, any draft, the records of your uploads and your processing records. It keeps your account, so you can sign "
    "in again, your permission choices and your target-role preferences.\n\n"
    "Sharing. Sharing career patterns with other users isn't offered on this site. Nothing from your profile is added to "
    "any shared pool, and Trace keeps no copy of your contact details.\n\n"
    "You can withdraw this agreement at any time in Profile settings. Withdrawing stops any further reading of your "
    "resumes and deletes any unconfirmed draft. It doesn't delete your confirmed profile: clear it from your profile "
    "page if you want it deleted.")
PRODUCTION_BODY_SHA256 = hashlib.sha256(PRODUCTION_BODY.encode("utf-8")).hexdigest()

# Every notice text this server holds, by version: an installed document's text is shown only when its version and
# hash match one of these.
KNOWN = {
    VERSION: ("resume_processing", BODY, BODY_SHA256),
    PRODUCTION_VERSION: ("resume_processing", PRODUCTION_BODY, PRODUCTION_BODY_SHA256),
}


# --------------------------------------------------------------------------- shared contribution (Phase 6)
# A separate purpose (deidentified_shared_dataset) with its own document and decision. Never implied by the
# resume-processing consent, by uploading or by any terms.
CONTRIBUTION_VERSION = "trace-demo-synthetic-shared-contribution-v1"
CONTRIBUTION_TITLE = ("Synthetic demo notice: contribute my de-identified career history to Trace's shared career "
                      "intelligence (local demo only, not legal text)")
CONTRIBUTION_BODY = (
    "This is a local demonstration notice. It is not a legal consent text and it has not been reviewed by counsel.\n\n"
    "Separately from analysing your own profile, Trace can add a de-identified copy of your career history to its shared "
    "reference pool. That pool is used to calculate aggregate career statistics, observed career transitions and the "
    "routes shown to other people. Before anything enters the pool, direct identifiers such as your name, email "
    "address, phone number, profile links and addresses are removed; your employers, education, roles, dates, skills "
    "and achievements are kept because they are what makes the statistics useful. Only aggregates are shown to others, "
    "and categories shared by fewer than five people are never shown.\n\n"
    "This is optional. Saying no does not affect your own pathways. You can withdraw at any time; your contribution is "
    "then excluded immediately and its de-identified copy is deleted.")
CONTRIBUTION_BODY_SHA256 = hashlib.sha256(CONTRIBUTION_BODY.encode("utf-8")).hexdigest()

KNOWN[CONTRIBUTION_VERSION] = ("deidentified_shared_dataset", CONTRIBUTION_BODY, CONTRIBUTION_BODY_SHA256)

DOCUMENTS = {
    "resume_processing": (VERSION, TITLE, BODY, BODY_SHA256),
    "deidentified_shared_dataset": (CONTRIBUTION_VERSION, CONTRIBUTION_TITLE, CONTRIBUTION_BODY, CONTRIBUTION_BODY_SHA256),
}
