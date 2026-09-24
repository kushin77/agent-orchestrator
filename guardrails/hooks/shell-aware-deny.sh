#!/usr/bin/env bash
# ============================================================================
# guardrails/hooks/shell-aware-deny.sh — shell-aware Bash deny guard (#476).
#
# PROBLEM (#476, filed as a follow-up to #465/#470): .claude/settings.json's
# `permissions.deny` list matches Bash tool_input.command by literal
# substring (Claude Code's documented permission-matching semantics). That
# means any shell construct that changes the literal command TEXT while
# producing the SAME effective command at execution time bypasses the deny
# pattern — e.g. `git push --forc''e` never literally contains the substring
# `--force`, so `Bash(git push* --force*)` doesn't match, yet bash itself
# resolves the adjacent-quote empty-string concatenation to
# `git push --force` before executing it. Same for `--for""ce`, `--forc\e`,
# `$(:)--force`, etc. — the bypass space is combinatorially infinite for a
# literal-substring matcher (#476's own analysis).
#
# FIX (#476 option 3): this is a REAL PreToolUse hook on the `Bash` tool
# (not `Agent` — see dispatch-tier-guard.sh/dispatch-rule9-guard.sh for that
# separate matcher) that:
#   1. Reads tool_input.command from the hook's JSON stdin.
#   2. Normalizes it with Python's shlex (word-splitting + quote/
#      concatenation resolution) rather than another literal-substring check
#      — this is a real shell-command-aware parse, so `--forc''e` and
#      `--for""ce` resolve to the single token `--force` the same way bash's
#      own parser would.
#   3. Re-checks the NORMALIZED, re-joined command against the SAME deny
#      patterns already declared in .claude/settings.json's
#      permissions.deny list — read from that file at hook-run time so
#      there is exactly one source of truth for the pattern list (no
#      duplicated/drifting copy in this script).
#
# SCOPE: only the settings.json patterns that are shell-command patterns
# meaningful to re-check post-normalization, i.e. the `Bash(...)` deny
# entries (force-push / push-to-main patterns from #465/#470, plus
# `terraform apply`). Non-Bash-shaped deny entries (there are none today)
# would not apply here.
#
# NORMALIZATION LIMITS (documented, not silently papered over): shlex
# resolves quoting/concatenation/whitespace but is NOT a full bash parser —
# it does not expand variables, command substitution, or arithmetic. A
# command like `git push --force$(true)` or `F=--force; git push "$F"`
# would still evade this (and the settings.json literal matcher, and
# honestly any static local check) because the actual flag value is only
# known at execution time, not at parse time. This hook closes the
# documented #476 bypass CLASS (quote/concatenation splitting resolvable by
# shell lexing alone), not every conceivable dynamic-command bypass — the
# server-side ruleset remains the actual backstop per #465.
#
# FAILURE POSTURE — deliberately NOT dispatch-tier-guard.sh's "malformed
# input -> allow" posture. This hook is a security boundary ON the Bash tool
# itself, built specifically to close a matcher-bypass gap; a hook whose own
# parse failure silently allows the exact ambiguous/adversarial input it
# exists to catch would just relocate the bypass one layer down (craft a
# command that also breaks OUR parser, get waved through). So:
#   - Command present, shlex parses cleanly, normalized form does not match
#     any deny pattern -> ALLOW.
#   - Command present, shlex parses cleanly, normalized form matches a deny
#     pattern -> DENY (loud, blocking), citing the matched pattern.
#   - Command present but shlex CANNOT parse it (genuinely malformed shell,
#     e.g. unbalanced quotes) -> DENY (fail CLOSED). Ambiguous/unparseable
#     input on a security-boundary check is exactly the posture #476 asks
#     for: the local deny is defense-in-depth backstopped by the server-side
#     main-protection ruleset (#465), so failing closed here costs only a
#     blocked local command (re-typeable, correctable) — never a missed
#     bypass — while the server-side gate still holds the real line for
#     anything that manages to reach `git push` regardless.
#   - No command field at all (not a Bash-shaped call, or tool_input missing
#     it) -> ALLOW (nothing to check).
#   - Cannot even parse the outer hook JSON, OR jq/python3 both unavailable
#     -> DENY (fail CLOSED) with a loud stderr explanation — same reasoning:
#     this hook exists to be the shell-aware check, so an environment where
#     it cannot run at all must not silently look identical to "checked and
#     clean."
#   - .claude/settings.json itself unreadable/unparseable -> DENY (fail
#     CLOSED) — the pattern list is the check; if it can't be read, the
#     check cannot be performed, and this hook does not default to "assume
#     safe" for a security boundary.
#
# Denial output shape (Claude Code hook contract), mirroring the existing
# Agent-tool hooks' JSON deny payload:
#   { "hookSpecificOutput": { "hookEventName": "PreToolUse",
#       "permissionDecision": "deny", "permissionDecisionReason": "..." } }
# ============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SETTINGS="${CMR_SHELL_DENY_SETTINGS:-$ROOT/.claude/settings.json}"

