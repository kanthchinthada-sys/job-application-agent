"""Skills that tech job postings ask for, as an ATS would scan them ("vocabulary_v1").

Built from real postings for QA / SDET / test automation, forward deployed and solutions engineering,
AI / ML engineering, software development, DevOps / SRE and data engineering roles (November 2026 sample).
Each entry is the name a resume should use; ``ALIASES`` maps other spellings in postings to it.

The list is only for READING postings without AI and for scoring ATS keywords. It never adds anything to a
resume: what goes on a resume comes from your own profile.
"""

import re

TOOLS = [
    # languages
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Kotlin", "Swift", "Objective-C", "C#", ".NET", "C++", "C",
    "Rust", "Ruby", "PHP", "Scala", "R", "SQL", "Bash", "PowerShell", "Groovy", "Dart", "Elixir", "MATLAB",
    # web / backend frameworks
    "React", "React Native", "Angular", "Vue", "Next.js", "Node.js", "Express", "Django", "Flask", "FastAPI",
    "Spring Boot", "Ruby on Rails", "Rails", "Laravel", "ASP.NET", "GraphQL", "gRPC", "REST APIs", "OpenAPI",
    "Swagger", "WebSockets", "HTML", "CSS", "Tailwind", "Flutter",
    # test automation and QA tools
    "Playwright", "Selenium", "Selenium WebDriver", "Selenium Grid", "Cypress", "WebdriverIO", "Puppeteer",
    "Appium", "XCUITest", "Espresso", "Detox", "BrowserStack", "Sauce Labs", "LambdaTest", "pytest", "unittest",
    "JUnit", "TestNG", "Mockito", "Jest", "Mocha", "Vitest", "Cucumber", "Gherkin", "Behave", "SpecFlow",
    "Robot Framework", "REST Assured", "Postman", "Newman", "SoapUI", "Karate", "Pact", "WireMock",
    "Testcontainers", "k6", "JMeter", "Gatling", "Locust", "LoadRunner", "TestRail", "Xray", "Zephyr",
    "qTest", "HP ALM", "Allure", "Axe", "Lighthouse", "Cyara", "Botium",
    # cloud, containers, infrastructure
    "AWS", "Azure", "GCP", "Kubernetes", "Docker", "Helm", "Terraform", "Pulumi", "Ansible", "CloudFormation",
    "Lambda", "EC2", "S3", "ECS", "EKS", "AKS", "GKE", "SQS", "SNS", "EventBridge", "Step Functions", "Kinesis",
    "DynamoDB", "CloudWatch", "IAM", "Heroku", "Vercel", "Linux", "nginx", "Istio", "Linkerd", "OpenShift",
    # CI/CD and source control
    "CI/CD", "Jenkins", "GitHub Actions", "GitLab CI", "CircleCI", "Azure DevOps", "ArgoCD", "Bamboo",
    "TeamCity", "Gradle", "Maven", "Git", "GitHub", "GitLab", "Bitbucket", "Jira", "Confluence",
    # observability
    "Datadog", "Prometheus", "Grafana", "Splunk", "New Relic", "OpenTelemetry", "Sentry", "Elasticsearch",
    "OpenSearch", "Kibana", "PagerDuty",
    # data
    "PostgreSQL", "MySQL", "Oracle", "SQL Server", "MongoDB", "Redis", "Cassandra", "Snowflake", "BigQuery",
    "Redshift", "Databricks", "Spark", "PySpark", "SparkSQL", "Kafka", "RabbitMQ", "Airflow", "dbt", "Hadoop",
    "Azure Data Factory", "pandas", "Tableau", "Power BI", "Looker",
    # AI / ML
    "LLM", "RAG", "MCP", "LangChain", "LangGraph", "LlamaIndex", "OpenAI", "Claude", "GPT", "Gemini",
    "AWS Bedrock", "Vertex AI", "Vertex AI Search", "Gemini Enterprise", "Gemini CLI", "Google ADK", "Codex",
    "LangSmith", "Langfuse", "Promptfoo", "DeepEval", "RAGAS", "pgvector", "Hugging Face", "PyTorch",
    "TensorFlow", "scikit-learn", "MLflow", "Ollama",
    "vector databases", "embeddings", "fine-tuning", "GitHub Copilot", "Claude Code", "Cursor", "n8n",
    # business tools often asked of FDE / solutions roles
    "Salesforce", "HubSpot", "ServiceNow", "SAP", "Workday", "Slack", "Zapier", "Make", "GDS", "NDC",
]

