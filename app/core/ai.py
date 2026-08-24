import json
import logging
from typing import cast

from app.core.anthropic_llm import AsyncClaudeOpenAI, claude_json

logger = logging.getLogger(__name__)

# Claude-backed, OpenAI-shaped chat client. Other modules (e.g. the sourcing calibrate/sequence
# flows in sourcing_chat.py) do `from app.core.ai import client` and call
# `client.chat.completions.create(...)` — this shim routes those to Claude.
client = AsyncClaudeOpenAI()


async def analyze_text_with_llm(prompt: str) -> str:
    """
    Analyzes text using Claude (Anthropic).
    Returns the raw JSON string from the LLM.
    """
    try:
        # 8k tokens so large payloads (coding questions with test cases) aren't truncated.
        return await claude_json(prompt, max_tokens=8000)
    except Exception as e:
        print(f"CRITICAL: Claude Call Error: {e}")
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


def _resolve_correct_answer(correct: object, options: list[object]) -> str | None:
    """Map an LLM ``correct_answer`` to the exact option string it refers to.

    The model often returns an answer that is semantically one of the options but not a
    byte-exact copy — extra whitespace, trailing punctuation, an "Option B" / "B" prefix,
    or (in non-English output) subtle formatting drift. A strict ``in`` check then drops
    EVERY question and the whole assessment comes back empty (worst in KO/JA). This resolves
    the intended option tolerantly and returns the canonical option text, or None if it
    genuinely cannot be matched.
    """
    opts = [str(o) for o in options]
    if not opts:
        return None
    ca = str(correct or "").strip()
    if not ca:
        return None

    # 1) exact match
    if ca in opts:
        return ca

    def norm(s: str) -> str:
        return " ".join(s.strip().strip(".").casefold().split())

    nmap = {norm(o): o for o in opts}
    # 2) whitespace/case/punctuation-insensitive match
    if norm(ca) in nmap:
        return nmap[norm(ca)]
    # 3) letter or "Option X" reference -> index into options
    letter = ca.strip().lstrip("(").rstrip(").:").strip()
    for prefix in ("option ", "옵션 ", "選択肢 "):
        if letter.lower().startswith(prefix):
            letter = letter[len(prefix) :].strip()
    if len(letter) == 1 and letter.upper() in "ABCDEFGH":
        idx = ord(letter.upper()) - ord("A")
        if 0 <= idx < len(opts):
            return opts[idx]
    # 4) unique substring containment either direction
    contains = [o for o in opts if norm(ca) and (norm(ca) in norm(o) or norm(o) in norm(ca))]
    if len(contains) == 1:
        return contains[0]
    return None


def _language_directive(language: str | None) -> str:
    """Instruction appended to generation prompts so the LLM writes output in the chosen language.

    Returns "" for English (the model's default) so existing behaviour is unchanged. Code,
    identifiers and JSON structure are always kept in English so parsing/validation still works.
    """
    lang = (language or "English").strip()
    if lang.lower() in ("", "english", "en"):
        return ""
    return (
        f"\n\nLANGUAGE REQUIREMENT: Write ALL human-readable text — question text, options, answer "
        f"choices, correct answers, explanations, titles and problem statements — in {lang}. "
        f"Keep programming code, code identifiers, technical keywords and every JSON key/enum value "
        f'(e.g. "MCQ", "TECHNICAL", "difficulty") in English. Do NOT translate the JSON structure.\n'
    )


