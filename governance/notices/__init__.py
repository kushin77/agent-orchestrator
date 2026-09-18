"""Standing notices and their acks (issue #1269, EPIC #1268).

A rule between runtimes is a RECORD with a schema and an acknowledgement, never a
sentence in a chat. This package is the notice half of that:

  * ``runtime_registry`` derives the registered-runtime set from the registry the
    repository already owns (the AgentPack release snapshots plus the gateway
    catalog) -- a runtime is REGISTERED, so nobody maintains a list of who must
    ack;
  * ``notice_records`` holds the notice/ack records, their refusals, and the
    evaluator the gate drives;
  * ``ledger`` chains every write (a publish, an ack) with the digest of the record
    it wrote, so a record edited after the fact is named;
  * ``controls`` holds the declaration in ``controls.yaml`` to the code it
    describes and to the script that arms each refusal;
  * ``cli`` is the verb surface (``runtimes``, ``publish``, ``ack``, ``evaluate``,
    ``ledger``, ``controls``) a runtime's transport or the director calls.

The gate of record is ``scripts/check-notice-acks.sh`` (auto-discovered into
``scripts/verify.sh`` by ``scripts/discover-checks.sh``).
"""
