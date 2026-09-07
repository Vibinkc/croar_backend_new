"""Fictional sourcing results, for exercising the Sourcing Hub without a live provider.

Why this exists: the hub's UI cannot be tested when the provider is down or out of credit, and
"nothing on screen" is a poor way to review a screen full of components. So this returns a
fixed cast of invented people.

Three rules keep that from becoming a liability, because a fake candidate that reaches a real
pipeline is a genuine problem — someone gets emailed, or worse, doesn't:

  1. Off unless asked for. ``SOURCING_SAMPLE_DATA=true`` must be set explicitly, and it is
     refused outright when APP_ENV is production. A flag that can be left on by accident is
     the same as no flag.
  2. Undeliverable by construction. Every address is on ``example.com`` and every link on
     ``example.org`` — reserved by RFC 2606 precisely so they can never resolve to a real
     person's inbox or profile. If one of these is imported and a sequence fires at it, the
     mail bounces; it cannot reach a stranger.
  3. Visibly fake. The response carries ``sample: true`` so the hub can label every row, and
     the names are plainly invented rather than plausible-looking real ones.

The cast is deliberately uneven — some have no email, some no education, one has more skills
than a row can show — because the interesting parts of a UI are its missing-data branches, and
a tidy dataset tests none of them.
"""

from __future__ import annotations

from typing import Any

# Kept as a module constant so callers can state the reason in an error rather than guessing.
SAMPLE_DOMAIN = "example.com"

