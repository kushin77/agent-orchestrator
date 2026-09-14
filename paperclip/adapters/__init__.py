"""The thin upstream-family adapters (EPIC #410).

Each ``adapters/<family>/`` package derives an upstream resource shape from the
fleet's own authoritative surfaces. It never writes a ledger, never persists a
projection, and never grants a privilege the fleet did not grant.

One module per seam contract (fleet -> upstream).
"""
