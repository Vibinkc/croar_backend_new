import json
from typing import Any

from openai import AsyncOpenAI

from app.core.settings import get_settings

_settings = get_settings()
client = AsyncOpenAI(api_key=_settings.openai_api_key)


async def analyze_text_with_llm(prompt: str) -> str:
    """
    Analyzes text using OpenAI.
    Returns the raw JSON string from the LLM.
    """
    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return content
    except Exception as e:
        print(f"CRITICAL: OpenAI Call Error: {e}")
        return json.dumps(
            {
                "issues": [
                    {
                        "quote": "System Error",
                        "issue": "AI Analysis Failed",
                        "improvement": f"An error occurred during AI analysis: {e!s}",
                        "severity": "high",
                    }
                ]
            }
        )


async def analyze_resume_or_jd(text: str, source_type: str) -> dict[str, Any]:
    """
    Analyzes resume or JD and detects technical domains with weightages.
    """
    prompt = f"""You are an expert technical recruiter. Analyze the following {source_type} and identify the key technical domains/skills required.

{source_type}: {text[:3000]}

Return ONLY a JSON object with domains and their importance weightage (must sum to 100).
Also, determine if a CODING round is needed (look for keywords like Python, Java, C++, React, Node, SQL, Algorithms, Data Structures).

{{
  "domains": {{
    "Domain Name": weightage_percentage,
    ...
  }},
  "coding_needed": true/false
}}

Common domains include:
- Full Stack Development
- Frontend Development
- Backend Development
- Mobile Development (Android/iOS)
- Data Science
- Machine Learning
- Gen AI / LLM
- DevOps
- Cloud Computing
- Database Management
- System Design
- Cybersecurity
- UI/UX Design
- Quality Assurance

Focus on the top 3-6 most relevant domains based on the {source_type}.
Weightages must be integers and sum to exactly 100.
Set "coding_needed" to true ONLY if the text explicitly mentions programming languages or software engineering roles that require writing code.
"""

    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)

        domains = response_data.get("domains", {})

        total = sum(domains.values())
        if total != 100 and total > 0:
            domains = {k: round((v / total) * 100) for k, v in domains.items()}
            diff = 100 - sum(domains.values())
            if diff != 0:
                max_domain = max(domains, key=domains.get)
                domains[max_domain] += diff

        return {
            "domains": domains,
            "modules": ["APTITUDE", "CODING"] if response_data.get("coding_needed", False) else ["APTITUDE"],
        }
    except Exception as e:
        print(f"Error in analyze_resume_or_jd: {e}")
        return {
            "domains": {"Full Stack Development": 40, "Frontend Development": 30, "Backend Development": 30}
        }


async def generate_aptitude_questions(
    domain: str, count: int, difficulty: str, context: str
) -> list[dict[str, Any]]:
    """
    Generates aptitude questions for a specific domain.
    """
    prompt = f"""You are an expert technical interviewer. Generate {count} aptitude questions for the domain: {domain}.

Difficulty: {difficulty}
Context from resume/JD: {context[:500]}

Generate questions that test:
- Logical reasoning
- Problem-solving
- Domain-specific knowledge
- Analytical thinking
- Technical concepts understanding

Return ONLY a JSON object with this structure:

{{
  "questions": [
    {{
      "question_text": "Clear, concise question text",
      "type": "MCQ",
      "options": ["Option A text", "Option B text", "Option C text", "Option D text"],
      "correct_answer": "Option B text",
      "explanation": "Brief explanation of why this is correct"
    }},
    ...
  ]
}}

IMPORTANT:
- Make questions relevant to {domain} but suitable for aptitude testing
- Ensure correct_answer EXACTLY matches one of the options
- Keep questions clear and unambiguous
- Vary question difficulty within the {difficulty} range
- Generate exactly {count} questions
"""

    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        questions = response_data.get("questions", [])

        valid_questions = []
        for q in questions:
            if all(k in q for k in ["question_text", "type", "options", "correct_answer", "explanation"]):
                if q["correct_answer"] in q["options"]:
                    valid_questions.append(q)

        return valid_questions[:count]
    except Exception:
        return []