_PEOPLE: list[dict[str, Any]] = [
    {
        "full_name": "Priya Ramanathan",
        "headline": "Senior Frontend Developer at Northwind Labs",
        "location": "Bengaluru, India",
        "company": "Northwind Labs",
        "email": "priya.ramanathan@example.com",
        "skills": ["React", "TypeScript", "Next.js", "GraphQL", "Testing Library", "CSS"],
        "education": [
            {
                "school": "Indian Institute of Technology Madras",
                "degree": "B.Tech",
                "field": "Computer Science",
                "years": "2014 – 2018",
            }
        ],
        "experience": [
            {"title": "Senior Frontend Developer", "company": "Northwind Labs", "years": "2022 – present"},
            {"title": "Frontend Developer", "company": "Tessellate", "years": "2018 – 2022"},
        ],
        "ai_summary": "Six years on design-system and dashboard work; has led a migration from a legacy Angular app to Next.js.",
    },
    {
        # No email: exercises the "can't be reached" branch on the row.
        "full_name": "Tomás Iglesias",
        "headline": "Full-stack Developer, freelance",
        "location": "Valencia, Spain",
        "company": "Freelance",
        "email": None,
        "skills": ["Node.js", "PostgreSQL", "Vue", "Docker"],
        "education": [
            {
                "school": "Universitat de València",
                "degree": "Grado",
                "field": "Ingeniería Informática",
                "years": "2013 – 2017",
            }
        ],
        "experience": [{"title": "Full-stack Developer", "company": "Freelance", "years": "2019 – present"}],
        "ai_summary": "Contracts with small product teams; strong on the backend half of a full-stack brief.",
    },
    {
        # No education at all: the drawer's Education panel should say so rather than sit empty.
        "full_name": "Amara Okonkwo",
        "headline": "Software Engineer at Kestrel Systems",
        "location": "Lagos, Nigeria",
        "company": "Kestrel Systems",
        "email": "a.okonkwo@example.com",
        "skills": [
            "Python",
            "Django",
            "AWS",
            "Terraform",
            "Kubernetes",
            "Go",
            "Redis",
            "Kafka",
            "gRPC",
            "Prometheus",
        ],
        "education": [],
        "experience": [
            {"title": "Software Engineer", "company": "Kestrel Systems", "years": "2021 – present"},
            {"title": "Backend Developer", "company": "Paystream", "years": "2019 – 2021"},
        ],
        "ai_summary": "Platform engineer; ten listed skills, so the row should show eight and count the rest.",
    },
    {
        # No structured history: the Experience panel falls back to the headline and says so.
        "full_name": "Wei Chen",
        "headline": "Mobile Developer at Lantern",
        "location": "Singapore",
        "company": "Lantern",
        "email": "wei.chen@example.com",
        "skills": ["Swift", "Kotlin", "Flutter"],
        "education": [
            {
                "school": "National University of Singapore",
                "degree": "BSc",
                "field": "Information Systems",
                "years": "2016 – 2020",
            }
        ],
        "experience": [],
        "ai_summary": "Ships iOS and Android from one Flutter codebase.",
    },
    {
        # A long name and a long headline, to prove the row truncates rather than reflows.
        "full_name": "Alexandra Vasquez-Hollingsworth",
        "headline": "Principal Software Developer, Platform Infrastructure & Developer Experience at Meridian Analytics International",
        "location": "Toronto, Ontario, Canada",
        "company": "Meridian Analytics International",
        "email": "alexandra.vasquez-hollingsworth@example.com",
        "skills": ["Rust", "C++", "Distributed systems", "Observability"],
        "education": [
            {
                "school": "University of Waterloo",
                "degree": "MMath",
                "field": "Computer Science",
                "years": "2011 – 2013",
            },
            {
                "school": "McGill University",
                "degree": "BSc",
                "field": "Software Engineering",
                "years": "2007 – 2011",
            },
        ],
        "experience": [
            {
                "title": "Principal Software Developer",
                "company": "Meridian Analytics International",
                "years": "2020 – present",
            },
            {"title": "Staff Engineer", "company": "Cordell Data", "years": "2016 – 2020"},
            {"title": "Software Developer", "company": "Bellcast", "years": "2013 – 2016"},
        ],
        "ai_summary": "Fifteen years on infrastructure; the longest name and headline in this set, on purpose.",
    },
    {
        "full_name": "Ingrid Solberg",
        "headline": "Backend Developer at Fjord Systems",
        "location": "Oslo, Norway",
        "company": "Fjord Systems",
        "email": "ingrid.solberg@example.com",
        "skills": ["Java", "Spring Boot", "Kafka", "PostgreSQL"],
        "education": [
            {"school": "NTNU", "degree": "MSc", "field": "Computer Science", "years": "2015 – 2020"}
        ],
        "experience": [{"title": "Backend Developer", "company": "Fjord Systems", "years": "2020 – present"}],
        "ai_summary": "JVM services in a regulated domain; comfortable with event-driven designs.",
    },
    {
        "full_name": "Rafael Duarte",
        "headline": "Software Developer at Bluepeak",
        "location": "São Paulo, Brazil",
        "company": "Bluepeak",
        "email": "rafael.duarte@example.com",
        "skills": ["C#", ".NET", "Azure", "SQL Server"],
        "education": [
            {
                "school": "Universidade de São Paulo",
                "degree": "Bacharelado",
                "field": "Ciência da Computação",
                "years": "2014 – 2018",
            }
        ],
        "experience": [
            {"title": "Software Developer", "company": "Bluepeak", "years": "2021 – present"},
            {"title": "Junior Developer", "company": "Aurora Tech", "years": "2018 – 2021"},
        ],
        "ai_summary": "Enterprise .NET; has run two Azure migrations end to end.",
    },
    {
        # Neither email nor education nor experience: the sparsest row the UI must still render.
        "full_name": "Sam Whitfield",
        "headline": "Developer",
        "location": None,
        "company": None,
        "email": None,
        "skills": [],
        "education": [],
        "experience": [],
        "ai_summary": None,
    },
    {
        "full_name": "Hana Kobayashi",
        "headline": "Data Engineer at Tsubaki Analytics",
        "location": "Tokyo, Japan",
        "company": "Tsubaki Analytics",
        "email": "hana.kobayashi@example.com",
        "skills": ["Python", "dbt", "Snowflake", "Airflow", "SQL"],
        "education": [
            {"school": "Keio University", "degree": "MSc", "field": "Data Science", "years": "2017 – 2019"}
        ],
        "experience": [{"title": "Data Engineer", "company": "Tsubaki Analytics", "years": "2019 – present"}],
        "ai_summary": "Builds and owns the warehouse layer; strong SQL.",
    },
    {
        "full_name": "Marcus Adeyemi",
        "headline": "Senior Software Developer at Halden Group",
        "location": "Manchester, United Kingdom",
        "company": "Halden Group",
        "email": "marcus.adeyemi@example.com",
        "skills": ["Ruby", "Rails", "React", "PostgreSQL", "Sidekiq"],
        "education": [
            {
                "school": "University of Manchester",
                "degree": "BSc",
                "field": "Computer Science",
                "years": "2012 – 2015",
            }
        ],
        "experience": [
            {"title": "Senior Software Developer", "company": "Halden Group", "years": "2019 – present"},
            {"title": "Software Developer", "company": "Northgate Digital", "years": "2015 – 2019"},
        ],
        "ai_summary": "Long-tenured Rails engineer who has mentored three juniors to mid-level.",
    },
    {
        "full_name": "Elena Petrova",
        "headline": "QA Automation Developer at Vantage Software",
        "location": "Warsaw, Poland",
        "company": "Vantage Software",
        "email": "elena.petrova@example.com",
        "skills": ["Playwright", "TypeScript", "CI/CD", "Python"],
        "education": [
            {
                "school": "Warsaw University of Technology",
                "degree": "MSc",
                "field": "Software Engineering",
                "years": "2016 – 2021",
            }
        ],
        "experience": [
            {"title": "QA Automation Developer", "company": "Vantage Software", "years": "2021 – present"}
        ],
        "ai_summary": "Owns an end-to-end suite of ~400 Playwright specs.",
    },
    {
        "full_name": "Diego Morales",
        "headline": "Junior Software Developer at Cobalt Interactive",
        "location": "Mexico City, Mexico",
        "company": "Cobalt Interactive",
        "email": "diego.morales@example.com",
        "skills": ["JavaScript", "React", "Node.js"],
        "education": [
            {
                "school": "Tecnológico de Monterrey",
                "degree": "Ingeniería",
                "field": "Sistemas Computacionales",
                "years": "2019 – 2023",
            }
        ],
        "experience": [
            {"title": "Junior Software Developer", "company": "Cobalt Interactive", "years": "2023 – present"}
        ],
        "ai_summary": "Two years in; the least experienced person in this set, so ranking has something to sort.",
    },
]