PRACTICES = [
    # testing
    "test automation", "API testing", "UI testing", "end-to-end testing", "integration testing", "unit testing",
    "regression testing", "performance testing", "load testing", "security testing", "accessibility testing",
    "mobile testing", "exploratory testing", "manual testing", "contract testing", "usability testing",
    "test strategy", "test planning", "test plans", "test cases", "test data management", "data-driven testing",
    "BDD", "TDD", "shift-left", "flaky tests", "test frameworks", "automation frameworks", "page object model",
    "root cause analysis", "defect triage", "acceptance criteria", "UAT", "release readiness", "quality metrics",
    "risk-based testing", "WCAG", "iOS", "Android", "cross-browser testing", "release automation",
    # engineering practice
    "microservices", "distributed systems", "system design", "event-driven architecture", "serverless",
    "APIs", "API design", "integrations", "third-party integrations", "code reviews", "design patterns",
    "clean code", "observability", "monitoring", "logging", "alerting", "SLOs", "SLIs", "error budgets",
    "incident management", "incident response", "on-call", "postmortems", "capacity planning",
    "infrastructure as code", "containerization", "DevSecOps", "authentication", "authorization", "OAuth",
    "SSO", "encryption", "compliance", "Agile", "Scrum", "Kanban", "SDLC", "version control",
    # data
    "data pipelines", "ETL", "ELT", "data modeling", "data quality", "data warehouse", "data lake",
    "data validation", "analytics", "data engineering", "data integration",
    # AI
    "machine learning", "deep learning", "generative AI", "AI agents", "agentic workflows", "multi-agent systems",
    "prompt engineering", "evaluation frameworks", "LLM evaluation", "prompt injection", "guardrails", "NLP",
    "computer vision", "MLOps", "model deployment", "model training", "model serving", "model evaluation",
    "production ML systems", "vector search", "semantic search", "conversational AI",
    # customer-facing / FDE / solutions
    "customer-facing", "pre-sales", "technical discovery", "solution architecture", "solution design",
    "proof of concept", "prototyping", "requirements gathering", "stakeholder management", "technical workshops",
    "implementation", "onboarding", "enterprise customers", "SaaS",
    # people and communication (ATS filters often include them)
    "communication", "collaboration", "mentoring", "leadership", "problem-solving", "ownership",
    "technical writing", "documentation", "debugging", "troubleshooting",
]

