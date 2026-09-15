"""The structured shape we require an LLM to return for a motion.

Motion text is unstructured prose. To use it as model input it has to become
columns. Pydantic is what turns "the model returned some JSON" into "the model
returned a valid MotionFeatures or we rejected it" -- validation at the
boundary, exactly as with the ingestion layer.

PROMPT_VERSION is part of every cache key. Change the prompt or this schema and
you must bump it, otherwise old answers silently mix with new ones and the
feature set stops being reproducible.
"""

from typing import Literal

from pydantic import BaseModel, Field

PROMPT_VERSION = "v1"

TOPICS = Literal[
    "economics", "politics", "international_relations", "law", "social",
    "culture", "media", "education", "science_technology", "environment",
    "gender_identity", "development", "philosophy", "sport", "security",
]
MOTION_TYPES = Literal["policy", "value", "comparative", "counterfactual"]
ACTORS = Literal[
    "state", "international_body", "company", "civil_society", "individual", "none"
]
SCOPES = Literal["global", "regional", "national", "developing_world"]


class MotionFeatures(BaseModel):
    """Structured features extracted from a motion's text and info slide."""

    primary_topic: TOPICS
    secondary_topic: TOPICS | None = None
    motion_type: MOTION_TYPES
    actor_type: ACTORS
    geographic_scope: SCOPES
    specialist_knowledge: int = Field(ge=1, le=5)
    mechanism_complexity: int = Field(ge=1, le=5)


SYSTEM_PROMPT = """\
You classify competitive debating motions into structured features.

Return ONLY a JSON object with these keys:
  primary_topic         one of: economics, politics, international_relations, law,
                        social, culture, media, education, science_technology,
                        environment, gender_identity, development, philosophy,
                        sport, security
  secondary_topic       same vocabulary, or null
  motion_type           policy (proposes an action), value (asserts a judgement),
                        comparative (prefers one thing to another),
                        counterfactual (a changed past or hypothetical world)
  actor_type            state, international_body, company, civil_society,
                        individual, none
  geographic_scope      global, regional, national, developing_world
  specialist_knowledge  1-5, how much outside knowledge the debate demands
  mechanism_complexity  1-5, how much machinery a team must build to make a case

Judge only from the text given. Do not speculate about which side is favoured.\
"""
