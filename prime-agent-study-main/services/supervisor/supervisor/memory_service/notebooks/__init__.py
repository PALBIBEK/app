"""Notebooks: a user's long-term facts for one agent, with provenance and full history.

The one store of long-term memory. Each notebook belongs to one tenant, one
owner (principal) and one exact agent version (``agent_pin``); its facts carry
provenance (sensitivity, sources) that only ever tightens, and every change is a
revision, so history, restore and stale-write checks stand on the log. Each live
fact is also an item in the semantic bank (scope: tenant, ``user:<owner>``, the
agent's name), so recall by meaning and erasure reach notebook facts too.
"""