# Passive metrics ledger (lane: metrics-ledger, #444): a deny here is a
# signal worth counting (fires on every Bash call, so this is the normal
# path — no new command anyone must remember). Non-fatal; never touches the
# deny/allow decision itself.
if [[ -f "$ROOT/scripts/lib/metrics.sh" ]]; then
  # shellcheck source=/dev/null
  source "$ROOT/scripts/lib/metrics.sh" 2>/dev/null || true
fi

warn_stderr() {
  echo "shell-aware-deny: $1" >&2
}

deny() {
  # $1 = permissionDecisionReason. JSON-string-escape before embedding — the
  # reason can echo attacker-influenceable content (the command itself). See
  # dispatch-tier-guard.sh's deny() for the same rationale: a naive printf
  # here would let a crafted command containing a literal '"' break out of
  # the JSON string and inject keys into hookSpecificOutput. If neither
  # python3 nor jq can escape it, fail closed via plain non-JSON stderr +
  # non-zero exit (Claude Code hooks support that as a deny path too)
  # rather than risk emitting a corrupt/injectable payload.
  local escaped
  escaped="$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null)" \
    || escaped="$(printf '%s' "$1" | jq -Rs '.' 2>/dev/null)"
  if [ -n "$escaped" ]; then
    printf '{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": %s } }\n' "$escaped"
  else
    warn_stderr "DENY (no safe JSON escaper available — python3/jq both failed): $1"
  fi
  if command -v metrics_emit >/dev/null 2>&1; then
    metrics_emit "deny" "shell-aware-deny" "bash-command" "" "${SESSION_ID:-}" 2>/dev/null || true
  fi
  exit 2
}

if ! command -v python3 >/dev/null 2>&1; then
  warn_stderr "python3 not available — cannot shell-normalize the command to check for quote-split bypasses. FAIL CLOSED (this hook exists specifically to close that gap; an environment where it can't run must not look identical to 'checked and clean')."
  deny "shell-aware-deny hook cannot run: python3 is unavailable, so the Bash command cannot be shlex-normalized for a shell-aware deny check. Denying rather than silently allowing an unverifiable command through what is meant to be a security boundary."
fi

STDIN_JSON="$(cat 2>/dev/null)"
if [ -z "$STDIN_JSON" ]; then
  warn_stderr "empty stdin — cannot evaluate. FAIL CLOSED."
  deny "shell-aware-deny hook received empty stdin (no hook payload) — cannot evaluate the Bash command. Denying rather than silently allowing."
fi

# Best-effort session_id for the metrics ledger row deny() writes below.
# python3 is already confirmed present above; failure here must never
# affect the ALLOW/DENY decision, so it's swallowed and left empty.
SESSION_ID="$(printf '%s' "$STDIN_JSON" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("session_id") or "")
except Exception:
    print("")' 2>/dev/null)"

# ---- extract tool_name / command, and the current deny patterns, all via a
# single python3 pass (shlex lives here too; no bash-side re-parsing of
# anything security-relevant). -----------------------------------------------
RESULT="$(python3 - "$STDIN_JSON" "$SETTINGS" <<'PYEOF'
import fnmatch
import json
import re
import shlex
import sys

stdin_json, settings_path = sys.argv[1], sys.argv[2]

def fail(reason):
    print(json.dumps({"decision": "deny", "reason": reason}))
    sys.exit(0)

def allow():
    print(json.dumps({"decision": "allow"}))
    sys.exit(0)

# ---- parse the hook payload ----
try:
    payload = json.loads(stdin_json)
    if not isinstance(payload, dict):
        raise ValueError("top-level hook payload is not an object")
except Exception as exc:
    fail(f"could not parse hook stdin JSON ({exc}); failing closed.")

tool_name = payload.get("tool_name")
tool_input = payload.get("tool_input")
if tool_input is None:
    tool_input = {}
