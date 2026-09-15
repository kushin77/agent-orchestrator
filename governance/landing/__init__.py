"""Code-native lane landing for agent-orchestrator (issue #764).

Landing a verified lane used to be a manual sequence — push the branch, open a
PR, run the pre-merge contract, merge, delete the branch, close the issue — so
every delivery ended with "…and then a human does the last mile". This package
is that last mile as code: ``make land`` drives it with no human step and no
console (GR-5 declared change path, GR-15 code-native automation).

The contract is documented in ``README.md`` next to this module. It is imported
as ``governance.landing`` (the repository root is on ``sys.path``).
"""
