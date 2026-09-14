"""Every number that came from outside this repository, with where it came from.

A staffing model is a chain of assumptions, and the arithmetic is the least
interesting link. Handle time, service promise, shrinkage and the cost of an
hour decide the answer; the queueing theory only arranges them. So they live
here, in one file, each with a citation and a confidence, rather than scattered
through the code as defaults nobody can audit.

Two of these were *not* found, and saying so is part of the job:

* **NYC 311 does not publish a handle time.** The Mayor's Management Report
  gives call volume, wait time and the percentage answered in thirty seconds,
  but never AHT. Any NYC handle time in a README is invented, so the default
  here is Toronto's, which is measured and published.
* **Nobody publishes the chat concurrency penalty.** The academic literature
  takes the agent service-rate function as given and solves for routing around
  it. The figure that circulates in practice (2→1.7, 3→2.5, 4→2.9) has no
  traceable derivation. What is used here is a model with a measurable input
  instead of a table with none.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Benchmark:
    value: float
    unit: str
    what: str
    source: str
    url: str
    confidence: str          # "measured" | "published" | "derived" | "assumed"
    note: str = ""


# ── the workload ─────────────────────────────────────────────────────────

VOICE_AHT = Benchmark(
    290.0, "seconds",
    "average handle time on a general-purpose municipal 311 phone line",
    "City of Toronto, 311 Toronto Annual Report 2025 — 4:50 over ~1.01M calls",
    "https://www.toronto.ca/legdocs/mmis/2026/se/bgrd/backgroundfile-285323.pdf",
    "published",
    "The only large, officially published, general-purpose 311 handle time found. "
    "Toronto's specialist lines run 6:13–8:20, so complexity moves this a lot. "
    "Defensible range for a general 311 line: 3–5 minutes.",
)

VOICE_SLA = Benchmark(
    0.80, "share",
    "share of calls NYC 311 commits to answering within the target",
    "NYC Mayor's Management Report FY2025 — target 80%, actual 80%",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
)

VOICE_SLA_SECONDS = Benchmark(
    30.0, "seconds",
    "the answer threshold NYC 311 commits to",
    "NYC Mayor's Management Report FY2025. Toronto commits to 75s, San Francisco to 60s",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
)

VOICE_ASA = Benchmark(
    25.0, "seconds",
    "average wait before reaching a tier-1 agent, NYC 311, FY2025",
    "NYC Mayor's Management Report FY2025 — 0:25 against a 0:30 target",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
)

NYC_ANNUAL_CALLS = Benchmark(
    17_377_000, "calls/year",
    "calls to NYC 311 in Fiscal 2025",
    "NYC Mayor's Management Report FY2025, '311 calls (000)' = 17,377",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
    "Five-year series: FY21 21,715k, FY22 18,231k, FY23 17,886k, FY24 17,458k, FY25 17,377k.",
)

SHRINKAGE = Benchmark(
    0.30, "of paid time",
    "paid hours an agent is not available to take contacts",
    "assumed by this project; no single published figure covers it",
    "",
    "assumed",
    "Breaks, training, meetings, sickness, holiday and after-call work. Every "
    "workforce team measures its own and they differ widely; 30% is a common "
    "planning figure rather than a measured one, and nothing here measures it. "
    "It bites harder than it looks: staffing divides by (1 - shrinkage), so 30% "
    "is a x1.43 multiplier on every hour Erlang C asks for, and it stacks with "
    "HEADCOUNT_UPLIFT for x1.69 over the theoretical minimum. Both are "
    "assumptions. An operation with its own number should pass --shrinkage and "
    "read the difference; the simulator on the page moves it live for the same "
    "reason.",
)

HEADCOUNT_UPLIFT = Benchmark(
    1.18, "x the theoretical floor",
    "how far above demand-divided-by-a-contracted-week to set the headcount",
    "assumed by this project; not taken from any published figure",
    "",
    "assumed",
    "The floor — total agent-hours over the contracted week — assumes hours can "
    "be sliced freely. They cannot: shifts are contiguous 4-9h blocks, demand is "
    "not flat so the peak sets the headcount and the trough wastes it, and the "
    "day and rest rules take usable capacity out. Some margin is therefore "
    "necessary. This particular margin is not derived and not sourced. It puts "
    "67 agents on the floor of 57, and every euro on the page scales with it. "
    "Deriving it properly means searching for the smallest headcount that still "
    "covers the week, which scripts/price_rules.py can do and which takes about "
    "an hour; until that is run and recorded, read this as the assumption it is.",
)

NYC_SPANISH_CALLS = Benchmark(
    484_000, "calls/year",
    "calls to NYC 311's Spanish-language line in Fiscal 2025",
    "NYC Mayor's Management Report FY2025, '311 Spanish language calls (000)' = 484",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
    "2.8% of all 311 calls, and the reason this desk is the one the report "
    "rosters: it is a real, named, separately reported operation of about "
    "twenty-five people, where the whole line is twelve hundred. Five-year "
    "series: FY21 648k, FY22 529k, FY23 598k, FY24 575k, FY25 484k.",
)

NYC_COMPLETED_REQUESTS = Benchmark(
    3_766_000, "requests/year",
    "service requests completed by NYC 311 in Fiscal 2025",
    "NYC Mayor's Management Report FY2025, 'Completed service requests (000)' = 3,766",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
)

NYC_TEXT_CONTACTS = Benchmark(
    270_000, "contacts/year",
    "311-NYC text message contacts in Fiscal 2025",
    "NYC Mayor's Management Report FY2025, '311-NYC (text) contacts (000)' = 270",
    "https://www.nyc.gov/assets/operations/downloads/pdf/mmr2025/311.pdf",
    "published",
    "The only genuinely conversational non-voice channel NYC reports. No hourly "
    "breakdown is published, so it cannot be driven from open data.",
)

TICKET_AHT = Benchmark(
    480.0, "seconds",
    "handling time for one deferred service request",
    "No public-sector figure found. Eight minutes is an assumption, exposed as a parameter",
    "",
    "assumed",
    "There is no defensible published handle time for email or ticket work in a "
    "public-sector contact centre. This is the weakest number in the model and "
    "the report says so.",
)

TICKET_ON_TIME = Benchmark(
    0.95, "share",
    "share of deferred transactions processed within the targeted cycle time",
    "COPC CX Standard Release 7.0 (2021), Exhibit 1, 'Human Assisted Deferred "
    "Transactions' — best-practice target for the 'On Time' metric",
    "https://cx.copc.com/hubfs/PDF/COPC_2021_CX_Standard_for_Customer_Operations_Release_7.0.pdf",
    "published",
    "Edition-dated: this is Release 7.0, not necessarily current COPC doctrine.",
)

CHAT_COMPOSE_RATIO = Benchmark(
    1.0, "ratio",
    "customer compose time divided by agent compose time, the one input the "
    "chat concurrency model needs",
    "US 8,064,589 B2 (Lewis & Beshears, 2011), 'Estimating number of agents for "
    "multiple chat channels'",
    "https://patents.google.com/patent/US8064589B2/en",
    "derived",
    "r = 1 means effective concurrency saturates at 2.0 however many windows are "
    "open. Measurable from agent-active-time telemetry; guessed here.",
)


# ── the cost of an hour, in Spain ────────────────────────────────────────

CONVENIO = "III Convenio colectivo estatal del sector de contact center"
CONVENIO_URL = "https://www.boe.es/buscar/doc.php?id=BOE-A-2023-13741"

GROSS_ANNUAL = Benchmark(
    17_139.58, "EUR/year",
    "gross annual salary, Nivel 10 (Teleoperador/a), 2026 table",
    f"{CONVENIO}, 2026 salary tables — BOE-A-2026-5300",
    "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-5300",
    "published",
    "14 payments: 12 monthly plus two extraordinary, June and December. "
    "2025 was €16,576.00; the 2026 tables rose 3.4%.",
)

ANNUAL_HOURS = Benchmark(
    1_764.0, "hours/year",
    "maximum ordinary annual working time",
    f"{CONVENIO}, art. 22 (Jornada) — 1,764 h/year, 39 h/week of effective work",
    CONVENIO_URL,
    "published",
    "These are ROSTERED hours, so dividing by them gives cost per scheduled hour, "
    "not per hour spent handling contacts. Shrinkage is applied separately.",
)

EMPLOYER_SS = Benchmark(
    0.3215, "share of gross",
    "employer social security contribution in Spain",
    "23.60% common contingencies + 5.50% unemployment + 1.50% AT/EP + 0.75% MEI "
    "+ 0.60% vocational training + 0.20% FOGASA",
    "https://www.seg-social.es/",
    "published",
)

NIGHT_PREMIUM = Benchmark(
    1.96, "EUR/hour",
    "night work premium, flat per hour worked between 22:00 and 06:00",
    f"{CONVENIO}, plus de nocturnidad (2026 table)",
    "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-5300",
    "published",
    "The law itself sets no amount: ET art. 36.2 requires that night work carry "
    "a specific premium and delegates the figure entirely to bargaining.",
)

SUNDAY_PREMIUM = Benchmark(
    15.33, "EUR/day",
    "premium for a shift worked on a Sunday",
    f"{CONVENIO}, plus de domingo (2026 table)",
    "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-5300",
    "published",
    "There is no statutory Sunday premium in Spain at all; this is entirely "
    "collective agreement.",
)

HOLIDAY_PREMIUM = Benchmark(
    44.51, "EUR/day",
    "premium for a shift worked on a normal public holiday",
    f"{CONVENIO}, plus de festivo (2026 table). Special holidays carry €94.39",
    "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-5300",
    "published",
)

OVERTIME_UPLIFT = Benchmark(
    0.25, "share",
    "uplift on the ordinary hour for daytime overtime",
    f"{CONVENIO}. Night overtime +60%, holiday +60%, holiday night +80%",
    "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-5300",
    "published",
    "ET art. 35.1 only requires overtime to be worth no less than an ordinary "
    "hour; every percentage above that is collective agreement, not statute.",
)


ALL: list[Benchmark] = [
    VOICE_AHT, VOICE_SLA, VOICE_SLA_SECONDS, VOICE_ASA,
    NYC_ANNUAL_CALLS, NYC_SPANISH_CALLS, NYC_COMPLETED_REQUESTS, NYC_TEXT_CONTACTS,
    TICKET_AHT, TICKET_ON_TIME, CHAT_COMPOSE_RATIO,
    GROSS_ANNUAL, ANNUAL_HOURS, EMPLOYER_SS,
    NIGHT_PREMIUM, SUNDAY_PREMIUM, HOLIDAY_PREMIUM, OVERTIME_UPLIFT,
    SHRINKAGE, HEADCOUNT_UPLIFT,
]

WEAKEST = [b for b in ALL if b.confidence == "assumed"]