async def generate_coding_questions(
    domain: str, count: int, difficulty: str, context: str
) -> list[dict[str, Any]]:
    """
    Generates coding questions for a specific domain.
    """
    prompt = f"""You are an expert technical interviewer at a top tech company (FAANG level). Generate {count} high-quality coding challenge(s) for the domain: {domain}.

Difficulty: {difficulty}
Context: {context[:500]}

**STRICT REQUIREMENT: Generate ONLY Algorithmic/Data Structure problems.**

Return ONLY a JSON object with this structure:

{{
  "questions": [
    {{
      "title": "Short Algorithmic Title",
      "question_text": "Detailed Markdown problem statement...",
      "type": "CODING",
      "topic": "{domain} - Algorithms",
      "content": {{
          "problem_description": "## Problem Description\\nProvide a clear, formal description of the task.",
          "constraints": [
              "1 <= N <= 10^5",
              "Each element is an integer between -10^9 and 10^9"
          ],
          "examples": [
              {{
                  "input": "nums = [2,7,11,15], target = 9",
                  "output": "[0,1]",
                  "explanation": "Because nums[0] + nums[1] == 9, we return [0, 1]."
              }}
          ],
          "test_cases": [
              {{ "input": "[2,7,11,15]\\n9", "output": "[0,1]", "is_hidden": false }},
              {{ "input": "[3,2,4]\\n6", "output": "[1,2]", "is_hidden": false }},
              {{ "input": "[3,3]\\n6", "output": "[0,1]", "is_hidden": true }}
          ],
          "initial_code": {{
              "python": "def solve(nums, target):\\n    # Write your code here\\n    pass",
              "java": "class Solution {{\\n    public int[] solve(int[] nums, int target) {{\\n        return new int[]{{}};\\n    }}\\n}}",
              "javascript": "function solve(nums, target) {{\\n    // Write your code here\\n}}"
          }}
      }},
      "difficulty": "{difficulty}"
    }}
  ]
}}
"""
    try:
        response_str = await analyze_text_with_llm(prompt)

        if "```json" in response_str:
            response_str = response_str.split("```json")[1].split("```")[0].strip()
        elif "```" in response_str:
            response_str = response_str.split("```")[1].split("```")[0].strip()

        response_data = json.loads(response_str)
        questions = response_data.get("questions", [])
        return questions[:count]
    except Exception as e:
        print(f"Error in generate_coding_questions: {e}")
        return []


async def generate_job_description_ai(
    title: str,
    existing_description: str = "",
    location: str = "",
    experience_min: str = "",
    experience_max: str = "",
) -> dict[str, Any]:
    """
    Generates or enhances a job description based on title and existing content.
    """
    is_enhancement = len(existing_description.strip()) > 10

    prompt = f"""You are an expert technical recruiter and HR consultant. 
Your goal is to {"enhance and fine-tune the existing job description" if is_enhancement else "generate a professional, high-impact job description from scratch"} for the role of '{title}'.

Context:
- Title: {title}
- Location: {location or "Remote"}
- Experience Range: {experience_min or "0"} to {experience_max or "5"} years
{f"- Existing Draft: {existing_description}" if is_enhancement else ""}

Requirements:
1. Provide a comprehensive JD in professional HTML format. 
2. Suggest a market-competitive salary range (Minimum and Maximum) in LPA.
3. Suggest a list of 5-8 top required skills.

Return ONLY a JSON object:
{{
  "description": "HTML formatted JD string",
  "salary_min": number_in_LPA,
  "salary_max": number_in_LPA,
  "currency": "INR",
  "skills": ["Skill1", "Skill2", ...]
}}
"""

    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        return response_data
    except Exception as e:
        print(f"Error in generate_job_description_ai: {e}")
        return {
            "description": f"<p><strong>{title} Role</strong></p>",
            "salary_min": 10,
            "salary_max": 20,
            "currency": "INR",
            "skills": [],
        }


async def generate_interview_questions(
    topic: str, count: int, difficulty: str, context: str = ""
) -> list[dict[str, Any]]:
    """
    Generates interactive interview questions for a 1-on-1 AI interview.
    """
    prompt = f"""You are an elite technical interviewer. Generate {count} high-quality interview questions for the topic: {topic}.

**STRICT REQUIREMENT:** The Difficulty Level of the questions MUST strictly be: {difficulty}. 
Adjust the technical depth, complexity, and expected knowledge strictly in alignment with a '{difficulty}' level candidate. Beginner questions should be fundamental, while Expert questions should explore deep systemic knowledge, edge cases, and complex architecture.

Context: {context}

Requirements for the questions:
- Mix of technical, behavioral, and situational questions.
- Questions should be conversational and suitable for a 1-on-1 voice/video interview.
- Avoid simple true/false or one-word answer questions.
- Focus on depth and understanding.

Return ONLY a JSON object with this structure:
{{
  "questions": [
    {{
      "id": "1",
      "question": "The question text...",
      "type": "TECHNICAL/BEHAVIORAL/SITUATIONAL",
      "expected_answer_points": ["Point 1", "Point 2"],
      "difficulty": "{difficulty}"
    }},
    ...
  ]
}}
"""
    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        return response_data.get("questions", [])[:count]
    except Exception as e:
        print(f"Error in generate_interview_questions: {e}")
        return []