if not isinstance(tool_input, dict):
    fail("hook payload's tool_input is not an object; failing closed.")

# Only Bash-shaped calls carry a command worth normalizing. Anything else
# (including a Bash-matcher call somehow missing tool_input.command) has
# nothing for this hook to check.
command = tool_input.get("command")
if not isinstance(command, str) or command == "":
    allow()

# ---- shlex-normalize the command ----
# posix=True resolves quote/backslash/adjacent-fragment concatenation the
# same way a POSIX shell's lexer would -- this is what turns
# `git push --forc''e` into the tokens ["git", "push", "--force"], closing
# the literal-substring bypass. punctuation_chars=True keeps shell
# metacharacters (;, |, &, (, ), <, >) as their own tokens instead of
# silently merging into adjacent words, so a chained/piped command doesn't
# accidentally get treated as one opaque token.
lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
lexer.whitespace_split = True
try:
    tokens = list(lexer)
except ValueError as exc:
    # Genuinely malformed shell (e.g. an unbalanced quote) -- shlex raises
    # ValueError. This is exactly the "can't parse it" case #476 (and this
    # hook's own header) says must fail CLOSED: an unparseable command is
    # the most ambiguous possible input to a matcher-bypass check, and
    # ambiguity here must resolve to deny, not allow.
    fail(f"could not shell-normalize command (malformed shell syntax: {exc}); "
         f"failing closed rather than allowing an unparseable command past a "
         f"check built specifically to catch shell-quote-splitting bypasses. "
         f"Original command: {command!r}")

normalized = " ".join(tokens)

# ---- load deny patterns from .claude/settings.json (single source of
# truth -- do not duplicate the list here) ----
try:
    with open(settings_path, "r", encoding="utf-8") as fh:
        settings = json.load(fh)
except Exception as exc:
    fail(f"could not read/parse {settings_path} to load deny patterns ({exc}); "
         f"failing closed -- this hook's entire check is comparing against "
         f"that pattern list, so if it can't be read, the check cannot be "
         f"performed, and a security boundary does not default to 'assume "
         f"safe' on its own config failing to load.")

deny_patterns = (
    (settings.get("permissions") or {}).get("deny") or []
)
if not isinstance(deny_patterns, list):
    fail(f"{settings_path}'s permissions.deny is not a list; failing closed.")

globs = [
    pat[len("Bash("):-1]
    for pat in deny_patterns
    if isinstance(pat, str) and pat.startswith("Bash(") and pat.endswith(")")
]

# ---- command-position + interpreter-aware matching (#647) ----
# settings.json's deny globs are anchored at the start of the command
# ("terraform apply*"), so a literal/prefix matcher misses anything that puts
# the denied command anywhere other than token 0. This matcher therefore
# evaluates every glob at every COMMAND POSITION of the parsed token stream,
# and recurses into any text that a wrapper would hand to a shell.
#
# Command positions recognised (#647):
#   - start of input, and after a separator: ; | & && || ( ) { } newline
#   - after an env-var assignment prefix:      FOO=bar terraform apply
#   - after a "transparent" wrapper:           sudo/env/nohup/timeout/xargs/...
#   - the ARGUMENT of an interpreter wrapper:  bash -c '...', su -c '...',
#     eval ..., here-strings (<<< '...'), python3 -c/node -e string literals
#   - the body of a command substitution:      $(...) and backticks
# Heads are basename-normalised, so `/usr/bin/terraform apply` and
# `./terraform apply` still match a `terraform apply*` glob.
#
# DELIBERATE OVER-BLOCKING (do NOT "fix" this into fail-open): a candidate is
# built as the token stream from the command position to END OF INPUT, and any
# `-c`-style argument is re-lexed as a command. That means some contrived but
# harmless strings can trip a glob's tail. That is the intended direction: per
# #647 a false prompt costs one keystroke, a missed `terraform apply` costs
# infrastructure. Where a construct cannot be confidently decomposed we DENY
# and say so in permissionDecisionReason.
SEPARATORS = {";", "|", "&", "&&", "||", "(", ")", "{", "}", "\n"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "busybox", "ash", "fish"}
# Wrappers that run their non-flag argument list as a command, unchanged.
TRANSPARENT = {"sudo", "doas", "env", "nohup", "timeout", "xargs", "stdbuf",
               "nice", "ionice", "command", "builtin", "exec", "setsid",
               "time", "chroot", "proot", "script", "unbuffer", "runuser",
               "su", "watch", "parallel", "flock"}
