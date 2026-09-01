"""
Shared in-memory state for the running bot process. Extracted out of
bot.py (phase C of the module split; see CLAUDE.md's change log) so that
once handlers are split across multiple files (phase D onward), they can
all import these same dict objects rather than each holding their own
copy.

Everything here is process-local and cleared on restart — there's no
persistence layer backing any of it (that's what services/access_store.py
and services/settings_store.py are for; this is purely mid-conversation
scratch state, most of it short-lived by design).

IMPORTANT: importers must only *mutate* these dicts (item assignment,
`.pop(...)`, `del d[key]`, iteration) and never reassign the name itself
(e.g. `pending_files = {}`) — a reassignment would rebind the local name to
a brand-new dict instead of mutating the shared one, silently breaking
every other module's view of the same state.
"""
from __future__ import annotations

from models.pending_file import PendingFile
from models.pending_photo import PendingPhoto

# One entry per file the bot has received but not finished processing/
# delivering yet, keyed by a short random pid.
pending_files: dict[str, PendingFile] = {}

# One entry per plain photo waiting for a watermark decision, keyed by pid.
pending_photos: dict[str, PendingPhoto] = {}

# user_id -> state tag, for the free-text "awaited input" flows (admin
# add/renew/disable/delete, settings text fields, archive passwords, ...).
awaiting_state: dict[int, str] = {}

# Transient scratch space for the multi-step "admin adds/renews a user"
# flows (chosen duration, manually-typed name/username while we wait for
# more messages). Keyed by the *admin's* user_id, not the target user's.
admin_flow: dict[int, dict] = {}

# chat_id (== user_id for private chats) -> job_id waiting for an archive
# password reply.
pending_passwords: dict[int, str] = {}

# job_id -> [(folder_name, message_id_in_destination_chat), ...], used to
# build a linked Table Of Contents once an archive job finishes.
job_folder_links: dict[str, list[tuple[str, int]]] = {}

# "worker" -> time.time() of the last heartbeat received through the
# bridge group (see handlers/bridge.py). Used by the admin /status
# command to notice a crashed worker without SSHing in. Kept as a dict
# per state.py's mutate-in-place rule.
worker_last_seen: dict[str, float] = {}

# user_id -> list of monotonic-ish (time.time) submission timestamps, used
# by utils/rate_limit.py to enforce the per-user file-submission cap.
# Lists are pruned in place; entries older than the window are dropped on
# every check.
user_submission_times: dict[int, list[float]] = {}

# user_id -> {dedup_key: time.time() confirmed}, used by utils/dedup.py to
# reject re-submitting the same file/URL while an earlier one is still
# fresh. dedup_key is a Telegram file_unique_id (uploads) or a normalized
# URL (link submissions). Entries older than
# Queue.DUPLICATE_SUBMISSION_WINDOW_MINUTES are pruned on every check.
recent_submission_keys: dict[int, dict[str, float]] = {}
