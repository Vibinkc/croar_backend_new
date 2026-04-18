import json
from typing import cast

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
        content = response.choices[0].message.content or "{}"
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


async def analyze_resume_or_jd(text: str, source_type: str) -> dict[str, object]:
    """
    Analyze resume or JD and detects technical domains with weightages.
    """
    prompt = (
        f"You are an expert technical recruiter. Analyze the following {source_type} "
        "and identify the key technical domains/skills required.\n\n"
        f"{source_type}: {text[:3000]}\n\n"
        "Return ONLY a JSON object with domains and their importance weightage (must sum to 100).\n"
        "Also, determine if a CODING round is needed (look for keywords like Python, Java, C++, React, "
        "Node, SQL, Algorithms, Data Structures).\n\n"
        "{\n"
        '  "domains": {\n'
        '    "Domain Name": weightage_percentage,\n'
        "    ...\n"
        "  },\n"
        '  "coding_needed": true/false\n'
        "}\n\n"
        "Common domains include:\n"
        "- Full Stack Development\n"
        "- Frontend Development\n"
        "- Backend Development\n"
        "- Mobile Development (Android/iOS)\n"
        "- Data Science\n"
        "- Machine Learning\n"
        "- Gen AI / LLM\n"
        "- DevOps\n"
        "- Cloud Computing\n"
        "- Database Management\n"
        "- System Design\n"
        "- Cybersecurity\n"
        "- UI/UX Design\n"
        "- Quality Assurance\n"
        "- Target Management\n\n"
        f"Focus on the top 3-6 most relevant domains based on the {source_type}.\n"
        "Weightages must be integers and sum to exactly 100.\n"
        'Set "coding_needed" to true ONLY if the text explicitly mentions programming languages '
        "or software engineering roles that require writing code.\n"
    )

    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)

        domains = cast("dict[str, int]", response_data.get("domains", {}))

        total = sum(domains.values())
        if total != 100 and total > 0:
            domains = {k: round((v / total) * 100) for k, v in domains.items()}
            diff = 100 - sum(domains.values())
            if diff != 0:
                max_domain = max(domains, key=lambda k: domains[k])
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
) -> list[dict[str, object]]:
    """
    Generate aptitude questions for a specific domain.
    """
    prompt = (
        f"You are an expert technical interviewer. Generate {count} aptitude questions "
        f"for the domain: {domain}.\n\n"
        f"Difficulty: {difficulty}\n"
        f"Context from resume/JD: {context[:500]}\n\n"
        "Generate questions that test:\n"
        "- Logical reasoning\n"
        "- Problem-solving\n"
        "- Domain-specific knowledge\n"
        "- Analytical thinking\n"
        "- Technical concepts understanding\n\n"
        "Return ONLY a JSON object with this structure:\n\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "question_text": "Clear, concise question text",\n'
        '      "type": "MCQ",\n'
        '      "options": ["Option A text", "Option B text", "Option C text", "Option D text"],\n'
        '      "correct_answer": "Option B text",\n'
        '      "explanation": "Brief explanation of why this is correct"\n'
        "    },\n"
        "    ...\n"
        "  ]\n"
        "}\n\n"
        "IMPORTANT:\n"
        f"- Make questions relevant to {domain} but suitable for aptitude testing\n"
        "- Ensure correct_answer EXACTLY matches one of the options\n"
        "- Keep questions clear and unambiguous\n"
        f"- Vary question difficulty within the {difficulty} range\n"
        f"- Generate exactly {count} questions\n"
    )

    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        questions = cast("list[dict[str, object]]", response_data.get("questions", []))

        valid_questions = []
        for q in questions:
            if all(k in q for k in ["question_text", "type", "options", "correct_answer", "explanation"]):
                if q["correct_answer"] in cast("list[object]", q["options"]):
                    valid_questions.append(q)

        return valid_questions[:count]
    except Exception:
        return []


