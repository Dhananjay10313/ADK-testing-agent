import logging
import json
from typing import AsyncGenerator, Any, Dict
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

# --- Configure logging to show output in the console ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def get_feature_list(state):
    features = state.get("features_to_process", [])
    # Handle if accidentally stored as JSON string
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except Exception:
            # If it's not valid JSON, fallback to treating as plain string (unlikely, log or raise)
            features = []
    # At this point, features is always a list
    return features


class TestCaseProcessorAgent(BaseAgent):
    """
    An ADK agent that processes features, aggregates test cases, and terminates
    a loop. This version correctly handles logging and event authoring.
    """

    def __init__(self, name: str = "TestCaseProcessorAgent", **kwargs):
        """Initializes the agent."""
        super().__init__(name=name, **kwargs)
        # The self.logger attribute is no longer initialized here to prevent the error.

    @override
    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """
        Implements the agent's logic with correct logging and event creation.
        """
        # --- Get the logger instance here ---
        logger = logging.getLogger(self.name)

        # Log the entire session state for debugging
        try:
            state_json = json.dumps(ctx.session.state, indent=2)
            state_dict = json.loads(state_json)
            tlst=json.dumps(state_dict['features_to_process'])
            tllst=json.loads(tlst)
            logger.info(f"Current session state on invocation:\n{tllst[0]}")
        except TypeError:
            logger.info(f"Current session state (raw): {ctx.session.state}")

        state = ctx.session.state
        state_delta: Dict[str, Any] = {}
        output_message = "Processed a feature and updated test cases."

        features_to_process = state.get("features_to_process", [])
        try:
            features_to_process = json.loads(features_to_process)
            logger.info(f"Current session state on invocation:\n{features_to_process}")
        except Exception:
            logger.error(f"Failed to parse features_to_process string as JSON list{features_to_process}")


        # If the processing list is empty, terminate the loop.
        if not features_to_process:
            logger.info(f"Current session state on invocation:\n{state}")
            logger.info("No features left to process. Terminating loop.")
            yield Event(actions=EventActions(escalate=True), author=self.name)
            return

        # Process the first feature in the list
        features_to_process.pop(0)
        state_delta["features_to_process"] = features_to_process

        # Aggregate the test cases
        current_testcases = state.get("current_testcases")
        aggregated_testcases = list(state.get("aggregated_testcases", []))
        if current_testcases:
            aggregated_testcases.append(current_testcases)
            state_delta["aggregated_testcases"] = aggregated_testcases
        
        # Clear the current_testcases variable for the next iteration
        state_delta["current_testcases"] = ""
        
        # Check for termination condition *after* processing
        if not features_to_process:
            output_message = "Processed the final feature. Terminating loop."
            logger.info(f"Current session state on invocation:\n{state}")
            logger.info(output_message)
            yield Event(
                actions=EventActions(state_delta=state_delta, escalate=True),
                author=self.name
            )
            return

        # If the loop is not finished, yield an event to update the state
        logger.info(output_message)
        yield Event(
            actions=EventActions(state_delta=state_delta),
            author=self.name
        )