# other spellings in postings -> the name used above
ALIASES = {
    "rest api": "REST APIs", "rest apis": "REST APIs", "restful apis": "REST APIs", "restful api": "REST APIs",
    "restful": "REST APIs", "llms": "LLM", "large language models": "LLM", "large language model": "LLM",
    "retrieval-augmented generation": "RAG", "retrieval augmented generation": "RAG",
    "model context protocol": "MCP", "google cloud": "GCP", "google cloud platform": "GCP",
    "amazon web services": "AWS", "k8s": "Kubernetes", "postgres": "PostgreSQL", "golang": "Go",
    "node": "Node.js", "nodejs": "Node.js", "reactjs": "React", "react.js": "React", "vue.js": "Vue",
    "ci / cd": "CI/CD", "continuous integration": "CI/CD", "continuous delivery": "CI/CD",
    "behavior-driven development": "BDD", "behaviour-driven development": "BDD",
    "behavior driven development": "BDD", "test-driven development": "TDD", "test driven development": "TDD",
    "user acceptance testing": "UAT", "e2e testing": "end-to-end testing", "e2e tests": "end-to-end testing",
    "end to end testing": "end-to-end testing", "root-cause analysis": "root cause analysis",
    "infrastructure-as-code": "infrastructure as code", "iac": "infrastructure as code",
    "slo": "SLOs", "sli": "SLIs", "agentic ai": "AI agents", "ai agent": "AI agents", "agents": "AI agents",
    "genai": "generative AI", "gen ai": "generative AI", "ml": "machine learning", "ai/ml": "machine learning", "graphql": "GraphQL", "rest": "REST APIs", "a11y": "accessibility testing",
    "accessibility": "accessibility testing", "load tests": "load testing", "performance tests": "performance testing",
    "unit tests": "unit testing", "integration tests": "integration testing", "api tests": "API testing",
    "pre sales": "pre-sales", "presales": "pre-sales", "poc": "proof of concept", "proofs of concept": "proof of concept",
    "stakeholders": "stakeholder management", "customer facing": "customer-facing", "mentor": "mentoring",
    "problem solving": "problem-solving", "communicate": "communication", "communicating": "communication",
    "communication skills": "communication", "collaborating": "collaboration", "collaborate": "collaboration",
    "api": "APIs", "debug": "debugging", "debugging tools": "debugging", "troubleshoot": "troubleshooting",
    "mentor and guide": "mentoring", "mentoring or guiding": "mentoring", "agile/scrum": "Agile", "github copilot": "GitHub Copilot", "copilot": "GitHub Copilot",
    "sparksql": "SparkSQL", "spark sql": "SparkSQL", "xray test management": "Xray", "webdriver io": "WebdriverIO",
    "selenium webdriver": "Selenium WebDriver", "ms sql": "SQL Server", "mssql": "SQL Server",
    "amazon bedrock": "AWS Bedrock", "bedrock": "AWS Bedrock", "chatgpt": "GPT", "openai api": "OpenAI",
    "production machine learning systems": "production ML systems", "serve the models": "model serving",
    "serve models in production": "model serving", "train models": "model training",
    "evaluate production ml models": "model evaluation", "model evaluations": "model evaluation",
    "agent development kit": "Google ADK", "prompt foo": "Promptfoo",
}

# words that are skills only in their exact case / form (avoid "Go" in "go above", "C" in "C-level")
CASE_SENSITIVE = {"Go", "C", "R", "Make", "Claude", "Gemini", "Cursor", "Express", "Lambda", "Swift", "Rails",
                  "Spark", "Rust", "Dart", "SAP", "IAM", "S3", "SNS", "SQS", "EC2", "GPT", "MCP", "RAG", "LLM",
                  "GDS", "NDC"}

ALL: list[str] = list(dict.fromkeys(TOOLS + PRACTICES))
_CANON = {t.lower(): t for t in ALL}


def canonical(term: str) -> str:
    low = " ".join(term.lower().split())
    return ALIASES.get(low) or _CANON.get(low) or term.strip()


def _pattern(term: str) -> re.Pattern:
    flags = 0 if term in CASE_SENSITIVE else re.IGNORECASE
    body = re.escape(term).replace(r"\ ", r"[\s-]+")
    return re.compile(rf"(?<![\w.+#-]){body}(?![\w+#]|\.\w)", flags)


_PATTERNS = [(t, _pattern(t)) for t in ALL] + [(v, _pattern(k)) for k, v in ALIASES.items()]


def find_terms(text: str) -> list[tuple[int, int, str]]:
    """Every vocabulary term in the text: (start, end, canonical name), longest match first, no overlaps."""
    t = text.replace("’", "'")
    hits = []
    for name, pat in _PATTERNS:
        for m in pat.finditer(t):
            if name == "Go" and re.match(r"\s+(to|above|beyond|live|through|out|back|ahead|deep)\b", t[m.end():]):
                continue                                         # "go above and beyond" is not the language
            if name == "C" and not re.match(r"\s*(,|/|\+\+| or | and |\s*\(|\s+programming|\s+language)", t[m.end():]):
                continue
            hits.append((m.start(), m.end(), name))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, last_end = [], -1
    for st, en, name in hits:
        if st >= last_end:
            out.append((st, en, name))
            last_end = en
    return out


