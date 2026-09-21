# scripts/lib/unset-git-env.sh — neutralise an exported git repo/identity
# environment before a gate seeds a throwaway fixture repo (issue #1642, SP-11).
#
# `git -C <path>` does NOT override an exported GIT_DIR: git honors the
# environment variable ahead of -C, so a caller (a hook running in a linked
# worktree, in particular — every lane here is one, golden rule 15) that
# exports GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE makes a fixture-seeding check
# write into the REAL repository the variable points at instead of its own
# scratch fixture. scripts/check-ratchets.sh:677-678 found and fixed this
# already; this file is that same remedy, shared instead of re-derived.
#
# Usage: source this file once, near the top of the script, before any
# fixture-seeding function runs.
#
# ---knowledge---
# module_id: scripts.lib.unset-git-env
# system: scripts
# app: lib
# solution_class: template
# patterns: [neutralise-inherited-env]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: []
# invariants: "must be sourced before any fixture-seeding function runs, or an inherited GIT_DIR wins over -C"
# gotchas: ""
# related: ["#1642"]
# do_not_duplicate: null
# ---knowledge---
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR GIT_PREFIX
unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