CODE_INTERPRETERS = {"python", "python2", "python3", "node", "nodejs",
                     "perl", "ruby", "php", "deno", "bun"}
CODE_FLAGS = {"-c", "-e", "-r", "--command", "eval"}
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
MAX_DEPTH = 6
STRING_LITERAL_RE = (
    r"""'((?:[^'\\]|\\.)*)'"""
    r'''|"((?:[^"\\]|\\.)*)"'''
    r"""|`((?:[^`\\]|\\.)*)`"""
)


def lex(text):
    lx = shlex.shlex(text, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    try:
        return list(lx)
    except ValueError:
        return None  # malformed inner string -> caller treats as opaque


def base(tok):
    return tok.rsplit("/", 1)[-1]


def substitutions(text):
    """Bodies of $(...) and backtick substitutions in the raw text, so a
    denied command hidden inside one is still evaluated."""
    out = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            out.append(text[i + 2:j - 1])
            i = j
        elif text[i] == "`":
            j = text.find("`", i + 1)
            if j == -1:
                break
            out.append(text[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


def candidate_starts(toks, i):
    """From base command position i, return every index that is ALSO a command
    position because only env assignments / transparent wrappers (and those
    wrappers' option flags) precede it."""
    starts, k, seen_wrapper, n = [], i, False, len(toks)
    while k < n and toks[k] not in SEPARATORS:
        starts.append(k)
        tok = toks[k]
        if ENV_ASSIGN_RE.match(tok):
            k += 1
            continue
        if base(tok) in TRANSPARENT:
            seen_wrapper, k = True, k + 1
            continue
        if seen_wrapper:
            # Past a transparent wrapper we cannot know which tokens are the
            # wrapper's own options/values (`timeout 5 ...`, `sudo -u root ...`)
            # versus the start of the wrapped command, so EVERY remaining
            # position up to the next separator is treated as a candidate
            # command start. Over-blocks by design (#647 fail-closed posture).
            k += 1
            continue
        break
    return starts


def match_tokens(toks, depth=0):
    """Return (glob, candidate) on a deny hit, else None."""
    if not toks or depth > MAX_DEPTH:
        return None
    n = len(toks)
    for i in range(n):
        if i != 0 and toks[i - 1] not in SEPARATORS:
            continue
        for s in candidate_starts(toks, i):
            rest = toks[s:]
            if not rest:
                continue
            # The candidate for a PLAIN glob match is bounded at the next
            # separator, i.e. it is one SIMPLE command. Running it to end of
            # input instead would let a later, unrelated token satisfy a
            # glob's tail -- measured: `git push origin feat/x && git checkout
            # main` matched `git push* main*` on the trailing `main` and was
            # denied, which hard-blocks an everyday push-then-switch-back
            # under bypassPermissions. Bounding keeps every #647 attack caught
            # (each one puts the denied binary AT a command position, so the
            # bounded slice still contains it) while not eating routine work.
            # Globs that legitimately SPAN a separator -- the
            # `curl* | bash*` family -- are handled by the whole-raw-text
            # fallback after this loop, which is why that fallback is load-
            # bearing and must not be removed.
            end = s
            while end < n and toks[end] not in SEPARATORS:
                end += 1
            simple = toks[s:end]
            if not simple:
                continue
            # Match the raw candidate AND a basename-normalised head, so
            # /usr/bin/terraform and ./terraform still hit `terraform apply*`.
            for cand in {" ".join(simple),
                         " ".join([base(simple[0])] + simple[1:])}:
                for glob in globs:
                    if fnmatch.fnmatchcase(cand, glob):
                        return (glob, cand)
            head = base(rest[0])
            # eval WORD... -> the shell re-parses the joined words
            if head == "eval":
                inner = lex(" ".join(rest[1:]))
                hit = match_tokens(inner, depth + 1) if inner else None
                if hit:
                    return hit
            for j in range(1, len(rest)):
                if rest[j] in SEPARATORS:
                    break
                # Any `-c`/`-e`-style argument is re-lexed as a command.
                # Applied regardless of head, which covers `$SHELL -c`,
                # `su -c` and unknown wrappers -- fail closed by design.
                if rest[j] in CODE_FLAGS and j + 1 < len(rest):
                    arg = rest[j + 1]
                    inner = lex(arg)
                    hit = match_tokens(inner, depth + 1) if inner else None
                    if hit:
                        return hit
                    # interpreter source: treat string literals as commands
                    if (head in CODE_INTERPRETERS
                            or head.startswith(("python3.", "node"))):
                        for m in re.finditer(STRING_LITERAL_RE, arg):
                            lit = next(g for g in m.groups() if g is not None)
                            li = lex(lit)
                            hit = match_tokens(li, depth + 1) if li else None
                            if hit:
                                return hit
                # here-string / here-doc body: bash <<< 'terraform apply'
                if rest[j].startswith("<") and j + 1 < len(rest):
                    inner = lex(rest[j + 1])
                    hit = match_tokens(inner, depth + 1) if inner else None
                    if hit:
                        return hit
    return None


class UnparseableFragment(Exception):
    """One line of an otherwise-parseable command could not be lexed."""


def evaluate(text, depth=0):
    """Evaluate one raw command string: splice line continuations, then
    newline-split (shlex's whitespace_split eats newlines, so they never
    survive as separator tokens), then token matching, then command
    substitutions."""
    if depth > MAX_DEPTH:
        return None
    # A backslash-newline is a line continuation: the shell splices the two
    # lines into ONE command before parsing. We must do the same BEFORE
    # splitting on newlines, or `terraform \<newline>apply` gets split into the
    # fragments `terraform \` and `apply`, neither of which matches
    # `terraform apply*` -- a live bypass of the whole deny floor.
    text = re.sub(r"\\\n", "", text)
    for line in text.split("\n"):
        if not line.strip():
            continue
        toks = lex(line)
        if toks is None:
            # An unparseable LINE inside an otherwise-parseable command. The
            # outer lexer does not cover this after the newline split, so
            # skipping here would be a fail-OPEN hole. Per this hook's stated
            # posture, a fragment we cannot confidently decompose is a DENY.
            raise UnparseableFragment(line)
        hit = match_tokens(toks, depth)
        if hit:
            return hit
    for sub in substitutions(text):
        hit = evaluate(sub, depth + 1)
        if hit:
            return hit
    return None

matched_pattern = None
try:
    hit = evaluate(command)
except UnparseableFragment as exc:
    fail(f"could not shell-normalize part of the command (unparseable fragment: "
         f"{str(exc)!r}); failing closed rather than allowing a command this "
         f"check could not fully decompose. Original command: {command!r}")
if hit:
    matched_pattern = f"Bash({hit[0]})"
    normalized = hit[1]
else:
    # Whole-raw-text fallback, restricted to globs that DELIBERATELY span a
    # pipeline/separator -- the `curl* | bash*` / `wget* | sh*` family, whose
    # whole point is "fetch here, execute there". Those can never match a
    # single simple command, so the command-position matcher above cannot see
    # them and this fallback is load-bearing for that family.
    #
    # It is deliberately NOT applied to ordinary globs. Doing so re-introduced
    # a measured false positive: `git push* main*` matched the whole string of
    # `git push origin feat/x && git checkout main` via the trailing `main`,
    # hard-blocking an everyday push-then-switch-back under bypassPermissions.
    # Nothing is lost by the restriction -- a non-spanning glob that genuinely
    # describes one command is already checked at every command position.
    for glob in globs:
        if any(sep in glob for sep in ("|", "&", ";")) and \
                fnmatch.fnmatchcase(command, glob):
            matched_pattern = f"Bash({glob})"
            break

if matched_pattern is not None:
    fail(
        f"Bash command matches denied pattern {matched_pattern!r} after "
        f"shell-aware normalization (this closes the #476 quote-splitting "
        f"bypass: the literal text you typed does not contain the denied "
        f"substring, but a real shell parse resolves it to the same "
        f"effective command). Original command: {command!r} -> normalized: "
        f"{normalized!r}."
    )

allow()
PYEOF
)"
PY_RC=$?

if [ "$PY_RC" != "0" ] || [ -z "$RESULT" ]; then
  warn_stderr "python3 normalizer crashed or produced no output (rc=$PY_RC). FAIL CLOSED."
  deny "shell-aware-deny hook's python3 normalizer failed to produce a decision (rc=$PY_RC). Denying rather than silently allowing an unverifiable command through a security boundary."
fi

DECISION="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("decision",""))' 2>/dev/null)"
REASON="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason",""))' 2>/dev/null)"

if [ "$DECISION" = "deny" ]; then
  warn_stderr "$REASON"
  deny "$REASON"
fi

exit 0
