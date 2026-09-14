"""The rule table, and the two things it must refuse to say.

This table is read wrong by default. People see a rule, a cost, and a
difference, and conclude that loosening the rule saves the money. At a fixed
headcount that is backwards: the solver is minimising uncovered demand, not
payroll, so extra freedom gets spent on covering more of the curve — and more
coverage is more rostered hours, which is a dearer week. A relaxation coming
out more expensive is the *expected* result.

So the property under test is not about money at all. It is that the feasible
sets constrain the coverage column and nothing else: a relaxation adds legal
rosters and can never cover less than the baseline, a tightening removes them
and can never cover more. A row that moves the wrong way was decided by the
clock running out.

What the table does about that changed once. It used to print the number under
a caveat; it now drops the whole column. Marking a value as unreadable and
leaving it on the page asks the reader to disregard something in front of them,
which nobody does — and a paragraph of apology for a number reads worse than
the number's absence. So when the search has not converged, the searched
columns go and the enumerated one carries the table.
"""

from shiftmesh.report import rule_prices_table


def row(rule, *, coverage=99.0, cost=30_000, spare=60, relaxation=True, note="why"):
    return {"rule": rule, "note": note, "relaxation": relaxation,
            "coverage": coverage, "spare_hours": spare, "cost": cost,
            "short_hours": 0, "rostered_hours": 2400.0, "shifts": 145,
            "status": "OPTIMAL", "objective": 100.0, "best_bound": 100.0,
            "gap": 0.0}


def test_an_empty_measurement_renders_nothing():
    assert rule_prices_table([], 67) == ""


def test_the_baseline_row_carries_no_difference():
    html = rule_prices_table([row("baseline", coverage=99.0)], 67)
    assert "<b>baseline</b>" in html
    assert "—" in html


def test_a_relaxation_that_covers_more_shows_what_it_bought():
    html = rule_prices_table([
        row("baseline", coverage=99.43),
        row("rest 12h → 11h", coverage=99.87),
    ], 67)
    assert "+0.44pp" in html
    assert "accent2" in html, "coverage gained should read as a gain"


def test_a_rule_that_buys_no_coverage_says_so():
    """The most useful row in the table: a constraint you can defend for free."""
    html = rule_prices_table([
        row("baseline", coverage=99.43),
        row("5 → 6 working days", coverage=99.431),
    ], 67)
    assert "nothing" in html
    assert "0.00pp" not in html, "zero is a number; 'nothing' is the finding"


def test_a_relaxation_that_costs_money_is_priced_normally():
    """The regression that matters.

    An earlier version refused any relaxation whose week came out dearer, on
    the theory that relaxing a rule cannot make things worse. That theory holds
    when you are searching for the smallest headcount; it is false at a fixed
    one, where the extra freedom is spent on coverage and paid for in hours.
    The guard fired on six rows out of seven and blanked a correct table.
    """
    html = rule_prices_table([
        row("baseline", coverage=99.43, cost=31_169),
        row("rest 12h → 11h", coverage=99.87, cost=31_385),
    ], 67)
    assert "+0.44pp" in html
    assert "€31,385" in html
    assert "ran out of time" not in html
    assert "?" not in html




def test_a_tightening_that_covers_less_is_reported_normally():
    html = rule_prices_table([
        row("baseline", coverage=99.43),
        row("start times pinned to ±3h", coverage=98.90, relaxation=False),
    ], 67)
    assert "−0.53pp" in html
    assert "ran out of time" not in html


def test_the_table_does_not_call_itself_a_price_list():
    """The caption is the only defence against the reading everyone brings."""
    html = rule_prices_table([row("baseline")], 67)
    assert "not what it costs" in html
    assert "coverage, not on savings" in html


def test_the_headcount_is_stated_because_the_answer_depends_on_it():
    html = rule_prices_table([row("baseline")], 67)
    assert "67 agents" in html


def test_rule_names_are_escaped():
    html = rule_prices_table([row("<script>x</script>")], 67)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html



def test_a_self_consistent_table_carries_no_warning():
    """The banner has to stay rare, or it stops being read."""
    html = rule_prices_table([
        row("baseline", coverage=99.43),
        row("rest 12h → 11h", coverage=99.87),
        row("start times pinned to ±3h", coverage=98.90, relaxation=False),
    ], 67)
    assert "not converged" not in html
    assert "note warn" not in html


def test_the_exact_column_the_warning_points_at_actually_exists():
    """The banner tells the reader to fall back on the shift count.

    That advice is only honest if the count is on screen. It is the one number
    in the table that is enumerated rather than searched, so it survives a
    search that proved nothing.
    """
    html = rule_prices_table([
        row("baseline", coverage=99.43),
        dict(row("split shifts allowed", coverage=99.16), shifts=1105),
    ], 67)
    assert "Legal shifts" in html
    assert "1,105" in html



def test_a_converged_table_says_nothing_about_gaps():
    html = rule_prices_table([
        dict(row("baseline", coverage=99.43), gap=0.0),
        dict(row("rest 12h → 11h", coverage=99.87), gap=0.0),
    ], 67)
    assert "optimality gap" not in html
    assert "note warn" not in html

def test_an_unreadable_measurement_drops_the_columns_it_cannot_support():
    """The contract that replaced the caveat.

    Printing euros under a paragraph explaining that the euros cannot be read
    asks the reader to do something nobody does. When the search has not
    converged, the searched columns are simply not shown, and the one column
    that is enumerated rather than searched carries the table on its own.
    """
    html = rule_prices_table([
        dict(row("baseline", coverage=99.43), gap=0.99),
        dict(row("split shifts allowed", coverage=99.16), gap=0.98, shifts=1105),
    ], 67)
    assert "Week costs" not in html
    assert "Coverage" not in html
    assert "€" not in html
    assert "no price column, on purpose" in html
    assert "1,105" in html, "the exact column has to survive"


def test_an_ordering_inversion_alone_is_enough_to_drop_them():
    """A tightening cannot out-cover the baseline, so one that did proves the
    baseline is the weaker solve — and every number measured against it goes."""
    html = rule_prices_table([
        dict(row("baseline", coverage=99.43), gap=0.0),
        dict(row("start times pinned to ±3h", coverage=99.74, relaxation=False), gap=0.0),
    ], 67)
    assert "Week costs" not in html
    assert "no price column, on purpose" in html


def test_a_converged_self_consistent_table_keeps_everything():
    html = rule_prices_table([
        dict(row("baseline", coverage=99.43), gap=0.0),
        dict(row("rest 12h → 11h", coverage=99.87), gap=0.0),
    ], 67)
    assert "Week costs" in html
    assert "Coverage" in html
    assert "+0.44pp" in html
    assert "no price column" not in html
