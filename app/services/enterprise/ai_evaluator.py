import json
from typing import Any, cast

from openai import AsyncOpenAI

from app.core.settings import settings


class AIEvaluatorService:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def generate_question_details(self, topic: str, difficulty: str) -> dict[str, Any] | None:
        if not self.client.api_key:
            return None

        prompt = (
            f'Generate a subjective assessment question based on the topic: "{topic}" '
            f'and difficulty level: "{difficulty}".\n'
            "Return a JSON object with the following fields:\n"
            "- question: The question text.\n"
            "- model_answer: A high-quality model answer.\n"
            "- criteria: A dictionary of grading criteria (e.g., grammar, relevance)\n"
            "  and their weights (summing to 100)."
        )

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert educational assessment creator. Output valid JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(response.choices[0].message.content or "{}")
            return cast("dict[str, Any]", json.loads(content))
        except Exception as e:
            print(f"Error generating question: {e}")
            return None

    async def evaluate_response(
        self, question: str, model_answer: str, student_response: str
    ) -> dict[str, Any] | None:
        prompt = f"""
        Evaluate the student's response to the following question:
        Question: "{question}"
        Model Answer: "{model_answer}"
        Student Response: "{student_response}"

        CRITICAL VALIDATION:
        1. If the Student Response is nonsensical, completely irrelevant,
           or extremely short (e.g., "m", "ok", "idk", single words),
           the `score` MUST be 0.
        2. If the response is irrelevant to the question, `score` MUST be 0.
        3. Do NOT give points for "Grammar" or "Tone" if the content is meaningless.
           Set ALL metrics to 0 in that case.

        Return a JSON object with:
        - score: A score out of 100.
        - feedback: Constructive feedback. If score is 0, explain why
          (e.g. "Response was too short" or "Irrelevant").
        - metrics: A dictionary with 'grammar', 'tone', 'structure', 'relevance' scores (0-100).
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert grader. Output valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(response.choices[0].message.content or "{}")
            return cast("dict[str, Any]", json.loads(content))
        except Exception as e:
            print(f"Error evaluating response: {e}")
            return None

    async def evaluate_code_response(
        self, question: str, test_cases: list[dict[str, str]], student_code: str
    ) -> dict[str, Any]:
        prompt = (
            "Evaluate the following student code against the problem statement and test cases.\n\n"
            f'Question: "{question}"\n'
            f"Test Cases: {json.dumps(test_cases, indent=2)}\n"
            f"Student Code:\n{student_code}\n\n"
            "CRITICAL VALIDATION:\n"
            "1. Mentally execute the code against EVERY test case provided.\n"
            "2. Calculate the success rate (e.g., if 3 out of 4 test cases pass, the base score is 75).\n"
            "3. Adjust the final score (0-100) based on code quality, efficiency, "
            "and edge case handling.\n"
            "4. If the code is completely nonsensical or doesn't address the problem, "
            "the score MUST be 0.\n\n"
            "Return a JSON object with:\n"
            "- score: A score out of 100 based primarily on test case success rate.\n"
            "- feedback: A single string containing detailed feedback, which test cases\n"
            "  passed/failed (simulated), and suggestions for improvement.\n"
            "- success_rate: percentage (0-100)."
        )

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert code reviewer and execution engine. Output valid JSON only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(response.choices[0].message.content or "{}")
            return cast("dict[str, Any]", json.loads(content))
        except Exception as e:
            print(f"Error evaluating code: {e}")
            return {"score": 0, "feedback": "Evaluation failed."}

    async def generate_job_simulation(
        self, role: str, rounds_count: int, round_titles: list[str] | None = None
    ) -> dict[str, Any] | None:
        if not self.client.api_key:
            return None

        round_context = ""
        if round_titles and len(round_titles) > 0:
            round_context = (
                f"The client has requested the following specific rounds: "
                f"{', '.join(round_titles)}. Use these titles and adjust "
                "question types to fit their purpose."
            )
        else:
            round_context = f"Design a realistic {rounds_count}-round hiring process."

        prompt = f"""
        Design a realistic hiring simulation for a "{role}" at a leading tech company.
        {round_context}

        Return a JSON object with a 'rounds' array. Each item should have:
        - round_number: integer
        - round_title: string
        - questions: array of objects {{"id": int, "text": string,
          "type": "mcq"|"code"|"text"}}
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert technical recruiter. Output valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(response.choices[0].message.content or "{}")
            return cast("dict[str, Any]", json.loads(content))
        except Exception as e:
            print(f"Error generating job simulation: {e}")
            return None

    async def generate_labyrinth_level(self, count: int = 1) -> list[dict[str, Any]] | None:
        if not self.client.api_key:
            return None

        prompt = f"""
        Generate {count} unique Labyrinth game levels (Space themed) on an 8x8 grid.
        Return a JSON object with a "levels" array.
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a puzzle game designer. Output valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(response.choices[0].message.content or "{}")
            data: dict[str, list[dict[str, Any]]] = json.loads(content)
            return data.get("levels", [])
        except Exception as e:
            print(f"Error generating labyrinth levels: {e}")
            return None

    async def generate_image(self, prompt: str) -> str | None:
        if not self.client.api_key:
            return None
        try:
            response = await self.client.images.generate(
                model="dall-e-3",
                prompt=(
                    "A cinematic, high-fidelity holographic portrait of a "
                    f"futuristic starship pilot. {prompt}."
                ),
                size="1024x1024",
                n=1,
            )
            if response.data and len(response.data) > 0:
                return response.data[0].url
            return None
        except Exception as e:
            print(f"Error generating image: {e}")
            return None


ai_evaluator_service = AIEvaluatorService()