async def generate_coding_questions(
    domain: str, count: int, difficulty: str, context: str
) -> list[dict[str, object]]:
    """
    Generate coding questions for a specific domain.
    """
    prompt = (
        "You are an expert technical interviewer at a top tech company (FAANG level). "
        f"Generate {count} high-quality coding challenge(s) for the domain: {domain}.\n\n"
        f"Difficulty: {difficulty}\n"
        f"Context: {context[:500]}\n\n"
        "**STRICT REQUIREMENT: Generate ONLY Algorithmic/Data Structure problems.**\n\n"
        "Return ONLY a JSON object with this structure:\n\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "title": "Short Algorithmic Title",\n'
        '      "question_text": "Detailed Markdown problem statement...",\n'
        '      "type": "CODING",\n'
        '      "topic": "' + domain + ' - Algorithms",\n'
        '      "content": {\n'
        '          "problem_description": "## Problem Description\\n'
        'Provide a clear, formal description of the task.",\n'
        '          "constraints": [\n'
        '              "1 <= N <= 10^5",\n'
        '              "Each element is an integer between -10^9 and 10^9"\n'
        "          ],\n"
        '          "examples": [\n'
        "              {\n"
        '                  "input": "nums = [2,7,11,15], target = 9",\n'
        '                  "output": "[0,1]",\n'
        '                  "explanation": "Because nums[0] + nums[1] == 9, we return [0, 1]."\n'
        "              }\n"
        "          ],\n"
        '          "test_cases": [\n'
        '              { "input": "[2,7,11,15]\\n9", "output": "[0,1]", "is_hidden": false },\n'
        '              { "input": "[3,2,4]\\n6", "output": "[1,2]", "is_hidden": false },\n'
        '              { "input": "[3,3]\\n6", "output": "[0,1]", "is_hidden": true }\n'
        "          ],\n"
        '          "initial_code": {\n'
        '              "python": "def solve(nums, target):\\n    # Write your code here\\n    pass",\n'
        '              "java": "class Solution {\\n    public int[] solve(int[] nums, int target) {\\n'
        '        return new int[]{};\\n    }\\n}",\n'
        '              "javascript": "function solve(nums, target) {\\n    // Write your code here\\n}"\n'
        "          }\n"
        "      },\n"
        '      "difficulty": "' + difficulty + '"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    try:
        response_str = await analyze_text_with_llm(prompt)

        if "```json" in response_str:
            response_str = response_str.split("```json")[1].split("```")[0].strip()
        elif "```" in response_str:
            response_str = response_str.split("```")[1].split("```")[0].strip()

        response_data = json.loads(response_str)
        questions = cast("list[dict[str, object]]", response_data.get("questions", []))
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
) -> dict[str, object]:
    """
    Generate or enhance a job description based on title and existing content.
    """
    is_enhancement = len(existing_description.strip()) > 10

    prompt = (
        "You are an expert technical recruiter and HR consultant.\n"
        "Your goal is to "
        + (
            "enhance and fine-tune the existing job description"
            if is_enhancement
            else "generate a professional, high-impact job description from scratch"
        )
        + f" for the role of '{title}'.\n\n"
        "Context:\n"
        f"- Title: {title}\n"
        f"- Location: {location or 'Remote'}\n"
        f"- Experience Range: {experience_min or '0'} to {experience_max or '5'} years\n"
        + (f"- Existing Draft: {existing_description}" if is_enhancement else "")
        + "\n\nRequirements:\n"
        "1. Provide a comprehensive JD in professional HTML format.\n"
        "2. Suggest a market-competitive salary range (Minimum and Maximum) in LPA.\n"
        "3. Suggest a list of 5-8 top required skills.\n\n"
        "Return ONLY a JSON object:\n"
        "{\n"
        '  "description": "HTML formatted JD string",\n'
        '  "salary_min": number_in_LPA,\n'
        '  "salary_max": number_in_LPA,\n'
        '  "currency": "INR",\n'
        '  "skills": ["Skill1", "Skill2", ...]\n'
        "}\n"
    )
    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        return cast("dict[str, object]", response_data)
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
) -> list[dict[str, object]]:
    """
    Generate interactive interview questions for a 1-on-1 AI interview.
    """
    prompt = (
        f"You are an elite technical interviewer. Generate {count} high-quality interview "
        f"questions for the topic: {topic}.\n\n"
        f"**STRICT REQUIREMENT:** The Difficulty Level of the questions MUST strictly be: {difficulty}.\n"
        f"Adjust the technical depth, complexity, and expected knowledge strictly in alignment with a "
        f"'{difficulty}' level candidate. Beginner questions should be fundamental, while Expert questions "
        "should explore deep systemic knowledge, edge cases, and complex architecture.\n\n"
        f"Context: {context}\n\n"
        "Requirements for the questions:\n"
        "- Mix of technical, behavioral, and situational questions.\n"
        "- Questions should be conversational and suitable for a 1-on-1 voice/video interview.\n"
        "- Avoid simple true/false or one-word answer questions.\n"
        "- Focus on depth and understanding.\n\n"
        "Return ONLY a JSON object with this structure:\n"
        "{\n"
        '  "questions": [\n'
        "    {\n"
        '      "id": "1",\n'
        '      "question": "The question text...",\n'
        '      "type": "TECHNICAL/BEHAVIORAL/SITUATIONAL",\n'
        '      "expected_answer_points": ["Point 1", "Point 2"],\n'
        '      "difficulty": "' + difficulty + '"\n'
        "    },\n"
        "    ...\n"
        "  ]\n"
        "}\n"
    )
    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        return cast("list[dict[str, object]]", response_data.get("questions", []))[:count]
    except Exception as e:
        print(f"Error in generate_interview_questions: {e}")
        return []
