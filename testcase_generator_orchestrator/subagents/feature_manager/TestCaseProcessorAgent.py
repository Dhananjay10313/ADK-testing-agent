import logging
import json
from typing import AsyncGenerator, Any, Dict
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

import uuid
import json
import logging
from vertexai.generative_models import GenerativeModel

logger = logging.getLogger(__name__)

async def parse_testcases_to_json(current_testcases: str, model_name: str = "gemini-2.0-flash") -> dict:
    """
    Parses markdown table test cases into structured JSON format using Vertex AI.
    
    Args:
        current_testcases: Markdown table string containing test cases
        model_name: Model identifier (default: gemini-2.0-flash)
        
    Returns:
        Dictionary with parsed test cases and compliance information
    """
    
    parsing_prompt = f"""
You are a test case parser. Extract the following from the markdown table:

1. **Testcase Title**: Generate a concise title summarizing the test cases (max 10 words)
2. **Test Cases**: Extract each row into format [Sr.No, Test Description, Expected Result]
3. **Compliance Rules**: Extract all compliance rule IDs from the "Applied Compliance Rules" section

Input:
{current_testcases}

Return ONLY a valid JSON object in this exact format:
{{
  "testcase_id": "generate-random-uuid",
  "Testcase Title": "concise title here",
  "testcases": [
    ["1.", "Test description...", "Expected result..."],
    ["2.", "Another test...", "Expected result..."]
  ],
  "compliance_ids": ["HIPAA", "ISO 27001", "FDA", "ISO 9001"]
}}

Important:
- Extract Sr.No exactly as shown (including periods/dots)
- Keep test descriptions concise but complete
- Extract only the compliance rule names/IDs (e.g., "HIPAA", "SOC 2", "GDPR Article 32")
- Return ONLY the JSON, no explanations
"""
    
    try:
        # Initialize Vertex AI Generative Model
        model = GenerativeModel(model_name)
        
        # Generate content
        response = model.generate_content(parsing_prompt)
        response_text = response.text.strip()
        
        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # Parse JSON response
        parsed_data = json.loads(response_text)
        
        # Generate UUID if not present or placeholder
        if "testcase_id" not in parsed_data or parsed_data["testcase_id"] == "generate-random-uuid":
            parsed_data["testcase_id"] = str(uuid.uuid4())
            
        return parsed_data
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        logger.error(f"Response text: {response_text}")
        raise
    except Exception as e:
        logger.error(f"Error parsing test cases: {e}")
        raise


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
            tllst=json.loads(tlst["features_to_process"])
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

        current_testcases = state.get("current_testcases")
        aggregated_testcases = list(state.get("aggregated_testcases", []))
        
        if current_testcases:
            # Parse the test cases using Vertex AI before appending
            try:
                parsed_json = await parse_testcases_to_json(
                    current_testcases, 
                    model_name="gemini-2.0-flash"  # or "gemini-2.5-pro" for better accuracy
                )
                logger.info(f"Successfully parsed test cases: {parsed_json['testcase_id']}")
                
                aggregated_testcases.append(parsed_json)
            except Exception as e:
                logger.error(f"Failed to parse test cases: {e}")
                # Fallback: append error record
                aggregated_testcases.append({
                    "testcase_id": str(uuid.uuid4()),
                    "Testcase Title": "Parse Error",
                    "testcases": [],
                    "compliance_ids": [],
                    "raw_content": current_testcases,
                    "error": str(e)
                })
        
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