# --- technical or not ---------------------------------------------------------------------------------------
# The ATS check scores technical experience only. People skills ("curiosity", "ownership"), ways of working
# ("hybrid working policy", "in-person interactions") and hiring words ("recruitment process") are left out:
# a resume can't honestly "contain" them as keywords, and they are judged in the interview, not by the filter.
SOFT_TERMS = {
    "communication", "collaboration", "mentoring", "leadership", "problem-solving", "ownership",
    "technical writing", "documentation", "customer-facing", "pre-sales", "stakeholder management",
    "requirements gathering", "technical workshops", "onboarding", "enterprise customers",
}
NOT_TECH = re.compile(
    r"\b(curio\w*|initiative|self[\s-]?motivat\w*|motivat\w*|mindset|ambigu\w*|relationships?|business development|"
    r"seller|doer|ownership|owner|accountab\w*|attitude|passion\w*|eager\w*|enthusias\w*|empath\w*|integrity|"
    r"proactiv\w*|self[\s-]starter|team player|teamwork|collaboration|collaborating|cross[\s-]functional|communicat\w*|"
    r"interpersonal|presentation skills|stakeholders?|leadership|mentor\w*|coach\w*|"
    r"time zones?|disciplines|culture|values|in[\s-]person|interactions?|hybrid|remote|on[\s-]?site|office|"
    r"policy|policies|adjustments?|accommodat\w*|recruit\w*|hiring|interview\w*|application experience|"
    r"diversity|inclusion|benefits?|salary|travel|relocat\w*|visa|sponsorship|clearance|degree|"
    r"years?|fast[\s-]paced|detail[\s-]oriented|organi[sz]ed|independen\w*|autonom\w*|adaptab\w*|flexib\w*|"
    r"analytic(al)?|problem[\s-]?solv\w*|critical[\s-]thinking|preferred|asset|plus)\b",
    re.IGNORECASE)
# words that make an unknown term technical ("Jupyter notebooks", "data engineering workflows")
_TECH_WORD = re.compile(
    r"\b(data|database|sql|api|apis|cloud|pipelines?|etl|elt|platforms?|software|code|coding|programming|"
    r"scripts?|scripting|automat\w*|test\w*|qa|devops|ci/cd|deploy\w*|infrastructure|containers?|containeri[sz]\w*|"
    r"kubernetes|microservices?|architecture|integrations?|security|network\w*|linux|ai|ml|llm|models?|"
    r"machine learning|analytics|visuali[sz]ation|dashboards?|notebooks?|frameworks?|libraries|sdk|"
    r"engineering|systems?|server\w*|backend|frontend|web|mobile|ios|android|monitoring|observability|"
    r"debug\w*|troubleshoot\w*|version control|git|agile|scrum|prompt\w*|semantic|warehouse|lake\w*)\b",
    re.IGNORECASE)


def is_people_skill(term: str) -> bool:
    """Positively about personality, ways of working or hiring (not merely an unknown word)."""
    t = " ".join(term.split())
    return canonical(t).lower() in {x.lower() for x in SOFT_TERMS} or (
        bool(NOT_TECH.search(t)) and canonical(t) not in _CANON.values())


def is_technical(term: str) -> bool:
    """Is this keyword about technical experience (tools, languages, platforms, engineering practice)?

    Alternatives ("Power BI/Databricks") are technical when any part is. Unknown terms count when they name a
    technical thing ("Jupyter notebooks") or look like a product name ("Palantir Foundry"), and never when they
    are about personality, ways of working or hiring.
    """
    parts = [p.strip() for p in term.split("/")] if "/" in term and term.lower() != "ci/cd" else [term.strip()]
    if len(parts) > 1:
        return any(is_technical(p) for p in parts if p)
    t = " ".join(term.split())
    known = canonical(t)
    if known.lower() in {s.lower() for s in SOFT_TERMS}:
        return False
    if known in _CANON.values():
        return True
    if NOT_TECH.search(t):
        return False
    if _TECH_WORD.search(t) or find_terms(t):
        return True
    # product names: every word capitalised, or a word with inner capitals / digits ("PyTorch", "S3")
    words = t.split()
    return bool(words) and (all(w[:1].isupper() for w in words) or any(re.search(r"[a-z][A-Z]|\d", w) for w in words))