def _slug(name: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")


def _matches(person: dict[str, Any], terms: list[str]) -> bool:
    """Loose contains-match over the fields a recruiter would have typed into the rail.

    Not an attempt to imitate real relevance ranking — just enough that typing "python" and
    typing "swift" return visibly different sets, so filtering can be reviewed at all.
    """
    if not terms:
        return True
    haystack = " ".join(
        [
            person.get("full_name") or "",
            person.get("headline") or "",
            person.get("location") or "",
            person.get("company") or "",
            " ".join(person.get("skills") or []),
            " ".join(
                f"{row.get('school', '')} {row.get('degree', '')} {row.get('field', '')}"
                for row in person.get("education") or []
            ),
        ]
    ).lower()
    return any(term in haystack for term in terms)


def sample_profiles(query: str, location: str | None = None, limit: int = 15) -> list[dict[str, Any]]:
    """Return the fictional cast, filtered loosely by the search text.

    Falls back to the whole cast when nothing matches: an empty sample set would put the
    reviewer back where they started, staring at an empty results panel.
    """
    words = [w.strip(" \"'()").lower() for w in query.replace(" OR ", " ").split()]
    terms = [w for w in words if len(w) > 2]
    if location:
        terms.append(location.strip().lower())

    hits = [p for p in _PEOPLE if _matches(p, terms)] or list(_PEOPLE)

    out: list[dict[str, Any]] = []
    for person in hits[:limit]:
        out.append(
            {
                **person,
                "platform": "Sample data",
                # example.org is reserved too, so a click cannot land on a stranger's profile.
                "profile_url": f"https://www.example.org/in/{_slug(person['full_name'])}",
                "avatar_url": None,
                "raw_data": {"sample": True},
            }
        )
    return out