async def generate_aptitude_questions(
    domain: str, count: int, difficulty: str, context: str, language: str = "English"
) -> list[dict[str, object]]:
    """
    Generate aptitude questions for a specific domain.
    """
    prompt = (
        f"You are an expert assessment designer. Generate {count} multiple-choice (MCQ) questions "
        f"that evaluate a candidate's PRACTICAL, ROLE-SPECIFIC skills and knowledge for: {domain}.\n\n"
        f"Difficulty: {difficulty}\n"
        f"Context from resume/JD: {context[:500]}\n\n"
        "Tailor every question to the ACTUAL work of this role — not generic logic puzzles. Examples:\n"
        "- Engineering / data: core concepts, tools, best practices, debugging & analysis scenarios.\n"
        "- UI/UX design: design principles, usability heuristics, accessibility, Figma/prototyping, "
        "design process & critique.\n"
        "- Digital marketing: SEO/SEM, campaign strategy, funnels, analytics & metrics, content, "
        "social & email marketing.\n"
        "- Sales / HR / Ops / Finance / product / etc.: the concepts, tools and day-to-day judgement "
        "calls that role actually performs.\n"
        "Mix factual knowledge with real-world scenario/judgement questions. If the role is "
        "non-technical, do NOT ask coding or heavy quantitative-reasoning questions.\n\n"
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
        f"- Keep every question directly relevant to a {domain} professional's day-to-day work\n"
        "- Ensure correct_answer EXACTLY matches one of the options\n"
        "- Keep questions clear and unambiguous\n"
        f"- Vary question difficulty within the {difficulty} range\n"
        f"- Generate exactly {count} questions\n"
    )
    prompt += _language_directive(language)

    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        questions = cast("list[dict[str, object]]", response_data.get("questions", []))

        valid_questions = []
        dropped = 0
        for q in questions:
            if not all(k in q for k in ["question_text", "type", "options", "correct_answer", "explanation"]):
                dropped += 1
                continue
            resolved = _resolve_correct_answer(q["correct_answer"], cast("list[object]", q["options"]))
            if resolved is None:
                dropped += 1
                continue
            # Canonicalize to the exact option text so downstream grading matches.
            q["correct_answer"] = resolved
            valid_questions.append(q)

        if dropped:
            logger.warning(
                "Dropped %d/%d aptitude question(s) with unresolvable answers", dropped, len(questions)
            )
        return valid_questions[:count]
    except Exception:
        return []


async def generate_coding_questions(
    domain: str, count: int, difficulty: str, context: str, language: str = "English"
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
    prompt += _language_directive(language)
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
    additional_instructions: str = "",
    work_mode: str = "",
) -> dict[str, object]:
    """
    Generate or enhance a job description based on title and existing content.

    `additional_instructions` is free-text the user wants folded INTO the existing description —
    the AI must integrate those points into the appropriate sections while preserving the rest of
    the current draft, rather than rewriting it from scratch.
    """
    has_existing = len(existing_description.strip()) > 10
    has_additional = len(additional_instructions.strip()) > 0

    if has_additional and has_existing:
        goal = (
            "incorporate the user's additional requirements into the existing job description and refine it"
        )
    elif has_existing:
        goal = "enhance and fine-tune the existing job description"
    else:
        goal = "generate a professional, high-impact job description from scratch"

    # Respect the work mode the user picked (On-Site / Remote / Hybrid). Never default to
    # "Remote" — that used to contradict an On-Site/Hybrid selection in the generated JD.
    mode = (work_mode or "").strip()
    loc = (location or "").strip()
    if loc and mode:
        location_line = f"{loc} ({mode})"
    elif loc:
        location_line = loc
    elif mode:
        location_line = mode
    else:
        location_line = "Not specified"

    prompt = (
        "You are an expert technical recruiter and HR consultant.\n"
        f"Your goal is to {goal} for the role of '{title}'.\n\n"
        "Context:\n"
        f"- Title: {title}\n"
        f"- Location: {location_line}\n"
        + (f"- Work Mode: {mode}\n" if mode else "")
        + f"- Experience Range: {experience_min or '0'} to {experience_max or '5'} years\n"
        + (f"- Existing Draft: {existing_description}" if has_existing else "")
        + (
            "\n\nThe user wants to ADD the following extra requirements/details to the job "
            "description. Integrate them naturally into the most appropriate sections while "
            "preserving ALL of the existing draft's content and structure above. Do not drop or "
            "summarise away existing details; only add and gently refine for coherence:\n"
            f'"{additional_instructions.strip()}"\n'
            "IMPORTANT: Wrap ONLY the newly added words/sentences in <mark>...</mark> HTML tags so "
            "the user can see exactly what was added. Do NOT put <mark> around any pre-existing "
            "content, and do not use <mark> anywhere else."
            if has_additional
            else ""
        )
        + "\n\nRequirements:\n"
        "1. Provide a comprehensive JD in professional HTML format.\n"
        "2. Suggest a market-competitive salary range (Minimum and Maximum) in LPA.\n"
        "3. Suggest a list of 5-8 top required skills.\n"
        "4. Reflect the Location and Work Mode above EXACTLY as given (e.g. On-Site / Hybrid / "
        "Remote). Do NOT assume or write 'Remote' unless that is the stated work mode.\n\n"
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
    topic: str, count: int, difficulty: str, context: str = "", language: str = "English"
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
    prompt += _language_directive(language)
    try:
        response_str = await analyze_text_with_llm(prompt)
        response_data = json.loads(response_str)
        return cast("list[dict[str, object]]", response_data.get("questions", []))[:count]
    except Exception as e:
        print(f"Error in generate_interview_questions: {e}")
        return []
