import re


SESSION_PATH_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})$")
TURN_PATH_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})/turns$")
SESSION_CLIENT_PATH_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})/client$")
SESSION_COMMAND_ACTION_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})/commands/([^/]+)$")
SESSION_COMMAND_JOBS_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})/command-jobs$")
SESSION_COMMAND_JOB_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})/command-jobs/([A-Za-z0-9_-]{32,128})$")
COMMAND_JOB_UPDATE_RE = re.compile(r"^/v1/command-jobs/([A-Za-z0-9_-]{32,128})/updates$")
SESSION_COMMAND_JOB_STOP_RE = re.compile(r"^/v1/sessions/([A-Za-z0-9_-]{32})/command-jobs/([A-Za-z0-9_-]{32,128})/stop$")
CONVERSATION_COMMAND_JOB_STOP_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})/command-jobs/([A-Za-z0-9_-]{32,128})/stop$")
CLIENT_PATH_RE = re.compile(r"^/v1/clients/([A-Za-z0-9_-]{32})$")
SERVER_TRUST_PATH_RE = re.compile(r"^/v1/servers/([^/]+)/trust$")
SERVER_NAME_PATH_RE = re.compile(r"^/v1/servers/([^/]+)/name$")
CONVERSATION_PATH_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})$")
CONVERSATION_EVENTS_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})/events$")
CONVERSATION_TURN_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})/turns$")
CONVERSATION_STOP_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})/stop$")
CONVERSATION_BRANCH_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/branches/([A-Za-z0-9_-]{32})$"
)
CONVERSATION_ARCHIVE_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/archive$"
)
CONVERSATION_METADATA_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/metadata$"
)
CONVERSATION_RUNNER_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/runner$"
)
CONVERSATION_ATTACHMENTS_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})/attachments$")
ATTACHMENT_RE = re.compile(r"^/v1/conversations/([A-Za-z0-9_-]{32})/attachments/([A-Za-z0-9_-]{32})$")
CONVERSATION_COMMAND_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/commands/([^/]+)$"
)
CONVERSATION_FILE_EDIT_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/file-edits/([^/]+)$"
)
CONVERSATION_FILE_TOTAL_RE = re.compile(
    r"^/v1/conversations/([A-Za-z0-9_-]{32})/file-edits/([^/]+)/total/([^/]+)$"
)
RUNNER_CHECK_RE = re.compile(
    r"^/v1/runners/([A-Za-z0-9_-]{32})/check$"
)
RUNNER_UPDATE_RE = re.compile(
    r"^/v1/runners/([A-Za-z0-9_-]{32})/update$"
)
RUNNER_INSTALL_RE = re.compile(r"^/runner/install/([A-Za-z0-9_-]{32,128})$")
RUNNER_ENROLL_RE = re.compile(
    r"^/v1/runner-enrollments/([A-Za-z0-9_-]{32,128})$"
)
MEMORY_PATH_RE = re.compile(r"^/v1/memories/([A-Za-z0-9_-]{32})$")
AI_SERVER_PATH_RE = re.compile(r"^/v1/ai/servers/([A-Za-z0-9_-]{32})$")
AI_SERVER_MODELS_RE = re.compile(
    r"^/v1/ai/servers/([A-Za-z0-9_-]{32})/models$"
)