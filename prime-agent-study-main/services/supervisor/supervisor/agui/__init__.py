"""A session as an AG-UI agent: our journal written as the AG-UI protocol's events.

AG-UI (https://docs.ag-ui.com) is the event protocol agent frontends speak: CopilotKit and any
other AG-UI client can follow one of our sessions, send it messages, and render its tool calls
and generated UI, and read the task it shows as the session's state. The events are the official ``ag-ui-protocol`` SDK's models, encoded by its
``EventEncoder``; ``translate`` maps our journal onto them, and ``supervisor.api.agui`` serves
them.
"""

from supervisor.agui.translate import A2UI_ACTIVITY, A2UI_OPERATIONS, TASK_STATE, AgUiTranslator

__all__ = ["A2UI_ACTIVITY", "A2UI_OPERATIONS", "TASK_STATE", "AgUiTranslator"]
