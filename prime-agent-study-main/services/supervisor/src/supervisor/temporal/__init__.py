"""Every session runs as a Temporal workflow.

- ``contracts``  — types shared by API, workflow and activities (workflow-sandbox safe)
- ``workflow``   — AgentSessionWorkflow: ordering, durability, messages
- ``activities`` — every session side effect, delegating to SessionManager
- ``sessions``   — the API's client facade (SessionWorkflows)
- ``worker``     — the worker process (``python -m supervisor.temporal.worker``)
"""
